"""M08 memory through the runner, store, replay, restart and observer API."""

import itertools
import time

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.persistence.sqlite import SqliteStore

_ids = itertools.count(1)


@pytest.fixture
def database(tmp_path):
    return tmp_path / "runs.sqlite3"


def control(client, command, **extra):
    body = {"command": command, "command_id": f"mem-{next(_ids):06d}", **extra}
    return client.post("/api/control", json=body)


def run_until(client, tick):
    assert control(client, "start", until_tick=tick).status_code == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        frame = client.get("/api/world").json()
        if frame["status"] == "paused" and (frame["tick"] >= tick or frame["extinct"]):
            return frame
        time.sleep(0.02)
    raise AssertionError("run did not reach the tick")


def test_memory_return_recall_is_inspectable_and_survives_restart(database):
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "memory_return"})
        frame = run_until(client, 9)
        (organism,) = frame["organisms"]
        assert (organism["x"], organism["y"], organism["carried_material"]) == (10, 10, 5)
        detail = client.get("/api/organisms/org-0001").json()
        mem = detail["memory"]
        assert mem["active_records"] == len(mem["active"]) > 0 and mem["forgotten"] == []
        assert mem["limits"]["max_records"] == 64 and mem["limits"]["recall_max"] == 16
        stockpile = mem["active"][-1]  # newest first: the last entry is the oldest record
        assert stockpile["id"] == "org-0001-m00001" and "loose material 25" in stockpile["text"]
        assert (stockpile["x"], stockpile["y"], stockpile["tick"]) == (10, 10, 1)
        event = client.get(f"/api/events/{stockpile['added_sequence']}").json()
        assert event["type"] == "memory_written" and event["actor_id"] == "org-0001"
        assert event["memory_changes"][0]["record"]["id"] == "org-0001-m00001"
        # The recall behind the latest decision: ids, scores and what the organism received.
        recall = mem["last_recall"]
        assert recall["recall"]["version"] == "recall-v1"
        assert recall["recall"]["selected_ids"] == [r["id"] for r in recall["recalled"]]
        assert all("x" not in r for r in recall["recalled"])
        # The decision at tick 5 recalled the stockpile from three cells away.
        decisions = client.get("/api/decisions", params={"limit": 50}).json()["decisions"]
        (at5,) = [d for d in decisions if d["tick"] == 5]
        request = client.get(f"/api/decisions/{at5['request_id']}").json()["request"]
        stock = [r for r in request["context"]["recalled"] if r["id"] == "org-0001-m00001"]
        assert stock and (stock[0]["dx"], stock[0]["dy"]) == (-3, 0)
        assert "move" in request["recall"]["query"]  # its last accepted action
        live_hash = frame["state_hash"]
        # History: the active set at an earlier tick is reconstructed, not current.
        early = client.get("/api/organisms/org-0001", params={"at_tick": 2}).json()["memory"]
        assert [m["id"] for m in early["active"]] == [
            "org-0001-m00003",
            "org-0001-m00002",
            "org-0001-m00001",
        ]
    store = SqliteStore(database)
    store.initialize()
    loaded = store.load_active()
    assert loaded.world.state_hash() == live_hash and loaded.world.memories
    replayed = store.history(loaded.meta.run_id, loaded.world.tick, use_snapshots=False)
    assert replayed.world == loaded.world


def test_memory_budget_forgets_oldest_records_but_history_keeps_them(database):
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "memory_budget"})
        frame = run_until(client, 60)
        assert frame["extinct"] and frame["ledger"]["balanced"]
        mem = client.get("/api/organisms/org-0001").json()["memory"]
        assert mem["active_records"] == 6 and mem["written_total"] > 6
        forgotten = {m["id"] for m in mem["forgotten"]}
        assert "org-0001-m00001" in forgotten and not forgotten & {m["id"] for m in mem["active"]}
        assert mem["written_total"] == len(forgotten) + mem["active_records"]
        oldest = next(m for m in mem["forgotten"] if m["id"] == "org-0001-m00001")
        assert oldest["text"].startswith("cell:") and oldest["forgotten_tick"] > oldest["tick"]
        events = client.get("/api/events", params={"limit": 200}).json()["events"]
        types = {e["type"] for e in events}
        assert {"memory_written", "memory_skipped", "memory_upkeep"} <= types
        died = next(e for e in events if e["type"] == "organism_died")
        assert died["summary"]["cause"] == "starvation"


def test_snapshot_with_memory_restores_without_full_replay(tmp_path):
    store = SqliteStore(tmp_path / "db.sqlite3")
    store.initialize()
    store.create_run(WorldConfig.for_run(42, "memory_return"), FIXTURES["memory_return"].life)
    from genesis.runtime.commands import Pause, Start
    from genesis.runtime.runner import SimulationRunner

    runner = SimulationRunner(store, speed=1000)
    runner.start_thread()
    runner.execute(Start("start-000001", until_tick=12))
    deadline = time.monotonic() + 20
    while runner.snapshot().world.tick < 12 and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.execute(Pause("pause-000001"))  # graceful snapshot (format 3, with memory)
    world = runner.snapshot().world
    runner.stop()
    again = SqliteStore(store.path)
    again.initialize()
    loaded = again.load_active()
    assert loaded.restore.source == "snapshot" and loaded.restore.snapshot_tick == 12
    assert loaded.world == world and len(loaded.world.memories) > 0
