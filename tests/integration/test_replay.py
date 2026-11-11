"""M03: snapshots, restart and replay without recomputation."""

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from genesis.domain.config import WorldConfig
from genesis.engine import world as engine_world
from genesis.engine.world import advance
from genesis.persistence import snapshots
from genesis.persistence.events import SequenceError
from genesis.persistence.manifest import sha256
from genesis.persistence.replay import HistoryError, HistoryUnavailable
from genesis.persistence.sqlite import ControlRecord, SqliteStore, StoreError

FIXTURE = Path(__file__).parents[1] / "fixtures" / "m02_schema_v2.sql"
HISTORY_TABLES = ("events", "event_cell_changes", "ledger_entries", "ticks", "cells", "control_log")
FIXTURE_RUN = "3512623a-72fb-44fb-b521-2cd916508b35"
FIXTURE_TICK_25_HASH = "b1383cddfac08047c345f06bf4d01faf1c94cb82caeb1275960d5e4e5ea08b36"


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "genesis.sqlite3")
    s.initialize()
    return s


def run_ticks(store, n, run_id=None):
    loaded = store.load_run(run_id) if run_id else store.load_active()
    world = loaded.world
    for _ in range(n):
        result = advance(world)
        store.commit_tick(loaded.meta.run_id, result)
        world = result.world
    return loaded.meta.run_id, world


def db(store):
    return closing(sqlite3.connect(store.path))


def table_counts(store, run_id):
    with db(store) as conn:
        return {
            t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE run_id = ?", (run_id,)).fetchone()[0]
            for t in ("events", "event_cell_changes", "ledger_entries", "ticks", "snapshots")
        }


# -- snapshots ---------------------------------------------------------------------------


def test_snapshots_written_at_creation_and_every_100_ticks_in_the_tick_transaction(store):
    run_id, _ = run_ticks(store, 250)
    rows = {r["tick"]: r for r in store.list_snapshots(run_id)}
    assert set(rows) == {0, 100, 200}
    assert rows[0]["reason"] == "initial" and rows[100]["reason"] == "periodic"
    assert all(r["checksum_ok"] for r in rows.values())


def test_snapshot_payload_contains_state_rng_scheduler_and_sequence(store):
    run_id, world = run_ticks(store, 100)
    with db(store) as conn:
        payload, checksum, seq = conn.execute(
            "SELECT payload, checksum, last_sequence FROM snapshots WHERE run_id=? AND tick=100",
            (run_id,),
        ).fetchone()
    assert sha256(payload) == checksum
    data = json.loads(payload)
    assert data["rng_state"] == world.rng_state and data["rng_state"] is not None
    assert data["scheduler"] == {
        "next_tick": 101,
        "next_inflow_tick": 110,
        "rules": ["env.energy_inflow.v1", "life.upkeep.v1"],
        "deciding": [],  # standard runs have no organisms until cognition exists
    }
    assert data["schema"] == 9 and data["organisms"] == [] and data["memories"] == []
    assert data["messages"] == []
    assert data["life"]["upkeep"] == 1
    assert data["last_sequence"] == seq == 11  # creation + 10 inflow events
    decoded, _ = snapshots.decode(payload)
    assert decoded == world


def test_snapshot_of_uncommitted_state_is_refused(store):
    loaded = store.load_active()
    candidate = advance(loaded.world).world
    with pytest.raises(StoreError):
        store.write_snapshot(loaded.meta.run_id, candidate, "pause")


# -- replay ------------------------------------------------------------------------------


def test_full_replay_equals_snapshot_plus_tail_at_every_checked_tick(store):
    run_id, final = run_ticks(store, 230)
    for tick in (0, 1, 9, 10, 99, 100, 101, 150, 199, 200, 229, 230):
        full = store.history(run_id, tick, use_snapshots=False)
        fast = store.history(run_id, tick)
        assert full.world == fast.world
        assert full.world.state_hash() == fast.world.state_hash() == full.recorded_hash
        assert full.source == "initial" and full.ticks_replayed == tick
        assert fast.source == ("snapshot" if tick >= 0 else "initial")
        assert fast.snapshot_tick == max(t for t in (0, 100, 200) if t <= tick)
    assert store.history(run_id, 230).world == final


def test_replay_never_recomputes_rules_or_seeding(store, monkeypatch):
    run_id, final = run_ticks(store, 120)
    store._records.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not run environmental rules or seeding")

    monkeypatch.setattr(engine_world, "advance", forbidden)
    monkeypatch.setattr(engine_world, "create_world", forbidden)
    monkeypatch.setattr(engine_world, "RULES", (forbidden,))
    monkeypatch.setattr("genesis.engine.rules.energy_inflow", forbidden)
    monkeypatch.setattr("genesis.persistence.sqlite.create_world", forbidden)
    assert store.history(run_id, 120, use_snapshots=False).world == final
    assert store.load_run(run_id).world == final


@pytest.mark.parametrize("tick", [121, 10_000, -1])
def test_unavailable_history_is_an_explicit_error(store, tick):
    run_id, _ = run_ticks(store, 120)
    with pytest.raises(HistoryUnavailable):
        store.history(run_id, tick)


def test_missing_event_makes_history_unavailable_not_regenerated(store):
    run_id, _ = run_ticks(store, 40)
    with db(store) as conn, conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "DELETE FROM event_cell_changes WHERE run_id=? AND event_sequence=3", (run_id,)
        )
        conn.execute("DELETE FROM events WHERE run_id=? AND sequence=3", (run_id,))
    with pytest.raises(HistoryError):
        store.history(run_id, 40, use_snapshots=False)
    with pytest.raises(StoreError):
        SqliteStore(store.path).load_run(run_id)


def test_history_reads_change_nothing(store):
    run_id, final = run_ticks(store, 150)
    before = table_counts(store, run_id)
    with db(store) as conn:
        version = conn.execute("PRAGMA data_version").fetchone()[0]
        for tick in (0, 50, 100, 150):
            store.history(run_id, tick)
            store.history(run_id, tick, use_snapshots=False)
        assert conn.execute("PRAGMA data_version").fetchone()[0] == version
    assert table_counts(store, run_id) == before
    assert store.load_run(run_id).world == final


# -- corrupted snapshots fall back ---------------------------------------------------------


def corrupt(store, run_id, tick, **changes):
    with db(store) as conn, conn:
        for column, value in changes.items():
            conn.execute(
                f"UPDATE snapshots SET {column} = ? WHERE run_id = ? AND tick = ?",
                (value, run_id, tick),
            )


def test_corrupted_snapshot_falls_back_to_earlier_valid_snapshot(store):
    run_id, final = run_ticks(store, 230)
    corrupt(store, run_id, 200, payload='{"tampered": true}')
    result = store.history(run_id, 230)
    assert result.source == "snapshot" and result.snapshot_tick == 100
    assert result.skipped_snapshots[0][0] == 200 and "checksum" in result.skipped_snapshots[0][1]
    assert result.world == final


@pytest.mark.parametrize(
    "tamper",
    [
        "checksum",  # payload intact, checksum wrong
        "rehashed",  # payload altered and re-checksummed: caught by recorded tick hash
        "format",  # unsupported snapshot format version
        "sequence",  # event boundary disagrees with events
    ],
)
def test_each_snapshot_defect_is_detected(store, tamper):
    run_id, final = run_ticks(store, 150)
    with db(store) as conn:
        payload = conn.execute(
            "SELECT payload FROM snapshots WHERE run_id=? AND tick=100", (run_id,)
        ).fetchone()[0]
    if tamper == "checksum":
        corrupt(store, run_id, 100, checksum="0" * 64)
    elif tamper == "rehashed":
        data = json.loads(payload)
        data["cells"][0][0] = (data["cells"][0][0] + 1) % 100
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        corrupt(store, run_id, 100, payload=text, checksum=sha256(text))
    elif tamper == "format":
        corrupt(store, run_id, 100, format_version=99)
    else:
        data = json.loads(payload)
        data["last_sequence"] = 3
        text = json.dumps(data, sort_keys=True, separators=(",", ":"))
        corrupt(store, run_id, 100, payload=text, checksum=sha256(text), last_sequence=3)
    result = store.history(run_id, 150)
    assert result.snapshot_tick == 0 and result.skipped_snapshots[0][0] == 100
    assert result.world == final


def test_all_snapshots_corrupt_falls_back_to_initial_and_is_logged_on_restart(store):
    run_id, final = run_ticks(store, 120)
    with db(store) as conn, conn:
        conn.execute("UPDATE snapshots SET checksum = '0' WHERE run_id = ?", (run_id,))
    restarted = SqliteStore(store.path)
    restarted.initialize()
    loaded = restarted.load_run(run_id)
    assert loaded.world == final
    assert loaded.restore.source == "initial" and loaded.restore.ticks_replayed == 120
    assert {t for t, _ in loaded.restore.skipped_snapshots} == {0, 100}
    notes = restarted.technical_log(run_id)
    assert {n["subject"] for n in notes} == {"tick 0", "tick 100"}
    assert all(n["kind"] == "snapshot_invalid" for n in notes)


# -- restart -------------------------------------------------------------------------------


def test_restart_restores_from_latest_snapshot_plus_tail(store):
    run_id, final = run_ticks(store, 157)
    restarted = SqliteStore(store.path)
    restarted.initialize()
    loaded = restarted.load_active()
    assert loaded.world == final and loaded.world.tick == 157
    assert (loaded.restore.source, loaded.restore.snapshot_tick) == ("snapshot", 100)
    assert loaded.restore.ticks_replayed == 57 and loaded.restore.events_applied == 5


def test_restart_adds_no_duplicate_environmental_effects(store):
    run_id, _ = run_ticks(store, 10)
    first = table_counts(store, run_id)
    for _ in range(3):
        restarted = SqliteStore(store.path)
        restarted.initialize()
        assert restarted.load_active().world.tick == 10
    assert table_counts(store, run_id) == first
    restarted = SqliteStore(store.path)
    restarted.initialize()
    loaded = restarted.load_active()
    result = advance(loaded.world)
    assert result.events == ()  # tick 11 is not an inflow tick; tick 10's is not repeated
    restarted.commit_tick(run_id, result)
    with db(store) as conn:
        assert conn.execute(
            "SELECT tick, COUNT(*) FROM events "
            "WHERE run_id=? AND type='energy_inflow' GROUP BY tick",
            (run_id,),
        ).fetchall() == [(10, 1)]


def test_projection_disagreeing_with_history_stops_recovery(store):
    run_id, _ = run_ticks(store, 30)
    with db(store) as conn, conn:
        conn.execute("UPDATE cells SET energy = energy + 1 WHERE run_id=? AND idx=7", (run_id,))
    with pytest.raises(StoreError, match="disagrees with recorded history"):
        SqliteStore(store.path).initialize()


def test_events_beyond_last_committed_tick_stop_recovery(store):
    run_id, world = run_ticks(store, 19)
    with db(store) as conn, conn:
        conn.execute(
            "INSERT INTO events (run_id, sequence, tick, phase, type, rule_id, cause, "
            "summary_json) VALUES (?, 3, 20, 'environment', 'energy_inflow', "
            "'env.energy_inflow.v1', 'environment', '{}')",
            (run_id,),
        )
    with pytest.raises(StoreError):
        SqliteStore(store.path).initialize()


# -- transaction uniqueness ------------------------------------------------------------------


def test_duplicate_event_sequence_is_refused_and_tick_rolled_back(store, monkeypatch):
    run_id, world = run_ticks(store, 9)
    before = table_counts(store, run_id)
    original = store._insert_event

    def duplicate(db_, run, sequence, event, width):
        original(db_, run, sequence - 1, event, width)  # reuse an existing sequence

    monkeypatch.setattr(store, "_insert_event", staticmethod(duplicate))
    with pytest.raises(SequenceError):
        store.commit_tick(run_id, advance(world))
    monkeypatch.undo()
    assert table_counts(store, run_id) == before
    with db(store) as conn, conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (run_id, sequence, tick, phase, type, rule_id, cause, "
            "summary_json) VALUES (?, 1, 0, 'x', 'x', 'x', 'x', '{}')",
            (run_id,),
        )


# -- crash before / after commit (real process termination) ------------------------------


CRASH_SCRIPT = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from genesis.engine.world import advance
    from genesis.persistence.sqlite import SqliteStore
    store = SqliteStore(Path(sys.argv[1]))
    store.initialize()
    loaded = store.load_active()
    result = advance(loaded.world)
    mode = sys.argv[2]
    if mode == "before":
        original = store._write_state
        def write_then_die(db, run_id, res):
            original(db, run_id, res)
            os._exit(17)  # killed inside the transaction, before COMMIT
        store._write_state = write_then_die
    store.commit_tick(loaded.meta.run_id, result)
    if mode == "after":
        os._exit(17)  # killed after COMMIT, before anything is published
    """
)


@pytest.mark.parametrize("mode,expected_tick", [("before", 9), ("after", 10)])
def test_process_killed_around_tick_commit(store, mode, expected_tick):
    run_id, _ = run_ticks(store, 9)
    before = table_counts(store, run_id)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "backend")}
    proc = subprocess.run(
        [sys.executable, "-c", CRASH_SCRIPT, str(store.path), mode], env=env, timeout=60
    )
    assert proc.returncode == 17
    restarted = SqliteStore(store.path)
    restarted.initialize()
    loaded = restarted.load_active()
    assert loaded.world.tick == expected_tick
    after = table_counts(store, run_id)
    if mode == "before":
        assert after == before  # nothing partial survived
    else:
        assert after["ticks"] == before["ticks"] + 1 and after["events"] == before["events"] + 1
    # Continuing from the recovered state never duplicates tick 10's inflow.
    world = loaded.world
    while world.tick < 12:
        result = advance(world)
        restarted.commit_tick(run_id, result)
        world = result.world
    with db(store) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id=? AND tick=10", (run_id,)
            ).fetchone()[0]
            == 1
        )


# -- schema / manifest versioning --------------------------------------------------------------


def test_new_runs_have_sealed_manifest_and_rng_state_in_hash(store):
    loaded = store.load_active()
    assert loaded.meta.state_schema == 9 and loaded.meta.manifest_version == 3
    with db(store) as conn:
        text, digest, rng = conn.execute(
            "SELECT manifest_json, manifest_hash, initial_rng_state FROM run_manifest"
        ).fetchone()
    data = json.loads(text)
    assert sha256(text) == digest and data["initial_rng_state_sha256"] == sha256(rng)
    assert data["snapshot_interval"] == 100 and data["state_schema"] == 9
    assert data["rules_version"] == "m10-rules-1" and data["life"]["seeds"] == []
    assert data["life"]["decision_interval"] == 4 and data["life"]["cognition_fee"] == 1
    assert data["life"]["material_capacity"] == 20
    world = loaded.world
    assert replace(world, rng_state=world.rng_state + " ").state_hash() != world.state_hash()


def test_tampered_manifest_is_rejected(store):
    with db(store) as conn, conn:
        text = conn.execute("SELECT manifest_json FROM run_manifest").fetchone()[0]
        conn.execute(
            "UPDATE run_manifest SET manifest_json = ?", (text.replace('"seed":42', '"seed":43'),)
        )
    with pytest.raises(StoreError, match="manifest"):
        SqliteStore(store.path).initialize()


def test_unsupported_run_schema_is_rejected(store):
    with db(store) as conn, conn:
        text = conn.execute("SELECT manifest_json FROM run_manifest").fetchone()[0]
        data = json.loads(text)
        data["state_schema"] = 99
        new = json.dumps(data, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE run_manifest SET manifest_json=?, manifest_hash=?, schema_version=99",
            (new, sha256(new)),
        )
    with pytest.raises(StoreError, match="Unsupported state schema"):
        SqliteStore(store.path).initialize()


def test_unsupported_database_version_is_refused_untouched(tmp_path):
    path = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE something (x)")
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(StoreError, match="Unsupported database schema version 99"):
        SqliteStore(path).initialize()
    with closing(sqlite3.connect(path)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"something"}  # no M03 tables created in an unknown database


def test_m02_database_migrates_without_rewriting_history(tmp_path):
    path = tmp_path / "m02.sqlite3"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(FIXTURE.read_text())
    with closing(sqlite3.connect(path)) as conn:
        before = {
            t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
            for t in (
                "events",
                "event_cell_changes",
                "ledger_entries",
                "ticks",
                "cells",
                "control_log",
            )
        }
    store = SqliteStore(path)
    store.initialize()
    loaded = store.load_active()
    assert loaded.meta.run_id == FIXTURE_RUN and loaded.world.tick == 25
    assert loaded.meta.state_schema == 2 and loaded.meta.manifest_version == 1
    assert loaded.world.rng_state is None
    assert loaded.world.state_hash() == FIXTURE_TICK_25_HASH  # M02 hash still verifies
    assert loaded.started is True
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
        for table, rows in before.items():
            current = conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            if table == "events":  # M04 appended actor_id/payload_json columns only
                current = [row[:8] for row in current]
            assert current == rows
        migrations = conn.execute(
            "SELECT from_version, to_version, detail FROM schema_migrations ORDER BY 1"
        ).fetchall()
        assert [(a, b) for a, b, _ in migrations] == [(2, 3), (3, 4), (4, 5), (5, 6), (6, 7)]
        assert "sealed 1" in migrations[0][2]
    # Legacy run: full replay, history, new ticks and snapshots all work.
    assert store.history(FIXTURE_RUN, 12, use_snapshots=False).world.tick == 12
    world = loaded.world
    for _ in range(80):
        result = advance(world)
        store.commit_tick(FIXTURE_RUN, result)
        world = result.world
    assert store.write_snapshot(FIXTURE_RUN, world, "pause")
    again = SqliteStore(path)
    again.initialize()
    assert again.load_active().world == world
    assert again.load_active().restore.snapshot_tick == 105
    # Re-initializing a migrated database is a no-op.
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 5


def test_control_log_survives_and_new_run_gets_initial_snapshot(store):
    run_id = store.load_active().meta.run_id
    store.log_control(run_id, ControlRecord("command-01", "start", {}, 0))
    second = store.create_run(WorldConfig.for_run(5))
    assert [s["tick"] for s in store.list_snapshots(second.meta.run_id)] == [0]
    assert SqliteStore(store.path).load_run(run_id).started
