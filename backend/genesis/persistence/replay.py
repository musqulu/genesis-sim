"""Reconstruct recorded history by applying stored deltas.

Replay never calls environmental rules, seeding or any model: it uses only the manifest's
initial state, recorded events and (optionally) verified snapshots. Missing or
inconsistent history raises an error; nothing is regenerated to fill a gap.
"""

import json
import sqlite3
from dataclasses import dataclass, replace

from genesis.domain.config import LifeConfig, WorldConfig
from genesis.domain.state import Cell, Ledger, Organism, World
from genesis.engine.ledger import require_balanced
from genesis.engine.world import apply_event
from genesis.persistence import snapshots
from genesis.persistence.events import last_sequence, read_events
from genesis.persistence.manifest import Manifest, sha256


class HistoryError(RuntimeError):
    """Recorded history is missing or inconsistent. Recovery stops; nothing is invented."""


class HistoryUnavailable(HistoryError):
    """The requested tick was never committed."""


@dataclass(frozen=True, slots=True)
class ReplayResult:
    world: World
    source: str  # "snapshot" | "initial"
    snapshot_tick: int | None
    ticks_replayed: int
    events_applied: int
    skipped_snapshots: tuple[tuple[int, str], ...]
    recorded_hash: str
    last_sequence: int


def initial_world(
    manifest: Manifest,
    initial_cells_json: str,
    initial_rng: str | None,
    initial_organisms_json: str | None = None,
) -> World:
    """The recorded initial state, from stored values only (no re-seeding)."""
    data = manifest.data
    schema = data["state_schema"]
    if sha256(initial_cells_json) != data["initial_cells_sha256"]:
        raise HistoryError("Initial cells do not match the run manifest")
    if schema >= 3:
        if initial_rng is None or sha256(initial_rng) != data["initial_rng_state_sha256"]:
            raise HistoryError("Initial PRNG state does not match the run manifest")
    elif initial_rng is not None:
        raise HistoryError("Schema 2 runs have no recorded PRNG state")
    life, organisms = None, ()
    if schema >= 4:
        if (
            initial_organisms_json is None
            or sha256(initial_organisms_json) != data["initial_organisms_sha256"]
        ):
            raise HistoryError("Initial organisms do not match the run manifest")
        life = LifeConfig.from_dict(data["life"])
        organisms = tuple(Organism.from_dict(o) for o in json.loads(initial_organisms_json))
    elif initial_organisms_json is not None:
        raise HistoryError("Only schema 4 runs record organisms")
    config = WorldConfig(**data["config"])
    rows = json.loads(initial_cells_json)
    cells = tuple(
        Cell(i % config.width, i // config.width, e, m, p) for i, (e, m, p) in enumerate(rows)
    )
    world = World(
        config,
        cells,
        Ledger(
            initial_energy=sum(e for e, _, _ in rows) + sum(o.energy for o in organisms),
            initial_material=sum(m + p for _, m, p in rows),
        ),
        0,
        initial_rng,
        schema,
        life,
        organisms,
    )
    if world.state_hash() != data["initial_state_hash"]:
        raise HistoryError("Initial state hash does not match the run manifest")
    return world


def committed_tick(db: sqlite3.Connection, run_id: str) -> int:
    tick = db.execute("SELECT MAX(tick) FROM ticks WHERE run_id = ?", (run_id,)).fetchone()[0]
    if tick is None:
        raise HistoryError("Run has no committed ticks")
    return tick


def reconstruct(
    db: sqlite3.Connection,
    run_id: str,
    tick: int,
    manifest: Manifest,
    initial: World,
    *,
    use_snapshots: bool = True,
    verify_each_tick: bool = False,
) -> ReplayResult:
    live = committed_tick(db, run_id)
    if type(tick) is not int or not 0 <= tick <= live:
        raise HistoryUnavailable(f"Tick {tick} is not in recorded history (0–{live})")
    recorded = dict(
        db.execute(
            "SELECT tick, state_hash FROM ticks WHERE run_id = ? AND tick <= ?", (run_id, tick)
        ).fetchall()
    )
    if sorted(recorded) != list(range(tick + 1)):
        raise HistoryError("Recorded tick sequence has gaps; history is incomplete")

    world, source, snap_tick, skipped = None, "initial", None, []
    if use_snapshots:
        for row in snapshots.list_rows(db, run_id, tick):
            row_tick = row[0]
            try:
                world = snapshots.validate(
                    row,
                    config=initial.config,
                    recorded_hash=recorded.get(row_tick),
                    last_sequence=last_sequence(db, run_id, row_tick),
                    schema=initial.schema,
                    life=initial.life,
                )
                source, snap_tick = "snapshot", row_tick
                break
            except snapshots.SnapshotInvalid as exc:
                skipped.append((row_tick, str(exc)))
    if world is None:
        if initial.state_hash() != recorded[0]:
            raise HistoryError("Initial state does not match recorded tick 0")
        world = initial

    start = world.tick
    sequence = last_sequence(db, run_id, start)
    by_tick: dict[int, list] = {}
    for seq, event in read_events(db, run_id, start, tick):
        sequence += 1
        if seq != sequence:
            raise HistoryError(f"Event sequence gap at {sequence}; history is incomplete")
        by_tick.setdefault(event.tick, []).append(event)
    applied = 0
    for t in range(start + 1, tick + 1):
        try:
            for event in by_tick.get(t, ()):
                world = apply_event(world, event)
                applied += 1
        except ValueError as exc:
            raise HistoryError(f"Recorded deltas at tick {t} do not chain: {exc}") from exc
        world = replace(world, tick=t)
        if verify_each_tick and world.state_hash() != recorded[t]:
            raise HistoryError(f"Replayed state differs from the recorded hash at tick {t}")
    if world.state_hash() != recorded[tick]:
        raise HistoryError(f"Replayed state differs from the recorded hash at tick {tick}")
    require_balanced(world)
    return ReplayResult(
        world=world,
        source=source,
        snapshot_tick=snap_tick,
        ticks_replayed=tick - start,
        events_applied=applied,
        skipped_snapshots=tuple(skipped),
        recorded_hash=recorded[tick],
        last_sequence=sequence,
    )
