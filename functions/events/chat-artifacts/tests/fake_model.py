"""A tiny OpenAI-compatible model server for testing Artifacts against a real Open WebUI.

    python tests/fake_model.py 9999

WHATMODE says whether the Artifacts mode instructions reached it. A message containing MAKEPAGE gets a streamed HTML page in a code block, over
about three seconds, so the live view and auto-open can be seen. FIXIT gets a
corrected page with the same title, as a new version. Anything else gets a
short streamed reply. Every reply reports usage.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
MODELS = [{"id": "fake-artist", "object": "model", "owned_by": "test", "context_length": 32768}]
PAGE = """Here is the page.

```html
<!doctype html>
<html>
<head><title>Live counter</title></head>
<body>
  <h1 id="n">0</h1>
  <button onclick="document.getElementById('n').textContent = +document.getElementById('n').textContent + 1">Add one</button>
  <p>Built while you watched.</p>
</body>
</html>
```

Press the button to count."""
FIXED = PAGE.replace("Built while you watched.", "Fixed version.").replace("Here is the page.", "Here is the fixed page.")


def tone(seconds, rate=24000):
    """A mono 16-bit WAV of a soft 220 Hz tone that fades in and out."""
    import math
    import struct

    count = int(seconds * rate)
    frames = bytearray()
    for i in range(count):
        envelope = min(1.0, i / (0.05 * rate), (count - i) / (0.05 * rate))
        frames += struct.pack("<h", int(9000 * envelope * math.sin(2 * math.pi * 220 * i / rate)))
    header = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(frames))
    return header + bytes(frames)


class Handler(BaseHTTPRequestHandler):
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

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        # Text to speech for Artifacts' narrated animations: a tone as long as the words would take to say
        if self.path.rstrip("/").endswith("/audio/speech"):
            words = max(1, len(str(body.get("input", "")).split()))
            data = tone(max(0.8, 0.32 * words))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        messages = body.get("messages") or []
        last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
        text = last.get("content") if isinstance(last.get("content"), str) else json.dumps(last.get("content"))
        text = text or ""
        system = " ".join(m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content")) for m in messages if m.get("role") == "system")
        if "WHATMODE" in text:
            reply, seconds = ("Artifacts mode is on." if "<!-- artifacts-mode -->" in system else "Artifacts mode is off."), 0.2
        elif "MAKEPAGE" in text:
            reply, seconds = PAGE, 3.0
        elif "FIXIT" in text or "error" in text.lower() and "artifact" in text.lower():
            reply, seconds = FIXED, 1.0
        else:
            reply, seconds = "Answer from the fake model: " + text[:200], 0.3
        usage = {"prompt_tokens": len(json.dumps(messages)) // 4, "completion_tokens": len(reply) // 4, "total_tokens": len(json.dumps(messages)) // 4 + len(reply) // 4}
        if not body.get("stream"):
            return self.send(200, {"id": "x", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}], "usage": usage})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        pieces = [reply[i:i + 12] for i in range(0, len(reply), 12)]
        for piece in pieces:
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(seconds / max(1, len(pieces)))
        final = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage}
        self.wfile.write(b"data: " + json.dumps(final).encode() + b"\n\ndata: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
