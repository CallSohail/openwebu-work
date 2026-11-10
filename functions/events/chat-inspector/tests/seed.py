"""Seed a throwaway Open WebUI with what the Chat Inspector browser tests need.

    python tests/fake_model.py 9999 &
    python tests/seed.py http://127.0.0.1:8080 chat_inspector.py [other plugin sources...] > seed.json

Start Open WebUI with ENABLE_OPENAI_API=true and OPENAI_API_BASE_URL pointing
at the fake model, against an instance with no users yet. Never point it at an
instance people use.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1].rstrip("/")
SOURCES = sys.argv[2:]
PASSWORD = "inspector-test-password"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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


admin = call("POST", "/api/v1/auths/signup", body={"name": "Inspector Admin", "email": "admin@example.test", "password": PASSWORD})
admin_token = admin["token"]
installed = []
for path in SOURCES:
    function_id = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    call("POST", "/api/v1/functions/create", admin_token, {"id": function_id, "name": function_id, "content": open(path, encoding="utf-8").read(), "meta": {"description": function_id}})
    call("POST", f"/api/v1/functions/id/{function_id}/toggle", admin_token)
    installed.append(function_id)
call("POST", "/api/v1/functions/id/chat_inspector/valves/update", admin_token, {"context_windows": "fake-mini = 4000"})
# Plain users see a connection's models only when granted; make the fake models public
for model_id in ("fake-gpt-4o", "fake-mini", "llama3.1:8b"):
    call("POST", "/api/v1/models/create", admin_token, {"id": model_id, "base_model_id": None, "name": model_id, "meta": {"capabilities": {"vision": True, "web_search": model_id != "fake-mini", "citations": True}}, "params": {}, "access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}], "is_active": True})
call("POST", "/api/v1/auths/add", admin_token, {"name": "Inspector User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user_token = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]
call("POST", "/api/v1/users/user/settings/update", user_token, {"ui": {"system": "You are a helpful assistant for students at a French university. Answer clearly and cite sources when you can."}})

LONG = "This paragraph explains the enrolment process, the documents to bring, the deadlines and who to contact. " * 30


def usage(prompt, completion, reasoning=0, cached=0):
    u = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
    if reasoning:
        u["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    if cached:
        u["prompt_tokens_details"] = {"cached_tokens": cached}
    return u


def make_chat(token, title, models, turns, params=None, history_extra=None):
    now = int(time.time())
    messages, previous, current = {}, None, None
    for n, turn in enumerate(turns):
        role, content = turn[0], turn[1]
        extra = turn[2] if len(turn) > 2 else {}
        mid = extra.pop("id", f"m{n + 1}")
        parent = extra.pop("parent", previous)
        message = {"id": mid, "parentId": parent, "childrenIds": [], "role": role, "timestamp": now - 900 + n * 10, "content": content, **extra}
        if role == "assistant":
            message.setdefault("model", models[0])
            message.setdefault("done", True)
        messages[mid] = message
        if parent:
            messages[parent]["childrenIds"].append(mid)
        if not extra.get("side"):
            previous = mid
        current = mid if not extra.get("side") else current
    for m in messages.values():
        m.pop("side", None)
    chat = call("POST", "/api/v1/chats/new", token, {"chat": {"id": str(uuid.uuid4()), "title": title, "models": models, "params": params or {}, "files": [], "tags": [],
                                                                "history": {"messages": messages, "currentId": current}, "messages": [], "timestamp": now * 1000}})
    return chat["id"]


def seed(token):
    out = {}
    out["usage"] = make_chat(token, "Counted chat", ["fake-gpt-4o"], [
        ("user", "How do I enrol for the second semester?"),
        ("assistant", "You enrol online through the student portal. " + LONG[:900], {"usage": usage(640, 260)}),
        ("user", "Which documents do I need?"),
        ("assistant", "Bring your ID, your previous transcript and proof of payment. " + LONG[:700], {"usage": usage(1180, 210, reasoning=40, cached=512), "sources": [{"source": {"name": "Handbook"}, "document": ["..."]}]}),
        ("user", "Thanks! And the deadline?"),
    ], params={"system": "Always answer in English."})
    out["estimated"] = make_chat(token, "Estimated chat", ["fake-mini"], [
        ("user", "Résume ce texte en français, s'il te plaît."),
        ("assistant", "Voici un résumé : l'inscription se fait en ligne, avec une pièce d'identité. " * 12),
        ("user", "اشرح ذلك باللغة العربية من فضلك.", {"files": [{"type": "image", "url": "data:image/png;base64,AAAA"}]}),
        ("assistant", "يتم التسجيل عبر الإنترنت من خلال بوابة الطلاب. " * 15),
        ("user", "再用中文解释一下。"),
        ("assistant", "注册通过学生门户网站在线完成。" * 20),
    ])
    out["full"] = make_chat(token, "Nearly full chat", ["fake-gpt-4o"], [
        ("user", "Here is a long document: " + LONG * 3),
        ("assistant", "Summary: " + LONG[:600], {"usage": usage(7300, 180)}),
        ("user", "Continue."),
    ])
    out["over"] = make_chat(token, "Over the window", ["fake-gpt-4o"], [
        ("user", "Huge paste: " + LONG * 5),
        ("assistant", "That's a lot. " + LONG[:400], {"usage": usage(9800, 150)}),
    ])
    out["multi"] = make_chat(token, "Two models", ["fake-gpt-4o", "llama3.1:8b"], [
        ("user", "Compare yourselves."),
        ("assistant", "I am the first model. " * 10, {"id": "a1", "model": "fake-gpt-4o", "usage": usage(500, 90)}),
        ("assistant", "I am the second model, with a longer answer. " * 20, {"id": "a2", "parent": "m1", "model": "llama3.1:8b", "usage": usage(520, 300), "side": True}),
    ])
    out["summary"] = make_chat(token, "Summarised chat", ["fake-gpt-4o"], [
        ("user", "Old question one. " + LONG),
        ("assistant", "Old answer one. " + LONG),
        ("user", "New question.", {"contextSummary": "The student asked about enrolment and got the steps."}),
        ("assistant", "New answer about the next step."),
    ])
    out["unknown"] = make_chat(token, "Unknown model", ["retired-model"], [
        ("user", "Hello there, are you still around?"),
        ("assistant", "Yes. " * 20, {"model": "retired-model"}),
    ])
    out["streaming"] = make_chat(token, "Live chat", ["fake-gpt-4o"], [])
    return out


print(json.dumps({
    "base": BASE, "password": PASSWORD, "installed": installed,
    "admin": {"email": "admin@example.test", "token": admin_token, **seed(admin_token)},
    "user": {"email": "user@example.test", "token": user_token, **seed(user_token)},
}, indent=2))
