"""M05 through persistence, replay, restart and the API."""

import itertools
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition import fixture_policy
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.engine.world import advance
from genesis.persistence.sqlite import SqliteStore

V4_FIXTURE = Path(__file__).parents[1] / "fixtures" / "m04_schema_v4.sql"
V4_RUN = "42a1a354-1380-4d30-a24e-0d888f0683c2"
V4_TICK_22_HASH = "91b9024636d496428274c4fa79a89f64a7a1c85292833e1a6e7d9b5081d9135c"
_ids = itertools.count(1)


def control(client, command, **extra):
    return client.post(
        "/api/control", json={"command": command, "command_id": f"cmd-{next(_ids):06d}", **extra}
    )


def workshop(tmp_path):
    store = SqliteStore(tmp_path / "workshop.sqlite3")
    store.initialize()
    loaded = store.create_run(
        WorldConfig.for_run(42, "material_workshop"), FIXTURES["material_workshop"].life
    )
    world = loaded.world
    while not world.extinct:
        result = advance(world, fixture_policy.decide)
        store.commit_tick(loaded.meta.run_id, result)
        world = result.world
    return store, loaded.meta.run_id, world


def test_modification_persists_after_death_replay_and_restart(tmp_path):
    store, run_id, final = workshop(tmp_path)
    assert final.cell_at(10, 10).packed_material == 8 and not final.organisms[0].alive
    restarted = SqliteStore(store.path)
    restarted.initialize()
    loaded = restarted.load_run(run_id)
    assert loaded.world == final and loaded.world.cell_at(10, 10).packed_material == 8
    for tick in (0, 5, 14, final.tick):
        assert (
            restarted.history(run_id, tick, use_snapshots=False).world
            == restarted.history(run_id, tick).world
        )
    assert restarted.history(run_id, 4).world.cell_at(10, 10).packed_material == 0
    assert restarted.history(run_id, 14).world.cell_at(10, 10).packed_material == 8


def test_every_packed_unit_links_to_a_living_actors_accepted_action(tmp_path):
    store, run_id, final = workshop(tmp_path)
    with closing(sqlite3.connect(store.path)) as db:
        rows = db.execute(
            "SELECT e.type, e.rule_id, e.actor_id, e.tick, c.before, c.after "
            "FROM event_cell_changes c JOIN events e "
            "ON e.run_id=c.run_id AND e.sequence=c.event_sequence "
            "WHERE c.run_id=? AND c.field='packed_material' ORDER BY e.sequence",
            (run_id,),
        ).fetchall()
    assert rows and all(r[:3] == ("action_accepted", "action.modify.v1", "org-0001") for r in rows)
    death_tick = final.organisms[0].death_tick
    assert all(
        tick < death_tick for *_, tick, _, _ in [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]
    )
    assert sum(after - before for *_, before, after in rows) == 8


def test_m04_database_migrates_additively_and_keeps_its_hash(tmp_path):
    path = tmp_path / "m04.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(V4_FIXTURE.read_text())
    store = SqliteStore(path)
    store.initialize()
    loaded = store.load_active()
    assert loaded.meta.run_id == V4_RUN and loaded.meta.state_schema == 4
    assert loaded.world.tick == 22 and loaded.world.state_hash() == V4_TICK_22_HASH
    assert loaded.world.extinct and loaded.meta.rules_version == "m04-rules-1"
    assert store.history(V4_RUN, 10, use_snapshots=False).world.tick == 10
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert db.execute("SELECT from_version, to_version FROM schema_migrations").fetchall() == [
            (4, 5),
            (5, 6),
            (6, 7),
        ]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "api.sqlite3", dev_fixtures=True)) as c:
        yield c


def test_api_cost_preview_matches_the_engine_outcome(client):
    client.post("/api/runs", json={"seed": 42, "fixture": "material_workshop"})
    for _ in range(7):
        control(client, "step")
    detail = client.get("/api/organisms/org-0001").json()
    preview = {
        (
            p["proposal"]["action"],
            p["proposal"].get("direction")
            or p["proposal"].get("mode")
            or p["proposal"].get("resource"),
        ): p
        for p in detail["action_preview"]
    }
    assert preview[("move", "east")]["accepted"] and preview[("move", "east")]["energy_cost"] == 1
    assert preview[("modify", "pack")]["energy_cost"] == 2
    assert preview[("modify", "unpack")]["accepted"]
    o = detail["organism"]
    assert (o["carried_material"], o["material_capacity"]) == (16, 20)
    control(client, "step")  # move east
    detail = client.get("/api/organisms/org-0001").json()
    west = next(p for p in detail["action_preview"] if p["proposal"].get("direction") == "west")
    assert west["energy_cost"] == 3  # re-entering the cell packed to 4
    frame = control(client, "step").json()  # the script moves west
    move = client.get("/api/organisms/org-0001").json()["latest_decision"]
    assert move["summary"]["energy_cost"] == 3 and move["summary"]["entered_packed_material"] == 4
    assert frame["ledger"]["material"]["carried"] == 16 and frame["ledger"]["balanced"]


def test_cell_history_names_the_actor_and_values(client):
    client.post("/api/runs", json={"seed": 42, "fixture": "material_workshop"})
    for _ in range(7):
        control(client, "step")
    cell = client.get("/api/cells/10/10").json()
    assert cell["cell"]["packed_material"] == 4 and cell["entry_cost"] == 3
    packs = [c for c in cell["recent_changes"] if c["field"] == "packed_material"]
    assert [(c["before"], c["after"], c["actor_id"]) for c in packs] == [
        (2, 4, "org-0001"),
        (0, 2, "org-0001"),
    ]
    loose = [c for c in cell["recent_changes"] if c["field"] == "loose_material"]
    assert [(c["before"], c["after"]) for c in loose] == [(15, 5), (25, 15)]
