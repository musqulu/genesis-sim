"""M07A local MLX-LM provider through a protocol-compatible fake worker (no model)."""

import itertools
import json
import os
import sqlite3
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition.context import build_request
from genesis.cognition.policy import parse
from genesis.domain.config import FIXTURES, WorldConfig
from genesis.engine.actions import available_actions
from genesis.engine.world import create_world, prepare_tick
from genesis.providers import mlx
from genesis.providers.base import ProviderError

FAKE = Path(__file__).parents[1] / "fixtures" / "fake_mlx_worker.py"
MODEL = "fake-model/8-bit"
_ids = itertools.count(1)


@pytest.fixture
def fake_mlx(tmp_path, monkeypatch):
    root = tmp_path / "models"
    model = root / MODEL
    model.mkdir(parents=True)
    (model / "config.json").write_text(json.dumps({"model_type": "fake", "quantization": {}}))
    (model / "tokenizer.json").write_text("{}")
    (model / "model-00001.safetensors").write_bytes(b"\0" * 16)
    monkeypatch.setenv("GENESIS_MLX_MODEL_DIRS", str(root))
    monkeypatch.setenv("GENESIS_MLX_PYTHON", sys.executable)
    monkeypatch.setenv("GENESIS_MLX_WORKER", str(FAKE))
    monkeypatch.setenv("FAKE_MLX_STATE", str(tmp_path / "fake-state"))
    monkeypatch.setattr(
        mlx,
        "runtime_status",
        lambda python: {"available": True, "python": "fake", "mlx": "fake", "mlx_lm": "fake"},
    )

    def script(*behaviours):
        monkeypatch.setenv("FAKE_MLX_SCRIPT", ",".join(behaviours))

    script.state = tmp_path / "fake-state"
    script.model_path = model
    return script


def request_for_tick1():
    world = create_world(WorldConfig.for_run(42, "recorded_valid"), FIXTURES["recorded_valid"].life)
    prepared = prepare_tick(world)
    return prepared, build_request("r", prepared.world, prepared.slots[0], None)


def provider(timeout=5.0):
    settings = mlx.MLXSettings(request_timeout_s=timeout, load_timeout_s=10)
    return mlx.MLXProvider(MODEL, mlx.profile_for(MODEL), settings=settings)


# -- provider contract ----------------------------------------------------------------------------


def test_valid_output_with_usage_prompt_and_provenance(fake_mlx):
    fake_mlx("valid")
    prepared, request = request_for_tick1()
    p = provider()
    try:
        response = p.decide(request, threading.Event())
        assert response.raw_text == '{"action": "wait", "explanation": "fake worker"}'
        assert (response.usage.input_tokens, response.usage.output_tokens) == (100, 12)
        assert response.finish_reason == "stop"
        detail = response.detail
        assert detail["prompt_version"] == "decision-v1"
        assert detail["sampling"]["seed"] == int(request.input_hash[:8], 16)
        system, user = detail["messages"]
        assert system["role"] == "system" and "exactly one JSON object" in system["content"]
        assert json.loads(user["content"])["observation"] == request.context["observation"]
        for forbidden in ("lifespan", "rng", "ledger", 'seed"'):
            assert forbidden not in user["content"]
        health = p.health()
        assert health["status"] == "ready" and health["requests"] == 1 and health["pid"]
        assert p.identity.kind == "mlx" and p.identity.revision == mlx.files_digest(
            fake_mlx.model_path
        )
    finally:
        p.close()
    assert p.health()["status"] == "stopped"


def test_invalid_and_truncated_outputs_are_returned_verbatim_for_strict_parsing(fake_mlx):
    fake_mlx("invalid", "truncate")
    prepared, request = request_for_tick1()
    p = provider()
    try:
        prose = p.decide(request, threading.Event())
        cut = p.decide(request, threading.Event())
    finally:
        p.close()
    assert prose.raw_text == "Sure! I will wait."
    assert parse(prose.raw_text, prepared.world.life, available_actions(prepared.world)).status == (
        "malformed_json"
    )
    assert cut.finish_reason == "length"
    assert parse(cut.raw_text, prepared.world.life, ACTIONS).status == "malformed_json"


ACTIONS = ("wait", "move", "extract", "modify")


def test_timeout_kills_the_worker_and_its_output_can_never_be_used(fake_mlx):
    fake_mlx("hang", "valid")
    _, request = request_for_tick1()
    p = provider(timeout=0.5)
    try:
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "timeout" and err.value.retryable
        assert p.health()["pid"] is None  # killed, not left running
        response = p.decide(request, threading.Event())  # fresh worker
        assert response.raw_text.startswith('{"action": "wait"')
        assert p.health()["restarts"] == 1
    finally:
        p.close()


def test_worker_crash_is_typed_and_not_retryable(fake_mlx):
    fake_mlx("crash")
    _, request = request_for_tick1()
    p = provider()
    try:
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "worker_crashed" and not err.value.retryable
        assert p.health()["status"] == "crashed"
    finally:
        p.close()


def test_unavailable_model_and_runtime_are_config_errors(fake_mlx, monkeypatch, tmp_path):
    _, request = request_for_tick1()
    monkeypatch.setenv("FAKE_MLX_LOAD", "fatal")
    p = provider()
    with pytest.raises(ProviderError) as err:
        p.decide(request, threading.Event())
    assert err.value.kind == "config" and "weights missing" in str(err.value)
    with pytest.raises(ProviderError) as err:
        mlx.profile_for("no-such-model")
    assert err.value.kind == "config"
    missing = mlx.MLXProvider("gone", {"model_path": str(tmp_path / "nope")})
    with pytest.raises(ProviderError) as err:
        missing.decide(request, threading.Event())
    assert err.value.kind == "config"


def test_context_overflow_and_cancellation(fake_mlx):
    fake_mlx("overflow", "hang")
    _, request = request_for_tick1()
    p = provider(timeout=30)
    try:
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "context_overflow" and not err.value.retryable
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        with pytest.raises(ProviderError):
            p.decide(request, cancel)
        assert time.monotonic() - started < 5
    finally:
        p.close()


def test_discovery_lists_local_models_without_loading(fake_mlx):
    models = mlx.discover_models()
    assert [m["id"] for m in models] == [MODEL]
    assert not fake_mlx.state.exists()  # no worker was started


# -- runs ---------------------------------------------------------------------------------------


def control(client, command, **extra):
    return client.post(
        "/api/control", json={"command": command, "command_id": f"mlx-{next(_ids):06d}", **extra}
    )


def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_live_run_commits_a_traceable_decision_with_exact_prompt(fake_mlx, tmp_path):
    fake_mlx("valid")
    db = tmp_path / "live.sqlite3"
    with TestClient(create_app(db)) as client:
        models = client.get("/api/models").json()
        assert models["runtime"]["available"] and models["models"][0]["id"] == MODEL
        frame = client.post("/api/runs", json={"seed": 42, "cognition": f"mlx:{MODEL}"}).json()
        assert frame["config"]["fixture"] is None
        assert [o["label"] for o in frame["organisms"]] == ["LIVE MODEL"]
        assert frame["provider"]["kind"] == "mlx" and frame["provider"]["name"] == MODEL
        assert frame["provider"]["profile"]["settings"]["max_output_tokens"] == 256
        frame = control(client, "step").json()
        assert frame["tick"] == 1
        (decision,) = client.get("/api/decisions").json()["decisions"]
        detail = client.get(f"/api/decisions/{decision['request_id']}").json()
        assert detail["outcome"] == "accepted" and detail["outcome_rule"] == "action.wait.v1"
        assert detail["provider"]["kind"] == "mlx"
        assert detail["prompt"][0]["role"] == "system"
        assert detail["detail"]["prompt_version"] == "decision-v4"
        assert detail["usage"]["input_tokens"] == 100
        health = client.get("/api/world").json()["provider"]["health"]
        assert health["status"] == "ready" and health["requests"] == 1
    with closing(sqlite3.connect(db)) as conn:
        manifest = json.loads(
            conn.execute(
                "SELECT manifest_json FROM run_manifest ORDER BY created_at DESC"
            ).fetchone()[0]
        )
    assert manifest["provider_profile"]["model_id"] == MODEL
    assert manifest["provider_profile"]["files_digest"] == mlx.files_digest(fake_mlx.model_path)


def test_worker_crash_pauses_logical_time_and_resume_restores_the_worker(fake_mlx, tmp_path):
    fake_mlx("valid", "crash", "valid")
    with TestClient(create_app(tmp_path / "crash.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": f"mlx:{MODEL}"})
        control(client, "speed", ticks_per_second=1000)
        control(client, "start")
        wait_for(lambda: client.get("/api/world").json()["last_error"] is not None)
        frame = client.get("/api/world").json()
        assert "worker_crashed" in frame["last_error"] and frame["status"] == "paused"
        assert frame["tick"] == 4 and frame["pending"]["tick"] == 5  # no time passed
        assert frame["provider"]["health"]["status"] == "crashed"
        assert len(client.get("/api/decisions").json()["decisions"]) == 1
        control(client, "resume", until_tick=5)
        wait_for(lambda: client.get("/api/world").json()["tick"] == 5)
        frame = client.get("/api/world").json()
        assert frame["last_error"] is None and frame["provider"]["health"]["status"] == "ready"
        ticks = [d["tick"] for d in client.get("/api/decisions").json()["decisions"]]
        assert sorted(ticks) == [1, 5]  # the crashed request committed exactly once


def test_timeout_then_success_commits_once(fake_mlx, tmp_path):
    fake_mlx("hang", "valid")
    with TestClient(create_app(tmp_path / "timeout.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": f"mlx:{MODEL}"})
        runner = client.app.state.runner
        runner._provider.settings = mlx.MLXSettings(request_timeout_s=0.5)
        frame = control(client, "step").json()
        assert frame["tick"] == 1 and frame["last_error"] is None
        decisions = client.get("/api/decisions").json()["decisions"]
        assert len(decisions) == 1 and runner._provider.calls == 2


def test_invalid_live_output_is_recorded_not_retried(fake_mlx, tmp_path):
    fake_mlx("invalid")
    with TestClient(create_app(tmp_path / "invalid.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": f"mlx:{MODEL}"})
        control(client, "step")
        (decision,) = client.get("/api/decisions").json()["decisions"]
        assert decision["raw_output"] == "Sure! I will wait."
        assert decision["outcome"] == "unparsed" and decision["parse_status"] == "malformed_json"
        assert client.app.state.runner._provider.calls == 1


def test_replay_and_restart_need_no_model(fake_mlx, tmp_path, monkeypatch):
    fake_mlx("valid")
    db = tmp_path / "replay.sqlite3"
    with TestClient(create_app(db)) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": f"mlx:{MODEL}"})
        for _ in range(6):
            frame = control(client, "step").json()
    # Model, interpreter and worker all gone: history and restart still work.
    monkeypatch.setenv("GENESIS_MLX_MODEL_DIRS", str(tmp_path / "empty"))
    monkeypatch.setenv("GENESIS_MLX_PYTHON", str(tmp_path / "no-python"))
    monkeypatch.setenv("GENESIS_MLX_WORKER", str(tmp_path / "no-worker.py"))
    started_before = fake_mlx.state.read_text()
    with TestClient(create_app(db)) as client:
        again = client.get("/api/world").json()
        assert again["tick"] == 6 and again["state_hash"] == frame["state_hash"]
        h3 = client.get("/api/history/3", params={"source": "initial"}).json()
        assert h3["verified"]
        assert client.get("/api/world").json()["provider"]["health"]["status"] == "stopped"
    assert fake_mlx.state.read_text() == started_before  # no inference during replay


def test_unknown_model_or_mixed_selection_is_refused(fake_mlx, tmp_path):
    with TestClient(create_app(tmp_path / "x.sqlite3", dev_fixtures=True)) as client:
        assert client.post("/api/runs", json={"cognition": "mlx:nope"}).status_code == 422
        assert client.post("/api/runs", json={"cognition": "gpt:x"}).status_code == 422
        both = {"fixture": "recorded_valid", "cognition": f"mlx:{MODEL}"}
        assert client.post("/api/runs", json=both).status_code == 422


@pytest.mark.skipif(
    os.environ.get("GENESIS_LIVE_MLX") != "1",
    reason="live model smoke test: set GENESIS_LIVE_MLX=1, GENESIS_MLX_PYTHON and a model",
)
def test_live_model_smoke_nondeterministic():
    """Optional, nondeterministic: one real decision from the configured local model."""
    model_id = os.environ.get("GENESIS_LIVE_MLX_MODEL", "Qwen3.8-27B-Uncensored-MLX/8-bit")
    prepared, request = request_for_tick1()
    p = mlx.MLXProvider(model_id, mlx.profile_for(model_id))
    try:
        response = p.decide(request, threading.Event())
    finally:
        p.close()
    result = parse(response.raw_text, prepared.world.life, ACTIONS)
    print("live output:", response.raw_text, "->", result.status)
    assert response.usage.input_tokens and response.usage.latency_ms
