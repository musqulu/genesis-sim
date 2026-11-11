"""M07B API providers: shared HTTP handling and the OpenAI-compatible chat-completions adapter.

The provider sends exactly the rendered decision prompt and returns the reply text verbatim;
the strict parser and the engine validate it as for every other provider. Errors are typed
so the scheduler's single bounded retry and technical pause apply unchanged. Credentials
are read from the environment per request and redacted from every message.
"""

import threading
import time

import httpx2

from genesis.cognition.context import DecisionRequest
from genesis.cognition.prompts import prompt_version, render
from genesis.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderIdentity,
    ProviderResponse,
    Usage,
)
from genesis.providers.config import Profile

MAX_RETRY_AFTER_S = 30.0
EXCERPT = 300


class HttpProvider(Provider):
    """Common transport, budget, health and error handling for API adapters."""

    kind = "api"

    def __init__(
        self,
        profile: Profile,
        *,
        revision: str | None = None,
        used: tuple[int, int] = (0, 0),
        transport: httpx2.BaseTransport | None = None,
    ):
        self.profile = profile
        self.identity = ProviderIdentity("api", profile.id, model=profile.model, revision=revision)
        self.capabilities = Capabilities(
            profile.structured_output, profile.max_context_tokens, profile.max_output_tokens, 1
        )
        self._client = httpx2.Client(
            timeout=httpx2.Timeout(profile.timeout_s, connect=min(10.0, profile.timeout_s)),
            transport=transport,
        )
        self._lock = threading.Lock()  # one request at a time, like the local worker
        self._status = "idle"
        self._used_requests, self._used_tokens = used
        self._calls_per_request: dict[str, int] = {}
        self._metrics = {
            "requests": 0,
            "failures": 0,
            "last_latency_ms": None,
            "last_error_kind": None,
        }
        self.calls = 0

    # -- helpers ----------------------------------------------------------------------
    def redact(self, text: str) -> str:
        key = self.profile.key()
        if key:
            text = text.replace(key, "[redacted]")
        return text

    def headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        key = self.profile.key()
        if self.profile.api_key_env and key is None:
            raise ProviderError(
                "config", f"The API key variable {self.profile.api_key_env} is not set"
            )
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def send(
        self,
        method: str,
        url: str,
        cancel: threading.Event,
        *,
        extra_headers: dict | None = None,
        **kwargs,
    ) -> httpx2.Response:
        if cancel.is_set():
            raise ProviderError("unavailable", "Request cancelled")
        headers = {**self.headers(), **(extra_headers or {})}
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
        except httpx2.TimeoutException as exc:
            raise ProviderError(
                "timeout",
                f"No response from {self.profile.host} within {self.profile.timeout_s:.0f}s",
            ) from exc
        except httpx2.TransportError as exc:
            raise ProviderError(
                "transport", self.redact(f"Cannot reach {self.profile.host}: {type(exc).__name__}")
            ) from exc
        self.raise_for_status(response, cancel)
        return response

    def raise_for_status(self, response: httpx2.Response, cancel: threading.Event) -> None:
        code = response.status_code
        if code < 400:
            return
        excerpt = self.redact(response.text[:EXCERPT].strip())
        where = f"{self.profile.host} answered HTTP {code}"
        if code in (401, 403):
            raise ProviderError("auth", f"{where}: the key was refused. {excerpt}")
        if code == 429:
            try:
                wait = float(response.headers.get("retry-after", "1"))
            except ValueError:
                wait = 1.0
            cancel.wait(min(max(wait, 0.0), MAX_RETRY_AFTER_S))
            raise ProviderError("rate_limited", f"{where} (rate limited). {excerpt}")
        if code >= 500:
            raise ProviderError("unavailable", f"{where}. {excerpt}")
        raise ProviderError("config", f"{where}. {excerpt}")

    def json_of(self, response: httpx2.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("protocol", f"{self.profile.host} did not return JSON") from exc
        if not isinstance(data, dict):
            raise ProviderError("protocol", f"{self.profile.host} returned an unexpected reply")
        return data

    def sampling(self, seed: int) -> dict:
        return {
            "temperature": self.profile.temperature,
            "top_p": self.profile.top_p,
            "seed": seed if self.profile.send_seed else None,
        }

    def limits(self) -> dict:
        return {
            "max_context_tokens": self.profile.max_context_tokens,
            "max_output_tokens": self.profile.max_output_tokens,
            "reflection_max_output_tokens": self.profile.reflection_max_output_tokens,
        }

    # -- contract ---------------------------------------------------------------------
    def health(self) -> dict:
        return {
            "status": self._status,
            "pid": None,
            "load_seconds": None,
            "memory_gb": None,
            "endpoint": self.profile.host,
            "remote": self.profile.remote,
            "budget": {
                "requests": self._used_requests,
                "max_requests": self.profile.max_requests,
                "tokens": self._used_tokens,
                "max_total_tokens": self.profile.max_total_tokens,
            },
            **self._metrics,
        }

    def close(self) -> None:
        self._client.close()
        self._status = "idle"

    def decide(self, request: DecisionRequest, cancel: threading.Event):
        with self._lock:
            if self._used_requests >= self.profile.max_requests:
                raise ProviderError(
                    "budget", f"Request budget reached ({self.profile.max_requests} per run)"
                )
            if self._used_tokens >= self.profile.max_total_tokens:
                raise ProviderError(
                    "budget", f"Token budget reached ({self.profile.max_total_tokens} per run)"
                )
            self.calls += 1
            self._metrics["requests"] += 1
            self._used_requests += 1
            calls = self._calls_per_request.get(request.request_id, 0) + 1
            self._calls_per_request = {request.request_id: calls}  # only the latest matters
            self._status = "busy"
            started = time.monotonic()
            try:
                response = self._decide(request, cancel, calls)
            except ProviderError as exc:
                self._metrics["failures"] += 1
                self._metrics["last_error_kind"] = exc.kind
                self._status = "failed"
                raise
            self._status = "idle"
            latency = int((time.monotonic() - started) * 1000)
            self._metrics["last_latency_ms"] = latency
            self._metrics["last_error_kind"] = None
            usage = response.usage
            self._used_tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
            return ProviderResponse(
                response.request_id,
                response.input_hash,
                response.raw_text,
                Usage(usage.input_tokens, usage.output_tokens, latency),
                finish_reason=response.finish_reason,
                detail=response.detail,
            )

    def _decide(self, request: DecisionRequest, cancel: threading.Event, calls: int):
        raise NotImplementedError

    def common_detail(self, messages: list[dict], seed: int, calls: int, version: str) -> dict:
        return {
            "prompt_version": version,
            "messages": messages,
            "sampling": self.sampling(seed),
            "limits": self.limits(),
            "endpoint": {
                "protocol": self.profile.protocol,
                "host": self.profile.host,
                "model": self.profile.model,
                "remote": self.profile.remote,
            },
            # More than 1 means an earlier remote call for this request was retried (its
            # result, if any, was never used: one request commits at most once).
            "remote_calls": calls,
            "extra_body": self.profile.extra_body,
        }


def output_cap(profile, purpose: str) -> int:
    """Output token cap for one request: reflections (M11) get their own, larger cap."""
    return (
        profile.reflection_max_output_tokens if purpose == "reflect" else profile.max_output_tokens
    )


def seed_for(request: DecisionRequest) -> int:
    return int(request.input_hash[:8], 16)  # recorded; not a determinism guarantee


class HttpChatProvider(HttpProvider):
    """OpenAI-compatible `/chat/completions` (vllm-mlx, OpenRouter, OpenAI, ...)."""

    def _decide(self, request: DecisionRequest, cancel: threading.Event, calls: int):
        messages = render(request)
        seed = seed_for(request)
        body: dict = {
            "model": self.profile.model,
            "messages": messages,
            "max_tokens": output_cap(self.profile, request.purpose),
            "temperature": self.profile.temperature,
            "top_p": self.profile.top_p,
            "stream": False,
        }
        body.update(self.profile.extra_body)
        if self.profile.send_seed:
            body["seed"] = seed
        if self.profile.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.profile.structured_output == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "genesis_action",
                    "schema": request.context["action_schema"],
                },
            }
        url = self.profile.base_url.rstrip("/") + "/chat/completions"
        data = self.json_of(self.send("POST", url, cancel, json=body))
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("protocol", f"{self.profile.host} reply has no choices") from exc
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        finish = choice.get("finish_reason")
        detail = self.common_detail(messages, seed, calls, prompt_version(request))
        detail["response_model"] = data.get("model")
        detail["response_id"] = data.get("id")
        return ProviderResponse(
            request.request_id,
            request.input_hash,
            content if isinstance(content, str) else "",
            # A reported 0 prompt tokens is impossible for a real prompt: kept unknown.
            Usage(_int(usage.get("prompt_tokens")) or None, _int(usage.get("completion_tokens"))),
            finish_reason=finish if finish in ("stop", "length") else None,
            detail=detail,
        )

    def check(self) -> dict:
        """A cheap connectivity probe: list models. Not every server supports it."""
        url = self.profile.base_url.rstrip("/") + "/models"
        try:
            response = self.send("GET", url, threading.Event())
        except ProviderError as exc:
            if exc.kind == "config" and "HTTP 404" in str(exc):
                return {
                    "ok": None,
                    "message": "Reachable, but it does not list models (unverified)",
                }
            return {"ok": False, "message": f"{exc.kind}: {exc}"}
        ids = [
            m.get("id") for m in (self.json_of(response).get("data") or []) if isinstance(m, dict)
        ]
        if self.profile.model in ids:
            return {"ok": True, "message": f"Connected; {self.profile.model} is served"}
        return {
            "ok": None,
            "message": f"Connected; {self.profile.model} is not in its model list "
            f"({', '.join(map(str, ids[:5])) or 'empty'})",
        }


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
