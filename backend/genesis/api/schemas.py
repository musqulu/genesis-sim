from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from genesis.domain.state import World
from genesis.engine.ledger import reconcile
from genesis.persistence.replay import ReplayResult
from genesis.runtime.runner import RunSnapshot


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RunConfig(StrictRecord):
    width: int
    height: int
    seed: int
    energy_cap: int
    packed_cap: int
    inflow_interval: int
    inflow_amount: int
    fixture: str | None


class CellView(StrictRecord):
    x: int
    y: int
    energy: int
    loose_material: int
    packed_material: int


class EnergyLedger(StrictRecord):
    initial: int
    in_cells: int
    in_organisms: int
    environmental_inflow: int
    dissipation: int
    death_retirement: int
    expected: int
    actual: int


class MaterialLedger(StrictRecord):
    initial: int
    retired: int
    expected: int
    loose: int
    packed: int
    carried: int
    actual: int


class OrganismView(StrictRecord):
    """Observer view. The sampled lifespan is withheld while the organism lives."""

    id: str
    label: str
    policy: str
    generation: int
    birth_tick: int
    x: int
    y: int
    energy: int
    energy_capacity: int
    age: int
    alive: bool
    death_tick: int | None
    death_cause: str | None
    retired_energy: int
    carried_material: int
    material_capacity: int
    retired_material: int
    lifespan: int | None
    parent_ids: tuple[str, ...]  # v9: empty for seed organisms


class LedgerView(StrictRecord):
    energy: EnergyLedger
    material: MaterialLedger
    balanced: bool


class SkippedSnapshot(StrictRecord):
    tick: int
    reason: str


class ReplaySource(StrictRecord):
    """How a state was reconstructed from recorded history (no rule was re-run)."""

    kind: Literal["snapshot", "initial"]
    snapshot_tick: int | None
    ticks_replayed: int
    events_applied: int
    skipped_snapshots: tuple[SkippedSnapshot, ...]


class Interaction(StrictRecord):
    """An accepted transfer or message in the frame's tick (observer view)."""

    kind: Literal["transfer", "communicate", "reproduce"]
    from_id: str
    to_id: str
    from_x: int
    from_y: int
    to_x: int
    to_y: int
    detail: str


class GridFrame(StrictRecord):
    schema_version: Literal[9] = 9
    mode: Literal["live"] = "live"
    run_id: UUID
    created_at: datetime
    engine_version: str
    engine_source_hash: str
    rules_version: str
    state_schema: int
    manifest_hash: str
    config: RunConfig
    tick: int
    status: Literal["paused", "running"]
    started: bool
    speed: int
    until_tick: int | None
    last_event_sequence: int
    changed_cells: tuple[int, ...]
    changed_organisms: tuple[str, ...]
    interactions: tuple[Interaction, ...]  # v8: transfers/messages in this tick
    organisms: tuple[OrganismView, ...]
    extinct: bool
    last_error: str | None
    pending: dict | None  # tick waiting at the decision barrier (not committed)
    provider: dict | None  # the run's fixed provider profile (identity, capabilities)
    report_path: str | None  # latest saved plain-English summary (.md) of this run
    restored_from: ReplaySource | None
    dev_fixtures: tuple[str, ...]
    journal_mode: str
    cells: tuple[CellView, ...]
    ledger: LedgerView
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class HistoryFrame(StrictRecord):
    """A read-only reconstruction of an earlier committed tick."""

    schema_version: Literal[9] = 9
    mode: Literal["history"] = "history"
    run_id: UUID
    tick: int
    live_tick: int
    state_schema: int
    config: RunConfig
    changed_cells: tuple[int, ...]
    changed_organisms: tuple[str, ...]
    interactions: tuple[Interaction, ...]
    organisms: tuple[OrganismView, ...]
    extinct: bool
    cells: tuple[CellView, ...]
    ledger: LedgerView
    state_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    recorded_hash: str
    verified: bool
    source: ReplaySource


def config_view(world: World) -> RunConfig:
    config = asdict(world.config)
    for name in ("material_density_percent", "material_max"):
        del config[name]
    return RunConfig(**config)


def organism_views(world: World, revealed: frozenset[str]) -> tuple[OrganismView, ...]:
    """`revealed`: ids dead in the live run, whose lifespan the observer may inspect."""
    capacity = world.life.energy_capacity if world.life else 0
    return tuple(
        OrganismView(
            id=o.id,
            label=o.label,
            policy=o.policy,
            generation=o.generation,
            birth_tick=o.birth_tick,
            x=o.x,
            y=o.y,
            energy=o.energy,
            energy_capacity=capacity,
            age=(o.death_tick if not o.alive else world.tick) - o.birth_tick,
            alive=o.alive,
            death_tick=o.death_tick,
            death_cause=o.death_cause,
            retired_energy=o.retired_energy,
            carried_material=o.carried_material,
            material_capacity=world.life.material_capacity if world.schema >= 5 else 0,
            retired_material=o.retired_material,
            lifespan=o.lifespan if o.id in revealed else None,
            parent_ids=o.parent_ids,
        )
        for o in world.organisms
    )


def dead_ids(world: World) -> frozenset[str]:
    return frozenset(o.id for o in world.organisms if not o.alive)


def ledger_view(world: World) -> LedgerView:
    ledger = world.ledger
    report = reconcile(world)
    return LedgerView(
        energy=EnergyLedger(
            initial=ledger.initial_energy,
            in_cells=world.cell_energy,
            in_organisms=world.organism_energy,
            environmental_inflow=ledger.environmental_inflow,
            dissipation=ledger.dissipation,
            death_retirement=ledger.death_retirement,
            expected=report.energy_expected,
            actual=report.energy_actual,
        ),
        material=MaterialLedger(
            initial=ledger.initial_material,
            retired=ledger.retired_material,
            expected=report.material_expected,
            loose=sum(c.loose_material for c in world.cells),
            packed=sum(c.packed_material for c in world.cells),
            carried=world.carried_material,
            actual=report.material_actual,
        ),
        balanced=report.balanced,
    )


def source_view(
    kind: str, snapshot_tick: int | None, ticks: int, events: int, skipped
) -> ReplaySource:
    return ReplaySource(
        kind=kind,
        snapshot_tick=snapshot_tick,
        ticks_replayed=ticks,
        events_applied=events,
        skipped_snapshots=tuple(SkippedSnapshot(tick=t, reason=r) for t, r in skipped),
    )


def to_frame(snapshot: RunSnapshot, dev_fixtures: tuple[str, ...], journal_mode: str) -> GridFrame:
    world = snapshot.world
    restore = snapshot.restore
    return GridFrame(
        run_id=UUID(snapshot.meta.run_id),
        created_at=snapshot.meta.created_at,
        engine_version=snapshot.meta.engine_version,
        engine_source_hash=snapshot.meta.engine_source_hash,
        rules_version=snapshot.meta.rules_version,
        state_schema=snapshot.meta.state_schema,
        manifest_hash=snapshot.meta.manifest_hash,
        config=config_view(world),
        tick=world.tick,
        status=snapshot.status,
        started=snapshot.started,
        speed=snapshot.speed,
        until_tick=snapshot.until_tick,
        last_event_sequence=snapshot.last_event_sequence,
        changed_cells=snapshot.changed_indices,
        changed_organisms=snapshot.changed_organisms,
        interactions=tuple(Interaction(**i) for i in snapshot.interactions),
        organisms=organism_views(world, dead_ids(world)),
        extinct=world.extinct,
        last_error=snapshot.last_error,
        pending=snapshot.pending,
        provider=snapshot.provider,
        report_path=snapshot.report_path,
        restored_from=None
        if restore is None
        else source_view(
            restore.source,
            restore.snapshot_tick,
            restore.ticks_replayed,
            restore.events_applied,
            restore.skipped_snapshots,
        ),
        dev_fixtures=dev_fixtures,
        journal_mode=journal_mode,
        cells=tuple(CellView(**asdict(cell)) for cell in world.cells),
        ledger=ledger_view(world),
        state_hash=world.state_hash(),
    )


def to_history(
    result: ReplayResult,
    run_id: str,
    live_world: World,
    changed: list[int],
    changed_organisms: list[str],
    interactions: list[dict] = (),
) -> HistoryFrame:
    world = result.world
    state_hash = world.state_hash()
    return HistoryFrame(
        run_id=UUID(run_id),
        tick=world.tick,
        live_tick=live_world.tick,
        state_schema=world.state_schema,
        config=config_view(world),
        changed_cells=tuple(changed),
        changed_organisms=tuple(changed_organisms),
        interactions=tuple(Interaction(**i) for i in interactions),
        # Only deaths that had happened by this tick reveal a lifespan (no future data).
        organisms=organism_views(world, dead_ids(world)),
        extinct=world.extinct,
        cells=tuple(CellView(**asdict(cell)) for cell in world.cells),
        ledger=ledger_view(world),
        state_hash=state_hash,
        recorded_hash=result.recorded_hash,
        verified=state_hash == result.recorded_hash,
        source=source_view(
            result.source,
            result.snapshot_tick,
            result.ticks_replayed,
            result.events_applied,
            result.skipped_snapshots,
        ),
    )


class OrganismDetail(StrictRecord):
    run_id: UUID
    tick: int
    mode: Literal["live", "history"]
    organism: OrganismView
    latest_decision: dict | None  # recorded observation, proposal and engine outcome
    events: tuple[dict, ...]
    # Validator outcomes for representative proposals against this exact state (live
    # alive organisms only); the next tick applies inflow and upkeep first.
    action_preview: tuple[dict, ...]
    # Schema 7: active memory at this tick, forgotten records (observer only) and the
    # recall behind the latest decision. None when the run has no memory.
    memory: dict | None = None
    # Schema 8: its current inbox and recent items it sent or received (observer view).
    messages: dict | None = None
    # Schema 9: its starting tendencies and behaviour counts from recorded events.
    profile: dict | None = None
    # M12/M13: for an organism born in the run, its parent and logged inheritance.
    inheritance: dict | None = None


class PopulationEntry(StrictRecord):
    cognition: str = Field(max_length=300)
    organisms: int = Field(ge=1, le=16)


class CreateRun(StrictRecord):
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    fixture: str | None = None
    # "mlx:<model id>": one organism whose decisions come from a local MLX-LM model.
    cognition: str | None = Field(default=None, max_length=300)
    # How many organisms share the chosen model (one resident model, isolated contexts).
    organisms: int = Field(default=1, ge=1, le=16)
    # A mixed-model population instead of `cognition`: each entry gives a model and how
    # many organisms use it (organisms are numbered in this order).
    population: list[PopulationEntry] | None = Field(default=None, min_length=1, max_length=4)
    # Square map side for a new standard or live run (fixtures keep their own 32 x 32).
    size: int = Field(default=32, ge=8, le=64)
    # Starting tendencies of live organisms: drawn from the seed, or all 50 (the paired
    # control: the same world, placements and lifespans, only the tendencies differ).
    tendencies: Literal["seeded", "neutral"] = "seeded"
    # Energy added per cell on each inflow tick (default 1; 0 = none). An environment
    # parameter sealed in the run manifest; fixtures keep their own.
    inflow_amount: int | None = Field(default=None, ge=0, le=100)


class ControlBody(StrictRecord):
    command: Literal["start", "resume", "pause", "step", "speed"]
    command_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ticks_per_second: int | None = Field(default=None, ge=1, le=1000)
    until_tick: int | None = Field(default=None, ge=1)


class CellChangeView(StrictRecord):
    event_sequence: int
    tick: int
    type: str
    rule_id: str
    actor_id: str | None
    field: str
    before: int
    after: int
    delta: int


class CellInspection(StrictRecord):
    run_id: UUID
    tick: int
    mode: Literal["live", "history"] = "live"
    index: int
    cell: CellView
    entry_cost: int | None  # energy to enter, from the engine's move rule (None: no life)
    state_hash: str
    recent_changes: tuple[CellChangeView, ...]
