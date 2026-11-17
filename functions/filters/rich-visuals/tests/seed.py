"""Seed a throwaway Open WebUI 0.13.4 for the Rich Visuals browser test.

    python tests/fake_model.py 9994 &
    OPENAI_API_BASE_URL=http://127.0.0.1:9994/v1 OPENAI_API_KEY=x open-webui serve --port 8084 &
    python tests/seed.py http://127.0.0.1:8084 rich_visuals.py > seed.json

Installs the filter as a global filter and makes two models from the fake one: one with the built-in Visualization
tool turned on, one without. Never point it at an instance people use.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1].rstrip("/")
SOURCE = open(sys.argv[2], encoding="utf-8").read()
PASSWORD = "rich-visuals-test"
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
        raise SystemExit(f"{method} {path} -> {error.code}: {error.read()[:400]!r}")
    return json.loads(raw) if raw else None


admin = call("POST", "/api/v1/auths/signup", body={"name": "Visual Admin", "email": "admin@example.test", "password": PASSWORD})["token"]
call("POST", "/api/v1/functions/create", admin, {"id": "rich_visuals", "name": "Rich Visuals", "content": SOURCE, "meta": {"description": "Rich Visuals"}})
call("POST", "/api/v1/functions/id/rich_visuals/toggle", admin)
call("POST", "/api/v1/functions/id/rich_visuals/toggle/global", admin)
everyone = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
call("POST", "/api/v1/models/create", admin, {"id": "fake-visual", "base_model_id": None, "name": "Visual model", "params": {},
                                             "meta": {"builtinTools": {"visualizations": True}, "capabilities": {"builtin_tools": True}},
                                             "access_grants": everyone, "is_active": True})
call("POST", "/api/v1/models/create", admin, {"id": "fake-visual-off", "base_model_id": "fake-visual", "name": "Model without visualization",
                                             "params": {}, "meta": {"builtinTools": {"visualizations": False}}, "access_grants": everyone, "is_active": True})
call("POST", "/api/v1/auths/add", admin, {"name": "Visual User", "email": "user@example.test", "password": PASSWORD, "role": "user"})
user = call("POST", "/api/v1/auths/signin", body={"email": "user@example.test", "password": PASSWORD})["token"]
print(json.dumps({"base": BASE, "admin": admin, "user": user}))
