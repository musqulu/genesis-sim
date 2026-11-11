"""Recorded responses keyed by request input hash (deterministic diagnostics, not a model).

A request is answered only by an entry with the identical input hash. A mismatch is a
technical error; responses are never matched by call order.
"""

import json
import threading
import time
from pathlib import Path

from genesis.cognition.context import DecisionRequest
from genesis.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderIdentity,
    ProviderResponse,
    Usage,
)

RECORDINGS = Path(__file__).with_name("recordings")


def load_recording(name: str) -> dict:
    path = RECORDINGS / f"{name}.json"
    if not path.is_file():
        raise ProviderError("config", f"Recording {name!r} does not exist")
    return json.loads(path.read_text())


class RecordedProvider(Provider):
    capabilities = Capabilities("none", None, None, 1)

    def __init__(self, name: str, *, delay_scale: float = 1.0, recording: dict | None = None):
        data = recording if recording is not None else load_recording(name)
        if data.get("format") != 1:
            raise ProviderError("config", f"Recording {name!r} has an unsupported format")
        self.identity = ProviderIdentity("recorded", name, model=None, revision=data["sha256"])
        self.delay_scale = delay_scale
        self._by_hash = {e["input_hash"]: e for e in data["entries"]}
        self._by_request = {e["request_id"]: e for e in data["entries"]}
        self.calls = 0

    def decide(self, request: DecisionRequest, cancel: threading.Event) -> ProviderResponse:
        self.calls += 1
        started = time.monotonic()
        entry = self._by_hash.get(request.input_hash)
        if entry is None:
            known = self._by_request.get(request.request_id)
            detail = (
                "a recording exists for this request id but for different input"
                if known
                else "no recording for this request"
            )
            raise ProviderError(
                "mismatch",
                f"Recorded response mismatch for {request.request_id} "
                f"(input {request.input_hash[:12]}): {detail}",
            )
        delay = entry.get("delay_ms", 0) / 1000 * self.delay_scale
        if delay and cancel.wait(delay):
            raise ProviderError("unavailable", "Request cancelled while waiting")
        return ProviderResponse(
            request.request_id,
            request.input_hash,
            entry["raw"],
            Usage(latency_ms=int((time.monotonic() - started) * 1000)),
            finish_reason="stop",
        )
