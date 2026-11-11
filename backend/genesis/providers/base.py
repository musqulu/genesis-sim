"""Provider contract shared by recorded, local (MLX-LM, M07A) and API (M07B) adapters.

A provider turns a DecisionRequest into raw text or raises a typed technical error. It never
validates actions, creates resources or touches world state; the engine alone does that.
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass

from genesis.cognition.context import DecisionRequest


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    kind: str  # recorded | scripted | mlx | http
    name: str
    model: str | None = None
    revision: str | None = None  # None means unknown, never "pinned"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Capabilities:
    structured_output: str  # "none" | "json_schema" (only if verified for the runtime)
    max_context_tokens: int | None
    max_output_tokens: int | None
    concurrency: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Usage:
    """Unavailable values stay None (unknown), never zero."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    request_id: str
    input_hash: str
    raw_text: str
    usage: Usage
    finish_reason: str | None = None  # "stop" | "length" | None (unknown)
    detail: dict | None = None  # provider-specific provenance (prompt, sampling, runtime)


class ProviderError(RuntimeError):
    """A technical failure. It never consumes a decision opportunity or ages anyone."""

    # Transient transport problems get one bounded retry. A crashed local worker is not
    # retried automatically: it pauses the run for operator attention (resume restarts it).
    RETRYABLE = {"timeout", "unavailable", "transport", "rate_limited"}

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.retryable = kind in self.RETRYABLE


class Provider(ABC):
    identity: ProviderIdentity
    capabilities: Capabilities

    @abstractmethod
    def decide(self, request: DecisionRequest, cancel: threading.Event) -> ProviderResponse:
        """Return raw output for exactly this request or raise ProviderError."""
