"""Protocol-compatible stand-in for providers/mlx_worker.py (no MLX, no model).

Behaviour per request comes from FAKE_MLX_SCRIPT, a comma list consumed in order across
worker restarts (position kept in FAKE_MLX_STATE):
  valid | invalid | truncate | hang | crash | overflow | slow
FAKE_MLX_LOAD=fatal makes loading fail.
"""

import json
import os
import sys
import time


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def next_behaviour():
    script = os.environ.get("FAKE_MLX_SCRIPT", "valid").split(",")
    state = os.environ["FAKE_MLX_STATE"]
    index = int(open(state).read() or 0) if os.path.exists(state) else 0
    with open(state, "w") as handle:
        handle.write(str(index + 1))
    return script[min(index, len(script) - 1)]


send({"type": "starting", "pid": os.getpid()})
if os.environ.get("FAKE_MLX_LOAD") == "fatal":
    send({"type": "fatal", "error": "FileNotFoundError: weights missing"})
    sys.exit(2)
send(
    {
        "type": "ready",
        "pid": os.getpid(),
        "load_seconds": 0.01,
        "memory_gb": 0.0,
        "runtime": {"python": "fake", "mlx": "fake", "mlx_lm": "fake", "platform": "test"},
        "chat_template_sha256": None,
    }
)
for line in sys.stdin:
    request = json.loads(line)
    if request["type"] == "shutdown":
        sys.exit(0)
    behaviour = next_behaviour()
    rid = request["id"]
    with open(os.environ["FAKE_MLX_STATE"] + ".prompts", "a") as log:
        log.write(json.dumps(request["messages"]) + "\n")
    if behaviour == "crash":
        os._exit(9)
    if behaviour == "hang":
        time.sleep(3600)
    if behaviour == "overflow":
        send({"type": "error", "id": rid, "kind": "context_overflow", "message": "too long"})
        continue
    if behaviour == "slow":
        time.sleep(0.4)
    text = {
        "valid": '{"action": "wait", "explanation": "fake worker"}',
        "slow": '{"action": "wait"}',
        "invalid": "Sure! I will wait.",
        "truncate": '{"action": "extract", "resource": "mat',
    }[behaviour]
    send(
        {
            "type": "result",
            "id": rid,
            "text": text,
            "prompt_tokens": 100,
            "completion_tokens": 12,
            "finish_reason": "length" if behaviour == "truncate" else "stop",
            "seconds": 0.01,
            "peak_memory_gb": 0.0,
        }
    )
