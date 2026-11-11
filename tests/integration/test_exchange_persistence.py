"""M09 exchange through the runner, store, replay, restart and observer API."""

import itertools
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.domain.config import LifeConfig, SeedOrganism, WorldConfig
from genesis.persistence.sqlite import SqliteStore
from genesis.runtime.commands import Start
from genesis.runtime.runner import SimulationRunner

_ids = itertools.count(1)


@pytest.fixture
def database(tmp_path):
    return tmp_path / "runs.sqlite3"


def control(client, command, **extra):
    body = {"command": command, "command_id": f"ex-{next(_ids):06d}", **extra}
    return client.post("/api/control", json=body)


def run_until(client, tick):
    started = client.get("/api/world").json()["started"]
    assert control(client, "resume" if started else "start", until_tick=tick).status_code == 200
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        frame = client.get("/api/world").json()
        if frame["status"] == "paused" and (frame["tick"] >= tick or frame["extinct"]):
            return frame
        time.sleep(0.02)
    raise AssertionError("run did not reach the tick")


def test_exchange_pair_is_visible_inspectable_and_survives_restart(database):
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "exchange_pair"})
        frame = run_until(client, 4)
        assert frame["schema_version"] == 8 and frame["state_schema"] == 10
        # Tick 4: the giver's message is drawn on the grid for that tick.
        (line,) = frame["interactions"]
        assert line["kind"] == "communicate" and (line["from_id"], line["to_id"]) == (
            "org-0001",
            "org-0002",
        )
        assert (line["from_x"], line["to_x"]) == (12, 13)
        frame = run_until(client, 9)
        detail = client.get("/api/organisms/org-0002").json()
        recent = {m["id"]: m for m in detail["messages"]["recent"]}
        assert recent["org-0001-s00001"]["kind"] == "transfer"
        assert recent["org-0001-s00001"]["state"] == "read"
        assert recent["org-0001-s00003"]["text"] == "There is loose material on my cell."
        assert recent["org-0002-s00001"]["text"] == "Received. Thank you."  # its own reply
        assert detail["messages"]["inbox"] == [] and detail["messages"]["inbox_capacity"] == 8
        # History: at tick 4 the message was still pending (sent, not yet delivered).
        early = client.get("/api/organisms/org-0002", params={"at_tick": 4}).json()
        assert [m["id"] for m in early["messages"]["inbox"]] == ["org-0001-s00003"]
        assert early["messages"]["inbox"][0]["status"] == "pending"
        history = client.get("/api/history/4").json()
        assert [i["kind"] for i in history["interactions"]] == ["communicate"]
        events = client.get("/api/events", params={"limit": 200}).json()["events"]
        delivered = next(e for e in events if e["type"] == "messages_delivered")
        detail_event = client.get(f"/api/events/{delivered['sequence']}").json()
        assert {c["reason"] for c in detail_event["message_changes"]} >= {"delivered"}
        live_hash = frame["state_hash"]
    store = SqliteStore(database)
    store.initialize()
    loaded = store.load_active()
    assert loaded.world.state_hash() == live_hash
    replayed = store.history(loaded.meta.run_id, loaded.world.tick, use_snapshots=False)
    assert replayed.world == loaded.world


def test_ten_organisms_each_get_one_opportunity_per_due_tick_with_a_bounded_queue(tmp_path):
    store = SqliteStore(tmp_path / "ten.sqlite3")
    store.initialize()
    seeds = tuple(SeedOrganism(3 * n, 3 * n, "wait") for n in range(10))
    store.create_run(WorldConfig.for_run(7), LifeConfig(provider="scripted", seeds=seeds))
    runner = SimulationRunner(store, speed=1000)
    runner.start_thread()
    try:
        runner.execute(Start("start-ten-0001", until_tick=12))
        deadline = time.monotonic() + 30
        while runner.snapshot().world.tick < 12 and time.monotonic() < deadline:
            time.sleep(0.01)
        snapshot = runner.snapshot()
        assert snapshot.world.tick == 12 and len(snapshot.world.living) == 10
        assert runner._worker.max_depth <= snapshot.world.life.queue_capacity
        run_id = snapshot.meta.run_id
    finally:
        runner.stop()
    with closing(sqlite3.connect(store.path)) as db:
        per_tick = db.execute(
            "SELECT tick, COUNT(*), COUNT(DISTINCT actor_id) FROM decision_attempts "
            "WHERE run_id = ? GROUP BY tick ORDER BY tick",
            (run_id,),
        ).fetchall()
    # First decisions at tick 1 and periodic ones every 4 ticks: all 10, once each. Tick
    # 10's inflow (+1 per cell) can also cross the local-change trigger for some views.
    counts = {t: (n, distinct) for t, n, distinct in per_tick}
    assert [counts[t] for t in (1, 5, 9)] == [(10, 10)] * 3
    assert set(counts) <= {1, 5, 9, 10} and all(n == d for n, d in counts.values())


def test_new_runs_can_use_a_smaller_square_map(database):
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        frame = client.post("/api/runs", json={"seed": 42, "size": 16}).json()
        assert (frame["config"]["width"], frame["config"]["height"]) == (16, 16)
        assert len(frame["cells"]) == 256 and frame["ledger"]["balanced"]
        refused = client.post(
            "/api/runs", json={"seed": 42, "fixture": "exchange_pair", "size": 16}
        )
        assert refused.status_code == 422
        assert client.post("/api/runs", json={"seed": 42, "size": 7}).status_code == 422
    from genesis.api.routes import live_seeds

    placed = live_seeds(42, 10, 16)
    assert len({(s.x, s.y) for s in placed}) == 10
    assert all(0 <= s.x < 16 and 0 <= s.y < 16 for s in placed)
    assert live_seeds(42, 3) == live_seeds(42, 3, 32)  # 32 x 32 placements are unchanged
