"""M07B Replicate adapter: Replicate's predictions API is not chat-completions compatible.

At run creation the model's version and input schema are resolved once and sealed in the
manifest, so the run keeps using exactly that version. Only inputs the model declares are
sent; the decision prompt itself is unchanged (system text goes to `system_prompt` when the
model has one, otherwise it is placed before the user text, and that choice is recorded).
"""

import threading
import time

import httpx2

from genesis.cognition.context import DecisionRequest
from genesis.cognition.prompts import prompt_version, render
from genesis.providers.base import ProviderError, ProviderResponse, Usage
from genesis.providers.config import Profile
from genesis.providers.http_chat import HttpProvider, _int, output_cap, seed_for

WAIT_S = 60  # Replicate's synchronous wait limit
POLL_S = 1.0
TERMINAL = {"succeeded", "successful", "failed", "canceled", "cancelled", "aborted"}


def _split(model: str) -> tuple[str, str | None]:
    name, _, version = model.partition(":")
    if name.count("/") != 1 or not all(name.split("/")):
        raise ProviderError("config", f"Replicate model must be 'owner/name', got {model!r}")
    return name, version or None


def mapping_for(properties: dict | None) -> dict:
    """Which of the model's declared inputs carry the prompt and sampling settings."""
    if properties is None:  # schema unavailable: send the prompt alone
        return {"prompt": "prompt", "schema_known": False}
    if "prompt" not in properties:
        raise ProviderError(
            "config",
            "This Replicate model has no 'prompt' input; only text models with a prompt "
            "input are supported",
        )

    def first(*names):
        return next((n for n in names if n in properties), None)

    return {
        "prompt": "prompt",
        "system_prompt": first("system_prompt"),
        "max_tokens": first("max_tokens", "max_new_tokens", "max_completion_tokens"),
        "temperature": first("temperature"),
        "top_p": first("top_p"),
        "seed": first("seed"),
        "schema_known": True,
    }


def resolve(profile: Profile, transport: httpx2.BaseTransport | None = None) -> dict:
    """Pin the version and read the input schema (one network call at run creation)."""
    name, version = _split(profile.model)
    probe = ReplicateProvider(profile, {"version": None, "mapping": {}}, transport=transport)
    base = profile.base_url.rstrip("/")
    try:
        if version:
            data = probe.json_of(
                probe.send("GET", f"{base}/models/{name}/versions/{version}", threading.Event())
            )
            schema_holder = data
        else:
            data = probe.json_of(probe.send("GET", f"{base}/models/{name}", threading.Event()))
            schema_holder = data.get("latest_version") or {}
            version = schema_holder.get("id")
    finally:
        probe.close()
    try:
        properties = schema_holder["openapi_schema"]["components"]["schemas"]["Input"]["properties"]
    except (KeyError, TypeError):
        properties = None
    return {"version": version, "mapping": mapping_for(properties)}


class ReplicateProvider(HttpProvider):
    def __init__(self, profile: Profile, resolved: dict, **kwargs):
        super().__init__(profile, revision=resolved.get("version"), **kwargs)
        self.version = resolved.get("version")
        self.mapping = resolved.get("mapping") or {}

    def _input(self, messages: list[dict], seed: int, cap: int | None = None) -> tuple[dict, str]:
        system, user = messages[0]["content"], messages[1]["content"]
        m = self.mapping
        if m.get("system_prompt"):
            data, placement = {m["system_prompt"]: system, "prompt": user}, "system_prompt"
        else:
            data, placement = {"prompt": f"{system}\n\n{user}"}, "prefixed"
        for key, value in (
            ("max_tokens", cap or self.profile.max_output_tokens),
            ("temperature", self.profile.temperature),
            ("top_p", self.profile.top_p),
            ("seed", seed if self.profile.send_seed else None),
        ):
            if m.get(key) and value is not None:
                data[m[key]] = value
        data.update(self.profile.extra_body)  # declared extra inputs, recorded per decision
        return data, placement

    def _decide(self, request: DecisionRequest, cancel: threading.Event, calls: int):
        messages = render(request)
        seed = seed_for(request)
        model_input, placement = self._input(
            messages, seed, output_cap(self.profile, request.purpose)
        )
        base = self.profile.base_url.rstrip("/")
        name, _ = _split(self.profile.model)
        timeout = self.profile.timeout_s
        headers = {"Prefer": f"wait={int(min(WAIT_S, max(1, timeout)))}"}
        headers["Cancel-After"] = f"{int(max(5, timeout))}s"
        if self.version:
            url, body = f"{base}/predictions", {"version": self.version, "input": model_input}
        else:
            url, body = f"{base}/models/{name}/predictions", {"input": model_input}
        started = time.monotonic()
        data = self.json_of(self.send("POST", url, cancel, json=body, extra_headers=headers))
        prediction_id = data.get("id")
        while data.get("status") not in TERMINAL:
            if time.monotonic() - started > timeout or cancel.is_set():
                if prediction_id:
                    try:
                        self.send(
                            "POST", f"{base}/predictions/{prediction_id}/cancel", threading.Event()
                        )
                    except ProviderError:
                        pass
                if cancel.is_set():
                    raise ProviderError("unavailable", "Request cancelled")
                raise ProviderError(
                    "timeout", f"Replicate prediction did not finish in {timeout:.0f}s"
                )
            cancel.wait(POLL_S)
            if not prediction_id:
                raise ProviderError("protocol", "Replicate reply has no prediction id")
            data = self.json_of(self.send("GET", f"{base}/predictions/{prediction_id}", cancel))
        status = data.get("status")
        if status not in ("succeeded", "successful"):
            error = self.redact(str(data.get("error") or status))[:300]
            raise ProviderError("remote_failed", f"Replicate prediction {status}: {error}")
        output = data.get("output")
        if isinstance(output, list):
            text = "".join(str(part) for part in output)
        elif isinstance(output, str):
            text = output
        else:
            text = ""
        metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
        detail = self.common_detail(messages, seed, calls, prompt_version(request))
        detail["replicate"] = {
            "prediction_id": prediction_id,
            "version": self.version,
            "input_fields": sorted(model_input),
            "system_text": placement,
            "predict_time": metrics.get("predict_time"),
        }
        return ProviderResponse(
            request.request_id,
            request.input_hash,
            text,
            Usage(_int(metrics.get("input_token_count")), _int(metrics.get("output_token_count"))),
            finish_reason=None,  # Replicate does not report why generation stopped
            detail=detail,
        )

    def check(self) -> dict:
        name, _ = _split(self.profile.model)
        try:
            self.send(
                "GET", f"{self.profile.base_url.rstrip('/')}/models/{name}", threading.Event()
            )
        except ProviderError as exc:
            return {"ok": False, "message": f"{exc.kind}: {exc}"}
        return {"ok": True, "message": f"Connected; {name} found"}
