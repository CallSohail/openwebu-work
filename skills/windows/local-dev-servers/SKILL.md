---
name: local-dev-servers
description: Build and run web pages, apps and APIs on the user's Windows PC and test them locally, with Python http.server, Flask, FastAPI, Node.js or Vite. Use when the user asks to make a website or app, start a local server, open localhost, test an API, or stop a running server.
---

# Local Dev Servers

Follow the `windows-safe-terminal` skill as well. Put each project in its own folder inside `C:\Users\Pret\Desktop\Tether`.

## Starting a server

1. Check the port is free first: `netstat -ano | findstr :5500`. If it's taken, pick the next port (5501, 5502…) instead of killing the other process.
2. Bind to `127.0.0.1` only, never `0.0.0.0`, so the server isn't reachable from the network.
3. Start servers **in the background**, because they never finish on their own. Keep the process id.
4. Wait 2–3 seconds, then check the server answers: `curl -s -o NUL -w "%{http_code}" http://127.0.0.1:5500/`.
5. Give the user the URL, and open it in the browser tools if they want to see it.

| Stack | Start command |
| --- | --- |
| Static files | `python -m http.server 5500 --bind 127.0.0.1` |
| Flask | `python app.py` with `app.run(host="127.0.0.1", port=5000)` |
| FastAPI | `python -m uvicorn main:app --host 127.0.0.1 --port 8001` (port 8000 may already be used by Open Terminal) |
| Node / Express | `node server.js` listening on `127.0.0.1` |
| Vite | `npm create vite@latest app -- --template react`, then `npm install` and `npm run dev -- --host 127.0.0.1` |

## Testing

- APIs: test every endpoint with `curl` and show the status code and response body.
- Pages: open in the browser tools, take a snapshot, check the main elements exist, and take a screenshot.
- For a phone view, set the browser viewport to about 390×844 before the screenshot.

## Stopping

- Stop servers you started with `kill_process`, or `taskkill /PID <pid> /F` for that exact PID.
- Never kill processes you didn't start, and never kill by name (`taskkill /IM python.exe` would also stop Open Terminal itself).
- After stopping, confirm the port is free again.
- At the end of every answer, list servers still running, with URL and PID.

## Security

- No real secrets in code. Use placeholder values and tell the user where to put real ones.
- Use debug mode only on 127.0.0.1.
- Don't open firewall ports or expose servers to the internet or LAN without the user's explicit request.
