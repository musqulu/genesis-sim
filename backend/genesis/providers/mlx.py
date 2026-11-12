"""Local MLX-LM provider: one resident model in a dedicated worker subprocess.

The worker runs under a configured Python interpreter that has `mlx_lm` (Genesis itself
does not import MLX), so API-only and recorded runs never need it. One request is active at
a time. Failures are technical and typed:

  timeout        -> the worker is killed (its late output can never be used); retryable once
  worker_crashed -> the process exited; not retried automatically (technical pause);
                    the next decision after an explicit resume starts a fresh worker
  config         -> missing model/interpreter/runtime; not retried
  context_overflow / generation -> not retried

Output is never repaired: truncated or non-JSON text is returned as-is and recorded by the
strict parser as an unparsed decision.
"""

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from genesis.cognition.context import DecisionRequest
from genesis.cognition.prompts import DECISION_PROMPT_VERSION, prompt_version, render
from genesis.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderIdentity,
    ProviderResponse,
    Usage,
)

WORKER_SCRIPT = Path(__file__).with_name("mlx_worker.py")


@dataclass(frozen=True, slots=True)
class MLXSettings:
    max_context_tokens: int = 4096
    max_output_tokens: int = 256
    # Reflection replies (M11) cite several record ids; 256 tokens truncated 4 of 17 in the
    # first live run. Runs sealed before this field existed get this default.
    reflection_max_output_tokens: int = 512
    # Reuse the model state of shared prompt beginnings across requests (inference speed
    # only; outputs can differ from uncached ones by floating-point rounding).
    prefix_cache: bool = True
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 20
    enable_thinking: bool = False
    request_timeout_s: float = 120.0
    load_timeout_s: float = 900.0

    def to_dict(self) -> dict:
        return asdict(self)

    def output_cap(self, purpose: str) -> int:
        return self.reflection_max_output_tokens if purpose == "reflect" else self.max_output_tokens


# -- configuration and discovery ---------------------------------------------------------------


def model_dirs() -> list[Path]:
    raw = os.environ.get("GENESIS_MLX_MODEL_DIRS", str(Path.home() / "models"))
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p]


def mlx_python() -> str | None:
    """Interpreter with mlx_lm: GENESIS_MLX_PYTHON, else this interpreter if it has it."""
    configured = os.environ.get("GENESIS_MLX_PYTHON")
    if configured:
        return configured
    try:
        import importlib.util

        if importlib.util.find_spec("mlx_lm") is not None:
            return sys.executable
    except (ImportError, ValueError):
        pass
    return None


_runtime_cache: dict[str, dict] = {}


def runtime_status(python: str | None) -> dict:
    """Check (once per interpreter) that the runtime imports; never loads a model."""
    if not python:
        return {"available": False, "reason": "GENESIS_MLX_PYTHON is not set"}
    if python in _runtime_cache:
        return _runtime_cache[python]
    probe = (
        "import importlib.metadata as m, platform, json; import mlx.core, mlx_lm; "
        "print(json.dumps({'mlx': m.version('mlx'), 'mlx_lm': m.version('mlx-lm'), "
        "'python': platform.python_version()}))"
    )
    try:
        out = subprocess.run(
            [python, "-c", probe], capture_output=True, text=True, timeout=60, check=True
        )
        status = {"available": True, "python": python, **json.loads(out.stdout.strip())}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        status = {"available": False, "python": python, "reason": str(exc)[:300]}
    _runtime_cache[python] = status
    return status


def _is_mlx_model(path: Path) -> bool:
    try:
        config = json.loads((path / "config.json").read_text())
    except (OSError, ValueError):
        return False
    has_weights = any(path.glob("*.safetensors"))
    has_tokenizer = (path / "tokenizer.json").is_file() or (
        path / "tokenizer_config.json"
    ).is_file()
    return has_weights and has_tokenizer and "model_type" in config


def discover_models() -> list[dict]:
    """Local MLX model folders under the configured roots (no loading, no downloading)."""
    found = []
    for root in model_dirs():
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*")) + sorted(root.glob("*/*")):
            if path.is_dir() and _is_mlx_model(path):
                config = json.loads((path / "config.json").read_text())
                size = sum(p.stat().st_size for p in path.glob("*.safetensors"))
                found.append(
                    {
                        "id": path.relative_to(root).as_posix(),
                        "path": str(path),
                        "model_type": config.get("model_type"),
                        "quantization": config.get("quantization"),
                        "size_gb": round(size / 1e9, 1),
                    }
                )
    return found


def resolve_model(model_id: str) -> Path | None:
    for root in model_dirs():
        candidate = (root / model_id).resolve()
        if root.resolve() in candidate.parents and _is_mlx_model(candidate):
            return candidate
    return None


def files_digest(path: Path) -> str:
    """Provenance digest: full content of small files (config, tokenizer, template, index)
    plus name/size/mtime of weight shards. It identifies the installed files; it is not a
    content hash of the weights."""
    digest = hashlib.sha256()
    for item in sorted(path.iterdir()):
        if not item.is_file() or item.name.startswith("."):
            continue
        digest.update(item.name.encode() + b"\0")
        if item.suffix == ".safetensors":
            stat = item.stat()
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
        elif item.stat().st_size < 64 << 20:
            digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def profile_for(model_id: str, settings: MLXSettings | None = None) -> dict:
    """The provider profile recorded in a run's sealed manifest at creation."""
    path = resolve_model(model_id)
    if path is None:
        raise ProviderError("config", f"Local MLX model {model_id!r} was not found")
    python = mlx_python()
    runtime = runtime_status(python)
    if not runtime["available"]:
        raise ProviderError("config", f"MLX runtime unavailable: {runtime.get('reason')}")
    return {
        "kind": "mlx",
        "model_id": model_id,
        "model_path": str(path),
        "files_digest": files_digest(path),
        "runtime": {k: runtime[k] for k in ("python", "mlx", "mlx_lm")},
        "settings": (settings or MLXSettings()).to_dict(),
        "prompt_version": DECISION_PROMPT_VERSION,
        "license": "unknown (see the model's own files)",
    }


# -- provider -----------------------------------------------------------------------------------


class MLXProvider(Provider):
    def __init__(
        self,
        model_id: str,
        profile: dict | None = None,
        *,
        python: str | None = None,
        worker_script: Path | None = None,
        settings: MLXSettings | None = None,
    ):
        profile = profile or {}
        self.settings = settings or MLXSettings(**profile.get("settings", {}))
        path = resolve_model(model_id)
        self.model_path = str(path) if path else profile.get("model_path")
        self.python = python or mlx_python()
        # GENESIS_MLX_WORKER substitutes a protocol-compatible worker (used by tests).
        self.worker_script = worker_script or Path(
            os.environ.get("GENESIS_MLX_WORKER", WORKER_SCRIPT)
        )
        self.identity = ProviderIdentity(
            "mlx", model_id, model=self.model_path, revision=profile.get("files_digest")
        )
        self.capabilities = Capabilities(
            "none", self.settings.max_context_tokens, self.settings.max_output_tokens, 1
        )
        self._proc: subprocess.Popen | None = None
        self._messages: queue.Queue = queue.Queue()
        self._lock = threading.Lock()  # one active request
        self._status = "stopped"
        self._ready: dict = {}
        self._metrics = {"requests": 0, "failures": 0, "restarts": 0, "last_latency_ms": None}
        self.calls = 0

    # -- worker lifecycle ---------------------------------------------------------------------
    def _reader(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                self._messages.put((proc.pid, json.loads(line)))
            except ValueError:
                continue
        self._messages.put((proc.pid, {"type": "eof"}))

    def _next(self, pid: int, deadline: float, cancel: threading.Event) -> dict | None:
        while time.monotonic() < deadline:
            if cancel.is_set():
                raise ProviderError("unavailable", "Request cancelled")
            try:
                sender, message = self._messages.get(timeout=0.25)
            except queue.Empty:
                continue
            if sender == pid:
                return message
        return None

    def _start(self, cancel: threading.Event) -> None:
        if not self.model_path or not Path(self.model_path).is_dir():
            raise ProviderError("config", f"Model files not found: {self.model_path}")
        if not self.python:
            raise ProviderError("config", "No interpreter with mlx_lm (set GENESIS_MLX_PYTHON)")
        self._status = "loading"
        self._metrics["restarts"] += 1 if self._ready else 0
        try:
            proc = subprocess.Popen(
                [self.python, str(self.worker_script), "--model", self.model_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._status = "stopped"
            raise ProviderError("config", f"Cannot start MLX worker: {exc}") from exc
        self._proc = proc
        threading.Thread(target=self._reader, args=(proc,), daemon=True).start()
        deadline = time.monotonic() + self.settings.load_timeout_s
        while True:
            message = self._next(proc.pid, deadline, cancel)
            if message is None:
                self._kill()
                raise ProviderError("timeout", "Model did not load in time")
            if message["type"] == "ready":
                self._ready = message
                self._status = "ready"
                return
            if message["type"] in ("fatal", "eof"):
                self._kill()
                self._status = "failed"
                raise ProviderError("config", f"MLX worker failed to load: {message.get('error')}")

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._kill()
        self._status = "stopped"

    def health(self) -> dict:
        proc = self._proc
        alive = proc is not None and proc.poll() is None
        return {
            "status": self._status if alive or self._status in ("crashed", "failed") else "stopped",
            "pid": proc.pid if alive else None,
            "load_seconds": self._ready.get("load_seconds"),
            "memory_gb": self._ready.get("memory_gb"),
            "runtime": self._ready.get("runtime"),
            "chat_template_sha256": self._ready.get("chat_template_sha256"),
            **self._metrics,
        }

    # -- requests -----------------------------------------------------------------------------
    def decide(self, request: DecisionRequest, cancel: threading.Event) -> ProviderResponse:
        with self._lock:
            self.calls += 1
            self._metrics["requests"] += 1
            try:
                return self._decide(request, cancel)
            except ProviderError:
                self._metrics["failures"] += 1
                raise

    def _decide(self, request: DecisionRequest, cancel: threading.Event) -> ProviderResponse:
        if self._proc is None or self._proc.poll() is not None:
            self._start(cancel)
        proc = self._proc
        messages = render(request)
        seed = int(request.input_hash[:8], 16)  # recorded; not a determinism guarantee
        nonce = f"{request.request_id}:{time.monotonic_ns()}"
        payload = {
            "type": "generate",
            "id": nonce,
            "messages": messages,
            "max_tokens": self.settings.output_cap(request.purpose),
            "max_context_tokens": self.settings.max_context_tokens,
            "prefix_cache": self.settings.prefix_cache,
            "temperature": self.settings.temperature,
            "top_p": self.settings.top_p,
            "top_k": self.settings.top_k,
            "seed": seed,
            "enable_thinking": self.settings.enable_thinking,
        }
        started = time.monotonic()
        self._status = "busy"
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except OSError as exc:
            self._status = "crashed"
            self._kill()
            raise ProviderError("worker_crashed", f"MLX worker is gone: {exc}") from exc
        deadline = started + self.settings.request_timeout_s
        while True:
            message = self._next(proc.pid, deadline, cancel)
            if message is None:
                self._kill()  # its late output can never reach a request again
                self._status = "stopped"
                raise ProviderError(
                    "timeout", f"No response within {self.settings.request_timeout_s:.0f}s"
                )
            kind = message.get("type")
            if kind == "eof":
                self._status = "crashed"
                self._kill()
                raise ProviderError("worker_crashed", "MLX worker exited during the request")
            if message.get("id") != nonce:
                continue  # never accept output meant for another (earlier) request
            if kind == "error":
                self._status = "ready"
                raise ProviderError(message.get("kind", "generation"), message.get("message", ""))
            if kind == "result":
                self._status = "ready"
                latency = int((time.monotonic() - started) * 1000)
                self._metrics["last_latency_ms"] = latency
                return ProviderResponse(
                    request.request_id,
                    request.input_hash,
                    message["text"],
                    Usage(message.get("prompt_tokens"), message.get("completion_tokens"), latency),
                    finish_reason=message.get("finish_reason"),
                    detail={
                        "prompt_version": prompt_version(request),
                        "messages": messages,
                        "sampling": {
                            "temperature": self.settings.temperature,
                            "top_p": self.settings.top_p,
                            "top_k": self.settings.top_k,
                            "seed": seed,
                            "enable_thinking": self.settings.enable_thinking,
                        },
                        "limits": {
                            "max_context_tokens": self.settings.max_context_tokens,
                            "max_output_tokens": self.settings.output_cap(request.purpose),
                        },
                        "prefix_cache": {
                            "enabled": self.settings.prefix_cache,
                            "cached_tokens": message.get("cached_tokens"),
                        },
                        "runtime": self._ready.get("runtime"),
                        "worker_seconds": message.get("seconds"),
                        "peak_memory_gb": message.get("peak_memory_gb"),
                    },
                )
