import itertools
import json
import sqlite3
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.api.schemas import GridFrame
from genesis.persistence.sqlite import StoreError


@pytest.fixture
def database(tmp_path):
    return tmp_path / "runs.sqlite3"


_ids = itertools.count(1)


def control(client, command, **extra):
    body = {"command": command, "command_id": f"cmd-{next(_ids):06d}", **extra}
    return client.post("/api/control", json=body)


def test_world_api_contract_and_inspection_match(database):
    with TestClient(create_app(database)) as client:
        response = client.get("/api/world")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        frame = response.json()
        assert GridFrame.model_validate_json(json.dumps(frame))
        assert frame["schema_version"] == 9 and frame["mode"] == "live"
        assert frame["config"]["width"] == frame["config"]["height"] == 32
        assert frame["config"]["seed"] == 42 and frame["config"]["fixture"] is None
        assert (frame["tick"], frame["status"], frame["started"]) == (0, "paused", False)
        assert len(frame["cells"]) == 1024 and frame["ledger"]["balanced"] is True
        assert frame["dev_fixtures"] == [] and frame["journal_mode"] == "wal"
        for x, y in [(0, 0), (31, 31), (11, 23)]:
            inspected = client.get(f"/api/cells/{x}/{y}").json()
            assert inspected["cell"] == frame["cells"][y * 32 + x]
            assert (inspected["cell"]["x"], inspected["cell"]["y"]) == (x, y)
            assert inspected["index"] == y * 32 + x
            assert inspected["state_hash"] == frame["state_hash"]
        assert client.get("/api/world").json() == frame  # reads change nothing
        health = client.get("/api/health").json()
        assert health["milestone"] == "M13" and health["run_id"] == frame["run_id"]


@pytest.mark.parametrize("path", ["-1/0", "0/-1", "32/0", "0/32", "99999/99999"])
def test_cell_bounds_return_404(database, path):
    with TestClient(create_app(database)) as client:
        assert client.get(f"/api/cells/{path}").status_code == 404


def test_step_through_ticks_9_and_10_exposes_event_deltas_and_rule(database):
    with TestClient(create_app(database)) as client:
        for _ in range(9):
            frame = control(client, "step").json()
        assert frame["tick"] == 9 and frame["changed_cells"] == []
        before = {i: c["energy"] for i, c in enumerate(frame["cells"])}
        frame = control(client, "step").json()
        assert frame["tick"] == 10 and len(frame["changed_cells"]) > 0
        events = client.get("/api/events").json()["events"]
        assert [e["type"] for e in events] == ["energy_inflow", "run_created"]
        summary = events[0]["summary"]
        detail = client.get(f"/api/events/{events[0]['sequence']}").json()
        assert detail["rule_id"] == "env.energy_inflow.v1" and detail["tick"] == 10
        assert len(detail["changes"]) == summary["credited_total"]
        assert {c["x"] + 32 * c["y"] for c in detail["changes"]} == set(frame["changed_cells"])
        for change in detail["changes"]:
            index = change["y"] * 32 + change["x"]
            assert change["before"] == before[index]
            assert change["after"] == frame["cells"][index]["energy"]
            assert change["delta"] == 1
        # Cell view links back to the same event.
        x, y = detail["changes"][0]["x"], detail["changes"][0]["y"]
        history = client.get(f"/api/cells/{x}/{y}").json()["recent_changes"]
        assert history[0]["event_sequence"] == events[0]["sequence"]
        assert history[0]["rule_id"] == "env.energy_inflow.v1" and history[0]["delta"] == 1
        energy = frame["ledger"]["energy"]
        assert energy["environmental_inflow"] == summary["credited_total"]
        assert energy["expected"] == energy["actual"] and frame["ledger"]["balanced"]


def test_event_pagination_and_missing_event(database):
    with TestClient(create_app(database)) as client:
        for _ in range(30):
            control(client, "step")
        page = client.get("/api/events", params={"limit": 2}).json()
        assert [e["tick"] for e in page["events"]] == [30, 20] and page["has_more"]
        rest = client.get(
            "/api/events", params={"limit": 5, "before": page["events"][-1]["sequence"]}
        ).json()
        assert [e["tick"] for e in rest["events"]] == [10, 0] and not rest["has_more"]
        assert client.get("/api/events/999").status_code == 404
        assert client.get("/api/events", params={"limit": 0}).status_code == 422


def test_control_validation_and_state_errors(database):
    with TestClient(create_app(database)) as client:
        assert control(client, "resume").status_code == 409
        assert control(client, "speed").status_code == 422
        assert control(client, "speed", ticks_per_second=0).status_code == 422
        assert control(client, "start", until_tick=0).status_code == 422
        for body in (
            {"command": "explode", "command_id": "cmd-xyz-123"},
            {"command": "step", "command_id": "short"},
            {"command": "step", "command_id": "cmd-xyz-123", "extra": 1},
        ):
            assert client.post("/api/control", json=body).status_code == 422
        assert client.get("/api/world").json()["tick"] == 0


def test_start_pause_resume_and_speed_via_api(database):
    with TestClient(create_app(database)) as client:
        assert control(client, "speed", ticks_per_second=200).json()["speed"] == 200
        assert control(client, "start").json()["status"] == "running"
        deadline = time.monotonic() + 5
        while client.get("/api/world").json()["tick"] < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert control(client, "step").status_code == 409
        paused = control(client, "pause").json()
        assert paused["status"] == "paused"
        time.sleep(0.2)
        assert client.get("/api/world").json()["tick"] == paused["tick"]
        assert control(client, "resume", until_tick=paused["tick"] + 5).status_code == 200
        deadline = time.monotonic() + 5
        while client.get("/api/world").json()["status"] != "paused" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.get("/api/world").json()["tick"] == paused["tick"] + 5


def test_two_seed_42_runs_at_different_speeds_have_identical_hashes(tmp_path):
    hashes = []
    for name, speed in (("a", 300), ("b", 1000)):
        with TestClient(create_app(tmp_path / f"{name}.sqlite3")) as client:
            control(client, "speed", ticks_per_second=speed)
            control(client, "start", until_tick=100)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                frame = client.get("/api/world").json()
                if frame["tick"] == 100 and frame["status"] == "paused":
                    break
                time.sleep(0.01)
            assert frame["tick"] == 100
            hashes.append(frame["state_hash"])
    assert hashes[0] == hashes[1]


def test_restart_preserves_run_tick_and_events_but_not_running_state(database):
    with TestClient(create_app(database)) as client:
        for _ in range(12):
            frame = control(client, "step").json()
    with TestClient(create_app(database)) as client:
        again = client.get("/api/world").json()
        assert again["run_id"] == frame["run_id"] and again["tick"] == 12
        assert again["state_hash"] == frame["state_hash"] and again["status"] == "paused"
        assert [e["tick"] for e in client.get("/api/events").json()["events"]] == [10, 0]


def test_new_run_requires_explicit_post_and_preserves_previous(database):
    with TestClient(create_app(database)) as client:
        first = client.get("/api/world").json()
        control(client, "step")
        second = client.post("/api/runs", json={"seed": 123}).json()
        assert second["run_id"] != first["run_id"] and second["config"]["seed"] == 123
        assert second["tick"] == 0 and second["cells"] != first["cells"]
        assert client.get("/api/world").json() == second
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT COUNT(*) FROM run_manifest").fetchone()[0] == 2
        assert db.execute("SELECT MAX(tick) FROM ticks").fetchone()[0] == 1


@pytest.mark.parametrize(
    "body",
    [{"seed": -1}, {"seed": 2**32}, {"seed": True}, {"seed": 1.5}, {"seed": "42"}, {"width": 99}],
)
def test_invalid_new_run_does_not_change_active_run(database, body):
    with TestClient(create_app(database)) as client:
        before = client.get("/api/world").json()
        assert client.post("/api/runs", json=body).status_code == 422
        assert client.get("/api/world").json() == before


def test_equal_seed_gives_equal_state_hash_but_different_identity(database):
    with TestClient(create_app(database)) as client:
        first = client.get("/api/world").json()
        second = client.post("/api/runs", json={"seed": 42}).json()
        assert first["state_hash"] == second["state_hash"]
        assert first["run_id"] != second["run_id"]


def test_fixtures_exist_only_in_dev_mode_and_are_labeled(database):
    with TestClient(create_app(database)) as client:
        denied = client.post("/api/runs", json={"seed": 42, "fixture": "zero_inflow"})
        assert denied.status_code == 422
        assert client.get("/api/world").json()["config"]["fixture"] is None
    with TestClient(create_app(database, dev_fixtures=True)) as client:
        assert client.get("/api/world").json()["dev_fixtures"] == [
            "age_limit",
            "birth_memory",
            "birth_pair",
            "corner_perception",
            "exchange_pair",
            "material_workshop",
            "memory_budget",
            "memory_return",
            "recorded_delayed",
            "recorded_invalid",
            "recorded_mismatch",
            "recorded_valid",
            "reflection_invalid",
            "reflection_probe",
            "starvation",
            "tendency_social",
            "tendency_solitary",
            "zero_inflow",
        ]
        assert client.post("/api/runs", json={"fixture": "nope"}).status_code == 422
        frame = client.post("/api/runs", json={"seed": 42, "fixture": "zero_inflow"}).json()
        assert frame["config"]["fixture"] == "zero_inflow" and frame["config"]["inflow_amount"] == 0
        for _ in range(20):
            stepped = control(client, "step").json()
        assert [c["energy"] for c in stepped["cells"]] == [c["energy"] for c in frame["cells"]]
        assert [e["type"] for e in client.get("/api/events").json()["events"]] == ["run_created"]
        assert stepped["ledger"]["energy"]["environmental_inflow"] == 0
        assert stepped["ledger"]["balanced"] is True


def test_corrupt_saved_world_is_not_silently_replaced(database):
    with TestClient(create_app(database)):
        pass
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE cells SET energy = energy + 1 WHERE idx = 0")
    with pytest.raises(StoreError), TestClient(create_app(database)):
        pass
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT COUNT(*) FROM run_manifest").fetchone()[0] == 1


def test_missing_active_reference_preserves_existing_runs(database):
    with TestClient(create_app(database)):
        pass
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DELETE FROM active_sim_run")
    with pytest.raises(StoreError), TestClient(create_app(database)):
        pass


# -- M03: history, snapshots, restart ------------------------------------------------------


def run_until(client, tick, speed=1000):
    control(client, "speed", ticks_per_second=speed)
    frame = client.get("/api/world").json()
    command = "resume" if frame["started"] else "start"
    control(client, command, until_tick=tick)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        frame = client.get("/api/world").json()
        if frame["tick"] == tick and frame["status"] == "paused":
            return frame
        time.sleep(0.01)
    raise AssertionError("run did not reach target tick")


def test_history_seek_is_read_only_and_equal_from_snapshot_or_initial(database):
    with TestClient(create_app(database)) as client:
        live = run_until(client, 150)
        h50 = client.get("/api/history/50").json()
        h50_full = client.get("/api/history/50", params={"source": "initial"}).json()
        assert h50["mode"] == "history" and h50["tick"] == 50 and h50["live_tick"] == 150
        assert h50["verified"] and h50["state_hash"] == h50["recorded_hash"]
        assert h50["cells"] == h50_full["cells"] and h50["state_hash"] == h50_full["state_hash"]
        assert h50["source"]["kind"] == "snapshot" and h50["source"]["snapshot_tick"] == 0
        assert (
            h50_full["source"]["kind"] == "initial" and h50_full["source"]["ticks_replayed"] == 50
        )
        h150 = client.get("/api/history/150").json()
        h150_full = client.get("/api/history/150", params={"source": "initial"}).json()
        assert h150["state_hash"] == h150_full["state_hash"] == live["state_hash"]
        assert h150["source"]["snapshot_tick"] == 150  # auto-pause snapshot
        h10 = client.get("/api/history/10").json()
        assert len(h10["changed_cells"]) > 0
        # Earlier tick genuinely differs, and inspecting did not touch live state.
        assert h50["state_hash"] != live["state_hash"]
        assert (
            h50["ledger"]["energy"]["environmental_inflow"]
            < live["ledger"]["energy"]["environmental_inflow"]
        )
        assert client.get("/api/world").json() == live


def test_history_cell_inspection_and_events_exclude_the_future(database):
    with TestClient(create_app(database)) as client:
        run_until(client, 35)
        h15 = client.get("/api/history/15").json()
        cell = client.get("/api/cells/3/4", params={"at_tick": 15}).json()
        assert cell["mode"] == "history" and cell["tick"] == 15
        assert cell["cell"] == h15["cells"][4 * 32 + 3]
        assert all(c["tick"] <= 15 for c in cell["recent_changes"])
        events = client.get("/api/events", params={"max_tick": 15}).json()["events"]
        assert [e["tick"] for e in events] == [10, 0]
        assert client.get("/api/cells/3/4").json()["mode"] == "live"


@pytest.mark.parametrize("tick,status", [(999, 404), (-1, 422)])
def test_unavailable_history_tick_is_an_explicit_error(database, tick, status):
    with TestClient(create_app(database)) as client:
        response = client.get(f"/api/history/{tick}")
        assert response.status_code == status
        assert "detail" in response.json()


def test_corrupt_history_is_reported_not_invented(database):
    with TestClient(create_app(database)) as client:
        run_until(client, 30)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE event_cell_changes SET after = after + 5 WHERE event_sequence = 2")
        response = client.get("/api/history/20", params={"source": "initial"})
        assert response.status_code == 409
        assert "Historical data unavailable" in response.json()["detail"]


def test_pause_and_shutdown_write_snapshots_and_restart_restores(database):
    with TestClient(create_app(database)) as client:
        control(client, "speed", ticks_per_second=1000)
        control(client, "start")
        deadline = time.monotonic() + 5
        while client.get("/api/world").json()["tick"] < 30 and time.monotonic() < deadline:
            time.sleep(0.01)
        paused = control(client, "pause").json()
        snaps = client.get("/api/snapshots").json()["snapshots"]
        reasons = {s["tick"]: s["reason"] for s in snaps}
        assert reasons[paused["tick"]] == "pause"
        for _ in range(7):
            last = control(client, "step").json()
    with TestClient(create_app(database)) as client:
        frame = client.get("/api/world").json()
        assert frame["tick"] == last["tick"] and frame["state_hash"] == last["state_hash"]
        assert frame["status"] == "paused"
        restored = frame["restored_from"]
        assert restored["kind"] == "snapshot" and restored["snapshot_tick"] == last["tick"]
        reasons = {s["tick"]: s["reason"] for s in client.get("/api/snapshots").json()["snapshots"]}
        assert reasons[last["tick"]] == "close" and reasons[0] == "initial"
        health = client.get("/api/health").json()
        assert health["milestone"] == "M13" and health["restored_from"]["source"] == "snapshot"
