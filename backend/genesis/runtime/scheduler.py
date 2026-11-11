"""Decision barrier plumbing: pending tick state and the bounded inference worker.

The worker only moves requests to a provider and results back; it never sees world state
and never commits anything. One transport retry is allowed for transient errors; semantic
problems (bad JSON, illegal actions) are not errors here and are never retried.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from genesis.cognition.context import DecisionRequest
from genesis.engine.world import PreparedTick
from genesis.providers.base import Provider, ProviderError, ProviderResponse

log = logging.getLogger("genesis.scheduler")
TRANSPORT_ATTEMPTS = 2  # the first try plus one bounded retry


@dataclass
class PendingTick:
    """A prepared tick waiting at the barrier. Nothing in it is committed or published."""

    generation: int
    prepared: PreparedTick
    requests: dict[str, DecisionRequest]  # actor id -> request, in resolution order
    responses: dict[str, tuple[str, dict]] = field(default_factory=dict)  # raw, meta
    failures: dict[str, str] = field(default_factory=dict)
    queued: list[str] = field(default_factory=list)
    in_flight: set[str] = field(default_factory=set)
    started: float = field(default_factory=time.monotonic)

    @property
    def tick(self) -> int:
        return self.prepared.world.tick

    @property
    def complete(self) -> bool:
        return len(self.responses) == len(self.requests)

    def view(self) -> dict:
        return {
            "tick": self.tick,
            "total": len(self.requests),
            "completed": len(self.responses),
            "failed": len(self.failures),
            "waiting": sorted(set(self.requests) - set(self.responses)),
            "seconds": round(time.monotonic() - self.started, 1),
        }


Result = Callable[[int, DecisionRequest, ProviderResponse | None, ProviderError | None], None]


class InferenceWorker:
    """Single worker thread over a bounded queue (one inference at a time)."""

    def __init__(self, capacity: int, on_result: Result):
        self.capacity = capacity
        self._queue: queue.Queue = queue.Queue(maxsize=capacity)
        self._on_result = on_result
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="genesis-inference", daemon=True)
        self._thread.start()
        self.max_depth = 0  # observed queue depth (bounded by capacity)

    def submit(self, generation: int, provider: Provider, request: DecisionRequest) -> None:
        """Never blocks: the caller keeps at most `capacity` requests outstanding."""
        self._queue.put_nowait((generation, provider, request))
        self.max_depth = max(self.max_depth, self._queue.qsize())

    def stop(self) -> None:
        self._cancel.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._cancel.is_set():
            job = self._queue.get()
            if job is None:
                return
            generation, provider, request = job
            error: ProviderError | None = None
            for attempt in range(1, TRANSPORT_ATTEMPTS + 1):
                try:
                    response = provider.decide(request, self._cancel)
                except ProviderError as exc:
                    error = exc
                    if exc.retryable and attempt < TRANSPORT_ATTEMPTS:
                        log.warning("Transient provider error, retrying once: %s", exc)
                        continue
                    break
                except Exception as exc:  # noqa: BLE001 - adapter bug: technical failure
                    error = ProviderError("transport", f"{type(exc).__name__}: {exc}")
                    break
                else:
                    if (response.request_id, response.input_hash) != (
                        request.request_id,
                        request.input_hash,
                    ):
                        error = ProviderError(
                            "mismatch", "Provider answered a different request than it was given"
                        )
                        break
                    self._on_result(generation, request, response, None)
                    error = None
                    break
            if error is not None:
                self._on_result(generation, request, None, error)
