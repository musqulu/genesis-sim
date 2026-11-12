"""Multi-organism live runs and plain-English run summaries."""

import itertools
import random
import time

from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.api.routes import live_seeds
from genesis.ops import report

from tests.integration.test_mlx import MODEL, fake_mlx  # noqa: F401 - pytest fixture

_ids = itertools.count(1)


def control(client, command, **extra):
    body = {"command": command, "command_id": f"rep-{next(_ids):06d}", **extra}
    return client.post("/api/control", json=body)


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_live_placements_are_distinct_deterministic_and_backward_compatible():
    rng = random.Random("placement:42")
    single = (rng.randrange(32), rng.randrange(32))
    seeds = live_seeds(42, 12)
    assert (seeds[0].x, seeds[0].y) == single  # seed 42 with one organism is unchanged
    assert len({(s.x, s.y) for s in seeds}) == 12
    assert seeds == live_seeds(42, 12) and all(s.policy == "live" for s in seeds)


def test_organism_count_is_validated(fake_mlx, tmp_path):  # noqa: F811
    with TestClient(create_app(tmp_path / "v.sqlite3")) as client:
        for body in (
            {"organisms": 0, "cognition": f"mlx:{MODEL}"},
            {"organisms": 17, "cognition": f"mlx:{MODEL}"},
            {"organisms": 3},
        ):
            assert client.post("/api/runs", json=body).status_code == 422


def test_several_organisms_share_one_model_and_decide_in_rotated_order(fake_mlx, tmp_path):  # noqa: F811
    fake_mlx("valid")
    with TestClient(create_app(tmp_path / "multi.sqlite3")) as client:
        frame = client.post(
            "/api/runs", json={"seed": 7, "cognition": f"mlx:{MODEL}", "organisms": 3}
        ).json()
        assert [o["label"] for o in frame["organisms"]] == ["LIVE MODEL"] * 3
        assert len({(o["x"], o["y"]) for o in frame["organisms"]}) == 3
        frame = control(client, "step").json()
        assert frame["tick"] == 1
        decisions = client.get("/api/decisions").json()["decisions"]
        assert sorted(d["actor_id"] for d in decisions) == ["org-0001", "org-0002", "org-0003"]
        runner = client.app.state.runner
        assert runner._provider.calls == 3  # one resident model served all three
        events = client.get("/api/events", params={"limit": 20}).json()["events"]
        acted = [e["actor_id"] for e in reversed(events) if e["type"].startswith("action")]
        assert acted == ["org-0002", "org-0003", "org-0001"]  # rotated by tick 1


def test_summary_is_written_at_extinction_in_plain_english(tmp_path, monkeypatch):
    monkeypatch.setenv("GENESIS_REPORTS_DIR", str(tmp_path / "reports"))
    with TestClient(create_app(tmp_path / "s.sqlite3", dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "recorded_invalid"})
        control(client, "speed", ticks_per_second=1000)
        control(client, "start")
        wait_for(lambda: client.get("/api/world").json()["extinct"])
        frame = client.get("/api/world").json()
        assert frame["report_path"] and frame["report_path"].endswith(".md")
        text = client.get("/api/summary").json()["markdown"]
    files = list((tmp_path / "reports").glob("*.md"))
    assert [str(f) for f in files] == [frame["report_path"]]
    assert "The population went extinct at tick 48" in text
    assert "It died. Together they made 12 decisions." in text
    assert "died at tick 48 of starvation" in text
    assert "It made 12 decisions: 3 carried out and 9 rejected by the world." in text
    assert "replied with text that was not valid JSON" in text
    assert "tried to move off the edge of the world" in text
    assert "recorded test responses (recorded_invalid)" in text
    assert "no model wrote this text" in text
    assert "lifespan would have been" in text  # revealed only because it died


def test_summary_is_written_for_a_replaced_run_and_on_demand(tmp_path, monkeypatch):
    monkeypatch.setenv("GENESIS_REPORTS_DIR", str(tmp_path / "reports"))
    with TestClient(create_app(tmp_path / "r.sqlite3", dev_fixtures=True)) as client:
        first = client.post("/api/runs", json={"seed": 42, "fixture": "recorded_valid"}).json()
        for _ in range(3):
            control(client, "step")
        on_demand = client.post("/api/summary").json()
        assert on_demand["status"] == "stopped" and "stopped at tick 3" in on_demand["markdown"]
        client.post("/api/runs", json={"seed": 7})
        assert client.get("/api/summary").status_code == 404  # the new run has none yet
    files = sorted((tmp_path / "reports").glob("*.md"))
    assert len(files) == 1 and first["run_id"][:8] in files[0].name
    text = files[0].read_text()
    assert "was stopped at tick 3 when a new run was started" in text
    assert "collected 5 material" in text  # the recorded extract at tick 1


def test_an_untouched_run_is_not_summarized_when_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("GENESIS_REPORTS_DIR", str(tmp_path / "reports"))
    with TestClient(create_app(tmp_path / "u.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 1})
        client.post("/api/runs", json={"seed": 2})
    assert not (tmp_path / "reports").exists()


def test_a_report_failure_never_affects_the_run(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(report, "write_report", boom)
    with TestClient(create_app(tmp_path / "f.sqlite3", dev_fixtures=True)) as client:
        client.post("/api/runs", json={"seed": 42, "fixture": "starvation"})
        control(client, "speed", ticks_per_second=1000)
        control(client, "start")
        wait_for(lambda: client.get("/api/world").json()["extinct"])
        frame = client.get("/api/world").json()
        assert frame["last_error"] is None and frame["report_path"] is None
        assert frame["ledger"]["balanced"]
