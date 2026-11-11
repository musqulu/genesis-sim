import random
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from genesis.api.schemas import (
    CellInspection,
    ControlBody,
    CreateRun,
    GridFrame,
    HistoryFrame,
    OrganismDetail,
    dead_ids,
    organism_views,
    to_frame,
    to_history,
)
from genesis.domain.config import (
    TENDENCIES,
    LifeConfig,
    SeedOrganism,
    WorldConfig,
    life_for_run,
)
from genesis.engine import actions, memory, messages
from genesis.persistence.replay import HistoryError, HistoryUnavailable
from genesis.providers import config as api_config
from genesis.providers import mlx
from genesis.providers.base import ProviderError
from genesis.providers.http_chat import HttpChatProvider
from genesis.providers.replicate import ReplicateProvider, resolve
from genesis.runtime.commands import CommandError, Pause, Resume, SetSpeed, Start, Step
from genesis.runtime.runner import SimulationRunner

router = APIRouter(prefix="/api")


def runner(request: Request) -> SimulationRunner:
    return request.app.state.runner


Runner = Annotated[SimulationRunner, Depends(runner)]


def frame_of(request: Request, snapshot) -> GridFrame:
    state = request.app.state
    return to_frame(snapshot, state.dev_fixtures, state.runner.store.journal_mode)


@router.get("/world", response_model=GridFrame)
def get_world(request: Request, sim: Runner) -> GridFrame:
    return frame_of(request, sim.snapshot())


@router.post("/runs", response_model=GridFrame, status_code=201)
def create_run(body: CreateRun, request: Request, sim: Runner) -> GridFrame:
    if body.fixture is not None and body.fixture not in request.app.state.dev_fixtures:
        raise HTTPException(status_code=422, detail="Fixtures are available only in dev mode")
    if body.fixture is not None and body.size != 32:
        raise HTTPException(status_code=422, detail="Fixtures keep their own 32 x 32 map")
    if body.population is not None:
        return frame_of(request, sim.create_run(*mixed_run(body)))
    if body.cognition is None and body.organisms != 1:
        raise HTTPException(status_code=422, detail="Organisms need a model (cognition)")
    if body.cognition is not None:
        if body.fixture is not None:
            raise HTTPException(status_code=422, detail="Choose a fixture or a live model")
        kind, _, model_id = body.cognition.partition(":")
        if kind not in ("mlx", "api") or not model_id:
            raise HTTPException(
                status_code=422, detail="cognition must be 'mlx:<model id>' or 'api:<profile id>'"
            )
        try:
            profile = mlx.profile_for(model_id) if kind == "mlx" else api_profile_for(model_id)
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        life = LifeConfig(
            provider=body.cognition,
            seeds=live_seeds(body.seed, body.organisms, body.size),
            tendency_mode=body.tendencies,
        )
        config = WorldConfig.for_run(body.seed, size=body.size)
        return frame_of(request, sim.create_run(config, life, profile))
    return frame_of(
        request,
        sim.create_run(
            WorldConfig.for_run(body.seed, body.fixture, body.size), life_for_run(body.fixture)
        ),
    )


def profile_for(cognition: str) -> dict:
    kind, _, model_id = cognition.partition(":")
    if kind not in ("mlx", "api") or not model_id:
        raise HTTPException(
            status_code=422, detail="cognition must be 'mlx:<model id>' or 'api:<profile id>'"
        )
    try:
        return mlx.profile_for(model_id) if kind == "mlx" else api_profile_for(model_id)
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def mixed_run(body: CreateRun) -> tuple[WorldConfig, LifeConfig, dict]:
    """A mixed-model population (a labelled experiment): each organism has its own model,
    fixed for life; the assignment is sealed in the run manifest."""
    if body.fixture is not None or body.cognition is not None:
        raise HTTPException(
            status_code=422, detail="Choose either a population or a single model/fixture"
        )
    total = sum(entry.organisms for entry in body.population)
    if total > 12:
        raise HTTPException(status_code=422, detail="At most 12 organisms in total")
    cognitions = [entry.cognition for entry in body.population]
    if len(set(cognitions)) != len(cognitions):
        raise HTTPException(status_code=422, detail="List each model once in a population")
    seeds = live_seeds(body.seed, total, body.size)
    if len(cognitions) == 1:  # one model: an ordinary run
        profile = profile_for(cognitions[0])
        return (
            WorldConfig.for_run(body.seed, size=body.size),
            LifeConfig(provider=cognitions[0], seeds=seeds, tendency_mode=body.tendencies),
            profile,
        )
    members, assignment, n = [], {}, 0
    for index, entry in enumerate(body.population):
        members.append(
            {
                "cognition": entry.cognition,
                "organisms": entry.organisms,
                "profile": profile_for(entry.cognition),
            }
        )
        for _ in range(entry.organisms):
            n += 1
            assignment[f"org-{n:04d}"] = index
    profile = {"kind": "mixed", "members": members, "assignment": assignment}
    return (
        WorldConfig.for_run(body.seed, size=body.size),
        LifeConfig(provider="mixed", seeds=seeds, tendency_mode=body.tendencies),
        profile,
    )


def api_profile_for(profile_id: str) -> dict:
    """The sealed record of an API profile for a new run (no credential is stored).

    A Replicate model's version and inputs are resolved once here, so the run keeps
    exactly that version for its whole life."""
    profile = api_config.get_profile(profile_id)
    if not profile.model:
        raise ProviderError("config", f"API profile {profile_id!r} has no model set")
    if profile.key_status() == "missing":
        raise ProviderError("config", f"The API key variable {profile.api_key_env} is not set")
    extra = {"replicate": resolve(profile)} if profile.protocol == "replicate" else {}
    return api_config.sealed(profile, extra)


def live_seeds(seed: int, count: int, size: int = 32) -> tuple[SeedOrganism, ...]:
    """Seeded, distinct placements for live organisms on a size x size map (recorded in the
    run manifest). On 32 x 32 they are exactly the earlier placements for the same seed."""
    rng = random.Random(f"placement:{seed}")
    taken: list[tuple[int, int]] = []
    while len(taken) < count:
        cell = (rng.randrange(size), rng.randrange(size))
        if cell not in taken:
            taken.append(cell)
    return tuple(SeedOrganism(x, y, "live") for x, y in taken)


@router.post("/summary")
def write_summary(sim: Runner) -> dict:
    """Write (or refresh) the plain-English summary of the active run as Markdown."""
    with sim._cond:
        if sim._world.extinct:
            status = "extinct"
        else:
            status = "running" if sim._running else "stopped"
        path, text = sim.write_report(status)
        run_id = sim._meta.run_id
    return {"run_id": run_id, "status": status, "path": path, "markdown": text}


@router.get("/summary")
def read_summary(sim: Runner) -> dict:
    """The latest saved summary of the active run (404 if none was written yet)."""
    snapshot = sim.snapshot()
    if snapshot.report_path is None or not Path(snapshot.report_path).is_file():
        raise HTTPException(status_code=404, detail="No summary has been written for this run")
    return {
        "run_id": snapshot.meta.run_id,
        "path": snapshot.report_path,
        "markdown": Path(snapshot.report_path).read_text(encoding="utf-8"),
    }


@router.get("/models")
def list_models() -> dict:
    """Local MLX-LM models and runtime availability (nothing is loaded or downloaded), and
    the configured API profiles (no network call; keys are never returned)."""
    python = mlx.mlx_python()
    return {
        "runtime": mlx.runtime_status(python),
        "model_dirs": [str(p) for p in mlx.model_dirs()],
        "models": mlx.discover_models(),
        "api": api_config.list_profiles(),
    }


@router.post("/providers/{profile_id}/check")
def check_provider(profile_id: str) -> dict:
    """A cheap connectivity probe of one API profile. It creates no run and no decision."""
    try:
        profile = api_config.get_profile(profile_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not profile.model:
        return {"ok": False, "message": "No model is set in this profile"}
    if profile.key_status() == "missing":
        return {"ok": False, "message": f"{profile.api_key_env} is not set"}
    if profile.protocol == "replicate":
        provider = ReplicateProvider(profile, {})
    else:
        provider = HttpChatProvider(profile)
    try:
        return provider.check()
    except ProviderError as exc:
        return {"ok": False, "message": f"{exc.kind}: {exc}"}
    finally:
        provider.close()


@router.post("/control", response_model=GridFrame)
def control(body: ControlBody, request: Request, sim: Runner) -> GridFrame:
    match body.command:
        case "start":
            command = Start(body.command_id, body.until_tick)
        case "resume":
            command = Resume(body.command_id, body.until_tick)
        case "pause":
            command = Pause(body.command_id)
        case "step":
            command = Step(body.command_id)
        case "speed":
            if body.ticks_per_second is None:
                raise HTTPException(status_code=422, detail="ticks_per_second is required")
            command = SetSpeed(body.command_id, body.ticks_per_second)
    try:
        return frame_of(request, sim.execute(command))
    except CommandError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc


@router.get("/cells/{x}/{y}", response_model=CellInspection)
def inspect_cell(
    x: int,
    y: int,
    request: Request,
    sim: Runner,
    at_tick: Annotated[int | None, Query(ge=0)] = None,
) -> CellInspection:
    snapshot = sim.snapshot()
    world = snapshot.world
    if not 0 <= x < world.config.width or not 0 <= y < world.config.height:
        raise HTTPException(status_code=404, detail="Cell is outside the world")
    index = y * world.config.width + x
    run_id = snapshot.meta.run_id
    if at_tick is None:
        frame = frame_of(request, snapshot)
        cell, tick, state_hash = frame.cells[index], frame.tick, frame.state_hash
    else:
        history = _history(sim, at_tick, use_snapshots=True)
        cell, tick, state_hash = history.cells[index], history.tick, history.state_hash
        world = sim.store.history(run_id, at_tick).world
    entry_cost = actions.move_cost(world, x, y) if world.life is not None else None
    return CellInspection(
        run_id=UUID(run_id),
        tick=tick,
        mode="live" if at_tick is None else "history",
        index=index,
        cell=cell,
        entry_cost=entry_cost,
        state_hash=state_hash,
        recent_changes=tuple(sim.store.cell_history(run_id, index, max_tick=at_tick)),
    )


def _history(sim: SimulationRunner, tick: int, *, use_snapshots: bool) -> HistoryFrame:
    snapshot = sim.snapshot()
    run_id = snapshot.meta.run_id
    try:
        result = sim.store.history(run_id, tick, use_snapshots=use_snapshots)
    except HistoryUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HistoryError as exc:
        raise HTTPException(status_code=409, detail=f"Historical data unavailable: {exc}") from exc
    changed = sim.store.events_at_tick(run_id, tick) if tick > 0 else []
    organisms = sim.store.changed_organisms_at_tick(run_id, tick) if tick > 0 else []
    interactions = sim.store.interactions_at_tick(run_id, tick) if tick > 0 else []
    return to_history(result, run_id, snapshot.world, changed, organisms, interactions)


@router.get("/organisms/{organism_id}", response_model=OrganismDetail)
def inspect_organism(
    organism_id: str,
    sim: Runner,
    at_tick: Annotated[int | None, Query(ge=0)] = None,
) -> OrganismDetail:
    """Observer inspection: state, the last observation actually supplied, and history."""
    snapshot = sim.snapshot()
    run_id = snapshot.meta.run_id
    world = snapshot.world
    if at_tick is not None:
        try:
            world = sim.store.history(run_id, at_tick).world
        except HistoryUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HistoryError as exc:
            raise HTTPException(409, f"Historical data unavailable: {exc}") from exc
    views = {v.id: v for v in organism_views(world, dead_ids(snapshot.world))}
    if organism_id not in views:
        raise HTTPException(status_code=404, detail="No such organism at this tick")
    events, _ = sim.store.list_events(run_id, 20, None, at_tick, organism_id)
    organism = world.organism(organism_id)
    preview = actions.preview(world, organism) if at_tick is None and organism.alive else []
    latest = sim.store.latest_decision(run_id, organism_id, at_tick)
    return OrganismDetail(
        run_id=UUID(run_id),
        tick=world.tick,
        mode="live" if at_tick is None else "history",
        organism=views[organism_id],
        latest_decision=latest,
        events=tuple(events),
        action_preview=tuple(preview),
        memory=memory_detail(sim, run_id, world, organism_id, at_tick, latest),
        messages=None
        if not messages.enabled(world)
        else {
            "inbox": [m.to_dict() for m in world.inbox_of(organism_id)],
            "inbox_capacity": world.life.inbox_capacity,
            "recent": sim.store.message_history(run_id, organism_id, at_tick),
        },
        profile=None
        if world.schema < 9
        else {
            "tendencies": organism.tendency_map(),
            "tendency_mode": world.life.tendency_mode,
            "stats": sim.store.behaviour_stats(run_id, at_tick).get(organism_id),
        },
    )


@router.get("/population")
def population(sim: Runner, at_tick: Annotated[int | None, Query(ge=0)] = None) -> dict:
    """Every organism's starting tendencies beside its observed behaviour (observer view).
    Tendencies of the living are visible to the observer, never to other organisms."""
    snapshot = sim.snapshot()
    run_id = snapshot.meta.run_id
    world = snapshot.world
    if at_tick is not None:
        try:
            world = sim.store.history(run_id, at_tick).world
        except HistoryUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except HistoryError as exc:
            raise HTTPException(409, f"Historical data unavailable: {exc}") from exc
    stats = sim.store.behaviour_stats(run_id, at_tick)
    return {
        "run_id": run_id,
        "tick": world.tick,
        "tendency_names": list(TENDENCIES),
        "tendency_mode": None if world.schema < 9 else world.life.tendency_mode,
        "organisms": [
            {
                "id": o.id,
                "alive": o.alive,
                "death_tick": o.death_tick,
                "tendencies": o.tendency_map(),
                "stats": stats.get(o.id),
            }
            for o in world.organisms
        ],
    }


def memory_detail(
    sim: SimulationRunner,
    run_id: str,
    world,
    organism_id: str,
    at_tick: int | None,
    latest: dict | None,
) -> dict | None:
    """Active records at this tick (from reconstructed state) plus, for the observer only,
    every record forgotten so far (from the recorded deltas, which keep full records)."""
    if not memory.enabled(world):
        return None
    life = world.life
    history = sim.store.memory_history(run_id, organism_id, at_tick)
    added = {h["record"]["id"]: h["event_sequence"] for h in history if h["op"] == "add"}
    active = world.memories_of(organism_id)
    payload = (latest or {}).get("payload") or {}
    return {
        "limits": {
            "max_records": life.memory_max_records,
            "max_bytes": life.memory_max_bytes,
            "record_max_bytes": life.memory_record_max_bytes,
            "write_fee_per_kib": life.memory_write_fee_per_kib,
            "upkeep_per_kib": life.memory_upkeep_per_kib,
            "upkeep_interval": life.memory_upkeep_interval,
            "recall_max": life.recall_max,
            "recall_max_bytes": life.recall_max_bytes,
        },
        "active_records": len(active),
        "active_bytes": sum(m.size for m in active),
        "written_total": world.organism(organism_id).memories_written,
        "active": [
            {**m.to_dict(), "bytes": m.size, "added_sequence": added.get(m.id)}
            for m in reversed(active)
        ],
        "forgotten": [
            {
                **h["record"],
                "bytes": len(h["record"]["text"].encode()),
                "added_sequence": added.get(h["record"]["id"]),
                "forgotten_tick": h["tick"],
                "forgotten_sequence": h["event_sequence"],
            }
            for h in reversed(history)
            if h["op"] == "forget"
        ],
        "last_recall": None
        if latest is None
        else {
            "tick": latest["tick"],
            "recall": payload.get("recall"),
            "recalled": payload.get("recalled"),
        },
    }


@router.get("/history/{tick}", response_model=HistoryFrame)
def get_history(
    tick: int,
    sim: Runner,
    source: Annotated[Literal["snapshot", "initial"], Query()] = "snapshot",
) -> HistoryFrame:
    """Read-only state at an earlier committed tick, rebuilt from recorded deltas."""
    if tick < 0:
        raise HTTPException(status_code=422, detail="Tick must be nonnegative")
    return _history(sim, tick, use_snapshots=source == "snapshot")


@router.get("/snapshots")
def list_snapshots(sim: Runner) -> dict:
    run_id = sim.snapshot().meta.run_id
    return {"run_id": run_id, "snapshots": sim.store.list_snapshots(run_id)}


@router.get("/events")
def list_events(
    sim: Runner,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
    max_tick: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    run_id = sim.snapshot().meta.run_id
    events, has_more = sim.store.list_events(run_id, limit, before, max_tick)
    return {"run_id": run_id, "events": events, "has_more": has_more}


@router.get("/events/{sequence}")
def get_event(sequence: int, sim: Runner) -> dict:
    run_id = sim.snapshot().meta.run_id
    event = sim.store.get_event(run_id, sequence)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"run_id": run_id, **event}


@router.get("/decisions")
def list_decisions(
    sim: Runner,
    organism_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    max_tick: Annotated[int | None, Query(ge=0)] = None,
) -> dict:
    """Consumed decision attempts (input binding, raw output, parse and outcome)."""
    run_id = sim.snapshot().meta.run_id
    decisions = sim.store.list_decisions(run_id, organism_id, limit, max_tick)
    return {"run_id": run_id, "decisions": decisions}


@router.get("/decisions/{request_id}")
def get_decision(request_id: str, sim: Runner) -> dict:
    """One attempt with the exact request (context) the provider received."""
    run_id = sim.snapshot().meta.run_id
    decision = sim.store.get_decision(run_id, request_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")
    return {"run_id": run_id, **decision}


@router.get("/requests")
def list_requests(sim: Runner) -> dict:
    """Technical request inbox for the active run (requested/completed/consumed/...)."""
    run_id = sim.snapshot().meta.run_id
    return {"run_id": run_id, "requests": sim.store.request_status(run_id)}


@router.get("/health")
def health(request: Request, sim: Runner) -> dict:
    snapshot = sim.snapshot()
    return {
        "status": "ok",
        "milestone": "M10",
        "provider": snapshot.provider,
        "pending": snapshot.pending,
        "tick": snapshot.world.tick,
        "state_schema": snapshot.meta.state_schema,
        "restored_from": None
        if snapshot.restore is None
        else {
            "source": snapshot.restore.source,
            "snapshot_tick": snapshot.restore.snapshot_tick,
            "ticks_replayed": snapshot.restore.ticks_replayed,
            "skipped_snapshots": [list(s) for s in snapshot.restore.skipped_snapshots],
        },
        "run_id": str(snapshot.meta.run_id),
        "journal_mode": sim.store.journal_mode,
        "journal_note": sim.store.journal_note,
        "fixtures_enabled": bool(request.app.state.dev_fixtures),
    }
