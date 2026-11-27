"""Seed a throwaway Open WebUI for the Chat Pets browser tests.

    python ../chat-inspector/tests/fake_model.py 9999 &
    python tests/seed.py http://127.0.0.1:8080 chat_pets.py > seed.json
    node tests/e2e.mjs seed.json [screenshot-folder]

Start Open WebUI with ENABLE_OPENAI_API=true and OPENAI_API_BASE_URL pointing
at the fake model, against an instance with no users yet. Never point it at an
instance people use.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/")
SOURCE = sys.argv[2]
PASSWORD = "pets-test-password"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PUBLIC = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]


def call(method, path, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=60) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}")


admin = call("POST", "/api/v1/auths/signup", body={"name": "Sohail Admin", "email": "admin@example.test", "password": PASSWORD})
admin_token = admin["token"]
for model_id in ("fake-gpt-4o", "fake-mini"):
    call("POST", "/api/v1/models/create", admin_token, {"id": model_id, "base_model_id": None, "name": model_id, "meta": {}, "params": {}, "access_grants": PUBLIC, "is_active": True})
call("POST", "/api/v1/functions/create", admin_token, {"id": "chat_pets", "name": "Chat Pets", "content": open(SOURCE, encoding="utf-8").read(), "meta": {"description": "Chat Pets"}})
call("POST", "/api/v1/functions/id/chat_pets/toggle", admin_token)

users = {}
for key, name, email in (("user", "Marie Etudiante", "user@example.test"), ("other", "Paul Chercheur", "other@example.test")):
    call("POST", "/api/v1/auths/add", admin_token, {"name": name, "email": email, "password": PASSWORD, "role": "user"})
    signed = call("POST", "/api/v1/auths/signin", body={"email": email, "password": PASSWORD})
    users[key] = {"token": signed["token"], "id": signed["id"], "name": name}

json.dump({"base": BASE, "admin": {"token": admin_token, "id": admin["id"]}, **users}, sys.stdout)
