"""A tiny OpenAI-compatible model server for testing Chat Inspector against a real Open WebUI.

    python tests/fake_model.py 9999

It lists three models: one that reports its context window, one that doesn't,
and one with an Arabic-script name. Answers stream word by word, and the last
chunk carries usage the way OpenAI reports it. The prompt count is the
messages' length / 4 plus 300, standing in for the tools and instructions a
server adds, so the Inspector's inferred share can be checked. A message
containing SLOW streams over about five seconds; one containing NOUSAGE
reports no usage at all.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
MODELS = [
    {"id": "fake-gpt-4o", "object": "model", "owned_by": "test", "context_length": 8192},
    {"id": "fake-mini", "object": "model", "owned_by": "test"},
    {"id": "llama3.1:8b", "object": "model", "owned_by": "test", "top_provider": {"context_length": 131072}},
]
EXTRA = 300


def prompt_tokens(messages):
    return len(json.dumps(messages, ensure_ascii=False)) // 4 + EXTRA


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self.send(200, {"object": "list", "data": MODELS})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        text = last.get("content") if isinstance(last.get("content"), str) else json.dumps(last.get("content"))
        # Open WebUI asks for titles, tags and follow-ups as JSON; a short valid reply keeps it quiet
        if not body.get("stream") or "### Task" in (text or ""):
            reply = '{"title": "Test chat", "tags": ["test"], "follow_ups": []}' if "### Task" in (text or "") else "Short reply."
            return self.send(200, {"id": "x", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": prompt_tokens(messages), "completion_tokens": 5, "total_tokens": prompt_tokens(messages) + 5}})
        words = ("This is a streamed answer from the fake model about " + (text or "")[:60] + ". " + "It keeps going for a while. " * (8 if "SLOW" in (text or "") else 2)).split(" ")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        delay = 5.0 / len(words) if "SLOW" in (text or "") else 0.02
        for word in words:
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(delay)
        final = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        if "NOUSAGE" not in (text or ""):
            p, c = prompt_tokens(messages), len(words) + 12
            final["usage"] = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c,
                              "completion_tokens_details": {"reasoning_tokens": 12}, "prompt_tokens_details": {"cached_tokens": 64}}
        self.wfile.write(b"data: " + json.dumps(final).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
