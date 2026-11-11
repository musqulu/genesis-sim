"""A local HTTP fixture speaking OpenAI-style chat-completions and Replicate predictions.

Behaviours are scripted per call; every request is recorded. No network is used.
"""

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VALID = '{"action": "wait", "explanation": "fake api"}'
REPLICATE_OUTPUT = ['{"action": ', '"wait", ', '"explanation": "replicate"}']  # streamed parts
VERSION = "v" + "0" * 63
INPUT_SCHEMA = {
    "prompt": {"type": "string"},
    "system_prompt": {"type": "string"},
    "max_new_tokens": {"type": "integer"},
    "temperature": {"type": "number"},
    "top_p": {"type": "number"},
    "seed": {"type": "integer"},
}


class FakeAPI:
    def __init__(self):
        self.requests: list[dict] = []
        self.script: deque[str] = deque()
        self.list_models: list[str] | None = None  # None: GET /models is 404
        self.input_schema: dict | None = dict(INPUT_SCHEMA)
        self.hang_s = 3.0
        self.predictions: dict[str, dict] = {}
        self.cancelled: list[str] = []
        self._ids = 0
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, code: int, body, headers: dict | None = None):
                data = (body if isinstance(body, str) else json.dumps(body)).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def _record(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                api.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                return body

            def do_GET(self):
                self._record("GET")
                api.route(self, "GET")

            def do_POST(self):
                body = self._record("POST")
                api.route(self, "POST", body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def next(self, default="valid") -> str:
        return self.script.popleft() if self.script else default

    def errors(self, h, behaviour) -> bool:
        if behaviour in ("401", "403"):
            # echo the Authorization header back, as a careless server might
            h._reply(int(behaviour), {"error": f"bad key {h.headers.get('Authorization')}"})
        elif behaviour == "429":
            h._reply(429, {"error": "slow down"}, {"Retry-After": "0"})
        elif behaviour == "500":
            h._reply(500, {"error": "boom"})
        elif behaviour == "hang":
            time.sleep(self.hang_s)
            h._reply(200, {"error": "too late"})
        elif behaviour == "notjson":
            h._reply(200, "<html>proxy</html>")
        else:
            return False
        return True

    def route(self, h, method, body=None):
        path = h.path.split("?")[0]
        if method == "GET" and path == "/v1/models":
            if self.list_models is None:
                return h._reply(404, {"error": "not found"})
            return h._reply(200, {"data": [{"id": m} for m in self.list_models]})
        if method == "POST" and path == "/v1/chat/completions":
            return self.chat(h, body)
        if method == "GET" and path.startswith("/v1/models/"):
            schema = (
                None
                if self.input_schema is None
                else {"components": {"schemas": {"Input": {"properties": self.input_schema}}}}
            )
            return h._reply(200, {"latest_version": {"id": VERSION, "openapi_schema": schema}})
        if method == "POST" and path == "/v1/predictions":
            return self.predict(h, body)
        if method == "GET" and path.startswith("/v1/predictions/"):
            pid = path.rsplit("/", 1)[1]
            p = self.predictions[pid]
            if p["mode"] == "poll":
                p["status"] = "succeeded"
            return h._reply(200, self.prediction(pid))
        if method == "POST" and path.endswith("/cancel"):
            pid = path.split("/")[-2]
            self.cancelled.append(pid)
            self.predictions[pid]["status"] = "canceled"
            return h._reply(200, self.prediction(pid))
        h._reply(404, {"error": "no route"})

    def chat(self, h, body):
        behaviour = self.next()
        if self.errors(h, behaviour):
            return
        content = {
            "valid": VALID,
            "nousage": VALID,
            "invalid": "Sure! I will wait.",
            "truncate": '{"action": "wa',
        }
        if behaviour == "nochoices":
            return h._reply(200, {"id": "x", "choices": []})
        reply = {
            "id": "chatcmpl-1",
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content[behaviour]},
                    "finish_reason": "length" if behaviour == "truncate" else "stop",
                }
            ],
        }
        if behaviour != "nousage":
            reply["usage"] = {"prompt_tokens": 120, "completion_tokens": 14}
        h._reply(200, reply)

    def prediction(self, pid):
        p = self.predictions[pid]
        done = p["status"] == "succeeded"
        return {
            "id": pid,
            "status": p["status"],
            "output": (['{"action": ', '"wait", ', '"explanation": "replicate"}'])
            if done
            else None,
            "error": "model crashed" if p["status"] == "failed" else None,
            "metrics": {"input_token_count": 130, "output_token_count": 15, "predict_time": 0.4}
            if done
            else {},
        }

    def predict(self, h, body):
        behaviour = self.next("rvalid")
        if self.errors(h, behaviour):
            return
        self._ids += 1
        pid = f"pred{self._ids}"
        status = {"rvalid": "succeeded", "rfail": "failed"}.get(behaviour, "starting")
        mode = {"rpoll": "poll", "rhang": "hang"}.get(behaviour, "done")
        self.predictions[pid] = {"status": status, "mode": mode, "input": body["input"]}
        h._reply(201, self.prediction(pid))
