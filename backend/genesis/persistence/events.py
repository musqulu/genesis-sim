"""Append-only event storage: exact deltas, never descriptive text alone."""

import json
import sqlite3
from collections import defaultdict

from genesis.domain.events import (
    CellChange,
    LedgerEntry,
    MemoryChange,
    MessageChange,
    OrganismChange,
    WorldEvent,
)


class SequenceError(RuntimeError):
    """An event sequence was duplicated, skipped or missing."""


def last_sequence(db: sqlite3.Connection, run_id: str, through_tick: int | None = None) -> int:
    return db.execute(
        "SELECT COALESCE(MAX(sequence), 0) FROM events "
        "WHERE run_id = ? AND (? IS NULL OR tick <= ?)",
        (run_id, through_tick, through_tick),
    ).fetchone()[0]


def insert_event(
    db: sqlite3.Connection, run_id: str, sequence: int, event: WorldEvent, width: int
) -> None:
    """Insert one event; refuses any sequence other than the next one."""
    expected = last_sequence(db, run_id) + 1
    if sequence != expected:
        raise SequenceError(f"Event sequence {sequence} refused; next sequence is {expected}")
    db.execute(
        "INSERT INTO events (run_id, sequence, tick, phase, type, rule_id, cause, summary_json, "
        "actor_id, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            sequence,
            event.tick,
            event.phase,
            event.type,
            event.rule_id,
            event.cause,
            json.dumps(event.summary, sort_keys=True),
            event.actor_id,
            json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
        ),
    )
    db.executemany(
        "INSERT INTO event_organism_changes VALUES (?,?,?,?,?,?,?)",
        [
            (
                run_id,
                sequence,
                position,
                c.organism_id,
                c.field,
                json.dumps(c.before, sort_keys=True),
                json.dumps(c.after, sort_keys=True),
            )
            for position, c in enumerate(event.organism_changes)
        ],
    )
    db.executemany(
        "INSERT INTO event_cell_changes VALUES (?,?,?,?,?,?,?,?)",
        [
            (run_id, sequence, c.y * width + c.x, c.x, c.y, c.field, c.before, c.after)
            for c in event.changes
        ],
    )
    db.executemany(
        "INSERT INTO ledger_entries VALUES (?,?,?,?,?,?)",
        [(run_id, sequence, event.tick, e.resource, e.category, e.amount) for e in event.ledger],
    )
    db.executemany(
        "INSERT INTO event_memory_changes VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                sequence,
                position,
                event.tick,
                m.record["owner_id"],
                m.record["id"],
                m.op,
                json.dumps(m.record, sort_keys=True, separators=(",", ":")),
            )
            for position, m in enumerate(event.memory_changes)
        ],
    )
    db.executemany(
        "INSERT INTO event_message_changes VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                run_id,
                sequence,
                position,
                event.tick,
                m.record["id"],
                m.record["sender_id"],
                m.record["recipient_id"],
                m.op,
                m.reason,
                json.dumps(m.record, sort_keys=True, separators=(",", ":")),
            )
            for position, m in enumerate(event.message_changes)
        ],
    )


def append_events(
    db: sqlite3.Connection, run_id: str, events: tuple[WorldEvent, ...], width: int
) -> int:
    sequence = last_sequence(db, run_id)
    for event in events:
        sequence += 1
        insert_event(db, run_id, sequence, event, width)
    return sequence


def read_events(
    db: sqlite3.Connection, run_id: str, after_tick: int, through_tick: int
) -> list[tuple[int, WorldEvent]]:
    """Recorded events with after_tick < tick <= through_tick, as (sequence, event)."""
    rows = db.execute(
        "SELECT sequence, tick, phase, type, rule_id, cause, summary_json, actor_id "
        "FROM events WHERE run_id = ? AND tick > ? AND tick <= ? ORDER BY sequence",
        (run_id, after_tick, through_tick),
    ).fetchall()
    if not rows:
        return []
    first, last = rows[0][0], rows[-1][0]
    changes: dict[int, list[CellChange]] = defaultdict(list)
    for seq, x, y, field, before, after in db.execute(
        "SELECT event_sequence, x, y, field, before, after FROM event_cell_changes "
        "WHERE run_id = ? AND event_sequence BETWEEN ? AND ? ORDER BY event_sequence, idx, field",
        (run_id, first, last),
    ):
        changes[seq].append(CellChange(x, y, field, before, after))
    organism_changes: dict[int, list[OrganismChange]] = defaultdict(list)
    for seq, organism_id, field, before, after in db.execute(
        "SELECT event_sequence, organism_id, field, before_json, after_json "
        "FROM event_organism_changes WHERE run_id = ? AND event_sequence BETWEEN ? AND ? "
        "ORDER BY event_sequence, position",
        (run_id, first, last),
    ):
        organism_changes[seq].append(
            OrganismChange(organism_id, field, json.loads(before), json.loads(after))
        )
    ledger: dict[int, list[LedgerEntry]] = defaultdict(list)
    for seq, resource, category, amount in db.execute(
        "SELECT event_sequence, resource, category, amount FROM ledger_entries "
        "WHERE run_id = ? AND event_sequence BETWEEN ? AND ? "
        "ORDER BY event_sequence, resource, category",
        (run_id, first, last),
    ):
        ledger[seq].append(LedgerEntry(resource, category, amount))
    memory: dict[int, list[MemoryChange]] = defaultdict(list)
    for seq, op, record in db.execute(
        "SELECT event_sequence, op, record_json FROM event_memory_changes "
        "WHERE run_id = ? AND event_sequence BETWEEN ? AND ? ORDER BY event_sequence, position",
        (run_id, first, last),
    ):
        memory[seq].append(MemoryChange(op, json.loads(record)))
    mail: dict[int, list[MessageChange]] = defaultdict(list)
    for seq, op, reason, record in db.execute(
        "SELECT event_sequence, op, reason, record_json FROM event_message_changes "
        "WHERE run_id = ? AND event_sequence BETWEEN ? AND ? ORDER BY event_sequence, position",
        (run_id, first, last),
    ):
        mail[seq].append(MessageChange(op, json.loads(record), reason))
    return [
        (
            seq,
            WorldEvent(
                tick=tick,
                phase=phase,
                type=type_,
                rule_id=rule,
                cause=cause,
                summary=json.loads(summary),
                changes=tuple(changes[seq]),
                ledger=tuple(ledger[seq]),
                organism_changes=tuple(organism_changes[seq]),
                actor_id=actor,
                memory_changes=tuple(memory[seq]),
                message_changes=tuple(mail[seq]),
            ),
        )
        for seq, tick, phase, type_, rule, cause, summary, actor in rows
    ]
