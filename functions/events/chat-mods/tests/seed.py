"""Seed a throwaway Open WebUI with what the Mods browser tests need.

    python tests/fake_model.py 9996 &
    python tests/seed.py http://127.0.0.1:8080 chat_mods.py [other plugin sources...] > seed.json

Start Open WebUI against the fake model, on an instance with no users yet: the
first account becomes the administrator. Never point it at an instance people use.
"""

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1].rstrip("/")
SOURCES = sys.argv[2:]
PASSWORD = "mods-test-password"
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


admin = call("POST", "/api/v1/auths/signup", body={"name": "Mods Admin", "email": "admin@example.test", "password": PASSWORD})
admin_token = admin["token"]
for path in SOURCES:
    function_id = path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".py")
    call("POST", "/api/v1/functions/create", admin_token, {"id": function_id, "name": function_id, "content": open(path, encoding="utf-8").read(), "meta": {"description": function_id}})
    call("POST", f"/api/v1/functions/id/{function_id}/toggle", admin_token)
call("POST", "/api/v1/models/create", admin_token, {"id": "fake-modder", "base_model_id": None, "name": "fake-modder", "meta": {}, "params": {}, "access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}], "is_active": True})
call("POST", "/api/v1/auths/add", admin_token, {"name": "Mods User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user_token = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]

ANSWER = ("The library opens at eight. Students borrow books for three weeks. Group rooms are booked online. "
          "Printing costs five cents a page. Ask at the desk if you need help with anything at all.")


def make_chat(token, title, turns):
    messages, parent = {}, None
    now = int(time.time())
    for role, content in turns:
        message_id = str(uuid.uuid4())
        messages[message_id] = {"id": message_id, "parentId": parent, "childrenIds": [], "role": role, "content": content, "timestamp": now,
                                **({"model": "fake-modder", "modelName": "fake-modder", "done": True} if role == "assistant" else {"models": ["fake-modder"]})}
        if parent:
            messages[parent]["childrenIds"].append(message_id)
        parent = message_id
    chat = {"title": title, "models": ["fake-modder"], "params": {}, "files": [], "tags": [], "timestamp": now * 1000,
            "history": {"messages": messages, "currentId": parent}, "messages": list(messages.values())}
    return call("POST", "/api/v1/chats/new", token, {"chat": chat})["id"]


chats = {
    "admin": make_chat(admin_token, "Library questions", [("user", "When does the library open?"), ("assistant", ANSWER)]),
    "user": make_chat(user_token, "Library questions", [("user", "When does the library open?"), ("assistant", ANSWER), ("user", "And printing?"), ("assistant", "Printing costs five cents a page, paid with your card at any printer on the ground floor of the main library building.")]),
}
print(json.dumps({"base": BASE, "password": PASSWORD, "admin": {"email": "admin@example.test", "token": admin_token}, "user": {"email": "user@example.test", "token": user_token}, "chats": chats}))
