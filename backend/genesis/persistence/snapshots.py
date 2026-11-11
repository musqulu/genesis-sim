"""Checksummed state snapshots. An optimization over replay, never the authority."""

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime

from genesis.domain.config import LifeConfig, WorldConfig
from genesis.domain.events import INFLOW_RULE, UPKEEP_RULE
from genesis.domain.state import Cell, Ledger, MemoryRecord, Message, Organism, World
from genesis.persistence.manifest import canonical_json, sha256

FORMAT_VERSION = 4  # 2: state schema, lifecycle rules, organisms; 3: memory; 4: inboxes
SUPPORTED_FORMATS = (1, 2, 3, 4)
SNAPSHOT_INTERVAL = 100  # ticks; technical cadence, not part of world physics


class SnapshotInvalid(ValueError):
    pass


def scheduler_state(world: World) -> dict:
    """Recorded even while it is fully derivable, so later schedulers have a slot."""
    config = world.config
    next_inflow = (
        None
        if config.inflow_amount == 0
        else (world.tick // config.inflow_interval + 1) * config.inflow_interval
    )
    state = {"next_tick": world.tick + 1, "next_inflow_tick": next_inflow, "rules": [INFLOW_RULE]}
    if world.schema >= 4:
        state["rules"] = [INFLOW_RULE, UPKEEP_RULE]
        state["deciding"] = [o.id for o in world.living]  # every living organism, each tick
    return state


def encode(world: World, last_sequence: int) -> str:
    return canonical_json(
        {
            "format": FORMAT_VERSION,
            "tick": world.tick,
            "config": asdict(world.config),
            "cells": [[c.energy, c.loose_material, c.packed_material] for c in world.cells],
            "ledger": asdict(world.ledger),
            "rng_state": world.rng_state,
            "scheduler": scheduler_state(world),
            "last_sequence": last_sequence,
            "schema": world.schema,
            "life": None if world.life is None else world.life.to_dict(world.schema),
            "organisms": [o.to_dict(world.schema) for o in world.organisms],
            "memories": [m.to_dict() for m in world.memories],
            "messages": [m.to_dict() for m in world.messages],
        }
    )


def decode(payload: str) -> tuple[World, int]:
    try:
        data = json.loads(payload)
        fmt = data.get("format")
        if fmt not in SUPPORTED_FORMATS:
            raise SnapshotInvalid(f"unsupported snapshot format {fmt!r}")
        config = WorldConfig(**data["config"])
        cells = tuple(
            Cell(i % config.width, i // config.width, e, m, p)
            for i, (e, m, p) in enumerate(data["cells"])
        )
        if fmt == 1:  # M03 snapshots: schema implied by the PRNG state, no organisms
            schema, life, organisms = (2 if data["rng_state"] is None else 3), None, ()
        else:
            schema = data["schema"]
            life = None if data["life"] is None else LifeConfig.from_dict(data["life"])
            organisms = tuple(Organism.from_dict(o) for o in data["organisms"])
        memories = tuple(MemoryRecord.from_dict(m) for m in data["memories"]) if fmt >= 3 else ()
        inbox = tuple(Message.from_dict(m) for m in data["messages"]) if fmt >= 4 else ()
        world = World(
            config,
            cells,
            Ledger(**data["ledger"]),
            data["tick"],
            data["rng_state"],
            schema,
            life,
            organisms,
            memories,
            inbox,
        )
        if data["scheduler"] != scheduler_state(world):
            raise SnapshotInvalid("scheduler state is inconsistent with the snapshot")
        return world, data["last_sequence"]
    except SnapshotInvalid:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise SnapshotInvalid(f"undecodable snapshot: {exc}") from exc


def write(
    db: sqlite3.Connection, run_id: str, world: World, reason: str, last_sequence: int
) -> bool:
    """Store one snapshot; returns False if this tick already has one (never overwritten)."""
    payload = encode(world, last_sequence)
    cursor = db.execute(
        "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            world.tick,
            FORMAT_VERSION,
            reason,
            last_sequence,
            world.state_hash(),
            payload,
            sha256(payload),
            datetime.now(UTC).isoformat(),
        ),
    )
    return cursor.rowcount == 1


def due(world: World) -> bool:
    return world.tick % SNAPSHOT_INTERVAL == 0


def list_rows(db: sqlite3.Connection, run_id: str, max_tick: int | None = None) -> list[tuple]:
    return db.execute(
        "SELECT tick, format_version, reason, last_sequence, state_hash, payload, checksum, "
        "created_at FROM snapshots WHERE run_id = ? AND (? IS NULL OR tick <= ?) "
        "ORDER BY tick DESC",
        (run_id, max_tick, max_tick),
    ).fetchall()


def validate(
    row: tuple,
    *,
    config: WorldConfig,
    recorded_hash: str | None,
    last_sequence: int,
    schema: int,
    life: LifeConfig | None,
) -> World:
    """Full check: checksum, format, decode, config, recorded tick hash, event boundary."""
    tick, fmt, _reason, seq, state_hash, payload, checksum, _created = row
    if sha256(payload) != checksum:
        raise SnapshotInvalid("checksum mismatch")
    if fmt not in SUPPORTED_FORMATS:
        raise SnapshotInvalid(f"unsupported snapshot format {fmt}")
    world, payload_seq = decode(payload)
    if world.tick != tick or payload_seq != seq:
        raise SnapshotInvalid("snapshot header disagrees with its payload")
    if world.config != config or world.schema != schema or world.life != life:
        raise SnapshotInvalid("snapshot configuration differs from the run manifest")
    if recorded_hash is None or world.state_hash() != recorded_hash or state_hash != recorded_hash:
        raise SnapshotInvalid("snapshot state does not match the recorded tick hash")
    if seq != last_sequence:
        raise SnapshotInvalid("snapshot event boundary does not match recorded events")
    return world
