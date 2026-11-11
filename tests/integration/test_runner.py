import sqlite3
import time
from contextlib import closing

import pytest
from genesis.domain.config import WorldConfig
from genesis.persistence.sqlite import SqliteStore
from genesis.runtime.commands import CommandError, Pause, Resume, SetSpeed, Start, Step
from genesis.runtime.runner import SimulationRunner


@pytest.fixture
def make_runner(tmp_path):
    runners = []

    def make(name="a", speed=1000):
        store = SqliteStore(tmp_path / f"{name}.sqlite3")
        store.initialize()
        runner = SimulationRunner(store, speed=speed)
        runner.start_thread()
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        runner.stop()


def finished(runner, tick):
    snap = runner.snapshot()
    return snap.world.tick == tick and snap.status == "paused"


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached in time")


def test_new_run_is_paused_at_tick_zero_and_time_does_not_pass_by_itself(make_runner):
    runner = make_runner()
    time.sleep(0.15)
    snap = runner.snapshot()
    assert (snap.status, snap.world.tick, snap.started) == ("paused", 0, False)


def test_step_advances_exactly_one_tick_and_repeats_are_idempotent(make_runner):
    runner = make_runner()
    assert runner.execute(Step("step-0001")).world.tick == 1
    assert runner.execute(Step("step-0001")).world.tick == 1  # same command id: no new tick
    assert runner.execute(Step("step-0002")).world.tick == 2
    time.sleep(0.1)
    assert runner.snapshot().world.tick == 2 and runner.snapshot().status == "paused"


def test_stepping_through_ticks_9_and_10_shows_the_inflow_event(make_runner):
    runner = make_runner()
    for i in range(9):
        snap = runner.execute(Step(f"step-{i:04d}"))
    assert snap.world.tick == 9 and snap.changed_indices == ()
    assert snap.last_event_sequence == 1  # only the creation event so far
    snap = runner.execute(Step("step-0010"))
    assert snap.world.tick == 10 and snap.last_event_sequence == 2
    assert len(snap.changed_indices) > 0
    event = runner.store.get_event(snap.meta.run_id, 2)
    assert event["rule_id"] == "env.energy_inflow.v1"
    assert {c["x"] + 32 * c["y"] for c in event["changes"]} == set(snap.changed_indices)
    snap = runner.execute(Step("step-0011"))
    assert snap.changed_indices == ()  # highlights describe only the latest tick


def test_pause_stops_logical_time_and_resume_continues(make_runner):
    runner = make_runner(speed=200)
    runner.execute(Start("start-001"))
    wait_for(lambda: runner.snapshot().world.tick >= 5)
    paused = runner.execute(Pause("pause-001"))
    assert paused.status == "paused"
    frozen = runner.snapshot().world.tick
    time.sleep(0.3)
    assert runner.snapshot().world.tick == frozen
    assert runner.execute(Pause("pause-002")).world.tick == frozen  # idempotent pause
    runner.execute(Resume("resume-01"))
    wait_for(lambda: runner.snapshot().world.tick > frozen + 3)


def test_step_is_refused_while_running_and_start_resume_state_rules(make_runner):
    runner = make_runner(speed=5)
    with pytest.raises(CommandError) as err:
        runner.execute(Resume("resume-01"))
    assert err.value.status == 409
    runner.execute(Start("start-001"))
    for command in (Step("step-0001"), Start("start-002"), Resume("resume-02")):
        with pytest.raises(CommandError) as err:
            runner.execute(command)
        assert err.value.status == 409
    runner.execute(Pause("pause-001"))
    with pytest.raises(CommandError):
        runner.execute(Start("start-003"))  # already started: must resume
    with pytest.raises(CommandError) as err:
        runner.execute(Resume("resume-03", until_tick=0))
    assert err.value.status == 422


def test_speed_changes_pacing_only_final_hash_is_identical(make_runner):
    final = {}
    for name, speed in (("slow", 400), ("fast", 1000)):
        runner = make_runner(name, speed=speed)
        runner.execute(Start(f"start-{name}", until_tick=100))
        wait_for(
            lambda r=runner: r.snapshot().world.tick == 100 and r.snapshot().status == "paused"
        )
        final[name] = runner.snapshot().world.state_hash()
    stepped = make_runner("stepped")
    for i in range(100):
        snap = stepped.execute(Step(f"step-{i:04d}"))
    final["stepped"] = snap.world.state_hash()
    assert len(set(final.values())) == 1, final


def test_speed_can_change_mid_run_without_changing_the_world(make_runner):
    runner = make_runner(speed=300)
    runner.execute(Start("start-001", until_tick=100))
    wait_for(lambda: runner.snapshot().world.tick >= 20)
    assert runner.execute(SetSpeed("speed-001", 1000)).speed == 1000
    wait_for(lambda: runner.snapshot().world.tick == 100 and runner.snapshot().status == "paused")
    reference = make_runner("reference")
    for i in range(100):
        snap = reference.execute(Step(f"step-{i:04d}"))
    assert runner.snapshot().world.state_hash() == snap.world.state_hash()


@pytest.mark.parametrize("speed", [0, 1001, -3, True, 2.5])
def test_invalid_speed_is_rejected_without_change(make_runner, speed):
    runner = make_runner()
    with pytest.raises(CommandError):
        runner.execute(SetSpeed("speed-001", speed))
    assert runner.snapshot().speed == 1000


def test_auto_pause_at_target_tick_is_logged_and_stops_time(make_runner):
    runner = make_runner(speed=1000)
    runner.execute(Start("start-001", until_tick=12))
    wait_for(lambda: runner.snapshot().status == "paused")
    time.sleep(0.1)
    snap = runner.snapshot()
    assert snap.world.tick == 12 and snap.until_tick is None

    with closing(sqlite3.connect(runner.store.path)) as db:
        commands = [r[0] for r in db.execute("SELECT command FROM control_log ORDER BY id")]
    assert commands == ["start", "auto_pause"]


def test_restart_resumes_committed_tick_paused_and_without_duplicate_effects(make_runner, tmp_path):
    runner = make_runner("restart", speed=1000)
    runner.execute(Start("start-001", until_tick=25))
    wait_for(lambda: runner.snapshot().world.tick == 25 and runner.snapshot().status == "paused")
    before = runner.snapshot()
    runner.stop()
    store = SqliteStore(tmp_path / "restart.sqlite3")
    store.initialize()
    reopened = SimulationRunner(store)
    after = reopened.snapshot()
    assert (after.status, after.started) == ("paused", True)
    assert after.world == before.world and after.last_event_sequence == before.last_event_sequence
    assert reopened.execute(Resume("resume-01", until_tick=26)).status == "running"


def test_create_run_pauses_old_run_and_labels_fixture(make_runner):
    runner = make_runner(speed=500)
    runner.execute(Start("start-001"))
    wait_for(lambda: runner.snapshot().world.tick >= 3)
    old = runner.snapshot().meta.run_id
    snap = runner.create_run(WorldConfig.for_run(42, "zero_inflow"))
    assert snap.meta.run_id != old and snap.world.tick == 0 and snap.status == "paused"
    assert snap.world.config.fixture == "zero_inflow"
    time.sleep(0.1)
    assert runner.snapshot().world.tick == 0
    assert runner.store.load_run(old).world.tick >= 3  # old history preserved
    with closing(sqlite3.connect(runner.store.path)) as db:
        logged = db.execute(
            "SELECT command, params_json FROM control_log WHERE run_id = ? ORDER BY id", (old,)
        ).fetchall()
    assert logged[0][0] == "start"
    assert logged[-1][0] == "pause" and '"reason": "new_run"' in logged[-1][1]


def test_failed_run_creation_leaves_current_run_running(make_runner, monkeypatch):
    runner = make_runner(speed=200)
    runner.execute(Start("start-001"))

    def fail(config, life=None, profile=None):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(runner.store, "create_run", fail)
    with pytest.raises(sqlite3.OperationalError):
        runner.create_run(WorldConfig.for_run(7))
    before = runner.snapshot()
    assert before.status == "running"
    wait_for(lambda: runner.snapshot().world.tick > before.world.tick)
