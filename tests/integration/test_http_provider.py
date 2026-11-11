"""M07B API providers against a local HTTP fixture (no network, no model)."""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from genesis.api.app import create_app
from genesis.cognition.policy import parse
from genesis.cognition.prompts import render
from genesis.providers import config as api_config
from genesis.providers.base import ProviderError
from genesis.providers.http_chat import HttpChatProvider
from genesis.providers.replicate import ReplicateProvider, resolve

from tests.fixtures.fake_api import VALID, VERSION, FakeAPI
from tests.integration.test_mlx import (
    ACTIONS,
    MODEL,
    control,
    fake_mlx,  # noqa: F401 - pytest fixture
    request_for_tick1,
    wait_for,
)

SECRET = "sk-test-SECRET-4f9a"


@pytest.fixture
def api(tmp_path, monkeypatch):
    server = FakeAPI()
    monkeypatch.setenv("FAKE_API_KEY", SECRET)
    monkeypatch.setenv("GENESIS_MLX_PYTHON", "/nonexistent/python")  # API runs need no MLX
    monkeypatch.setenv("GENESIS_MLX_MODEL_DIRS", str(tmp_path / "no-models"))

    def profiles(**overrides):
        chat = {"model": "fake-chat", "timeout_s": 2, **overrides.get("chat", {})}
        rep = {"model": "owner/fake-text", "timeout_s": 3, **overrides.get("rep", {})}
        lines = []
        for pid, protocol, values in (("fake", "openai_chat", chat), ("rep", "replicate", rep)):
            lines.append(f"[profiles.{pid}]")
            lines.append(f'protocol = "{protocol}"')
            lines.append(f'base_url = "{server.url}"')
            lines.append('api_key_env = "FAKE_API_KEY"')
            for k, v in values.items():
                lines.append(f"{k} = {toml_value(v)}")
        path = tmp_path / "providers.toml"
        path.write_text("\n".join(lines) + "\n")
        monkeypatch.setenv("GENESIS_PROVIDERS_FILE", str(path))

    profiles()
    server.profiles = profiles
    yield server
    server.close()


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


def chat(**kw):
    return HttpChatProvider(api_config.get_profile("fake"), **kw)


# -- chat-completions contract --------------------------------------------------------------


def test_compatible_request_payload_usage_and_provenance(api):
    _, request = request_for_tick1()
    p = chat()
    try:
        response = p.decide(request, threading.Event())
    finally:
        p.close()
    sent = api.requests[-1]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == f"Bearer {SECRET}"
    body = sent["body"]
    assert body["model"] == "fake-chat" and body["messages"] == render(request)
    assert body["max_tokens"] == 256 and body["temperature"] == 0.7 and body["top_p"] == 0.95
    assert body["seed"] == int(request.input_hash[:8], 16) and "response_format" not in body
    assert response.raw_text == VALID and response.finish_reason == "stop"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (120, 14)
    assert (
        response.detail["remote_calls"] == 1 and response.detail["prompt_version"] == "decision-v1"
    )
    assert SECRET not in json.dumps(response.detail)
    assert p.identity.kind == "api" and p.identity.model == "fake-chat"


def test_unknown_usage_stays_unknown_and_structured_output_only_when_declared(api):
    api.profiles(
        chat={
            "structured_output": "json_object",
            "send_seed": False,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    )
    api.script.extend(["nousage"])
    _, request = request_for_tick1()
    p = chat()
    try:
        response = p.decide(request, threading.Event())
    finally:
        p.close()
    assert response.usage.input_tokens is None and response.usage.output_tokens is None
    body = api.requests[-1]["body"]
    assert body["response_format"] == {"type": "json_object"} and "seed" not in body
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert response.detail["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_profiles_refuse_secrets_shape_and_reserved_overrides(api, tmp_path, monkeypatch):
    path = tmp_path / "bad.toml"
    path.write_text(
        '[profiles.a]\nprotocol = "openai_chat"\nbase_url = "http://127.0.0.1/v1"\nmodel = "m"\n'
        "extra_body = { messages = [] }\n"
        '[profiles.b]\nprotocol = "openai_chat"\nbase_url = "http://127.0.0.1/v1"\nmodel = "m"\n'
        'api_key = "sk-inline"\n'
        '[profiles.c]\nprotocol = "ftp"\nbase_url = "http://127.0.0.1/v1"\nmodel = "m"\n'
        '[profiles.d]\nprotocol = "openai_chat"\nbase_url = "http://example.com/v1"\n'
        'model = "m"\n'
    )
    monkeypatch.setenv("GENESIS_PROVIDERS_FILE", str(path))
    profiles, problems = api_config.load_profiles()
    assert profiles == {} and len(problems) == 4
    assert any("plain http" in p for p in problems)  # remote endpoints must use https
    assert any("messages" in p for p in problems)
    assert any("api_key" in p for p in problems)  # an inline key is not a known setting


def test_invalid_and_truncated_replies_are_verbatim_for_the_strict_parser(api):
    api.script.extend(["invalid", "truncate"])
    prepared, request = request_for_tick1()
    p = chat()
    try:
        prose = p.decide(request, threading.Event())
        cut = p.decide(request, threading.Event())
    finally:
        p.close()
    assert prose.raw_text == "Sure! I will wait."
    assert parse(prose.raw_text, prepared.world.life, ACTIONS).status == "malformed_json"
    assert cut.finish_reason == "length"
    assert parse(cut.raw_text, prepared.world.life, ACTIONS).status == "malformed_json"


@pytest.mark.parametrize(
    "behaviour, kind, retryable",
    [
        ("401", "auth", False),
        ("403", "auth", False),
        ("429", "rate_limited", True),
        ("500", "unavailable", True),
        ("hang", "timeout", True),
        ("nochoices", "protocol", False),
        ("notjson", "protocol", False),
    ],
)
def test_typed_errors_are_redacted(api, behaviour, kind, retryable):
    api.profiles(chat={"timeout_s": 1})
    api.script.append(behaviour)
    _, request = request_for_tick1()
    p = chat()
    try:
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
    finally:
        p.close()
    assert err.value.kind == kind and err.value.retryable is retryable
    assert SECRET not in str(err.value) and SECRET not in json.dumps(p.health())
    assert p.health()["failures"] == 1 and p.health()["last_error_kind"] == kind


def test_missing_key_unreachable_endpoint_and_budget(api, monkeypatch):
    _, request = request_for_tick1()
    monkeypatch.delenv("FAKE_API_KEY")
    p = chat()
    with pytest.raises(ProviderError) as err:
        p.decide(request, threading.Event())
    assert err.value.kind == "config" and "FAKE_API_KEY" in str(err.value)
    p.close()
    monkeypatch.setenv("FAKE_API_KEY", SECRET)
    api.profiles(chat={"max_requests": 2})
    p = chat(used=(1, 0))  # one request already recorded for this run
    try:
        p.decide(request, threading.Event())
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "budget" and not err.value.retryable
        assert p.health()["budget"]["requests"] == 2
    finally:
        p.close()
    api.close()
    p = chat()
    with pytest.raises(ProviderError) as err:
        p.decide(request, threading.Event())
    assert err.value.kind in ("transport", "timeout") and err.value.retryable
    p.close()


def test_connectivity_check_handles_missing_model_list(api):
    p = chat()
    try:
        assert p.check()["ok"] is None  # 404 on /models: reachable, unverified
        api.list_models = ["other", "fake-chat"]
        assert p.check()["ok"] is True
        api.list_models = ["other"]
        assert p.check()["ok"] is None
    finally:
        p.close()


# -- Replicate --------------------------------------------------------------------------------


def test_replicate_pins_version_and_maps_declared_inputs(api):
    profile = api_config.get_profile("rep")
    resolved = resolve(profile)
    assert resolved["version"] == VERSION
    assert resolved["mapping"]["system_prompt"] == "system_prompt"
    assert resolved["mapping"]["max_tokens"] == "max_new_tokens"
    _, request = request_for_tick1()
    p = ReplicateProvider(profile, resolved)
    try:
        response = p.decide(request, threading.Event())
    finally:
        p.close()
    sent = api.requests[-1]
    assert sent["path"] == "/v1/predictions" and sent["body"]["version"] == VERSION
    assert sent["headers"]["Prefer"] == "wait=3" and sent["headers"]["Authorization"].endswith(
        SECRET
    )
    system, user = render(request)
    model_input = sent["body"]["input"]
    assert (
        model_input["system_prompt"] == system["content"]
        and model_input["prompt"] == user["content"]
    )
    assert model_input["max_new_tokens"] == 256 and model_input["seed"] == int(
        request.input_hash[:8], 16
    )
    assert response.raw_text == '{"action": "wait", "explanation": "replicate"}'  # list joined
    assert (response.usage.input_tokens, response.usage.output_tokens) == (130, 15)
    assert response.detail["replicate"]["system_text"] == "system_prompt"
    assert p.identity.revision == VERSION


def test_replicate_without_system_prompt_prefixes_the_system_text(api):
    api.input_schema = {"prompt": {"type": "string"}, "max_tokens": {"type": "integer"}}
    profile = api_config.get_profile("rep")
    resolved = resolve(profile)
    _, request = request_for_tick1()
    p = ReplicateProvider(profile, resolved)
    try:
        response = p.decide(request, threading.Event())
    finally:
        p.close()
    model_input = api.requests[-1]["body"]["input"]
    system, user = render(request)
    assert set(model_input) == {"prompt", "max_tokens"}
    assert model_input["prompt"] == f"{system['content']}\n\n{user['content']}"
    assert response.detail["replicate"]["system_text"] == "prefixed"


def test_replicate_poll_cancel_failure_and_unsupported_model(api):
    profile = api_config.get_profile("rep")
    resolved = resolve(profile)
    _, request = request_for_tick1()
    p = ReplicateProvider(profile, resolved)
    try:
        api.script.extend(["rpoll"])
        assert p.decide(request, threading.Event()).raw_text.startswith('{"action"')
        api.script.extend(["rhang"])
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "timeout" and api.cancelled  # the remote prediction was cancelled
        api.script.extend(["rfail"])
        with pytest.raises(ProviderError) as err:
            p.decide(request, threading.Event())
        assert err.value.kind == "remote_failed" and not err.value.retryable
    finally:
        p.close()
    api.input_schema = {"image": {"type": "string"}}
    with pytest.raises(ProviderError) as err:
        resolve(profile)
    assert err.value.kind == "config" and "prompt" in str(err.value)


# -- runs through the API ---------------------------------------------------------------------


def test_models_list_profiles_without_secrets(api, tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "m.sqlite3")) as client:
        listed = client.get("/api/models").json()
        assert SECRET not in json.dumps(listed)
        profiles = {p["id"]: p for p in listed["api"]["profiles"]}
        assert profiles["fake"]["available"] and profiles["fake"]["key_status"] == "set"
        assert listed["runtime"]["available"] is False  # no MLX needed for API runs
        monkeypatch.delenv("FAKE_API_KEY")
        profiles = {p["id"]: p for p in client.get("/api/models").json()["api"]["profiles"]}
        assert not profiles["fake"]["available"] and "FAKE_API_KEY" in profiles["fake"]["reason"]
        refused = client.post("/api/runs", json={"cognition": "api:fake"})
        assert refused.status_code == 422 and "FAKE_API_KEY" in refused.json()["detail"]
        assert client.post("/api/runs", json={"cognition": "api:nope"}).status_code == 422
        check = client.post("/api/providers/fake/check").json()
        assert check["ok"] is False


def test_api_run_two_organisms_one_provider_no_leakage_no_secret(api, tmp_path):
    db = tmp_path / "api.sqlite3"
    with TestClient(create_app(db)) as client:
        frame = client.post(
            "/api/runs", json={"seed": 42, "cognition": "api:fake", "organisms": 2}
        ).json()
        assert frame["provider"]["kind"] == "api" and frame["provider"]["name"] == "fake"
        assert frame["provider"]["profile"]["api_key_env"] == "FAKE_API_KEY"
        frame = control(client, "step").json()
        assert frame["tick"] == 1
        decisions = client.get("/api/decisions").json()["decisions"]
        assert {d["actor_id"] for d in decisions} == {"org-0001", "org-0002"}
        for d in decisions:
            assert d["parse_status"] == "valid"
            detail = client.get(f"/api/decisions/{d['request_id']}").json()
            user = json.loads(detail["prompt"][1]["content"])
            assert user["observation"] == detail["request"]["context"]["observation"]
            assert user["observation"]["organism_id"] == d["actor_id"]
        assert len([r for r in api.requests if r["path"] == "/v1/chat/completions"]) == 2
        world = client.get("/api/world").json()
        assert world["provider"]["health"]["budget"]["requests"] == 2
        assert SECRET not in json.dumps(world)
        assert client.post("/api/summary").json()["markdown"].count("API model fake-chat") >= 1
    raw = b"".join(p.read_bytes() for p in Path(tmp_path).glob("api.sqlite3*"))
    assert SECRET.encode() not in raw  # never in the database, manifest or logs


def test_auth_failure_pauses_without_fallback_and_resume_commits_once(api, tmp_path):
    api.script.extend(["401"])
    with TestClient(create_app(tmp_path / "auth.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": "api:fake"})
        frame = control(client, "step").json()
        wait_for(lambda: client.get("/api/world").json()["last_error"] is not None)
        frame = client.get("/api/world").json()
        assert frame["tick"] == 0 and frame["status"] == "paused"
        assert "auth" in frame["last_error"] and SECRET not in frame["last_error"]
        assert frame["provider"]["kind"] == "api"  # no switch to anything else
        assert client.get("/api/decisions").json()["decisions"] == []
        frame = control(client, "step").json()
        wait_for(lambda: client.get("/api/world").json()["tick"] == 1)
        assert len(client.get("/api/decisions").json()["decisions"]) == 1


def test_ambiguous_timeout_retry_commits_once(api, tmp_path):
    api.profiles(chat={"timeout_s": 1})
    api.hang_s = 1.5
    api.script.extend(["hang", "valid"])
    with TestClient(create_app(tmp_path / "retry.sqlite3")) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": "api:fake"})
        control(client, "step")
        wait_for(lambda: client.get("/api/world").json()["tick"] == 1)
        (decision,) = client.get("/api/decisions").json()["decisions"]
        detail = client.get(f"/api/decisions/{decision['request_id']}").json()
        assert detail["detail"]["remote_calls"] == 2  # the first remote call was never used
        assert decision["outcome"] == "accepted"


def test_replay_and_restart_need_no_endpoint_or_key(api, tmp_path, monkeypatch):
    db = tmp_path / "replay.sqlite3"
    with TestClient(create_app(db)) as client:
        client.post("/api/runs", json={"seed": 42, "cognition": "api:fake"})
        for _ in range(3):
            frame = control(client, "step").json()
        wait_for(lambda: client.get("/api/world").json()["tick"] == 3)
        frame = client.get("/api/world").json()
    api.close()
    monkeypatch.delenv("FAKE_API_KEY")
    monkeypatch.setenv("GENESIS_PROVIDERS_FILE", str(tmp_path / "gone.toml"))
    with TestClient(create_app(db)) as client:
        again = client.get("/api/world").json()
        assert (again["tick"], again["state_hash"]) == (frame["tick"], frame["state_hash"])
        assert again["provider"]["kind"] == "api"  # rebuilt from the sealed manifest
        history = client.get("/api/history/3", params={"source": "initial"}).json()
        assert history["verified"] is True
        assert client.get("/api/decisions").json()["decisions"]


def test_replicate_run_through_the_api(api, tmp_path):
    with TestClient(create_app(tmp_path / "rep.sqlite3")) as client:
        frame = client.post("/api/runs", json={"seed": 42, "cognition": "api:rep"}).json()
        assert frame["provider"]["revision"] == VERSION
        assert frame["provider"]["profile"]["replicate"]["version"] == VERSION
        control(client, "step")
        wait_for(lambda: client.get("/api/world").json()["tick"] == 1)
        (decision,) = client.get("/api/decisions").json()["decisions"]
        assert decision["parse_status"] == "valid" and decision["outcome"] == "accepted"
        api.input_schema = {"image": {"type": "string"}}
        refused = client.post("/api/runs", json={"seed": 1, "cognition": "api:rep"})
        assert refused.status_code == 422 and "prompt" in refused.json()["detail"]


# -- mixed-model populations ------------------------------------------------------------------


def test_mixed_population_each_organism_keeps_its_own_model(api, fake_mlx, tmp_path):  # noqa: F811
    fake_mlx("valid", "valid", "valid")
    db = tmp_path / "mixed.sqlite3"
    population = [
        {"cognition": f"mlx:{MODEL}", "organisms": 1},
        {"cognition": "api:fake", "organisms": 1},
    ]
    with TestClient(create_app(db)) as client:
        frame = client.post("/api/runs", json={"seed": 42, "population": population}).json()
        assert frame["provider"]["kind"] == "mixed"
        members = frame["provider"]["members"]
        assert members["org-0001"]["kind"] == "mlx" and members["org-0002"]["kind"] == "api"
        assert frame["provider"]["profile"]["assignment"] == {"org-0001": 0, "org-0002": 1}
        assert [(o["x"], o["y"]) for o in frame["organisms"]] == [
            (o["x"], o["y"])
            for o in client.post(
                "/api/runs", json={"seed": 42, "cognition": "api:fake", "organisms": 2}
            ).json()["organisms"]
        ]  # same placements as a single-model run with the same seed
        frame = client.post("/api/runs", json={"seed": 42, "population": population}).json()
        control(client, "step")
        wait_for(lambda: client.get("/api/world").json()["tick"] == 1)
        decisions = {d["actor_id"]: d for d in client.get("/api/decisions").json()["decisions"]}
        assert decisions["org-0001"]["provider"]["kind"] == "mlx"
        assert decisions["org-0002"]["provider"]["kind"] == "api"
        assert len([r for r in api.requests if r["path"] == "/v1/chat/completions"]) == 1
        summary = client.post("/api/summary").json()["markdown"]
        assert "mixed-model experiment" in summary
        assert f"org-0001 used the local MLX-LM model {MODEL}" in summary
        assert "org-0002 used the self-hosted API model fake-chat" in summary
        state = client.get("/api/world").json()
        frame_hash = state["state_hash"]
    with TestClient(create_app(db)) as client:  # rebuilt from the sealed assignment
        again = client.get("/api/world").json()
        assert again["state_hash"] == frame_hash
        assert again["provider"]["members"]["org-0002"]["name"] == "fake"


def test_population_validation(api, fake_mlx, tmp_path):  # noqa: F811
    with TestClient(create_app(tmp_path / "v.sqlite3")) as client:
        post = lambda body: client.post("/api/runs", json={"seed": 1, **body})  # noqa: E731
        assert post({"population": [{"cognition": "api:fake", "organisms": 13}]}).status_code == 422
        too_many = [
            {"cognition": "api:fake", "organisms": 7},
            {"cognition": f"mlx:{MODEL}", "organisms": 6},
        ]
        assert post({"population": too_many}).status_code == 422
        twice = [{"cognition": "api:fake", "organisms": 1}] * 2
        assert post({"population": twice}).status_code == 422
        both = {"cognition": "api:fake", "population": [{"cognition": "api:fake", "organisms": 1}]}
        assert post(both).status_code == 422
        one = post({"population": [{"cognition": "api:fake", "organisms": 2}]}).json()
        assert one["provider"]["kind"] == "api"  # a single model is an ordinary run


def test_reflection_requests_get_their_own_output_cap(api):
    """M11: reflection replies cite several ids; 256 tokens truncated some in a live run."""
    from dataclasses import replace

    _, request = request_for_tick1()
    reflect = replace(
        request,
        purpose="reflect",
        context={
            "status": {"age": 9, "energy": 50},
            "memories": [],
            "reflection_schema": {},
            "response_format": '{"reflections": []}',
        },
    )
    p = chat()
    try:
        p.decide(request, threading.Event())
        assert api.requests[-1]["body"]["max_tokens"] == 256
        p.decide(reflect, threading.Event())
        assert api.requests[-1]["body"]["max_tokens"] == 512
    finally:
        p.close()
    assert p.limits()["reflection_max_output_tokens"] == 512
    from genesis.providers.mlx import MLXSettings

    assert MLXSettings().output_cap("reflect") == 512 and MLXSettings().output_cap("decide") == 256
    # Profiles sealed before the field existed fall back to the default.
    old = {
        k: v
        for k, v in api_config.sealed(api_config.get_profile("fake")).items()
        if k != "reflection_max_output_tokens"
    }
    assert api_config.from_sealed(old).reflection_max_output_tokens == 512
