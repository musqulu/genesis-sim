"""M15: technical failure injection and safe recovery, with ecological outcomes untouched."""

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.ops import health, recovery
from genesis.persistence.sqlite import SqliteStore
from genesis.runtime.commands import CommandError, Resume, Start, Step
from genesis.runtime.runner import SimulationRunner

ROOT = Path(__file__).resolve().parents[2]


def make_store(path, fixture):
    store = SqliteStore(path)
    store.initialize()
    store.create_run(WorldConfig.for_run(42, fixture), FIXTURES[fixture].life)
    return store


def counts(path, run_id):
    with closing(sqlite3.connect(path)) as db:
        return {
            "tick": db.execute(
                "SELECT MAX(tick) FROM ticks WHERE run_id = ?", (run_id,)
            ).fetchone()[0],
            "events": db.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()[0],
            "attempts": db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT request_id) FROM decision_attempts "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone(),
            "births": db.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ? AND rule_id = 'life.birth.v1'",
                (run_id,),
            ).fetchone()[0],
            "organisms": db.execute(
                "SELECT COUNT(*) FROM organisms WHERE run_id = ?", (run_id,)
            ).fetchone()[0],
        }


def gapless(path, run_id):
    with closing(sqlite3.connect(path)) as db:
        seqs = [
            r[0]
            for r in db.execute(
                "SELECT sequence FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            )
        ]
    return seqs == list(range(1, len(seqs) + 1))


# -- in-process failure injection ----------------------------------------------------------


def test_crash_inside_the_tick_transaction_rolls_back_and_reuses_the_response(
    tmp_path, monkeypatch
):
    path = tmp_path / "db.sqlite3"
    store = make_store(path, "recorded_valid")
    runner = SimulationRunner(store, speed=1000)
    runner.start_thread()
    run_id = runner.snapshot().meta.run_id
    original = SqliteStore._write_attempts

    def boom(*args, **kwargs):
        raise RuntimeError("injected crash inside the tick transaction")

    monkeypatch.setattr(SqliteStore, "_write_attempts", staticmethod(boom))
    with pytest.raises(CommandError):
        runner.execute(Step("crash-step-01"))
    assert "injected crash" in (runner.snapshot().last_error or "")
    before = counts(path, run_id)
    assert before["tick"] == 0 and before["attempts"] == (0, 0)  # nothing half-committed
    runner._stopping = True  # abandon this process state, as a crash would
    monkeypatch.setattr(SqliteStore, "_write_attempts", staticmethod(original))
    again = SimulationRunner(SqliteStore(path), speed=1000)
    again.start_thread()
    try:
        provider = again._provider
        again.execute(Step("crash-step-02"))
        after = counts(path, run_id)
        assert after["tick"] == 1 and after["attempts"] == (1, 1)
        assert provider.calls == 0  # the stored response was reused, not asked again
        kinds = {e["kind"] for e in again.store.technical_log(run_id)}
        assert {"tick_failure", "response_reused", "loaded"} <= kinds
    finally:
        again.stop()


def test_crash_after_commit_before_publish_never_duplicates_a_birth(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    store = make_store(path, "birth_pair")
    runner = SimulationRunner(store, speed=1000)
    runner.start_thread()
    run_id = runner.snapshot().meta.run_id

    def lost(*args, **kwargs):
        raise RuntimeError("injected crash after commit")

    monkeypatch.setattr(SimulationRunner, "_publish", lost)
    with pytest.raises(CommandError):
        runner.execute(Step("birth-step-01"))
    runner._stopping = True
    committed = counts(path, run_id)
    assert committed["tick"] == 1 and committed["births"] == 1 and committed["organisms"] == 2
    monkeypatch.undo()
    again = SimulationRunner(SqliteStore(path), speed=1000)
    again.start_thread()
    try:
        assert again.snapshot().world.tick == 1 and len(again.snapshot().world.organisms) == 2
        for n in range(3):
            again.execute(Step(f"birth-step-1{n}"))
        after = counts(path, run_id)
        assert after["births"] == 1 and after["organisms"] == 2 and after["tick"] == 4
        assert gapless(path, run_id)
        assert health.invariants(again.snapshot().world)["ledger_balanced"]
    finally:
        again.stop()


def test_low_disk_pauses_before_a_tick_and_resumes_when_space_returns(tmp_path, monkeypatch):
    path = tmp_path / "db.sqlite3"
    store = make_store(path, "zero_inflow")
    runner = SimulationRunner(store, speed=1000)
    runner.start_thread()
    run_id = runner.snapshot().meta.run_id
    monkeypatch.setattr(health, "free_bytes", lambda p: 1024)
    try:
        runner.execute(Start("disk-start-01"))
        deadline = time.monotonic() + 10
        while runner.snapshot().status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = runner.snapshot()
        assert snapshot.world.tick == 0 and "Low disk space" in snapshot.last_error
        assert "low_disk" in {e["kind"] for e in store.technical_log(run_id)}
        monkeypatch.setattr(health, "free_bytes", lambda p: 10 * 2**30)
        runner.execute(Resume("disk-resume-01", until_tick=3))
        deadline = time.monotonic() + 10
        while runner.snapshot().world.tick < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert runner.snapshot().world.tick == 3 and runner.snapshot().last_error is None
    finally:
        runner.stop()


def test_health_report_backups_and_restore_checks(tmp_path):
    database = tmp_path / "db.sqlite3"
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "lineage"})
        for n in range(8):
            client.post("/api/control", json={"command": "step", "command_id": f"h-{n:06d}"})
        report = client.get("/api/health").json()["report"]
        assert report["status"] == "ok" and report["durable_tick"] == report["live_tick"] == 8
        assert report["invariants"]["ledger_balanced"] and report["disk"]["ok"]
        assert report["metrics"]["decisions"] >= 8
        made = client.post("/api/backups").json()
        assert made["ok"] and made["integrity"] == "ok"
        assert any(r["tick"] == 8 and r["hash_ok"] for r in made["runs"])
        listed = client.get("/api/backups").json()["backups"]
        assert listed[0]["verified_ok"] is True
        live_hash = client.get("/api/world").json()["state_hash"]
        app_store = client.app.state.runner.store
    target = tmp_path / "restored.sqlite3"
    restored = recovery.restore_check(app_store, listed[0]["name"], target)
    assert restored["ok"] and restored["tick"] == 8 and restored["state_hash"] == live_hash
    with pytest.raises(recovery.BackupError):
        recovery.restore_check(app_store, listed[0]["name"], database)  # never the live file
    with pytest.raises(recovery.BackupError):
        recovery.restore_check(app_store, listed[0]["name"], target)  # never overwrite
    broken = recovery.backup_dir(app_store) / listed[0]["name"]
    with closing(sqlite3.connect(broken)) as db, db:
        db.execute(
            "UPDATE event_cell_changes SET after = after + 1 WHERE event_sequence = "
            "(SELECT MIN(event_sequence) FROM event_cell_changes)"
        )
    assert recovery.verify(broken)["ok"] is False  # tampered history is detected


# -- real process kills ---------------------------------------------------------------------


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, database: Path):
        self.database, self.port, self.proc = database, free_port(), None

    def start(self):
        env = {
            **os.environ,
            "GENESIS_DB": str(self.database),
            "GENESIS_DEV": "1",
            "GENESIS_REPORTS_DIR": str(self.database.parent / "reports"),
        }
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "genesis.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url("/api/health"), timeout=1).status_code == 200:
                    return self
            except httpx.HTTPError:
                time.sleep(0.1)
        raise AssertionError("server did not start")

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        return httpx.get(self.url(path), timeout=10).json()

    def post(self, path, body):
        return httpx.post(self.url(path), json=body, timeout=30).json()

    def kill(self):
        os.kill(self.proc.pid, signal.SIGKILL)  # no graceful shutdown, no close snapshot
        self.proc.wait(timeout=10)


def wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def test_kill_9_mid_run_recovers_a_coherent_lineage(tmp_path):
    database = tmp_path / "db.sqlite3"
    server = Server(database).start()
    try:
        run_id = server.post("/api/runs", {"seed": 42, "fixture": "lineage"})["run_id"]
        server.post(
            "/api/control", {"command": "speed", "command_id": "k-speed-01", "ticks_per_second": 20}
        )
        server.post("/api/control", {"command": "start", "command_id": "k-start-01"})
        wait(lambda: server.get("/api/world")["tick"] >= 9)  # past the birth at tick 7
        server.kill()
        crashed = counts(database, run_id)
        server = Server(database).start()
        world = server.get("/api/world")
        assert world["tick"] == crashed["tick"] and world["status"] == "paused"
        report = server.get("/api/health")["report"]
        assert (
            report["durable_tick"] == report["live_tick"]
            and report["invariants"]["ledger_balanced"]
        )
        server.post("/api/control", {"command": "resume", "command_id": "k-resume-01"})
        wait(lambda: server.get("/api/world")["extinct"])
        final = counts(database, run_id)
        assert final["births"] == 1 and final["organisms"] == 2  # no duplicated child
        assert final["attempts"][0] == final["attempts"][1]  # one consumed attempt per request
        assert gapless(database, run_id)
        refused = httpx.post(
            server.url("/api/control"), json={"command": "resume", "command_id": "k-resume-02"}
        )
        assert refused.status_code == 409  # extinction is never reseeded by recovery
    finally:
        server.kill()


def test_kill_9_with_a_decision_pending_commits_that_tick_exactly_once(tmp_path):
    database = tmp_path / "db.sqlite3"
    server = Server(database).start()
    try:
        run_id = server.post("/api/runs", {"seed": 42, "fixture": "recorded_delayed"})["run_id"]
        server.post("/api/control", {"command": "start", "command_id": "p-start-01"})
        wait(lambda: server.get("/api/world")["pending"] is not None)
        server.kill()  # the 3-second recorded response never arrived
        assert counts(database, run_id)["tick"] == 0
        server = Server(database).start()
        server.post(
            "/api/control", {"command": "resume", "command_id": "p-resume-01", "until_tick": 2}
        )
        wait(lambda: server.get("/api/world")["tick"] >= 2, timeout=40)
        final = counts(database, run_id)
        assert final["tick"] == 2 and final["attempts"][0] == final["attempts"][1] == 1
        assert gapless(database, run_id)
    finally:
        server.kill()
