"""An OpenAI-compatible fake model for the Chat Pets browser tests.

    python tests/fake_model.py 9999

Words in the user's message choose what happens:

    SLOW       stream the answer over about five seconds
    REASON     stream reasoning first, then the answer
    ERROR500   fail with HTTP 500 before streaming
    RATELIMIT  fail with HTTP 429
    CUTOFF     stream a few words, then drop the connection
    TOOLS x y  call each offered tool whose name appears in the message, then answer

Built-in tools get plausible arguments, so Open WebUI really runs them.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
MODELS = [
    {"id": "fake-gpt-4o", "object": "model", "owned_by": "test", "context_length": 8192},
    {"id": "fake-mini", "object": "model", "owned_by": "test"},
]
ARGS = {
    "add": {"a": 2, "b": 3},
    "write_note": {"title": "Pet test note", "content": "Written by the fake model."},
    "add_memory": {"content": "Likes tea in the afternoon."},
    "search_memories": {"query": "tea"},
    "search_notes": {"query": "test"},
    "create_tasks": {"tasks": [{"content": "Check the pet", "status": "in_progress"}, {"content": "Write the report"}]},
    "search_calendar_events": {"query": "", "start": "2026-11-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"},
    "create_calendar_event": {"title": "Pet review", "start": "2030-12-02T10:00:00Z", "end": "2030-12-02T11:00:00Z"},
    "create_automation": {"name": "Daily pet check", "prompt": "Say hello", "rrule": ""},
    "list_automations": {},
    "timer": {"prompt": "Timer done", "at": "1h"},
    "get_current_timestamp": {},
    "search_chats": {"query": "test"},
    "list_skills": {},
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self.send(200, {"object": "list", "data": MODELS})
        self.send(404, {"error": "not found"})

    def chunk(self, model, delta, finish=None, usage=None):
        body = {"id": "x", "object": "chat.completion.chunk", "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage:
            body["usage"] = usage
        self.wfile.write(b"data: " + json.dumps(body).encode() + b"\n\n")
        self.wfile.flush()

    def start_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        model = body.get("model")
        messages = body.get("messages") or []
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        text = last_user.get("content") if isinstance(last_user.get("content"), str) else json.dumps(last_user.get("content"))
        text = text or ""
        if not body.get("stream") or "### Task" in text:
            reply = '{"title": "Pet test", "tags": ["test"], "follow_ups": []}' if "### Task" in text else "Short reply."
            return self.send(200, {"id": "x", "object": "chat.completion", "model": model,
                                   "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        if "ERROR500" in text:
            return self.send(500, {"error": {"message": "The provider had an internal error.", "type": "server_error"}})
        if "RATELIMIT" in text:
            return self.send(429, {"error": {"message": "Rate limit reached for requests.", "type": "rate_limit_error"}})
        offered = [t.get("function", {}).get("name") for t in body.get("tools") or [] if isinstance(t, dict)]
        last_user_index = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        answered = any(m.get("role") == "tool" and str(m.get("tool_call_id", "")).startswith("call_") for m in messages[last_user_index + 1:])
        if not answered and "TOOLS" in text and offered:
            wanted = [name for name in offered if name and f" {name}" in f" {text} "]
            if wanted:
                calls = [{"index": i, "id": f"call_{i}_{name}", "type": "function",
                          "function": {"name": name, "arguments": json.dumps(ARGS.get(name, {}))}} for i, name in enumerate(wanted)]
                self.start_stream()
                self.chunk(model, {"role": "assistant", "tool_calls": calls})
                time.sleep(0.6)
                self.chunk(model, {}, "tool_calls", {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
        self.start_stream()
        if "REASON" in text:
            for word in "Let me think about this carefully before answering.".split(" "):
                self.chunk(model, {"reasoning_content": word + " "})
                time.sleep(0.25)
        words = ("This is a streamed answer from the fake model about " + text[:60] + ". " + "It keeps going for a while. " * (8 if "SLOW" in text else 2)).split(" ")
        delay = 5.0 / len(words) if "SLOW" in text else 0.03
        for i, word in enumerate(words):
            if "CUTOFF" in text and i == 6:
                self.wfile.flush()
                self.connection.shutdown(2)
                return
            self.chunk(model, {"content": word + " "})
            time.sleep(delay)
        self.chunk(model, {}, "stop", {"prompt_tokens": 40, "completion_tokens": len(words), "total_tokens": 40 + len(words)})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
