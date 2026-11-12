"""M14: historical inspection across lives and genealogy (lineage fixture, via the API)."""

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.persistence.sqlite import SqliteStore


@pytest.fixture(scope="module")
def lineage(tmp_path_factory):
    """One lineage run stepped to extinction; yields (client, store, run_id, final_tick)."""
    database = tmp_path_factory.mktemp("m14") / "db.sqlite3"
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "lineage"})
        for n in range(40):
            frame = client.post(
                "/api/control", json={"command": "step", "command_id": f"lin-{n:05d}"}
            ).json()
            if frame.get("extinct"):
                break
        frame = client.get("/api/world").json()
        assert frame["extinct"]
        store = SqliteStore(database)
        yield client, store, frame["run_id"], frame["tick"], frame["state_hash"]


def ids(frame):
    return {o["id"] for o in frame["organisms"]}


def test_membership_boundaries_around_birth_and_death(lineage):
    client, _, _, _, _ = lineage
    before = client.get("/api/history/6").json()
    after = client.get("/api/history/7").json()
    assert ids(before) == {"org-0001"} and ids(after) == {"org-0001", "org-0002"}
    child = next(o for o in after["organisms"] if o["id"] == "org-0002")
    assert child["parent_ids"] == ["org-0001"] and child["generation"] == 1
    assert client.get("/api/organisms/org-0002", params={"at_tick": 6}).status_code == 404
    parent_18 = next(
        o for o in client.get("/api/history/18").json()["organisms"] if o["id"] == "org-0001"
    )
    parent_19 = next(
        o for o in client.get("/api/history/19").json()["organisms"] if o["id"] == "org-0001"
    )
    assert parent_18["alive"] and not parent_19["alive"]


def test_no_future_data_in_historical_views(lineage):
    client, _, _, final, _ = lineage
    # The parent dies at tick 19 in the live run; earlier views must not reveal its lifespan.
    early = client.get("/api/history/10").json()
    assert next(o for o in early["organisms"] if o["id"] == "org-0001")["lifespan"] is None
    detail = client.get("/api/organisms/org-0001", params={"at_tick": 10}).json()
    assert detail["organism"]["lifespan"] is None
    assert all(e["tick"] <= 10 for e in detail["events"])
    assert detail["latest_decision"]["tick"] <= 10
    assert all(m["tick"] <= 10 for m in detail["memory"]["active"])
    assert all(m["forgotten_tick"] <= 10 for m in detail["memory"]["forgotten"])
    late = client.get("/api/history/19").json()
    assert next(o for o in late["organisms"] if o["id"] == "org-0001")["lifespan"] == 19
    decisions = client.get(
        "/api/decisions", params={"organism_id": "org-0001", "max_tick": 5}
    ).json()["decisions"]
    assert decisions and all(d["tick"] <= 5 for d in decisions)
    assert final >= 19


def test_full_and_snapshot_replay_agree_at_every_tick(lineage):
    _, store, run_id, final, _ = lineage
    for tick in range(final + 1):
        full = store.history(run_id, tick, use_snapshots=False)
        fast = store.history(run_id, tick, use_snapshots=True)
        assert full.world == fast.world and full.world.state_hash() == full.recorded_hash


def test_lasting_cell_change_survives_its_makers_death(lineage):
    client, _, _, final, _ = lineage

    def packed(tick):
        return client.get("/api/cells/12/12", params={"at_tick": tick}).json()

    assert packed(4)["cell"]["packed_material"] == 0
    assert packed(6)["cell"]["packed_material"] == 4
    dead = packed(final)
    assert dead["cell"]["packed_material"] == 4  # the maker is long dead
    assert {c["actor_id"] for c in dead["recent_changes"] if c["field"] == "packed_material"} == {
        "org-0001"
    }


def test_inherited_memory_leads_to_its_source_at_birth(lineage):
    client, _, _, _, _ = lineage
    child = client.get("/api/organisms/org-0002", params={"at_tick": 8}).json()
    inherited = [m for m in child["memory"]["active"] if m["kind"] == "inherited"]
    assert len(inherited) == 2  # labeled 100% retention in this fixture
    birth_tick = child["inheritance"]["tick"]
    parent = client.get("/api/organisms/org-0001", params={"at_tick": birth_tick}).json()
    parent_ids = {m["id"] for m in parent["memory"]["active"]}
    for m in inherited:
        assert m["origin"]["source_id"] in parent_ids  # navigable to the very record


def test_key_moments_list_births_modifications_and_deaths_in_order(lineage):
    client, _, _, final, _ = lineage
    moments = client.get("/api/moments").json()["moments"]
    kinds = [(m["tick"], m["kind"]) for m in moments]
    assert (5, "modification") in kinds and (6, "modification") in kinds
    assert (7, "birth") in kinds and (19, "death") in kinds and (final, "extinction") in kinds
    assert [m["sequence"] for m in moments] == sorted(m["sequence"] for m in moments)
    assert not any(m["kind"] == "birth" and m["tick"] == 0 for m in moments)  # seeds excluded


def test_event_pages_are_ordered_and_gapless(lineage):
    client, _, _, _, _ = lineage
    seen, before = [], None
    while True:
        params = {"limit": 25, **({"before": before} if before else {})}
        page = client.get("/api/events", params=params).json()
        seen += [e["sequence"] for e in page["events"]]
        if not page["has_more"]:
            break
        before = page["events"][-1]["sequence"]
    assert seen == sorted(seen, reverse=True) and seen == list(range(seen[0], 0, -1))


def test_inspection_never_changes_the_live_state(lineage):
    client, _, _, _, live_hash = lineage
    for tick in (0, 7, 19):
        client.get(f"/api/history/{tick}")
        client.get("/api/organisms/org-0001", params={"at_tick": tick})
    client.get("/api/moments")
    client.get("/api/population", params={"at_tick": 10})
    assert client.get("/api/world").json()["state_hash"] == live_hash
