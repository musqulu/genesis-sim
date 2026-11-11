"""M04 through persistence, replay, the runner and the API."""

import itertools
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition import fixture_policy
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.engine.world import advance
from genesis.persistence.sqlite import SqliteStore, StoreError
from genesis.runtime.commands import CommandError, Start, Step
from genesis.runtime.runner import SimulationRunner

V3_FIXTURE = Path(__file__).parents[1] / "fixtures" / "m03_schema_v3.sql"
V3_RUN = "3a769e0a-79cb-42c5-b4e0-c3662a2ea0e0"
V3_TICK_35_HASH = "a212d5a9115cd67b6ccae85e6303662be6d1fd10fbef8485b10486d33f3b2511"
_ids = itertools.count(1)


def control(client, command, **extra):
    return client.post(
        "/api/control", json={"command": command, "command_id": f"cmd-{next(_ids):06d}", **extra}
    )


def fixture_store(tmp_path, name):
    store = SqliteStore(tmp_path / f"{name}.sqlite3")
    store.initialize()
    loaded = store.create_run(WorldConfig.for_run(42, name), FIXTURES[name].life)
    return store, loaded


def step_to_extinction(store, loaded, limit=500):
    world = loaded.world
    while not world.extinct and world.tick < limit:
        result = advance(world, fixture_policy.decide)
        store.commit_tick(loaded.meta.run_id, result)
        world = result.world
    return world


# -- persistence and replay -----------------------------------------------------------------


def test_birth_events_and_initial_organisms_are_recorded(tmp_path):
    store, loaded = fixture_store(tmp_path, "corner_perception")
    events, _ = store.list_events(loaded.meta.run_id, 10, None)
    assert [e["type"] for e in reversed(events)] == ["run_created", "organism_born"]
    born = events[0]
    assert born["actor_id"] == "org-0001" and born["summary"]["x"] == 0
    assert "lifespan" not in json.dumps(born)  # observer events do not reveal the deadline
    assert loaded.world.organisms[0].energy == 60 and loaded.meta.state_schema == 9


def test_replay_reconstructs_organisms_exactly_from_snapshot_or_initial(tmp_path):
    store, loaded = fixture_store(tmp_path, "starvation")
    final = step_to_extinction(store, loaded)
    run_id = loaded.meta.run_id
    for tick in range(final.tick + 1):
        full = store.history(run_id, tick, use_snapshots=False).world
        fast = store.history(run_id, tick).world
        assert full == fast and full.state_hash() == fast.state_hash()
    assert store.history(run_id, final.tick).world == final
    restarted = SqliteStore(store.path)
    restarted.initialize()
    assert restarted.load_run(run_id).world == final


def test_extinction_once_persisted_and_restart_cannot_advance(tmp_path):
    store, loaded = fixture_store(tmp_path, "age_limit")
    final = step_to_extinction(store, loaded)
    o = final.organisms[0]
    assert o.death_cause == "age" and o.death_tick == o.lifespan  # born at tick 0
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE type='extinction'").fetchone()[0] == 1
    restarted = SqliteStore(store.path)
    restarted.initialize()
    runner = SimulationRunner(restarted)
    assert runner.snapshot().world.extinct
    for command in (Step("step-0001"), Start("start-001")):
        with pytest.raises(CommandError) as err:
            runner.execute(command)
        assert err.value.status == 409 and "extinct" in err.value.message
    assert runner.snapshot().world.tick == final.tick


def test_death_leaves_cells_untouched_and_retires_energy(tmp_path):
    store, loaded = fixture_store(tmp_path, "age_limit")
    final = step_to_extinction(store, loaded)
    run_id = loaded.meta.run_id
    death_tick = final.organisms[0].death_tick
    before = store.history(run_id, death_tick - 1).world
    with closing(sqlite3.connect(store.path)) as db:
        cell_changes = db.execute(
            "SELECT COUNT(*) FROM event_cell_changes c JOIN events e ON e.run_id=c.run_id "
            "AND e.sequence=c.event_sequence WHERE e.tick=? AND e.run_id=? "
            "AND e.type != 'energy_inflow'",
            (death_tick, run_id),
        ).fetchone()[0]
        retired = db.execute(
            "SELECT SUM(amount) FROM ledger_entries WHERE run_id=? AND category='death_retirement'",
            (run_id,),
        ).fetchone()[0]
    assert cell_changes == 0
    assert retired == final.organisms[0].retired_energy > 0
    assert before.organisms[0].energy - 1 >= 0  # it was alive and charged the tick before
    assert final.total_energy == final.ledger.expected_energy


def test_organism_projection_tampering_stops_recovery(tmp_path):
    store, loaded = fixture_store(tmp_path, "corner_perception")
    world = loaded.world
    for _ in range(5):
        result = advance(world, fixture_policy.decide)
        store.commit_tick(loaded.meta.run_id, result)
        world = result.world
    with closing(sqlite3.connect(store.path)) as db, db:
        record = json.loads(db.execute("SELECT record_json FROM organisms").fetchone()[0])
        record["energy"] += 10
        db.execute("UPDATE organisms SET record_json = ?", (json.dumps(record),))
    with pytest.raises(StoreError, match="organism state disagrees"):
        SqliteStore(store.path).initialize()


def test_m03_database_migrates_and_schema_3_hash_still_verifies(tmp_path):
    path = tmp_path / "m03.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(V3_FIXTURE.read_text())
    store = SqliteStore(path)
    store.initialize()
    loaded = store.load_active()
    assert loaded.meta.run_id == V3_RUN and loaded.meta.state_schema == 3
    assert loaded.world.tick == 35 and loaded.world.state_hash() == V3_TICK_35_HASH
    assert loaded.restore.source == "snapshot" and loaded.restore.snapshot_tick == 35  # format 1
    assert store.history(V3_RUN, 20, use_snapshots=False).world.tick == 20
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute(
            "SELECT from_version, to_version FROM schema_migrations ORDER BY 1"
        ).fetchall() == [(3, 4), (4, 5), (5, 6), (6, 7)]
    world = loaded.world
    for _ in range(70):  # legacy run keeps advancing under its own schema (no organisms)
        result = advance(world)
        store.commit_tick(V3_RUN, result)
        world = result.world
    assert world.schema == 3 and SqliteStore(path).load_run(V3_RUN).world == world


# -- API ----------------------------------------------------------------------------------


@pytest.fixture
def dev_client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3", dev_fixtures=True)) as client:
        yield client


def new_fixture(client, name, seed=42):
    response = client.post("/api/runs", json={"seed": seed, "fixture": name})
    assert response.status_code == 201
    return response.json()


def test_frame_lists_organisms_with_hidden_lifespan_until_death(dev_client):
    frame = new_fixture(dev_client, "age_limit")
    (o,) = frame["organisms"]
    assert o["alive"] and o["lifespan"] is None and o["label"] == "TEST POLICY: wait"
    assert frame["ledger"]["energy"]["in_organisms"] == 100 and not frame["extinct"]
    control(dev_client, "speed", ticks_per_second=1000)
    control(dev_client, "start")
    deadline = time.monotonic() + 10
    while not dev_client.get("/api/world").json()["extinct"] and time.monotonic() < deadline:
        time.sleep(0.01)
    frame = dev_client.get("/api/world").json()
    (o,) = frame["organisms"]
    assert frame["extinct"] and frame["status"] == "paused"
    assert not o["alive"] and o["death_cause"] == "age" and o["lifespan"] == o["death_tick"]
    assert frame["ledger"]["balanced"] and frame["ledger"]["energy"]["death_retirement"] > 0
    assert control(dev_client, "resume").status_code == 409
    assert control(dev_client, "step").status_code == 409
    snaps = {s["tick"]: s["reason"] for s in dev_client.get("/api/snapshots").json()["snapshots"]}
    assert snaps[frame["tick"]] == "extinction"
    types = [e["type"] for e in dev_client.get("/api/events", params={"limit": 5}).json()["events"]]
    assert types[0] == "extinction" and "organism_died" in types


def test_supplied_observation_matches_the_recorded_world_at_that_tick(dev_client):
    new_fixture(dev_client, "corner_perception")
    for _ in range(6):
        frame = control(dev_client, "step").json()
    detail = dev_client.get("/api/organisms/org-0001").json()
    decision = detail["latest_decision"]
    assert decision["tick"] == 6 and decision["actor_id"] == "org-0001"
    obs = decision["payload"]["observation"]
    origin = decision["payload"]["origin"]
    for cell in obs["cells"]:
        x, y = origin["x"] + cell["dx"], origin["y"] + cell["dy"]
        actual = frame["cells"][y * 32 + x]
        assert (cell["energy"], cell["loose_material"]) == (
            actual["energy"],
            actual["loose_material"],
        )
    assert "lifespan" not in json.dumps(obs) and "tick" not in obs


def test_corner_fixture_rejects_off_grid_moves_then_moves_one_cell_at_a_time(dev_client):
    new_fixture(dev_client, "corner_perception")
    positions, outcomes = [], []
    for _ in range(5):
        frame = control(dev_client, "step").json()
        (o,) = frame["organisms"]
        positions.append((o["x"], o["y"]))
        decision = dev_client.get("/api/organisms/org-0001").json()["latest_decision"]
        outcomes.append(decision["summary"].get("reason", decision["rule_id"]))
    assert outcomes == [
        "out_of_bounds",
        "out_of_bounds",
        "action.move.v1",
        "action.move.v1",
        "action.move.v1",
    ]
    assert positions == [(0, 0), (0, 0), (1, 0), (2, 0), (2, 1)]
    energies = [o["energy"] for o in [frame["organisms"][0]]]
    assert energies == [60 - 5 - 3]  # five upkeeps and three moves; rejections cost nothing


def test_history_shows_organisms_as_they_were(dev_client):
    new_fixture(dev_client, "corner_perception")
    for _ in range(4):
        control(dev_client, "step")
    h2 = dev_client.get("/api/history/2").json()
    assert (h2["organisms"][0]["x"], h2["organisms"][0]["energy"]) == (0, 58)
    detail = dev_client.get("/api/organisms/org-0001", params={"at_tick": 2}).json()
    assert detail["mode"] == "history" and detail["latest_decision"]["tick"] == 2
    assert all(e["tick"] <= 2 for e in detail["events"])
    assert dev_client.get("/api/organisms/org-9999").status_code == 404


def test_standard_runs_have_no_organisms(dev_client):
    frame = dev_client.post("/api/runs", json={"seed": 42}).json()
    assert frame["organisms"] == [] and not frame["extinct"]
