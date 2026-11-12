"""Dedicated MLX-LM inference worker (one resident model, one request at a time).

Run by MLXProvider as a separate process, possibly under a different Python interpreter
that has `mlx` and `mlx_lm` installed. It is self-contained: it imports nothing from
Genesis, never sees world state, and only turns chat messages into raw text.

Protocol: JSON lines. stdout carries protocol messages only; diagnostics go to stderr.
  -> {"type": "starting", "pid": ...}
  -> {"type": "ready", ...runtime identity...}   or   {"type": "fatal", "error": ...}
  <- {"type": "generate", "id", "messages", "max_tokens", "max_context_tokens",
      "temperature", "top_p", "top_k", "seed", "enable_thinking", "prefix_cache"}
  -> {"type": "result", "id", "text", "prompt_tokens", "cached_tokens",
      "completion_tokens", "finish_reason", "seconds", "peak_memory_gb"}
     or   {"type": "error", "id", ...}

Prefix cache (an inference optimisation, no effect on what is asked): requests share long
identical beginnings (system prompt and action rules). The worker keeps up to four
snapshots of the model state after such a shared prefix and starts each request from a
copy of the longest matching one, so only the differing tail is computed. Copies are
used because hybrid-attention caches cannot be trimmed. Results can differ from uncached
inference only by floating-point rounding.
"""

import argparse
import copy
import hashlib
import json
import os
import platform
import sys
import time

# Run as a script, this file's directory is first on sys.path; it contains Genesis's own
# `mlx.py` provider module, which must never shadow Apple's `mlx` package here.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if os.path.abspath(p or os.curdir) != _HERE]

_out = sys.stdout


def send(message: dict) -> None:
    _out.write(json.dumps(message, ensure_ascii=True) + "\n")
    _out.flush()


def file_sha256(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


SNAPSHOTS = 4  # cached shared prefixes (decisions and reflections each have their own)
MIN_PREFIX = 128  # shorter shared beginnings are not worth a snapshot
PREFILL_CHUNK = 512


def common_length(a: list[int], b: list[int]) -> int:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n


class PrefixCache:
    """Snapshots of the model state after shared prompt prefixes (most recent last)."""

    def __init__(self, model, make_cache, mx):
        self.model, self.make_cache, self.mx = model, make_cache, mx
        self.snapshots: list[tuple[list[int], list]] = []
        self.recent: list[list[int]] = []  # recent prompts, to discover shared prefixes

    def start(self, tokens: list[int]) -> tuple[list | None, int]:
        """A cache primed with the longest known prefix of `tokens`, and its length."""
        best = max(
            (s for s in self.snapshots if len(s[0]) < len(tokens) and tokens[: len(s[0])] == s[0]),
            key=lambda s: len(s[0]),
            default=None,
        )
        if best is None:
            shared = max((common_length(tokens, r) for r in self.recent), default=0)
            shared = min(shared, len(tokens) - 1)
            if shared >= MIN_PREFIX:
                best = (tokens[:shared], self._prefill(tokens[:shared]))
                self.snapshots = [*self.snapshots, best][-SNAPSHOTS:]
        self.recent = [*self.recent, tokens][-SNAPSHOTS:]
        if best is None:
            return None, 0
        return copy.deepcopy(best[1]), len(best[0])

    def _prefill(self, tokens: list[int]) -> list:
        cache = self.make_cache(self.model)
        for i in range(0, len(tokens), PREFILL_CHUNK):
            self.model(self.mx.array(tokens[i : i + PREFILL_CHUNK])[None], cache=cache)
            self.mx.eval([c.state for c in cache])
        return cache


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    sys.stdout = sys.stderr  # library prints must never corrupt the protocol stream
    send({"type": "starting", "pid": os.getpid()})
    started = time.monotonic()
    try:
        import importlib.metadata as metadata

        import mlx.core as mx
        from mlx_lm import load, stream_generate
        from mlx_lm.models.cache import make_prompt_cache
        from mlx_lm.sample_utils import make_sampler

        model, tokenizer = load(args.model)
        prefixes = PrefixCache(model, make_prompt_cache, mx)
    except Exception as exc:  # noqa: BLE001 - reported to the provider as a fatal error
        send({"type": "fatal", "error": f"{type(exc).__name__}: {exc}"[:500]})
        return 2
    template = os.path.join(args.model, "chat_template.jinja")
    send(
        {
            "type": "ready",
            "pid": os.getpid(),
            "load_seconds": round(time.monotonic() - started, 2),
            "memory_gb": round(mx.get_active_memory() / 1e9, 2),
            "runtime": {
                "python": platform.python_version(),
                "mlx": metadata.version("mlx"),
                "mlx_lm": metadata.version("mlx-lm"),
                "platform": platform.platform(),
            },
            "chat_template_sha256": file_sha256(template),
        }
    )
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except ValueError:
            send({"type": "error", "id": None, "kind": "protocol", "message": "bad JSON"})
            continue
        if request.get("type") == "shutdown":
            return 0
        rid = request.get("id")
        try:
            prompt = tokenizer.apply_chat_template(
                request["messages"],
                add_generation_prompt=True,
                tokenize=False,
                enable_thinking=bool(request.get("enable_thinking", False)),
            )
            tokens = list(tokenizer.encode(prompt))
            prompt_tokens = len(tokens)
            max_tokens = int(request["max_tokens"])
            if prompt_tokens + max_tokens > int(request["max_context_tokens"]):
                send(
                    {
                        "type": "error",
                        "id": rid,
                        "kind": "context_overflow",
                        "message": f"{prompt_tokens} prompt + {max_tokens} output tokens "
                        f"exceed {request['max_context_tokens']}",
                    }
                )
                continue
            mx.random.seed(int(request["seed"]))
            sampler = make_sampler(
                temp=float(request["temperature"]),
                top_p=float(request["top_p"]),
                top_k=int(request["top_k"]),
            )
            begun = time.monotonic()
            cache, cached = (None, 0)
            if request.get("prefix_cache", False):
                cache, cached = prefixes.start(tokens)
            text, last = "", None
            extra = {} if cache is None else {"prompt_cache": cache}
            for chunk in stream_generate(
                model,
                tokenizer,
                mx.array(tokens[cached:]),
                max_tokens=max_tokens,
                sampler=sampler,
                **extra,
            ):
                text += chunk.text
                last = chunk
            send(
                {
                    "type": "result",
                    "id": rid,
                    "text": text,
                    "prompt_tokens": prompt_tokens,
                    "cached_tokens": cached,
                    "completion_tokens": None if last is None else last.generation_tokens,
                    "finish_reason": None if last is None else last.finish_reason,
                    "seconds": round(time.monotonic() - begun, 3),
                    "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 2),
                }
            )
        except Exception as exc:  # noqa: BLE001 - a failed request, not a dead worker
            send({"type": "error", "id": rid, "kind": "generation", "message": str(exc)[:500]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
