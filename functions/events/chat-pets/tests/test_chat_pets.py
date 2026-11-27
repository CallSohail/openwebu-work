"""Server-side tests for Chat Pets. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_pets.py

The function is loaded the way Open WebUI loads an Event Function, with the
one Open WebUI module it imports replaced by a stand-in. Browser behaviour is
covered by tests/e2e.mjs against a real Open WebUI.
"""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.testclient import TestClient

HERE = Path(__file__).resolve().parent
SOURCE_PATH = HERE.parent / "chat_pets.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

# Classic298's Shared Assets Protocol and its live reload add-on, byte for byte
# the same as in Chat Inspector and the other plugins of this repository.
PROTOCOL_BLOCKS = {
    "# Live reload broadcast": (
        "# =========================== end live reload block =========================",
        "fea3dadb266175baf8ae83d3f330fb72311d09971bb08f245e1304431f45db39",
    ),
    "# Shared static-asset registry": (
        "# =========================== end shared asset block ========================",
        "8293a34b015c84e9f53ac44a3cceabea6b190a2489fd55f8721f8427ae9c9cc9",
    ),
}
PETS = ["hibou", "ecureuil", "berger", "renard", "chat", "tortue"]


def load_plugin():
    """Execute the source as module "function_chat_pets_<hex>", as Open WebUI does."""
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_pets_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    exec(compile(SOURCE, str(SOURCE_PATH), "exec"), module.__dict__)
    return module


@pytest.fixture()
def plugin():
    return load_plugin()


@pytest.fixture()
def served(plugin):
    app = Starlette()
    event = plugin.Event()
    plugin.reload_mark_loaded(app)
    plugin.register(app)
    return plugin, event, app, TestClient(app)


def script_of(text):
    start, end = "// owui-chat-pets:start", "// owui-chat-pets:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def config_of(text):
    return json.loads(re.search(r"const CONFIG = (\{.*?\});\n", script_of(text)).group(1))


# ---------------------------------------------------------------- packaging


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Chat Pets\n')
    assert "required_open_webui_version: 0.13.0" in SOURCE
    assert f"## [{version}]" in (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("title", PROTOCOL_BLOCKS)
def test_protocol_blocks_are_untouched(title):
    end_marker, expected = PROTOCOL_BLOCKS[title]
    start = SOURCE.rindex("# ====", 0, SOURCE.index(title))
    block = SOURCE[start : SOURCE.index(end_marker) + len(end_marker)] + "\n"
    assert hashlib.sha256(block.encode("utf-8")).hexdigest() == expected


def test_function_id_and_key_drop_the_random_suffix(plugin):
    assert plugin.RELOAD_FUNCTION_ID == "chat_pets"
    assert plugin.ASSET_KEY == "function_chat_pets"


def test_no_raw_line_separators_in_source():
    # U+2028/U+2029 end a line inside a JavaScript regular expression
    assert " " not in SOURCE and " " not in SOURCE


# ---------------------------------------------------------------- serving


def test_script_is_served_with_the_default_settings(served):
    _, _, _, client = served
    response = client.get("/static/loader.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    script = script_of(response.text)
    assert "__CONFIG__" not in script and "__CSS__" not in script
    assert config_of(response.text) == {
        "pets": PETS,
        "allowCustom": True,
        "allowPhoto": True,
        "defaultPet": "",
        "invite": True,
        "paw": True,
        "bubbles": True,
        "bubbleInterval": 20,
        "sleepAfter": 3,
        "quickActions": True,
        "actions": {},
        "place": "composer",
        "hideOnMobile": False,
        "language": "auto",
        "reactActivity": True,
        "reactErrors": True,
        "notices": ["automations", "calendar", "files", "notes", "memories", "images", "chats"],
        "access": {"mode": "everyone", "roles": ["user", "admin"], "groups": [], "users": [], "blocked": [], "custom": []},
    }


def test_a_valve_change_reaches_the_next_request(served):
    _, event, _, client = served
    client.get("/static/loader.js")
    event.valves = event.Valves(available_pets="chat, hibou ,dragon", default_pet="hibou", speech_bubbles=False, language="fr", default_position="corner")
    config = config_of(client.get("/static/loader.js").text)
    assert config["pets"] == ["hibou", "chat"]  # catalogue order, unknown names dropped
    assert config["defaultPet"] == "hibou" and config["bubbles"] is False
    assert config["language"] == "fr" and config["place"] == "corner"


def test_default_pet_must_be_available(served):
    _, event, _, client = served
    event.valves = event.Valves(available_pets="chat", default_pet="hibou")
    assert config_of(client.get("/static/loader.js").text)["defaultPet"] == ""


def test_an_empty_pet_list_falls_back_to_all(served):
    _, event, _, client = served
    event.valves = event.Valves(available_pets=" , nonsense")
    assert config_of(client.get("/static/loader.js").text)["pets"] == PETS


def test_photos_need_custom_pets(served):
    _, event, _, client = served
    event.valves = event.Valves(allow_custom_pets=False, allow_photo_pets=True)
    config = config_of(client.get("/static/loader.js").text)
    assert config["allowCustom"] is False and config["allowPhoto"] is False


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-chat-pets" not in client.get("/static/loader.js").text
    plugin.reload_state(app)["active"] = True
    assert "owui-chat-pets:start" in client.get("/static/loader.js").text


def test_registering_twice_keeps_one_route(served):
    plugin, _, app, _ = served
    plugin.register(app)
    plugin.register(app)
    assert sum(getattr(route, "path", "") == "/static/loader.js" for route in app.routes) == 1


def test_fragment_is_cached_until_a_valve_changes(plugin):
    event = plugin.Event()
    first = event._loader_fragment()
    assert event._loader_fragment() is first
    event.valves = event.Valves(sleep_after_minutes=9)
    assert event._loader_fragment() is not first


# ---------------------------------------------------------------- valves


@pytest.mark.parametrize(
    "bad",
    [
        {"bubble_interval_minutes": 0},
        {"sleep_after_minutes": 0},
        {"sleep_after_minutes": 500},
        {"language": "de"},
        {"default_position": "top"},
    ],
)
def test_out_of_range_valves_are_refused(plugin, bad):
    with pytest.raises(ValidationError):
        plugin.Event.Valves(**bad)


def test_shortcut_overrides_are_validated(plugin):
    parse = plugin._parse_actions
    assert parse("") == {} and parse("not json") == {} and parse("[1, 2]") == {}
    out = parse(json.dumps({
        "hibou": [{"fr": "Résume mes emails\n", "en": "Summarise my emails"}, "Just text", 42, {"fr": ""}],
        "dragon": [{"fr": "x"}],
        "chat": "not a list",
        "renard": [{"en": "x" * 500}],
    }))
    assert set(out) == {"hibou", "renard"}
    assert out["hibou"] == [{"fr": "Résume mes emails", "en": "Summarise my emails"}, {"fr": "Just text", "en": "Just text"}]
    assert len(out["renard"][0]["fr"]) == 160
    many = parse(json.dumps({"tortue": [{"fr": str(i)} for i in range(20)]}))
    assert len(many["tortue"]) == 8


def test_shortcut_overrides_reach_the_page(served):
    _, event, _, client = served
    event.valves = event.Valves(quick_actions_json=json.dumps({"chat": [{"fr": "Mon agenda", "en": "My agenda"}]}))
    assert config_of(client.get("/static/loader.js").text)["actions"] == {"chat": [{"fr": "Mon agenda", "en": "My agenda"}]}


def test_a_valve_cannot_end_the_script(served):
    _, event, _, client = served
    event.valves = event.Valves(quick_actions_json=json.dumps({"hibou": [{"fr": "</script><script>alert(1)</script>"}]}))
    text = client.get("/static/loader.js").text
    assert "</script>" not in text and "<\\/script>" in text


# ---------------------------------------------------------------- script safety


def test_python_string_cannot_be_ended_by_the_script(plugin):
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.PETS_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_served_script_parses(served, tmp_path):
    _, _, _, client = served
    target = tmp_path / "loader.js"
    target.write_text(client.get("/static/loader.js").text, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_only_fixed_markup_is_written_as_html(plugin):
    # Pet names, roles and photos come from users: they must reach the page as text or as a checked attribute
    script = plugin.LOADER_SCRIPT
    for risky in ("outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in script, risky
    sinks = re.findall(r"\.innerHTML = ([^;]+);", script)
    assert sinks and all(re.fullmatch(r"sprite\(pet\)|ICON(\.\w+|\[name\])|emoteSvg\(c\.icon\)", s.strip()) for s in sinks), sinks
    # emoteSvg only looks names up in the fixed EMOTE table
    emote = script[script.index("const emoteSvg = ") : script.index("/* What each activity looks like")]
    assert "(EMOTE[name] || EMOTE.sparkle)" in emote and "name +" not in emote
    # The SVG templates interpolate only colours, numbers and the clip id; the photo goes in through setAttribute
    drawing = script[script.index("const eyesDot = ") : script.index("function paint(")]
    holes = set(re.findall(r"\$\{([^}]*)\}", drawing))
    allowed = re.compile(r"c\.(main|belly|dark|accent)|id|y|y - 1|62 - y|draw\(c\)")
    assert holes and all(allowed.fullmatch(h.strip()) for h in holes), sorted(holes)
    assert "img.setAttribute('href', pet.photo)" in script


def test_user_supplied_values_are_checked(plugin):
    script = plugin.LOADER_SCRIPT
    assert r"const HEX = /^#[0-9a-f]{6}$/i;" in script
    assert r"const PHOTO = /^data:image\/(png|webp|jpeg);base64,[A-Za-z0-9+/]+=*$/;" in script
    assert "p.photo.length <= 70000" in script
    assert "clean(p.name, 24)" in script and "clean(p.role, 40)" in script


def test_requests_stay_on_this_site(plugin):
    script = plugin.LOADER_SCRIPT
    assert script.count("fetch(") == 1, "only api() may call fetch()"
    paths = re.findall(r"api\('(GET|POST)', '([^']+)'", script)
    assert paths and all(p.startswith("/api/v1/") for _, p in paths), paths
    assert "credentials: 'same-origin'" in script


def test_nothing_is_ever_sent_to_the_model(plugin):
    # Shortcuts fill the message box; the person presses Enter themselves
    script = plugin.LOADER_SCRIPT
    insert = script[script.index("function insertPrompt(") : script.index("function flushPending(")]
    assert "requestSubmit" not in script and ".submit(" not in script and "dispatchEvent(new KeyboardEvent" not in script
    assert "Enter" not in insert
    # The only programmatic click opens the photo file chooser
    assert re.findall(r"\b(\w+)\.click\(\)", script) == ["file"]


def test_pet_choice_is_kept_under_its_own_settings_key(plugin):
    # Open WebUI's own settings saves patch "ui"; a separate top-level key is never overwritten by them
    script = plugin.LOADER_SCRIPT
    assert "{ owui_pets: state }" in script
    assert "ui:" not in script.split("/api/v1/users/user/settings/update")[1][:40]


def test_every_word_has_a_translation(plugin):
    script = plugin.LOADER_SCRIPT
    french = script[script.index("    fr: {") : script.index("    en: {")]
    english = script[script.index("    en: {") : script.index("  const T = ")]
    keys = lambda block: set(re.findall(r"(?:^|[{,]\s*)(\w+): '", block, re.M)) - {"en", "fr"}
    assert keys(english) and keys(english) == keys(french)
    used = set(re.findall(r"\bT\('(\w+)'", script))
    used |= {"hello_morning", "hello_afternoon", "hello_evening", "err_photo_type", "err_photo_size", "err_photo_read"}
    assert used <= keys(english), sorted(used - keys(english))


def test_every_pet_is_complete(plugin):
    script = plugin.LOADER_SCRIPT
    catalogue = script[script.index("const SPECIES = {") : script.index("const ORDER = ")]
    drawings = script[script.index("const DRAW = {") : script.index("let svgSeq")]
    for pet in PETS:
        assert re.search(rf"\b{pet}: \{{ name: '", catalogue), pet
        assert re.search(rf"\b{pet}: c => `", drawings), pet
    for field in ("kind:", "role:", "blurb:", "lines:", "actions:"):
        assert catalogue.count(field) >= len(PETS), field
    assert catalogue.count("{ fr: '") >= len(PETS) * 4  # four bilingual shortcuts each


def test_motion_can_be_reduced(plugin):
    assert "prefers-reduced-motion" in plugin.PETS_CSS
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in plugin.LOADER_SCRIPT


def test_pet_sits_below_open_webui_dialogs(plugin):
    css = plugin.PETS_CSS
    pet_z = int(re.search(r"#owp-pet\{position:fixed;left:0;top:0;z-index:(\d+)", css).group(1))
    menu_z = int(re.search(r"#owp-menu\{position:fixed;left:0;top:0;z-index:(\d+)", css).group(1))
    assert pet_z < 50 and menu_z < 50  # Open WebUI menus and modals sit at z-50 and above


def test_animated_overlays_are_placed_with_left_and_top(plugin):
    # Their pop-in and wiggle animations use transform; placing them with transform too would flash them at 0,0
    script = plugin.LOADER_SCRIPT
    for name in ("pawEl", "bubbleEl", "menuEl"):
        assert f"{name}.style.transform" not in script, name
        assert f"{name}.style.left = " in script and f"{name}.style.top = " in script, name



# ---------------------------------------------------------------- 2.0: access, hashing, relay, reactions


def js_function(script, start, end):
    body = script[script.index(start) : script.index(end)]
    return body


def run_node(code):
    result = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_browser_hash_matches_the_server(plugin):
    sha = js_function(plugin.LOADER_SCRIPT, "  function sha256(text) {", "  const idHash = ")
    samples = ["", "abc", "Enseignants", "marie.curie@exemple.fr", "Équipe Été — Ω", "x" * 55, "y" * 56, "z" * 200, "  Mixed Case Group  "]
    out = run_node(sha + "\nconst v = " + json.dumps(samples) + ";\nconsole.log(JSON.stringify(v.map(x => sha256('owui-pets:v2:' + x.trim().toLowerCase()))));")
    assert out == [plugin._hash_id(x) for x in samples]
    assert plugin._hash_id("abc") == hashlib.sha256(b"owui-pets:v2:abc").hexdigest()


def test_access_lists_are_published_only_as_hashes(served):
    _, event, _, client = served
    event.valves = event.Valves(access_mode="selected", allowed_roles="admin, pending, root", allowed_groups="Enseignants, 7a1b",
                                allowed_users="Marie.Curie@exemple.fr", blocked_users="spam@x.org", custom_pets_for="Design team")
    text = client.get("/static/loader.js").text
    access = config_of(text)["access"]
    assert access["mode"] == "selected" and access["roles"] == ["admin", "pending"]
    assert plugin_hash(event, "enseignants") in access["groups"] and len(access["groups"]) == 2
    assert access["users"] == [plugin_hash(event, "marie.curie@exemple.fr")]
    for secret in ("Enseignants", "Marie.Curie", "marie.curie", "spam@x.org", "Design team"):
        assert secret not in text


def plugin_hash(event, value):
    return sys.modules[type(event).__module__]._hash_id(value)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_browser_access_rules(plugin):
    script = plugin.LOADER_SCRIPT
    code = (js_function(script, "  function sha256(text) {", "  /* ================================================================\n     State")
            + "\nconst h = v => idHash(v);\n")
    cases = [
        ({"mode": "everyone"}, {"id": "u1", "email": "a@x", "role": "user"}, [], {"pets": True}),
        ({"mode": "everyone", "blocked": ["@a@x"]}, {"id": "u1", "email": "a@x", "role": "user"}, [], {"pets": False}),
        ({"mode": "selected", "roles": ["admin"]}, {"id": "u1", "email": "a@x", "role": "user"}, [], {"pets": False}),
        ({"mode": "selected", "roles": ["admin"]}, {"id": "u1", "email": "a@x", "role": "admin"}, [], {"pets": True}),
        ({"mode": "selected", "roles": [], "groups": ["@Enseignants"]}, {"id": "u1", "email": "a@x", "role": "user"}, [{"id": "g1", "name": "ENSEIGNANTS"}], {"pets": True}),
        ({"mode": "selected", "roles": [], "groups": ["@g9"]}, {"id": "u1", "email": "a@x", "role": "user"}, [{"id": "g1", "name": "Other"}], {"pets": False}),
        ({"mode": "selected", "roles": [], "users": ["@A@X"]}, {"id": "u1", "email": "a@x", "role": "user"}, [], {"pets": True}),
        ({"mode": "selected", "roles": ["user"], "blocked": ["@Bad group"]}, {"id": "u1", "email": "a@x", "role": "user"}, [{"id": "g2", "name": "bad group"}], {"pets": False}),
        ({"mode": "everyone", "custom": ["@designers"]}, {"id": "u1", "email": "a@x", "role": "user"}, [], {"pets": True, "custom": False}),
        ({"mode": "everyone", "custom": ["@designers"]}, {"id": "u1", "email": "a@x", "role": "user"}, [{"id": "g3", "name": "Designers"}], {"pets": True, "custom": True}),
    ]
    prog = code + "const out = [];\n"
    for acc, me, groups, _ in cases:
        hashed = {k: ([("h(" + json.dumps(x[1:]) + ")") if isinstance(x, str) and x.startswith("@") else json.dumps(x) for x in v] if isinstance(v, list) else json.dumps(v)) for k, v in acc.items()}
        obj = "{" + ",".join(f"{json.dumps(k)}: " + ("[" + ",".join(v) + "]" if isinstance(v, list) else v) for k, v in hashed.items()) + "}"
        prog += f"CONFIG.access = {obj}; out.push(accessOf({json.dumps(me)}, {json.dumps(groups)}));\n"
    prog = "const CONFIG = {allowCustom: true, access: {}};\n" + prog + "console.log(JSON.stringify(out));"
    results = run_node(prog)
    for (_, _, _, want), got in zip(cases, results):
        for k, v in want.items():
            assert got[k] == v, (want, got)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_tools_and_statuses_get_the_right_animation(plugin):
    script = plugin.LOADER_SCRIPT
    code = js_function(script, "  const TOOL_RULES = [", "  /* ================================================================\n     SHA-256")
    names = {
        "search_web": "web", "fetch_url": "web", "query_knowledge_files": "read", "view_file": "read", "write_note": "note", "search_notes": "note",
        "add_memory": "memory", "search_memories": "memory", "create_tasks": "tasks", "update_task": "tasks", "create_calendar_event": "calendar",
        "search_calendar_events": "calendar", "find_meeting_slots": "calendar", "create_automation": "automation", "list_automations": "automation",
        "timer": "timer", "execute_code": "code", "generate_image": "image", "edit_image": "image", "delegate_task": "agents", "send_message": "agents",
        "view_skill": "skill", "create_skill": "skill", "create_tool": "tool", "ask_user": "ask", "search_chats": "chats", "view_chat": "chats",
        "list_emails": "mail", "compose_email": "mail", "get_current_timestamp": "timer", "web_search": "web", "knowledge_search": "read",
        "sources_retrieved": "read", "code_interpreter": "code", "image_generation": "image", "some_custom_thing": "tool",
    }
    out = run_node(code + "\nconsole.log(JSON.stringify(" + json.dumps(list(names)) + ".map(n => classify(n, ''))));")
    assert dict(zip(names, out)) == names
    assert run_node(code + "\nconsole.log(JSON.stringify(classify('weather_lookup2', 'external')));") == "search"
    assert run_node(code + "\nconsole.log(JSON.stringify(classify('xyz', 'mcp')));") == "plug"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_errors_are_explained_in_plain_words(plugin):
    script = plugin.LOADER_SCRIPT
    strings = js_function(script, "  const STRINGS = {", "  const L = obj =>")
    explain = js_function(script, "  function explain(text) {", "  function onError(")
    clean = js_function(script, "  const clean = (s, n) =>", "  const TALK = ")
    prog = "const CONFIG = {}; const lang = 'en';\n" + strings + clean + explain + """
const cases = ['Rate limit reached for requests.', 'Error 429', "This model's maximum context length is 8192 tokens", '401 Unauthorized: invalid api key',
  'Model fake-x not found', 'Request timed out', 'ECONNREFUSED 127.0.0.1', 'The provider had an internal error.', ''];
console.log(JSON.stringify(cases.map(explain)));"""
    out = run_node(prog)
    assert out[0].startswith("Too many requests") and out[1].startswith("Too many requests")
    assert out[2].startswith("This chat is too long") and out[3].startswith("The model refused access")
    assert out[4].startswith("This model can") and out[5].startswith("The model took too long")
    assert out[6].startswith("The model isn") and out[7] == "Oops, an error: The provider had an internal error."
    assert out[8] == "Oops, something went wrong."


# ---- server relay, with Open WebUI's modules replaced by stand-ins


class FakeSio:
    def __init__(self):
        self.sent = []

    async def emit(self, name, data, room=None):
        self.sent.append((name, data, room))


@pytest.fixture()
def relay(plugin, monkeypatch):
    users = {"u1": types.SimpleNamespace(id="u1", email="Marie@Exemple.fr", role="user"), "u2": types.SimpleNamespace(id="u2", email="paul@exemple.fr", role="admin")}
    groups = {"u1": [types.SimpleNamespace(id="g1", name="Enseignants")], "u2": []}

    async def get_user_by_id(uid):
        return users.get(uid)

    async def get_groups_by_member_id(uid, include_inherited=False):
        assert include_inherited is True
        return groups.get(uid, [])

    sio = FakeSio()
    mods = {
        "open_webui.models": types.ModuleType("open_webui.models"),
        "open_webui.models.users": types.SimpleNamespace(Users=types.SimpleNamespace(get_user_by_id=get_user_by_id)),
        "open_webui.models.groups": types.SimpleNamespace(Groups=types.SimpleNamespace(get_groups_by_member_id=get_groups_by_member_id)),
        "open_webui.socket": types.ModuleType("open_webui.socket"),
        "open_webui.socket.main": types.SimpleNamespace(sio=sio),
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    event = plugin.Event()
    return plugin, event, sio


def fire(event, name, **fields):
    import asyncio

    payload = {"event": name, "actor": fields.pop("actor", None), "subject": fields.pop("subject", None), "data": fields.pop("data", {})}
    asyncio.run(event.event(event=payload, __event_name__=name))


def test_relay_sends_a_minimal_notice_to_the_right_person(relay):
    _, event, sio = relay
    fire(event, "automation.run_completed", actor={"id": "u1", "type": "user", "email": "Marie@Exemple.fr"},
         data={"name": "Daily digest <b>", "chat_id": "7ca9e2c1-313c-410e-8e33-0d8e5467260e", "prompt": "secret prompt"})
    assert sio.sent == [("owui-pets", {"e": "automation.run_completed", "t": "Daily digest <b>", "chat": "7ca9e2c1-313c-410e-8e33-0d8e5467260e"}, "user:u1")]


def test_relay_calendar_alert_uses_the_events_user(relay):
    _, event, sio = relay
    fire(event, "calendar.alert", data={"user_id": "u2", "title": "Jury", "minutes_until": 4, "description": "private notes", "location": "B204"})
    assert sio.sent == [("owui-pets", {"e": "calendar.alert", "t": "Jury", "m": 4}, "user:u2")]


def test_relay_ignores_other_events_bad_chat_ids_and_unknown_people(relay):
    _, event, sio = relay
    fire(event, "chat.updated", actor={"id": "u1", "type": "user"})
    fire(event, "note.created", actor={"id": "nobody", "type": "user"}, data={"title": "x"})
    fire(event, "note.created", actor={"id": "u1", "type": "system"}, data={"title": "x"})
    fire(event, "automation.run_completed", actor={"id": "u1", "type": "user"}, data={"name": "x", "chat_id": "../../etc"})
    assert sio.sent == [("owui-pets", {"e": "automation.run_completed", "t": "x"}, "user:u1")]


def test_relay_follows_the_notice_valve(relay):
    _, event, sio = relay
    event.valves = event.Valves(notices="calendar")
    fire(event, "note.created", actor={"id": "u1", "type": "user"}, data={"title": "x"})
    fire(event, "calendar.alert", data={"user_id": "u1", "title": "Jury", "minutes_until": 0})
    assert [s[1]["e"] for s in sio.sent] == ["calendar.alert"]


@pytest.mark.parametrize("valves,expected", [
    ({"access_mode": "everyone"}, {"u1", "u2"}),
    ({"access_mode": "everyone", "blocked_users": "marie@exemple.fr"}, {"u2"}),
    ({"access_mode": "everyone", "blocked_users": "enseignants"}, {"u2"}),
    ({"access_mode": "selected", "allowed_roles": "admin"}, {"u2"}),
    ({"access_mode": "selected", "allowed_roles": "", "allowed_groups": "ENSEIGNANTS"}, {"u1"}),
    ({"access_mode": "selected", "allowed_roles": "", "allowed_users": "paul@exemple.fr"}, {"u2"}),
    ({"access_mode": "selected", "allowed_roles": "", "allowed_groups": "g1", "blocked_users": "u1"}, set()),
])
def test_relay_only_reaches_people_who_may_have_a_pet(relay, valves, expected):
    _, event, sio = relay
    event.valves = event.Valves(**valves)
    for uid in ("u1", "u2"):
        fire(event, "memory.created", actor={"id": uid, "type": "user"})
    assert {room.split(":")[1] for _, _, room in sio.sent} == expected


def test_access_decisions_are_cached_briefly(relay):
    _, event, sio = relay
    fire(event, "memory.created", actor={"id": "u1", "type": "user"})
    assert "u1" in event._access_cache
    event.valves = event.Valves(blocked_users="u1")
    fire(event, "memory.created", actor={"id": "u1", "type": "user"})
    assert len(sio.sent) == 2  # the cached decision holds for up to a minute
    event._access_cache.clear()
    fire(event, "memory.created", actor={"id": "u1", "type": "user"})
    assert len(sio.sent) == 2


def test_a_failing_relay_never_breaks_the_dispatcher(relay, monkeypatch):
    _, event, _ = relay

    async def boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(sys.modules["open_webui.models.users"].Users, "get_user_by_id", boom)
    fire(event, "memory.created", actor={"id": "u9", "type": "user"})  # must not raise


def test_socket_hooks_only_listen(plugin):
    script = plugin.LOADER_SCRIPT
    hook = script[script.index("(function hookSocket() {") : script.index("let micTracks = 0;")]
    assert "ws.send" not in hook and ".send(" not in hook and "preventDefault" not in hook and "stopImmediatePropagation" not in hook
    assert "u.host === location.host && u.pathname.indexOf('/ws/socket.io') === 0" in hook
    assert "Hooked.prototype = Native.prototype" in hook
    mic = script[script.index("(function hookMic() {") : script.index("/* ================================================================\n     Words")]
    assert "return stream;" in mic and "original(constraints)" in mic
