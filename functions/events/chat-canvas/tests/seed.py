"""Seed a throwaway Open WebUI with what the Chat Canvas browser tests need.

    python tests/seed.py http://127.0.0.1:8080 chat_canvas.py [chat_files.py chat_gallery.py] > seed.json

Run it against an instance with no users yet: the first account it makes
becomes the administrator. Extra sources are installed too, to test the tabs
side by side. Never point it at an instance people use.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1].rstrip("/")
SOURCES = sys.argv[2:]
PASSWORD = "canvas-test-password"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, token=None, body=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path} -> {error.code}: {error.read()[:300]!r}")
    return json.loads(raw) if raw else None


admin = call("POST", "/api/v1/auths/signup", body={"name": "Canvas Admin", "email": "admin@example.test", "password": PASSWORD})
admin_token = admin["token"]
installed = []
for path in SOURCES:
    source = open(path, encoding="utf-8").read()
    function_id = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    call("POST", "/api/v1/functions/create", admin_token, {"id": function_id, "name": function_id, "content": source, "meta": {"description": function_id + " under test"}})
    call("POST", f"/api/v1/functions/id/{function_id}/toggle", admin_token)
    installed.append(function_id)
call("POST", "/api/v1/auths/add", admin_token, {"name": "Canvas User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user_token = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]

F3, F4 = "`" * 3, "`" * 4
PROSE = (
    "# Project plan\n\n"
    "This plan covers the **first release**. It has *three* phases and one ~~dropped~~ idea. See [the guide](https://docs.example.test/guide) "
    "and never follow [this](javascript:alert(1)). Raw HTML such as <img src=x onerror=\"window.__pwned=1\"> must stay text.\n\n"
    "## Phases\n\n- Research the options\n  - Compare two vendors\n- Build the prototype\n- [x] Agree the budget\n\n"
    "1. First step\n2. Second step\n\n"
    "> A quoted remark that matters.\n\n"
    "| Phase | Weeks |\n| --- | --- |\n| Research | 2 |\n| Build | 6 |\n\n"
    + "The remaining paragraphs describe risks, owners and dates in enough detail to make this a real document. " * 12
)
ANSWER = (
    '<details type="reasoning" done="true">\n<summary>Thought</summary>\n' + F3 + "python\nhidden = 1\nhidden = 2\nhidden = 3\n" + F3 + "\n</details>\n\n"
    + PROSE + "\n\n"
    + F3 + 'python title="app.py"\ndef greet(name):\n    return "Hello " + name\n\n\nprint(greet("world"))\n' + F3 + "\n\n"
    + F4 + "markdown\n# Readme\n\nInstall with:\n\n" + F3 + "bash\npip install demo\n" + F3 + "\n\nThen run it.\n" + F4 + "\n\n"
    + "~~~sql\nSELECT id, name\nFROM users\nWHERE active = 1;\n~~~\n\n"
    + F3 + "bash\necho one-liner\n" + F3 + "\n\n"
    + "- A step with code:\n  " + F3 + "js\n  const a = 1;\n  const b = 2;\n  console.log(a + b);\n  " + F3 + "\n\n"
    + F3 + "html\n<!doctype html>\n<html><head><title>Demo page</title></head>\n<body><h1 id=\"h\">Hello</h1>\n<script>\ndocument.getElementById('h').textContent = 'Script ran';\ntry { parent.document.documentElement.dataset.cvPwned = '1'; } catch (e) {}\ntry { top.__pwned = 1; } catch (e) {}\ntry { parent.localStorage.setItem('cvPwned', '1'); } catch (e) {}\n</script>\n</body></html>\n" + F3 + "\n\n"
    + F3 + "xml\n<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"120\" height=\"80\">\n  <rect width=\"120\" height=\"80\" fill=\"#3366cc\"/>\n  <script>top.__pwned = 1</script>\n</svg>\n" + F3 + "\n\n"
    + F3 + "json\n{\"name\": \"demo\",\n \"items\": [1, 2, 3],\n \"ok\": true}\n" + F3 + "\n\n"
    + F3 + "csv\nname,city\n\"Smith, Jane\",Évry\nAli,القاهرة\n" + F3 + "\n\n"
    + F3 + "jinja\n<h1>{{ user.name }}</h1>\n<p>{{CLIPBOARD}}</p>\n{% if ok %}yes{% endif %}\n" + F3 + "\n\n"
    + F3 + "markdown\n# تقرير\n\nهذا مستند باللغة العربية.\n\n- بند أول\n- بند ثان\n" + F3 + "\n"
)
SECOND = (
    "Here is the updated file.\n\n"
    + F3 + 'python title="app.py"\ndef greet(name, punctuation="!"):\n    return "Hello " + name + punctuation\n\n\nprint(greet("world"))\nprint(greet("again"))\n' + F3 + "\n\n"
    + "And one I am still writing:\n\n" + F3 + "python\nwhile True:\n    step()\n    wait(\n"
)
BIG = F3 + "python\n" + "".join(f"value_{i:05d} = {i}  # filler line\n" for i in range(25000)) + F3 + "\n"


def make_chat(token, title, turns):
    now = int(time.time())
    messages, previous = {}, None
    for n, (role, content, extra) in enumerate(turns):
        mid = f"m{n + 1}"
        messages[mid] = {"id": mid, "parentId": previous, "childrenIds": [], "role": role, "timestamp": now - 600 + n * 10, "content": content, **({"model": "test-model", "done": True} if role == "assistant" else {}), **extra}
        if previous:
            messages[previous]["childrenIds"].append(mid)
        previous = mid
    chat = call("POST", "/api/v1/chats/new", token, {"chat": {"id": str(uuid.uuid4()), "title": title, "models": ["test-model"], "params": {}, "files": [], "tags": [],
                                                                "history": {"messages": messages, "currentId": previous}, "messages": [], "timestamp": now * 1000}})
    return chat["id"]


def seed(token):
    return {
        "chat": make_chat(token, "Canvas test chat", [
            ("user", "Please review this:\n\n" + F3 + "python\nimport os\nfor name in os.listdir('.'):\n    print(name)\n" + F3, {}),
            ("assistant", ANSWER, {}),
            ("user", "Add punctuation please.", {}),
            ("assistant", SECOND, {"done": False}),
        ]),
        "big": make_chat(token, "Big chat", [("user", "big one", {}), ("assistant", BIG, {})]),
        "other": make_chat(token, "Other chat", [("user", "hi", {}), ("assistant", F3 + "css\nbody {\n  margin: 0;\n}\n" + F3, {})]),
        "empty": make_chat(token, "Empty chat", [("user", "hello", {}), ("assistant", "Hi! `inline code` only, and a short reply.", {})]),
    }


print(json.dumps({
    "base": BASE, "password": PASSWORD, "installed": installed,
    "admin": {"email": "admin@example.test", "token": admin_token, **seed(admin_token)},
    "user": {"email": "user@example.test", "token": user_token, **seed(user_token)},
}, indent=2))
