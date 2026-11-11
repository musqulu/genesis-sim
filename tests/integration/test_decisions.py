"""M06 decision barrier through the runner, store and API."""

import itertools
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.domain.config import FIXTURES, LifeConfig, SeedOrganism, WorldConfig
from genesis.persistence.sqlite import SqliteStore
from genesis.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderIdentity,
    ProviderResponse,
    Usage,
)
from genesis.runtime import runner as runner_module
from genesis.runtime.commands import CommandError, Pause, Resume, Start, Step
from genesis.runtime.runner import SimulationRunner

_ids = itertools.count(1)


def cid(prefix="cmd"):
    return f"{prefix}-{next(_ids):06d}"


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


@pytest.fixture
def runners(tmp_path):
    made = []

    def make(fixture=None, *, name="db", delay_scale=1.0, speed=1000, life=None):
        store = SqliteStore(tmp_path / f"{name}.sqlite3")
        store.initialize()
        if fixture or life:
            config = WorldConfig.for_run(42, fixture)
            store.create_run(config, life or FIXTURES[fixture].life)
        runner = SimulationRunner(store, speed=speed, delay_scale=delay_scale)
        runner.start_thread()
        made.append(runner)
        return runner

    yield make
    for r in made:
        r.stop()


def run_to_extinction(runner):
    runner.execute(Start(cid("start")))
    wait_for(lambda: runner.snapshot().world.extinct, timeout=30)
    return runner.snapshot()


def attempts(store, run_id):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(
            "SELECT request_id, tick, parse_status, outcome, outcome_reason FROM "
            "decision_attempts WHERE run_id = ? ORDER BY tick",
            (run_id,),
        ).fetchall()


def requests(store, run_id):
    with closing(sqlite3.connect(store.path)) as db:
        return db.execute(
            "SELECT request_id, status, attempts FROM request_records WHERE run_id = ? "
            "ORDER BY tick",
            (run_id,),
        ).fetchall()


# -- deterministic recorded runs --------------------------------------------------------------


def test_recorded_runs_are_deterministic_and_consume_one_proposal_per_request(runners):
    finals = []
    for i in range(2):
        r = runners("recorded_valid", name=f"valid{i}", speed=300 + 500 * i)
        final = run_to_extinction(r)
        finals.append(final.world.state_hash())
        rows = attempts(r.store, final.meta.run_id)
        assert len(rows) == 14 == r._provider.calls  # 14 recorded decisions
        assert len({row[0] for row in rows}) == len(rows)
        assert all(status == "consumed" for _, status, _ in requests(r.store, final.meta.run_id))
    assert finals[0] == finals[1]


def test_delay_and_pacing_do_not_change_the_world(runners):
    hashes = []
    for i, (scale, speed) in enumerate([(0.0005, 1000), (0.003, 200)]):
        r = runners("recorded_delayed", name=f"delay{i}", delay_scale=scale, speed=speed)
        hashes.append(run_to_extinction(r).world.state_hash())
    assert hashes[0] == hashes[1]


def test_invalid_outputs_are_recorded_consumed_and_never_retried(runners):
    r = runners("recorded_invalid")
    final = run_to_extinction(r)
    rows = attempts(r.store, final.meta.run_id)
    assert r._provider.calls == len(rows) == 12  # no retries for semantic problems
    assert [row[2] for row in rows][:9] == [
        "malformed_json",
        "schema_invalid",
        "schema_invalid",
        "schema_invalid",
        "schema_invalid",
        "empty_output",
        "valid",
        "not_an_object",
        "malformed_json",
    ]
    assert rows[6][3:] == ("rejected", "out_of_bounds")  # well-formed but illegal
    assert all(row[3] == "unparsed" for i, row in enumerate(rows[:9]) if i != 6)
    o = final.world.organisms[0]
    assert (o.x, o.y, o.carried_material) == (3, 0, 0)  # nothing but fees ever applied
    assert (
        final.world.ledger.dissipation
        == final.world.ledger.initial_energy
        - (final.world.total_energy + final.world.ledger.death_retirement)
        + final.world.ledger.environmental_inflow
    )


# -- the barrier: pending responses and pause -------------------------------------------------


def test_pause_with_a_delayed_response_pending_commits_nothing_then_exactly_once(runners):
    r = runners("recorded_delayed", delay_scale=0.15)  # 3 s recorded delay -> 0.45 s
    run_id = r.snapshot().meta.run_id
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().pending is not None)
    pending = r.snapshot().pending
    assert pending["tick"] == r.snapshot().world.tick + 1 and pending["completed"] == 0
    r.execute(Pause(cid("pause")))
    frozen = r.snapshot().world.tick
    wait_for(lambda: r.snapshot().pending["completed"] == 1, timeout=5)  # response retained
    time.sleep(0.3)
    snap = r.snapshot()
    assert snap.world.tick == frozen and snap.status == "paused"
    assert ("completed",) in {(s,) for _, s, _ in requests(r.store, run_id)}
    assert attempts(r.store, run_id) == []  # nothing consumed while paused
    r.execute(Resume(cid("resume")))
    wait_for(lambda: r.snapshot().world.tick > frozen)
    r.execute(Pause(cid("pause")))
    rows = attempts(r.store, run_id)
    assert rows[0][1] == pending["tick"] and len({row[0] for row in rows}) == len(rows)


def test_step_waits_for_the_response_and_commits_one_tick(runners):
    r = runners("recorded_delayed", delay_scale=0.05)
    snap = r.execute(Step(cid("step")))
    assert snap.world.tick == 1 and snap.pending is None
    assert len(attempts(r.store, snap.meta.run_id)) == 1


# -- technical failures ------------------------------------------------------------------------


def test_recording_mismatch_is_a_technical_pause_not_an_ecological_event(runners):
    r = runners("recorded_mismatch")
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().last_error is not None)
    snap = r.snapshot()
    assert "mismatch" in snap.last_error and snap.status == "paused"
    assert snap.world.tick == 4  # decision 2 would happen at tick 5: no time passed
    assert snap.pending["tick"] == 5 and snap.pending["failed"] == 1
    assert len(attempts(r.store, snap.meta.run_id)) == 1
    assert any(n["kind"] == "provider_failure" for n in r.store.technical_log(snap.meta.run_id))
    with pytest.raises(CommandError) as err:
        r.execute(Step(cid("step")))  # retrying cannot match either; still no commit
    assert err.value.status == 503 and "mismatch" in err.value.message
    assert r.snapshot().world.tick == 4


class Flaky(Provider):
    capabilities = Capabilities("none", None, None, 1)

    def __init__(self, failures, kind="timeout"):
        self.identity = ProviderIdentity("test", "flaky")
        self.failures, self.kind, self.calls = failures, kind, 0

    def decide(self, request, cancel):
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise ProviderError(self.kind, "simulated outage")
        return ProviderResponse(
            request.request_id, request.input_hash, '{"action": "wait"}', Usage()
        )


def flaky_runner(runners, monkeypatch, provider):
    monkeypatch.setattr(runner_module, "make_provider", lambda world, *args: provider)
    life = LifeConfig(seeds=(SeedOrganism(4, 4, "wait"),))
    return runners(life=life)


def test_one_transport_retry_then_success(runners, monkeypatch):
    provider = Flaky(1)
    r = flaky_runner(runners, monkeypatch, provider)
    snap = r.execute(Step(cid("step")))
    assert snap.world.tick == 1 and provider.calls == 2 and snap.last_error is None


def test_repeated_transport_failure_pauses_and_resume_retries(runners, monkeypatch):
    provider = Flaky(2)
    r = flaky_runner(runners, monkeypatch, provider)
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().last_error is not None)
    snap = r.snapshot()
    assert snap.world.tick == 0 and provider.calls == 2 and "timeout" in snap.last_error
    r.execute(Resume(cid("resume"), until_tick=1))  # retry the same request, then stop
    wait_for(lambda: r.snapshot().world.tick == 1 and r.snapshot().status == "paused")
    assert provider.calls == 3 and r.snapshot().last_error is None


def test_non_retryable_errors_are_not_retried(runners, monkeypatch):
    provider = Flaky(5, kind="auth")
    r = flaky_runner(runners, monkeypatch, provider)
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().last_error is not None)
    assert provider.calls == 1 and r.snapshot().world.tick == 0


def test_stale_and_duplicate_responses_are_refused(runners):
    r = runners("recorded_delayed", delay_scale=0.2)
    run_id = r.snapshot().meta.run_id
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().pending is not None)
    r.execute(Pause(cid("pause")))
    wait_for(lambda: r.snapshot().pending["completed"] == 1, timeout=5)
    pending = r._pending
    request = next(iter(pending.requests.values()))
    # A response from an earlier generation (e.g. before a restart or new run) is stale.
    r._on_result(pending.generation - 1, request, None, ProviderError("timeout", "late"))
    # The same response delivered twice is a duplicate.
    assert not r.store.complete_request(run_id, request, '{"action": "wait"}', {})
    kinds = {n["kind"] for n in r.store.technical_log(run_id)}
    assert "stale_response" in kinds
    assert r.snapshot().pending["failed"] == 0  # neither affected the pending tick


def test_queue_is_bounded(runners, monkeypatch):
    class Slow(Flaky):
        def decide(self, request, cancel):
            time.sleep(0.01)
            return super().decide(request, cancel)

    provider = Slow(0)
    monkeypatch.setattr(runner_module, "make_provider", lambda world, *args: provider)
    life = LifeConfig(
        queue_capacity=2, seeds=tuple(SeedOrganism(2 * i, 3, "wait") for i in range(6))
    )
    r = runners(life=life)
    snap = r.execute(Step(cid("step")))
    assert snap.world.tick == 1 and provider.calls == 6
    assert r._worker.max_depth <= 2


# -- recovery ----------------------------------------------------------------------------------


def test_recovery_reuses_a_completed_response_and_charges_no_duplicate_costs(runners, tmp_path):
    r = runners("recorded_delayed", name="crash", delay_scale=0.05)
    run_id = r.snapshot().meta.run_id
    r.execute(Start(cid("start")))
    wait_for(lambda: r.snapshot().pending is not None)
    r.execute(Pause(cid("pause")))
    wait_for(lambda: r.snapshot().pending["completed"] == 1, timeout=5)
    calls_before = r._provider.calls
    r.stop()  # the pending tick is never committed ("crash" before commit)

    store = SqliteStore(tmp_path / "crash.sqlite3")
    store.initialize()
    again = SimulationRunner(store, delay_scale=0.05)
    again.start_thread()
    try:
        snap = again.execute(Step(cid("step")))
        assert snap.world.tick == 1
        assert again._provider.calls == 0  # the stored response was reused
        rows = attempts(store, run_id)
        assert len(rows) == 1 and calls_before == 1
        decision = store.get_decision(run_id, rows[0][0])
        assert decision["usage"]["reused"] is True
        # Same costs as an uninterrupted run: fee + upkeep charged exactly once.
        reference = runners("recorded_delayed", name="reference", delay_scale=0.01)
        ref = reference.execute(Step(cid("step")))
        assert ref.world.state_hash() == snap.world.state_hash()
    finally:
        again.stop()


def test_consumption_key_refuses_a_second_attempt_for_one_request(runners):
    r = runners("recorded_valid")
    snap = r.execute(Step(cid("step")))
    ((request_id, *_),) = attempts(r.store, snap.meta.run_id)
    with closing(sqlite3.connect(r.store.path)) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO decision_attempts SELECT * FROM decision_attempts WHERE request_id = ?",
            (request_id,),
        )


# -- API -----------------------------------------------------------------------------------


def test_decision_inspector_api_shows_input_raw_parse_and_outcome(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3", dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "recorded_invalid"})
        frame = client.post("/api/control", json={"command": "step", "command_id": cid()}).json()
        assert frame["schema_version"] == 9 and frame["provider"]["kind"] == "recorded"
        assert frame["provider"]["capabilities"]["structured_output"] == "none"
        (decision,) = client.get("/api/decisions").json()["decisions"]
        assert decision["parse_status"] == "malformed_json" and decision["outcome"] == "unparsed"
        detail = client.get(f"/api/decisions/{decision['request_id']}").json()
        assert detail["raw_output"] == '{"action": "move", "direction": '
        assert detail["request"]["context"]["observation"]["age"] == 1
        assert detail["request"]["input_hash"] == detail["input_hash"]
        assert detail["usage"]["input_tokens"] is None
        event = client.get(f"/api/events/{detail['event_sequence']}").json()
        assert event["rule_id"] == "cognition.parse.v1" and event["organism_changes"] == []
        assert client.get("/api/decisions/nope").status_code == 404
        statuses = {r["status"] for r in client.get("/api/requests").json()["requests"]}
        assert statuses == {"consumed"}


def test_frame_shows_pending_work_separately_from_committed_tick(tmp_path):
    with TestClient(
        create_app(tmp_path / "api.sqlite3", dev_fixtures=True, delay_scale=0.2)
    ) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "recorded_delayed"})
        client.post("/api/control", json={"command": "start", "command_id": cid()})
        wait_for(lambda: client.get("/api/world").json()["pending"] is not None)
        frame = client.get("/api/world").json()
        assert frame["pending"]["tick"] == frame["tick"] + 1
        assert frame["pending"]["waiting"] == ["org-0001"]
        client.post("/api/control", json={"command": "pause", "command_id": cid()})
