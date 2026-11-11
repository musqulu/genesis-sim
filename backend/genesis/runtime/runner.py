"""Single simulation owner.

All state changes pass through one lock. A background thread decides *when* the next tick
is attempted; speed changes that pacing and nothing else. Logical time advances only by
committed ticks: a tick becomes visible to observers after its transaction commits.

State schema 6 ticks pass a decision barrier: the tick is prepared, its requests are
persisted and dispatched, and it commits only once every response is present and the run
is running (or one step was authorized). A pause while responses are pending keeps them
and commits nothing. Provider failures are technical: the run pauses, no ecological time
passes, and nothing is invented in place of a missing response.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from genesis.cognition import fixture_policy
from genesis.cognition.context import DecisionRequest, build_request
from genesis.cognition.session import decision_payload, parse_for, update_previous
from genesis.domain.config import LifeConfig, WorldConfig
from genesis.domain.state import World
from genesis.engine.actions import available_actions
from genesis.engine.world import (
    TickResult,
    advance,
    finish_tick,
    prepare_tick,
)
from genesis.ops import report
from genesis.persistence.sqlite import (
    ControlRecord,
    DecisionAttempt,
    RestoreInfo,
    RunMeta,
    SqliteStore,
    interaction,
)
from genesis.providers import config as api_config
from genesis.providers.base import Provider, ProviderError, ProviderResponse
from genesis.providers.http_chat import HttpChatProvider
from genesis.providers.mixed import MixedProvider
from genesis.providers.mlx import MLXProvider
from genesis.providers.recorded import RecordedProvider
from genesis.providers.replicate import ReplicateProvider
from genesis.providers.scripted import ScriptedProvider
from genesis.runtime.commands import (
    MAX_SPEED,
    MIN_SPEED,
    Command,
    CommandError,
    Pause,
    Resume,
    SetSpeed,
    Start,
    Step,
)
from genesis.runtime.scheduler import InferenceWorker, PendingTick

log = logging.getLogger("genesis.runner")
DEFAULT_SPEED = 5
STEP_WAIT_SECONDS = 5.0  # how long a Step request waits for its tick before returning


def make_provider(
    world: World,
    delay_scale: float = 1.0,
    profile: dict | None = None,
    usage: Callable[[tuple[str, ...] | None], tuple[int, int]] | None = None,
) -> Provider | None:
    """The run's provider profile, fixed at creation; never switched automatically.

    An unavailable local model does not fall back to anything: its requests fail as
    technical errors and the run pauses. `usage` returns recorded request/token totals
    (optionally for some organisms only) so API budgets survive a restart."""
    if world.schema < 6 or world.life is None:
        return None
    usage = usage or (lambda actors: (0, 0))
    if world.life.provider == "mixed":
        profile = profile or {}
        members, assignment = [], {}
        for index, member in enumerate(profile.get("members", [])):
            actors = tuple(a for a, i in profile["assignment"].items() if i == index)
            members.append(_single(world, member["cognition"], member["profile"], usage(actors)))
        assignment = {actor: int(i) for actor, i in profile.get("assignment", {}).items()}
        return MixedProvider(members, assignment)
    return _single(world, world.life.provider, profile, usage(None), delay_scale)


def _single(
    world: World,
    selection: str,
    profile: dict | None,
    used: tuple[int, int],
    delay_scale: float = 1.0,
) -> Provider:
    kind, _, name = selection.partition(":")
    if kind == "scripted":
        return ScriptedProvider({o.id: o.policy for o in world.organisms})
    if kind == "mlx":
        return MLXProvider(name, profile)
    if kind == "api":
        # Built from the sealed manifest record, never the live profiles file, so editing
        # the file cannot change a run that already exists.
        sealed = api_config.from_sealed(profile) if profile else api_config.get_profile(name)
        if sealed.protocol == "replicate":
            return ReplicateProvider(sealed, (profile or {}).get("replicate") or {}, used=used)
        return HttpChatProvider(sealed, used=used)
    return RecordedProvider(name, delay_scale=delay_scale)


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    meta: RunMeta
    world: World
    status: str  # "paused" | "running"
    started: bool
    speed: int
    until_tick: int | None
    last_event_sequence: int
    changed_indices: tuple[int, ...]  # cells changed by the latest committed tick
    changed_organisms: tuple[str, ...]  # organisms changed by the latest committed tick
    last_error: str | None
    restore: RestoreInfo | None
    pending: dict | None  # tick waiting at the decision barrier
    provider: dict | None  # identity and capabilities of the run's provider
    report_path: str | None  # latest saved plain-English summary of this run
    interactions: tuple[dict, ...] = ()  # transfers/messages committed in the latest tick


class SimulationRunner:
    def __init__(self, store: SqliteStore, *, speed: int = DEFAULT_SPEED, delay_scale: float = 1.0):
        self.store = store
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._speed = speed
        self.delay_scale = delay_scale
        self._generation = 0
        self._worker: InferenceWorker | None = None
        self._load(store.load_active())

    # -- state ------------------------------------------------------------------------
    def _load(self, loaded) -> None:
        self._meta = loaded.meta
        self._world = loaded.world
        self._started = loaded.started
        self._last_sequence = loaded.last_event_sequence
        self._restore = loaded.restore
        found = report.report_path(self.store, loaded.meta.run_id)
        self._report_path = None if found is None else str(found)
        self._changed = tuple(self.store.events_at_tick(loaded.meta.run_id, loaded.world.tick))
        self._changed_organisms = tuple(
            self.store.changed_organisms_at_tick(loaded.meta.run_id, loaded.world.tick)
        )
        self._interactions = tuple(
            self.store.interactions_at_tick(loaded.meta.run_id, loaded.world.tick)
        )
        self._running = False  # A restarted process is always paused; resume is explicit.
        self._until: int | None = None
        self._error: str | None = None
        self._generation += 1  # responses for anything earlier are stale from now on
        self._pending: PendingTick | None = None
        self._step_budget = 0
        self._step_control: ControlRecord | None = None
        self._next_due = time.monotonic()
        if getattr(self, "_provider", None) is not None and hasattr(self._provider, "close"):
            self._provider.close()  # one resident model: the previous run's worker exits
        self._provider = make_provider(
            loaded.world,
            self.delay_scale,
            loaded.meta.provider_profile,
            lambda actors: self.store.usage_totals(loaded.meta.run_id, actors),
        )
        self._previous = self.store.previous_results(loaded.meta.run_id) if self._provider else {}
        if self._worker is not None:
            self._worker.stop()
            self._worker = None
        if self._provider is not None:
            self._worker = InferenceWorker(loaded.world.life.queue_capacity, self._on_result)

    def _snapshot(self) -> RunSnapshot:
        return RunSnapshot(
            meta=self._meta,
            world=self._world,
            status="running" if self._running else "paused",
            started=self._started,
            speed=self._speed,
            until_tick=self._until,
            last_event_sequence=self._last_sequence,
            changed_indices=self._changed,
            changed_organisms=self._changed_organisms,
            last_error=self._error,
            restore=self._restore,
            pending=None if self._pending is None else self._pending.view(),
            provider=None
            if self._provider is None
            else {
                **self._provider.identity.to_dict(),
                "capabilities": self._provider.capabilities.to_dict(),
                "health": self._provider.health() if hasattr(self._provider, "health") else None,
                "profile": self._meta.provider_profile,
                "members": self._provider.members_view()
                if hasattr(self._provider, "members_view")
                else None,
            },
            report_path=self._report_path,
            interactions=self._interactions,
        )

    def _identity_for(self, actor_id: str):
        if hasattr(self._provider, "identity_for"):
            return self._provider.identity_for(actor_id)
        return self._provider.identity

    def snapshot(self) -> RunSnapshot:
        with self._cond:
            return self._snapshot()

    # -- lifecycle --------------------------------------------------------------------
    def start_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="genesis-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._running = False
            self._cond.notify_all()
        if self._thread:
            self._thread.join(timeout=10)
        if self._worker is not None:
            self._worker.stop()
        if self._provider is not None and hasattr(self._provider, "close"):
            self._provider.close()
        with self._cond:
            self._snapshot_committed("close")

    def _snapshot_committed(self, reason: str) -> None:
        """Graceful snapshot of the committed tick. A failure is technical, never ecological:
        history remains replayable from recorded events, so the run is not altered."""
        try:
            self.store.write_snapshot(self._meta.run_id, self._world, reason)
        except Exception:  # noqa: BLE001
            log.exception(
                "Snapshot at tick %s failed; history remains replayable", self._world.tick
            )

    # -- ticks ------------------------------------------------------------------------
    def _publish(self, result: TickResult, sequence: int) -> None:
        width = result.world.config.width
        self._world = result.world
        self._last_sequence = sequence
        self._changed = tuple(sorted({c.y * width + c.x for e in result.events for c in e.changes}))
        self._changed_organisms = tuple(
            sorted({c.organism_id for e in result.events for c in e.organism_changes})
        )
        self._interactions = tuple(
            interaction(e.actor_id, e.summary)
            for e in result.events
            if e.type == "action_accepted"
            and e.summary.get("action") in ("transfer", "communicate")
        )
        update_previous(self._previous, result)

    def _take_step_control(self) -> ControlRecord | None:
        control, self._step_control = self._step_control, None
        if self._step_budget:
            self._step_budget -= 1
        return control

    def _begin_tick(self) -> None:
        """Prepare the next tick. Legacy (schema <= 5) and decision-free ticks commit now;
        otherwise requests are persisted and the tick waits at the barrier."""
        if self._world.schema < 6:
            # Fixture organisms are driven by scripted test policies that see only observations.
            result = advance(self._world, fixture_policy.decide)
            control = self._take_step_control()
            self._publish(result, self.store.commit_tick(self._meta.run_id, result, control))
            self._after_commit()
            return
        prepared = prepare_tick(self._world)
        if not prepared.slots:  # no due decisions: zero requests, zero provider calls
            result = finish_tick(prepared, {})
            control = self._take_step_control()
            self._publish(result, self.store.commit_tick(self._meta.run_id, result, control))
            self._after_commit()
            return
        requests = {
            slot.organism_id: build_request(
                self._meta.run_id, prepared.world, slot, self._previous.get(slot.organism_id)
            )
            for slot in prepared.slots
        }
        reused = self.store.record_requests(self._meta.run_id, list(requests.values()))
        pending = PendingTick(self._generation, prepared, requests)
        for actor, request in requests.items():
            if request.request_id in reused:  # recovered response: never asked twice
                stored = reused[request.request_id]
                pending.responses[actor] = (stored["raw"], {**stored["meta"], "reused": True})
            else:
                pending.queued.append(actor)
        self._pending = pending

    def _feed_worker(self) -> None:
        pending = self._pending
        while pending.queued and len(pending.in_flight) < self._worker.capacity:
            actor = pending.queued.pop(0)
            pending.in_flight.add(actor)
            self._worker.submit(pending.generation, self._provider, pending.requests[actor])

    def _on_result(
        self,
        generation: int,
        request: DecisionRequest,
        response: ProviderResponse | None,
        error: ProviderError | None,
    ) -> None:
        """Called from the worker thread. Stale and duplicate responses are refused."""
        with self._cond:
            pending = self._pending
            current = (
                pending is not None
                and generation == pending.generation
                and pending.requests.get(request.actor_id) == request
            )
        if not current:
            self.store._technical(
                self._meta.run_id, "stale_response", request.request_id, "ignored"
            )
            return
        stored = False
        if response is not None:
            meta = {
                "usage": response.usage.to_dict(),
                "finish_reason": response.finish_reason,
                "provider": self._identity_for(request.actor_id).to_dict(),
                "detail": response.detail,
            }
            stored = self.store.complete_request(
                self._meta.run_id, request, response.raw_text, meta
            )
            if not stored:
                self.store._technical(
                    self._meta.run_id, "duplicate_response", request.request_id, "ignored"
                )
        else:
            self.store.fail_request(self._meta.run_id, request, f"{error.kind}: {error}")
        with self._cond:
            if self._pending is not pending:
                return
            pending.in_flight.discard(request.actor_id)
            if response is not None and stored:
                pending.responses[request.actor_id] = (response.raw_text, meta)
            elif error is not None:
                pending.failures[request.actor_id] = f"{error.kind}: {error}"
            self._cond.notify_all()

    def _commit_pending(self) -> None:
        pending = self._pending
        prepared = pending.prepared
        available = available_actions(prepared.world)
        outcomes, parsed_by_actor = {}, {}
        for slot in prepared.slots:
            request = pending.requests[slot.organism_id]
            raw, _ = pending.responses[slot.organism_id]
            parsed = parse_for(request, raw, prepared.world.life, available)
            parsed_by_actor[slot.organism_id] = parsed
            outcomes[slot.organism_id] = (
                parsed.outcome,
                decision_payload(slot, request, raw, parsed),
            )
        result = finish_tick(prepared, outcomes)
        outcome_events = {
            e.actor_id: e for e in result.events if e.type in ("action_accepted", "action_rejected")
        }
        attempts = []
        for slot in prepared.slots:
            actor = slot.organism_id
            request, parsed = pending.requests[actor], parsed_by_actor[actor]
            raw, meta = pending.responses[actor]
            event = outcome_events[actor]
            outcome = (
                "accepted"
                if event.type == "action_accepted"
                else "unparsed"
                if event.rule_id == "cognition.parse.v1"
                else "rejected"
            )
            attempts.append(
                DecisionAttempt(
                    request_id=request.request_id,
                    tick=prepared.world.tick,
                    actor_id=actor,
                    input_hash=request.input_hash,
                    provider=meta.get("provider", self._provider.identity.to_dict()),
                    raw_output=raw,
                    parse_status=parsed.status,
                    parse_error=parsed.detail,
                    proposal=parsed.outcome.to_dict() if parsed.status == "valid" else None,
                    outcome=outcome,
                    outcome_rule=event.rule_id,
                    outcome_reason=event.summary.get("reason"),
                    usage={
                        **meta.get("usage", {}),
                        "finish_reason": meta.get("finish_reason"),
                        "reused": bool(meta.get("reused")),
                    },
                )
            )
        control = self._take_step_control()
        sequence = self.store.commit_tick(self._meta.run_id, result, control, tuple(attempts))
        self._pending = None
        self._publish(result, sequence)
        self._after_commit()

    def write_report(self, status: str | None = None) -> tuple[str, str]:
        """Write the plain-English summary of the active run (caller holds the lock)."""
        path, text = report.write_report(self.store, self._meta.run_id, status)
        self._report_path = str(path)
        return self._report_path, text

    def _write_report_safely(self, status: str) -> None:
        """Summaries are technical output: a failure is logged and never alters the run."""
        try:
            self.write_report(status)
        except Exception:  # noqa: BLE001
            log.exception("Could not write the run summary for %s", self._meta.run_id)

    def _after_commit(self) -> None:
        if self._world.extinct:
            # Extinction archives the run: logical time stops for good.
            if self._running:
                self._log("auto_pause", {"reason": "extinction"})
            self._running = False
            self._until = None
            self._step_budget = 0
            self._snapshot_committed("extinction")
            self._write_report_safely("extinct")
        elif self._running and self._until is not None and self._world.tick >= self._until:
            self._running = False
            self._until = None
            self._log("auto_pause", {"reason": "target_tick"})
            self._snapshot_committed("auto_pause")
        self._next_due = time.monotonic() + 1 / self._speed
        self._cond.notify_all()

    def _technical_pause(self, message: str, exc: Exception | None = None) -> None:
        # A technical failure pauses logical time; it never alters or rescues the world.
        if exc is not None:
            log.exception("Tick failed; run paused without changing committed state")
        self._running = False
        self._until = None
        self._step_budget = 0
        self._step_control = None
        self._error = message
        self._cond.notify_all()

    def _retry_failures(self) -> None:
        """An explicit resume/step retries failed requests (same request ids and inputs)."""
        pending = self._pending
        if pending is None or not pending.failures:
            return
        failed = [a for a in pending.requests if a in pending.failures]
        self.store.record_requests(self._meta.run_id, [pending.requests[a] for a in failed])
        pending.failures.clear()
        pending.queued.extend(a for a in failed if a not in pending.queued)

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
                if not (self._running or self._step_budget):
                    self._cond.wait()
                    continue
                try:
                    if self._pending is None:
                        delay = self._next_due - time.monotonic()
                        if self._running and not self._step_budget and delay > 0:
                            self._cond.wait(timeout=delay)
                            continue
                        self._begin_tick()
                        continue
                    pending = self._pending
                    if pending.failures:
                        actor, reason = next(iter(pending.failures.items()))
                        self.store._technical(
                            self._meta.run_id, "provider_failure", f"tick {pending.tick}", reason
                        )
                        self._technical_pause(
                            f"Provider failure at tick {pending.tick} ({actor}): {reason}. "
                            "No ecological time passed; resume or step to retry."
                        )
                        continue
                    if pending.complete:
                        self._commit_pending()
                        continue
                    self._feed_worker()
                    self._cond.wait(timeout=0.5)
                except Exception as exc:  # noqa: BLE001 - reported, run paused
                    self._technical_pause(f"{type(exc).__name__}: {exc}", exc)

    # -- commands ---------------------------------------------------------------------
    def _log(self, command: str, params: dict, command_id: str | None = None) -> None:
        self.store.log_control(
            self._meta.run_id,
            ControlRecord(command_id or str(uuid4()), command, params, self._world.tick),
        )

    def execute(self, command: Command) -> RunSnapshot:
        with self._cond:
            if self.store.control_seen(self._meta.run_id, command.command_id):
                return self._snapshot()  # Idempotent replay of an already applied command.
            if self._world.extinct and isinstance(command, Start | Resume | Step):
                raise CommandError(
                    409,
                    f"The population went extinct at tick {self._world.tick}; this run is "
                    "archived and cannot advance. Inspect its history or start a new run.",
                )
            match command:
                case Start(until_tick=until) | Resume(until_tick=until):
                    self._begin(command, until)
                case Pause():
                    if self._running:
                        self._running = False
                        self._until = None
                    self._step_budget = 0
                    pending = None if self._pending is None else self._pending.view()
                    self._log("pause", {"pending": pending}, command.command_id)
                    self._snapshot_committed("pause")
                case Step():
                    if self._running:
                        raise CommandError(409, "Pause the run before stepping")
                    self._error = None
                    self._retry_failures()
                    target = self._world.tick + 1
                    self._step_control = ControlRecord(
                        command.command_id, "step", {}, self._world.tick
                    )
                    self._step_budget = 1
                    self.start_thread()
                    self._cond.notify_all()
                    deadline = time.monotonic() + STEP_WAIT_SECONDS
                    while (
                        self._world.tick < target
                        and self._step_budget
                        and self._error is None
                        and time.monotonic() < deadline
                    ):
                        self._cond.wait(timeout=deadline - time.monotonic())
                    if self._error is not None and self._world.tick < target:
                        raise CommandError(503, self._error)
                case SetSpeed(ticks_per_second=speed):
                    if type(speed) is not int or not MIN_SPEED <= speed <= MAX_SPEED:
                        raise CommandError(422, "Speed must be 1-1000 ticks per second")
                    self._speed = speed
                    self._log("speed", {"ticks_per_second": speed}, command.command_id)
            self._cond.notify_all()
            return self._snapshot()

    def _begin(self, command: Start | Resume, until: int | None) -> None:
        name = "start" if isinstance(command, Start) else "resume"
        if until is not None and until <= self._world.tick:
            raise CommandError(422, "Pause-at tick must be later than the current tick")
        if self._running:
            raise CommandError(409, "The run is already running")
        if name == "start" and self._started:
            raise CommandError(409, "The run was already started; use resume")
        if name == "resume" and not self._started:
            raise CommandError(409, "The run has not been started")
        self._error = None
        self._retry_failures()
        self._log(name, {"until_tick": until}, command.command_id)
        self._started = True
        self._until = until
        self._running = True
        self._next_due = time.monotonic()
        self.start_thread()

    def create_run(
        self,
        config: WorldConfig,
        life: LifeConfig | None = None,
        provider_profile: dict | None = None,
    ) -> RunSnapshot:
        with self._cond:
            # Create first: if that fails, the current run is left exactly as it was.
            loaded = self.store.create_run(config, life, provider_profile)
            if self._pending is not None:
                self.store.abandon_requests(self._meta.run_id, "new run created")
            if self._running:
                # Stopping the previous run is an operator intervention; record why.
                self._running = False
                self._log("pause", {"reason": "new_run", "next_run_id": loaded.meta.run_id})
                self._snapshot_committed("pause")
            if self._world.tick > 0:  # summarize the outgoing run before it is replaced
                self._write_report_safely("extinct" if self._world.extinct else "replaced")
            self._load(loaded)
            self._cond.notify_all()
            return self._snapshot()
