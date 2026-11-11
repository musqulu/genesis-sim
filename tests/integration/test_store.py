import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from genesis.domain.config import WorldConfig
from genesis.domain.events import CellChange, LedgerEntry, WorldEvent
from genesis.domain.state import Cell, Ledger, World
from genesis.engine.world import advance, apply_event
from genesis.persistence import sqlite as sqlite_module
from genesis.persistence.sqlite import ControlRecord, SqliteStore, StoreError, wal_runtime_safe


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "genesis.sqlite3")
    s.initialize()
    return s


def counts(path, run_id):
    with closing(sqlite3.connect(path)) as db:
        return {
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            for table in ("events", "event_cell_changes", "ledger_entries", "ticks", "control_log")
        }


def step_many(store, loaded, n):
    world = loaded.world
    for _ in range(n):
        result = advance(world)
        store.commit_tick(loaded.meta.run_id, result)
        world = result.world
    return world


def test_wal_is_enabled_only_after_runtime_probe(store):
    assert wal_runtime_safe((3, 51, 3)) and wal_runtime_safe((3, 53, 1))
    assert wal_runtime_safe((3, 50, 7)) and wal_runtime_safe((3, 44, 6))
    assert not wal_runtime_safe((3, 51, 2))
    assert not wal_runtime_safe((3, 50, 6)) and not wal_runtime_safe((3, 45, 0))
    assert store.journal_mode == "wal" and "WAL verified" in store.journal_note
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_unfixed_runtime_keeps_rollback_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_module, "wal_runtime_safe", lambda *a: False)
    s = SqliteStore(tmp_path / "old.sqlite3")
    s.initialize()
    assert s.journal_mode == "delete" and "lacks the WAL-reset fix" in s.journal_note


def test_initial_run_persists_manifest_state_and_creation_event(store):
    loaded = store.load_active()
    assert loaded.world.tick == 0 and loaded.world.config.seed == 42
    assert counts(store.path, loaded.meta.run_id) == {
        "events": 1,
        "event_cell_changes": 0,
        "ledger_entries": 0,
        "ticks": 1,
        "control_log": 0,
    }
    (event,), _ = store.list_events(loaded.meta.run_id, 10, None)
    assert event["type"] == "run_created" and event["rule_id"] == "init.seeded.v1"
    assert event["summary"]["initial_energy"] == loaded.world.ledger.initial_energy


def test_tick_commit_persists_exact_deltas_ledger_and_state(store):
    loaded = store.load_active()
    final = step_many(store, loaded, 10)
    reloaded = store.load_active()
    assert reloaded.world == final and reloaded.world.state_hash() == final.state_hash()
    assert reloaded.last_event_sequence == 2
    event = store.get_event(loaded.meta.run_id, 2)
    assert event["tick"] == 10 and event["rule_id"] == "env.energy_inflow.v1"
    assert len(event["changes"]) == event["summary"]["credited_total"]
    assert all(c["delta"] == c["after"] - c["before"] == 1 for c in event["changes"])
    assert event["ledger"] == [
        {
            "resource": "energy",
            "category": "environmental_inflow",
            "amount": event["summary"]["credited_total"],
        }
    ]
    assert reloaded.world.ledger.environmental_inflow == event["summary"]["credited_total"]


def test_every_state_change_is_traceable_to_a_recorded_event(store):
    loaded = store.load_active()
    final = step_many(store, loaded, 55)
    initial_cells = store.initial_cells(loaded.meta.run_id)
    rebuilt = World(
        loaded.world.config,
        tuple(Cell(i % 32, i // 32, e, m, p) for i, (e, m, p) in enumerate(initial_cells)),
        loaded.world.ledger,
    )
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute(
            "SELECT c.x, c.y, c.field, c.before, c.after FROM event_cell_changes c "
            "JOIN events e ON e.run_id = c.run_id AND e.sequence = c.event_sequence "
            "WHERE c.run_id = ? ORDER BY e.sequence, c.idx",
            (loaded.meta.run_id,),
        ).fetchall()
        credited = db.execute(
            "SELECT SUM(amount) FROM ledger_entries WHERE run_id = ?", (loaded.meta.run_id,)
        ).fetchone()[0]
    cells = list(rebuilt.cells)
    for x, y, field, before, after in rows:
        assert getattr(cells[y * 32 + x], field) == before  # deltas chain exactly
        cells[y * 32 + x] = replace(cells[y * 32 + x], **{field: after})
    assert tuple(cells) == final.cells
    assert sum(c.energy for c in final.cells) == loaded.world.ledger.initial_energy + credited


def test_failure_inside_tick_transaction_rolls_back_everything(store, monkeypatch):
    loaded = store.load_active()
    world = step_many(store, loaded, 9)
    before = counts(store.path, loaded.meta.run_id)
    before_state = store.load_active().world
    result = advance(world)
    assert result.events  # tick 10 has changes to lose

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected failure after events were written")

    monkeypatch.setattr(store, "_write_state", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.commit_tick(loaded.meta.run_id, result, ControlRecord("c-0001", "step", {}, 9))
    monkeypatch.undo()
    assert counts(store.path, loaded.meta.run_id) == before
    assert store.load_active().world == before_state
    # The same tick can still be committed afterwards: nothing was half-applied.
    store.commit_tick(loaded.meta.run_id, result)
    assert store.load_active().world == result.world


def test_failure_while_writing_ledger_rolls_back_events_and_cells(store, monkeypatch):
    loaded = store.load_active()
    world = step_many(store, loaded, 9)
    before = counts(store.path, loaded.meta.run_id)
    original = store._insert_event

    def broken(db, run_id, sequence, event, width):
        original(db, run_id, sequence, event, width)
        raise RuntimeError("crash after events and ledger rows were inserted")

    monkeypatch.setattr(store, "_insert_event", staticmethod(broken))
    with pytest.raises(RuntimeError):
        store.commit_tick(loaded.meta.run_id, advance(world))
    monkeypatch.undo()
    assert counts(store.path, loaded.meta.run_id) == before
    assert store.load_active().world.tick == 9


def test_duplicate_or_skipped_tick_commits_are_refused(store):
    loaded = store.load_active()
    result = advance(loaded.world)
    store.commit_tick(loaded.meta.run_id, result)
    before = counts(store.path, loaded.meta.run_id)
    with pytest.raises(StoreError):
        store.commit_tick(loaded.meta.run_id, result)  # duplicate
    with pytest.raises(StoreError):
        store.commit_tick(loaded.meta.run_id, advance(advance(result.world).world))  # skipped
    assert counts(store.path, loaded.meta.run_id) == before


def test_control_command_ids_are_unique_per_run(store):
    run_id = store.load_active().meta.run_id
    store.log_control(run_id, ControlRecord("command-1", "pause", {}, 0))
    assert store.control_seen(run_id, "command-1")
    with pytest.raises(sqlite3.IntegrityError):
        store.log_control(run_id, ControlRecord("command-1", "pause", {}, 0))


def test_corrupt_committed_state_is_detected_not_replaced(store):
    loaded = store.load_active()
    step_many(store, loaded, 3)
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("UPDATE cells SET energy = energy + 1 WHERE idx = 5")
    with pytest.raises(StoreError):
        store.load_active()
    with pytest.raises(StoreError):
        SqliteStore(store.path).initialize()
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM run_manifest").fetchone()[0] == 1


def test_missing_active_reference_preserves_runs(store):
    with closing(sqlite3.connect(store.path)) as db, db:
        db.execute("DELETE FROM active_sim_run")
    with pytest.raises(StoreError):
        SqliteStore(store.path).initialize()


def test_older_m01_tables_are_left_untouched(tmp_path):
    path = tmp_path / "m01.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, frame TEXT NOT NULL)")
        db.execute("CREATE TABLE active_run (singleton INTEGER PRIMARY KEY, run_id TEXT)")
        db.execute("INSERT INTO runs VALUES ('old', ?)", (json.dumps({"legacy": True}),))
        db.execute("INSERT INTO active_run VALUES (1, 'old')")
    SqliteStore(path).initialize()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT frame FROM runs").fetchall() == [('{"legacy": true}',)]
        assert db.execute("SELECT run_id FROM active_run").fetchall() == [("old",)]


def test_new_run_keeps_previous_run_and_restart_restores_active(store):
    first = store.load_active()
    step_many(store, first, 12)
    second = store.create_run(WorldConfig.for_run(7))
    assert second.meta.run_id != first.meta.run_id and second.world.tick == 0
    again = SqliteStore(store.path)
    again.initialize()
    assert again.load_active().meta.run_id == second.meta.run_id
    assert again.load_run(first.meta.run_id).world.tick == 12


def test_ledger_is_rebuilt_from_entries_and_checked_against_cells(store):
    loaded = store.load_active()
    step_many(store, loaded, 20)
    reloaded = store.load_active().world
    assert reloaded.ledger.environmental_inflow > 0
    assert reloaded.total_energy == reloaded.ledger.expected_energy
    assert isinstance(reloaded.ledger, Ledger)


def test_replayed_events_match_committed_state(store):
    loaded = store.load_active()
    final = step_many(store, loaded, 30)
    world = loaded.world
    for sequence in (2, 3, 4):
        detail = store.get_event(loaded.meta.run_id, sequence)
        event = WorldEvent(
            tick=detail["tick"],
            phase=detail["phase"],
            type=detail["type"],
            rule_id=detail["rule_id"],
            cause=detail["cause"],
            changes=tuple(
                CellChange(c["x"], c["y"], c["field"], c["before"], c["after"])
                for c in detail["changes"]
            ),
            ledger=tuple(
                LedgerEntry(e["resource"], e["category"], e["amount"]) for e in detail["ledger"]
            ),
        )
        world = apply_event(world, event)
    assert world.cells == final.cells
