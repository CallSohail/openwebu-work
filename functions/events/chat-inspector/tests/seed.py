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
# What the Tools and activity section needs: a workspace tool, an MCP server in the tool list, filters and an
# action on a model that calls tools natively. The MCP server isn't reachable; only its listing is used.
PUBLIC = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
CALC = '''class Tools:
    def add(self, a: int, b: int) -> str:
        """Add two numbers.
        :param a: first number
        :param b: second number
        """
        return str(a + b)
'''
call("POST", "/api/v1/tools/create", admin_token, {"id": "calc", "name": "Calculator", "content": CALC, "meta": {"description": "Adds numbers"}, "access_grants": PUBLIC})
call("POST", "/api/v1/configs/tool_servers", admin_token, {"TOOL_SERVER_CONNECTIONS": [{"url": "http://127.0.0.1:9", "path": "/mcp", "type": "mcp", "auth_type": "none", "key": "", "config": {"enable": True, "access_grants": PUBLIC}, "info": {"id": "docs", "name": "Docs MCP", "description": "University docs"}}]})
FILTER = '''class Filter:
    def inlet(self, body: dict) -> dict:
        return body
'''
TOGGLE = FILTER.replace("class Filter:", "class Filter:\n    def __init__(self):\n        self.toggle = True\n")
ACTION = '''class Action:
    async def action(self, body: dict) -> dict:
        return body
'''
for fid, name, content in (("tidy_filter", "Tidy input", FILTER), ("strict_mode", "Strict mode", TOGGLE), ("save_answer", "Save answer", ACTION)):
    call("POST", "/api/v1/functions/create", admin_token, {"id": fid, "name": name, "content": content, "meta": {"description": name}})
    call("POST", f"/api/v1/functions/id/{fid}/toggle", admin_token)
call("POST", "/api/v1/models/create", admin_token, {"id": "fake-tools", "base_model_id": "fake-gpt-4o", "name": "Fake with tools",
     "meta": {"capabilities": {"builtin_tools": True, "web_search": True, "code_interpreter": True}, "toolIds": ["calc", "server:mcp:docs"], "filterIds": ["tidy_filter", "strict_mode"], "actionIds": ["save_answer"]},
     "params": {"function_calling": "native"}, "access_grants": PUBLIC, "is_active": True})
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
    out["live_tools"] = make_chat(token, "Live tools", ["fake-tools"], [])
    out["tools"] = make_chat(token, "Tools chat", ["fake-tools"], [
        ("user", "What did we add last time?"),
        ("assistant", 'Earlier: <details type="tool_calls" done="true" id="c0" name="add" arguments="{&quot;a&quot;: 1, &quot;b&quot;: 1}" result="&quot;2&quot;">\n<summary>Tool Executed</summary>\n</details>\nIt was 2.', {"model": "fake-tools"}),
        ("user", "Find the enrolment deadline, add 2 and 3, and plot it."),
        ("assistant", "The deadline is 15 January. 2 + 3 = 5.", {"model": "fake-tools", "usage": {"input_tokens": 2400, "output_tokens": 300, "total_tokens": 2700, "prompt_tokens": 1300, "completion_tokens": 120, "cost": 0.0042},
            "output": [
                {"type": "reasoning", "id": "r1", "content": [{"type": "output_text", "text": "Plan the calls."}], "duration": 3.5, "status": "completed"},
                {"type": "function_call", "id": "h1", "call_id": "h1", "name": "recall_memories", "hidden": True, "arguments": "{}", "status": "completed"},
                {"type": "function_call_output", "id": "ho1", "call_id": "h1", "output": [{"type": "input_text", "text": "The student studies law."}], "status": "completed"},
                {"type": "function_call", "id": "c1", "call_id": "c1", "name": "search_web", "arguments": "{\"query\": \"enrolment deadline 2026\"}", "status": "completed", "duration": 1.2},
                {"type": "function_call", "id": "c2", "call_id": "c2", "name": "fetch_url", "arguments": "{\"url\": \"https://example.edu/dates\"}", "status": "completed", "duration": 0.8},
                {"type": "function_call", "id": "c3", "call_id": "c3", "name": "docs_search_pages", "arguments": "{\"q\": \"deadline\"}", "status": "completed", "duration": 0.4},
                {"type": "function_call", "id": "c4", "call_id": "c4", "name": "add", "arguments": "{\"a\": 2, \"b\": 3}", "status": "completed", "duration": 0.01},
                {"type": "function_call", "id": "c5", "call_id": "c5", "name": "petstore_get_pet", "arguments": "{\"id\": 7}", "status": "completed"},
                {"type": "function_call", "id": "c6", "call_id": "c6", "name": "get_current_timestamp", "arguments": "{}", "status": "completed"},
                {"type": "function_call", "id": "c7", "call_id": "c7", "name": "delete_memory", "arguments": "{\"id\": \"m1\"}", "status": "rejected"},
                {"type": "function_call_output", "id": "o1", "call_id": "c1", "output": [{"type": "input_text", "text": "Results: the deadline is 15 January."}], "status": "completed"},
                {"type": "function_call_output", "id": "o2", "call_id": "c2", "output": [{"type": "input_text", "text": "Key dates page. " * 40}], "status": "completed"},
                {"type": "function_call_output", "id": "o3", "call_id": "c3", "output": [{"type": "input_text", "text": "Page: Deadlines"}], "status": "completed"},
                {"type": "function_call_output", "id": "o4", "call_id": "c4", "output": [{"type": "input_text", "text": "5"}], "status": "completed"},
                {"type": "function_call_output", "id": "o5", "call_id": "c5", "output": [{"type": "input_text", "text": "{\"error\": \"Connection refused\"}"}], "status": "completed"},
                {"type": "function_call_output", "id": "o6", "call_id": "c6", "output": [{"type": "input_text", "text": "2026-11-12T10:00:00Z"}], "status": "completed"},
                {"type": "function_call_output", "id": "o7", "call_id": "c7", "output": [{"type": "input_text", "text": "Declined by the user"}], "status": "rejected"},
                {"type": "open_webui:code_interpreter", "id": "ci1", "lang": "python", "code": "print(2 + 3)", "output": {"stdout": "5"}, "status": "completed", "duration": 0.3},
                {"type": "message", "id": "msg1", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": "The deadline is 15 January. 2 + 3 = 5."}]}],
            "statusHistory": [{"action": "web_search_queries_generated", "queries": ["uni deadlines"], "done": False}, {"action": "web_search", "urls": ["https://example.edu/a", "https://example.edu/b"], "done": True},
                              {"action": "tool_connection", "description": "Failed to connect to MCP server 'wiki'", "error": True, "done": True}],
            "sources": [{"source": {"name": "Handbook"}, "document": ["First part", "Second part"], "metadata": [{"source": "Handbook"}]},
                        {"source": {"name": "calc/add"}, "document": ["5"], "metadata": [{"source": "calc/add", "parameters": {"a": 2, "b": 3}}], "tool_result": True}],
            "files": [{"type": "image", "url": "data:image/png;base64,AAAA"}]}),
    ])
    return out


print(json.dumps({
    "base": BASE, "password": PASSWORD, "installed": installed,
    "admin": {"email": "admin@example.test", "token": admin_token, **seed(admin_token)},
    "user": {"email": "user@example.test", "token": user_token, **seed(user_token)},
}, indent=2))
