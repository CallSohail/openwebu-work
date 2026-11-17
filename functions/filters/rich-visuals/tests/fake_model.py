"""An OpenAI-compatible model for testing Rich Visuals against a real Open WebUI 0.13.4.

    python tests/fake_model.py 9994

When it is offered render_visualization and asked about the weather, it answers the way models do: a sentence, then a
streamed tool call whose HTML uses icon tokens and an emoji, cut into small pieces. After the tool result it writes a
closing sentence with an emoji. The visual's script holds an icon token in a single-quoted string, which must
not break it. Anything else gets a short reply with an emoji. GET /log returns, for each request,
whether the Rich Visuals instructions and the visualization tool reached it.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9994
MODELS = [{"id": "fake-visual", "object": "model", "owned_by": "test"}]
LOG: list = []
HTML = (
    "<style>.row{display:flex;gap:.5rem;flex-wrap:wrap}.kpi{display:flex;align-items:center;gap:.4rem;padding:.4rem .6rem;"
    "border:1px solid var(--owui-border);border-radius:.5rem}.warn{color:#dc2626}</style>"
    "<div class=\"row\"><div class=\"kpi\">[[icon:thermometer|Temperature]] <b>18 °C</b> average</div>"
    "<div class=\"kpi\">[[icon:rain|Chance of rain]] 40% rain</div><div class=\"kpi\">☀️ 6 h of sun</div>"
    "<div class=\"kpi\">[[icon:wind]] 15 km/h</div>"
    "<div class=\"kpi warn\" title=\"[[icon:alert]] Storm\">[[icon:alert|Warning]] Storm on Friday</div></div>"
    # The case that used to break: an icon token inside a single-quoted JavaScript string
    "<p id=\"note\"></p><script>document.getElementById('note').textContent = '[[icon:sun]] Script ran ☀️';</script>"
)


def text_of(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def json(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self.json(200, {"object": "list", "data": MODELS})
        if self.path.startswith("/log"):
            return self.json(200, LOG)
        self.json(404, {})

    def chunk(self, delta, finish=None):
        body = {"id": "x", "object": "chat.completion.chunk", "model": "fake-visual", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        self.wfile.write(b"data: " + json.dumps(body, ensure_ascii=True).encode() + b"\n\n")
        self.wfile.flush()
        time.sleep(0.01)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        messages = body.get("messages") or []
        system = " ".join(text_of(m) for m in messages if m.get("role") == "system")
        user = next((text_of(m) for m in reversed(messages) if m.get("role") == "user"), "")
        tools = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        after_tool = bool(messages) and messages[-1].get("role") == "tool"
        LOG.append({"user": user[:80], "rich_visuals": "<!-- rich-visuals -->" in system, "icon_rules": "[[icon:name]]" in system,
                    "visual_rules": "A visual helps here" in system, "no_emoji_rule": "No emojis." in system,
                    "tools": tools, "after_tool": after_tool})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.chunk({"role": "assistant"})
        if after_tool:
            for piece in ["Mostly mild ", "✅ with ", "rain on Wednesday. ", "🌧️"]:
                self.chunk({"content": piece})
        elif "render_visualization" in tools and ("temperature" in user.lower() or "weather" in user.lower()):
            for piece in ["Here is ", "this week ", "in Paris 🌤️:"]:
                self.chunk({"content": piece})
            arguments = json.dumps({"title": "Paris this week", "html": HTML}, ensure_ascii=True)
            pieces = [arguments[i:i + 9] for i in range(0, len(arguments), 9)]
            first = True
            for piece in pieces:
                call = {"index": 0, "function": {"arguments": piece}}
                if first:
                    call = {"index": 0, "id": "call_viz_1", "type": "function", "function": {"name": "render_visualization", "arguments": piece}}
                    first = False
                self.chunk({"tool_calls": [call]})
            self.chunk({}, "tool_calls")
            self.wfile.write(b"data: [DONE]\n\n")
            return
        else:
            for piece in ["Hello! ", "👋 How can ", "I help?"]:
                self.chunk({"content": piece})
        self.chunk({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
