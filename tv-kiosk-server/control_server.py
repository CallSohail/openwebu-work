#!/usr/bin/env python3
"""
control_server.py — phone-friendly control panel for the TV kiosk.

Open http://<box-ip>:8080 from any phone/laptop on the same Wi-Fi.
Type a URL (or tap a preset) and the TV instantly switches to it.

Pure Python standard library — no pip installs needed.
Security: intended for your private LAN only. Do not expose port 8080
to the internet.
"""

import os
import html
import signal
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

STATE_DIR = os.environ.get("TV_KIOSK_STATE_DIR", os.path.expanduser("~/.tv-kiosk"))
URL_FILE = os.path.join(STATE_DIR, "url")
PRESETS_FILE = os.path.join(STATE_DIR, "presets.txt")
PORT = int(os.environ.get("TV_KIOSK_CONTROL_PORT", "8080"))
DEFAULT_URL = os.environ.get("TV_KIOSK_DEFAULT_URL", "http://localhost:8080/placeholder")

# Edit these, or put one "Name|URL" per line in ~/.tv-kiosk/presets.txt
DEFAULT_PRESETS = [
    ("OpenWebUI (local)", "http://localhost:3000"),
    ("EveGPT", "https://evegpt.univ-evry.fr"),
    ("Dashboard placeholder", "http://localhost:8080/placeholder"),
]


def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(URL_FILE):
        with open(URL_FILE, "w") as f:
            f.write(DEFAULT_URL)


def read_url():
    try:
        with open(URL_FILE) as f:
            return f.read().strip() or DEFAULT_URL
    except OSError:
        return DEFAULT_URL


def read_presets():
    if os.path.exists(PRESETS_FILE):
        out = []
        with open(PRESETS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or "|" not in line:
                    continue
                name, url = line.split("|", 1)
                out.append((name.strip(), url.strip()))
        if out:
            return out
    return DEFAULT_PRESETS


def set_url(url):
    url = url.strip()
    if not url:
        return
    # Minimal sanity: require a scheme so Chromium treats it as a URL.
    if not (url.startswith("http://") or url.startswith("https://")
            or url.startswith("file://")):
        url = "http://" + url
    with open(URL_FILE, "w") as f:
        f.write(url)
    # Nudge the kiosk loop: kill the running Chromium so it relaunches
    # with the new URL. Ignore errors if nothing is running yet.
    for name in ("chromium", "chromium-browser", "chrome", "google-chrome"):
        subprocess.run(["pkill", "-f", name], check=False)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TV Control</title>
<style>
  :root {{ --bg:#11171B; --panel:#1b2429; --line:#44545B; --text:#E9ECEC;
           --muted:#8C9A9E; --accent:#C9D2D4; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); padding:20px; }}
  h1 {{ font-size:14px; letter-spacing:3px; text-transform:uppercase;
       color:var(--muted); font-weight:600; margin:0 0 20px; }}
  .card {{ background:var(--panel); border:1px solid var(--line);
          border-radius:4px; padding:18px; margin-bottom:16px; }}
  .now {{ font-size:12px; color:var(--muted); letter-spacing:1px;
         text-transform:uppercase; margin-bottom:6px; }}
  .url {{ word-break:break-all; font-size:16px; color:var(--accent); }}
  input[type=text] {{ width:100%; padding:14px; font-size:16px;
         background:var(--bg); color:var(--text); border:1px solid var(--line);
         border-radius:4px; margin-bottom:12px; }}
  button {{ width:100%; padding:14px; font-size:15px; letter-spacing:1px;
         background:var(--accent); color:var(--bg); border:1px solid var(--accent);
         border-radius:4px; font-weight:600; cursor:pointer;
         transition:all 160ms ease-out; }}
  button:hover {{ filter:brightness(1.08); }}
  .preset {{ background:transparent; color:var(--text); border:1px solid var(--line);
         text-align:left; margin-bottom:10px; font-weight:400; }}
  .preset:hover {{ border-color:var(--accent); }}
  .preset small {{ display:block; color:var(--muted); font-size:12px; margin-top:4px;
         word-break:break-all; }}
  label {{ font-size:12px; color:var(--muted); letter-spacing:1px;
         text-transform:uppercase; display:block; margin-bottom:8px; }}
</style>
</head>
<body>
  <h1>TV Control</h1>

  <div class="card">
    <div class="now">Now showing</div>
    <div class="url">{current}</div>
  </div>

  <div class="card">
    <form method="POST" action="/set">
      <label>Show a URL on the TV</label>
      <input type="text" name="url" placeholder="http://... or an IP address"
             autocapitalize="off" autocorrect="off" spellcheck="false">
      <button type="submit">Send to TV</button>
    </form>
  </div>

  <div class="card">
    <label>Presets</label>
    {presets}
  </div>
</body>
</html>"""


def render():
    presets_html = ""
    for name, url in read_presets():
        presets_html += (
            '<form method="POST" action="/set">'
            f'<input type="hidden" name="url" value="{html.escape(url, quote=True)}">'
            f'<button class="preset" type="submit">{html.escape(name)}'
            f'<small>{html.escape(url)}</small></button></form>'
        )
    return PAGE.format(current=html.escape(read_url()), presets=presets_html)


PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{height:100%;margin:0;display:flex;align-items:center;
justify-content:center;background:#11171B;color:#8C9A9E;
font-family:system-ui,sans-serif;letter-spacing:4px;text-transform:uppercase;
font-size:20px}</style></head><body>TV Kiosk ready — set a URL from your phone
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, code=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/placeholder"):
            self._send(PLACEHOLDER)
        elif self.path in ("/", "/index.html"):
            self._send(render())
        else:
            self._send("Not found", code=404, ctype="text/plain")

    def do_POST(self):
        if self.path == "/set":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            params = parse_qs(body)
            url = params.get("url", [""])[0]
            set_url(url)
            # Redirect back to the panel.
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self._send("Not found", code=404, ctype="text/plain")

    def log_message(self, *args):
        pass  # quiet


def main():
    ensure_state()
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"TV control panel on http://0.0.0.0:{PORT}  (open it from your phone)")
    server.serve_forever()


if __name__ == "__main__":
    main()
