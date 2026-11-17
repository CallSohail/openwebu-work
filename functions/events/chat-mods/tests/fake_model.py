"""A tiny OpenAI-compatible model server for testing Mods against a real Open WebUI.

    python tests/fake_model.py 9996

Asked to write a mod (the Mods guide is in the system message), it answers with
a Character Counter mod; asked to change one, with version 2 of it. NOMOD gets
prose and never a mod; REPAIRME first gets a mod that uses fetch, then a good
one once told what was wrong; MESSYMOD gets a mod in the loose format real
models write; SLOWMOD waits six seconds; MODS_FAKE_DELAY adds that many seconds to every mod it writes. Anything else gets a streamed answer long enough
for the Readability mod to score.
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9996
MODELS = [{"id": "fake-modder", "object": "model", "owned_by": "test", "context_length": 32768}]
F = "```"
MOD = F + """mod
name: Character Counter
description: Shows how many characters your message has.
version: 1
permissions: composer.read, ui.status
---
mod.on('composer', text => {
  if (!text) { mod.status.clear(); return; }
  mod.status.set('LABEL' + text.length);
});
""" + F
FIRST = "Here is your mod.\n\n" + MOD.replace("LABEL", "chars: ")
# What real models write: a first try that reaches for the network, and a mod in a looser format
BROKEN = "Sure.\n\n" + MOD.replace("LABEL", "chars: ").replace("mod.on('composer'", "fetch('/x'); mod.on('composer'")
MESSY = """<think>The person wants a counter. I will use mod.status.</think>
Sure! Here's a mod for that:

""" + F + """mod
**Name:** Messy Counter
- description: "Counts characters."
permissions: [ui.status]

""" + F + """javascript
mod.on('composer', text => mod.status.set('messy ' + text.length));
""" + F + """
""" + F + """
Let me know if you want changes!"""
SECOND = "Done.\n\n" + MOD.replace("version: 1", "version: 2").replace("LABEL", "Characters: ")
ANSWER = ("The library opens at eight. Students borrow books for three weeks. "
          "Group rooms are booked online. Printing costs five cents a page. "
          "Ask at the desk if you need help with anything at all.")


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
        messages = body.get("messages") or []
        text = lambda m: m.get("content") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
        last = next((text(m) for m in reversed(messages) if m.get("role") == "user"), "") or ""
        system = " ".join(text(m) for m in messages if m.get("role") == "system")
        first = next((text(m) for m in messages if m.get("role") == "user"), "") or ""
        if "You write mods for Open WebUI" in system:
            time.sleep(float(os.environ.get("MODS_FAKE_DELAY") or 0))
            if "NOMOD" in first:
                reply = "I can't do that as a mod."
            elif "cannot be used" in last:
                reply = FIRST  # the repaired answer
            elif "REPAIRME" in first:
                reply = BROKEN
            elif "MESSYMOD" in first:
                reply = MESSY
            elif "SLOWMOD" in first:
                time.sleep(6)
                reply = FIRST
            else:
                reply = SECOND if "Change it:" in last else FIRST
        elif last.startswith("MODAI"):
            reply = "AI says: " + last[5:].strip()[:100]
        else:
            reply = ANSWER
        if not body.get("stream"):
            return self.send(200, {"id": "x", "object": "chat.completion", "model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        pieces = [reply[i:i + 16] for i in range(0, len(reply), 16)]
        for piece in pieces:
            chunk = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(0.6 / max(1, len(pieces)))
        final = {"id": "x", "object": "chat.completion.chunk", "model": body.get("model"), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(b"data: " + json.dumps(final).encode() + b"\n\ndata: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
