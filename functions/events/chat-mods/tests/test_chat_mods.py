"""Server-side tests for Mods. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_mods.py

The function is loaded the way Open WebUI loads an Event Function, with the
few Open WebUI modules it imports replaced by stand-ins. Tests that run parts of
the browser script need Node.js and are skipped without it.
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
SOURCE_PATH = HERE.parent / "chat_mods.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

# The two protocol blocks must stay byte for byte what Classic298 publishes at
# https://github.com/Classic298/open-webui-plugins/tree/main/shared-assets-protocol
# (commit 917c88f). Every plugin on a server shares one copy of this code.
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
NODE = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")


def load_plugin():
    """Execute the source as module "function_chat_mods_<hex>", as Open WebUI does."""
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_mods_{uuid.uuid4().hex}"
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
    return plugin, event, app, TestClient(app)


def script_of(text):
    start, end = "// owui-mods:start", "// owui-mods:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def config_of(script):
    return json.loads(re.search(r"const CONFIG = (\{.*?\});\n", script).group(1))


def a_mod(plugin, **changes):
    mod = dict(plugin.STARTERS[0], source="starter", granted=list(plugin.STARTERS[0]["permissions"]), enabled=True)
    mod.update(changes)
    return mod


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Mods\n')
    assert "required_open_webui_version: 0.13.0" in SOURCE
    assert f"## [{version}]" in (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"Version v{version}" in (HERE.parent / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("title", PROTOCOL_BLOCKS)
def test_protocol_blocks_are_untouched(title):
    end_marker, expected = PROTOCOL_BLOCKS[title]
    start = SOURCE.rindex("# ====", 0, SOURCE.index(title))
    block = SOURCE[start : SOURCE.index(end_marker) + len(end_marker)] + "\n"
    assert hashlib.sha256(block.encode("utf-8")).hexdigest() == expected


def test_function_id_drops_the_random_suffix(plugin):
    assert plugin.RELOAD_FUNCTION_ID == "chat_mods"


def test_script_is_served_with_the_default_settings(served):
    plugin, _, _, client = served
    response = client.get("/static/loader.js")
    assert response.status_code == 200
    script = script_of(response.text)
    assert "__CONFIG__" not in script and "__CSS__" not in script
    config = config_of(script)
    assert config["functionId"] == "chat_mods"
    assert config["enabled"] is True and config["allowUserMods"] is True
    assert config["allowedPermissions"] == list(plugin.PERMISSIONS)
    assert config["maxMods"] == 12 and config["aiPerMinute"] == 6
    assert config["catalog"] == [] and [m["id"] for m in config["starters"]] == [m["id"] for m in plugin.STARTERS]


def test_valve_changes_reach_the_next_request(served):
    plugin, event, _, client = served
    event.valves = plugin.Event.Valves(enabled=False, allowed_permissions="ui.status, ai", show_starters=False)
    config = config_of(script_of(client.get("/static/loader.js").text))
    assert config["enabled"] is False and config["allowedPermissions"] == ["ui.status", "ai"] and config["starters"] == []


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-mods" not in client.get("/static/loader.js").text


def test_published_code_cannot_end_the_script_early(served):
    # The loader is a plain script; a catalog mod's code is data inside it and must not close or break it
    plugin, event, _, client = served
    nasty = "mod.toast('</script><script>alert(1)</script>\u2028');"
    catalog = json.dumps([{"id": "nasty", "name": "Nasty", "code": nasty, "permissions": []}])
    event.valves = plugin.Event.Valves(catalog=catalog)
    script = script_of(client.get("/static/loader.js").text)
    raw = re.search(r"const CONFIG = (\{.*?\});\n", script).group(1)
    assert "</script" not in raw and "\u2028" not in raw
    assert config_of(script)["catalog"][0]["code"] == nasty


@pytest.mark.parametrize(
    "bad",
    [
        {"allowed_permissions": "ui.status, root"},
        {"max_mods_per_user": 0},
        {"max_mods_per_user": 25},
        {"ai_calls_per_minute": 61},
        {"language": "de"},
        {"catalog": "not json"},
        {"catalog": json.dumps([{"id": "Bad Id", "name": "x", "code": "x"}])},
        {"catalog": json.dumps([{"id": "a", "name": "x", "code": "x", "permissions": ["root"]}])},
        {"catalog": json.dumps([{"id": "a", "name": "x", "code": "x"}, {"id": "a", "name": "y", "code": "y"}])},
    ],
)
def test_bad_settings_are_refused(plugin, bad):
    with pytest.raises(ValidationError):
        plugin.Event.Valves(**bad)


def test_a_person_s_mods_are_checked_and_trimmed(plugin):
    extra = a_mod(plugin, sneaky="x", enabled=1)
    stored = json.loads(plugin.Event.UserValves(mods=json.dumps({"mods": [extra], "dismissed": ["pomodoro", "Bad Id", 3]})).mods)
    assert "sneaky" not in stored["mods"][0] and stored["mods"][0]["enabled"] is True
    assert stored["dismissed"] == ["pomodoro"]
    assert json.loads(plugin.Event.UserValves().mods) == {"mods": [], "dismissed": []}


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../x"},
        {"name": ""},
        {"name": "x" * 61},
        {"code": ""},
        {"code": "x" * 60001},
        {"permissions": ["root"]},
        {"granted": ["chat.send"]},
        {"source": "elsewhere"},
        {"version": "2"},
        {"version": True},
        {"description": "x" * 201},
    ],
)
def test_malformed_mods_are_refused(plugin, change):
    with pytest.raises(ValidationError):
        plugin.Event.UserValves(mods=json.dumps({"mods": [a_mod(plugin, **change)]}))


def test_a_person_can_t_store_too_many_or_duplicate_mods(plugin):
    many = [a_mod(plugin, id=f"m{i}") for i in range(25)]
    with pytest.raises(ValidationError):
        plugin.Event.UserValves(mods=json.dumps({"mods": many}))
    with pytest.raises(ValidationError):
        plugin.Event.UserValves(mods=json.dumps({"mods": [a_mod(plugin), a_mod(plugin)]}))


def test_starters_are_valid_mods(plugin):
    for starter in plugin.STARTERS:
        plugin._clean_mod(starter, "starter", catalog=True)
        # Ready-made mods are examples too: they build their panels as text, never as markup
        assert "innerHTML" not in starter["code"] and "eval(" not in starter["code"]


def test_every_word_has_a_translation(plugin):
    script = plugin.LOADER_SCRIPT
    english = script[script.index("    en: {") : script.index("    fr: {")]
    french = script[script.index("    fr: {") : script.index("  const language = ")]
    keys = lambda block: set(re.findall(r"(?:^|[{,]\s*)(\w+): '", block, re.M)) - {"en", "fr"}
    assert keys(english) and keys(english) == keys(french)
    used = set(re.findall(r"\bt\('(\w+)'(?=[,)])", script))
    used |= {"perm_" + p.replace(".", "_") for p in plugin.PERMISSIONS}
    assert used <= keys(english), sorted(used - keys(english))


def test_browser_and_server_know_the_same_permissions(plugin):
    script = plugin.LOADER_SCRIPT
    known = re.findall(r"'([a-z.]+)': '(?:low|medium|high)'", script[script.index("const PERMS = ") :].split("\n")[0])
    assert known == list(plugin.PERMISSIONS)


def test_mods_are_sandboxed(plugin):
    script = plugin.LOADER_SCRIPT
    # Scripts only: no same origin (so no storage, cookies or token), no popups, forms or top navigation
    assert "const SANDBOX = 'allow-scripts';" in script
    csp = re.search(r'const FRAME_CSP = "([^"]+)"', script).group(1)
    for rule in ("default-src 'none'", "connect-src 'none'", "form-action 'none'", "frame-src 'none'", "worker-src 'none'", "img-src data: blob:"):
        assert rule in csp, rule
    assert "https:" not in csp and "http:" not in csp
    # Each message from a frame must come from that frame's window and carry its own random key
    assert "each.frame.contentWindow === event.source && each.nonce === message.__mods" in script
    assert "if (handler.perm && !can(runner, handler.perm))" in script
    # A frame that navigates away is stopped
    assert "if (++runner.loads > 1) { stop(mod.id); fail(mod, 'stopped_escape'); }" in script


def test_page_text_is_never_written_as_html(plugin):
    script = plugin.LOADER_SCRIPT
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert risky not in script, risky
    # The only markup is each frame's document, and code in it can't close its script element
    assert script.count(".srcdoc = ") == 1
    assert "const scriptSafe = code => String(code).replace(/<\\/(script)/gi" in script


def test_sign_in_token_is_only_sent_to_this_site(plugin):
    script = plugin.LOADER_SCRIPT
    assert "new URL(url, location.href).origin === location.origin" in script
    assert script.count("fetch(") == 1, "only request() may call fetch()"


def test_python_string_cannot_be_ended_by_the_script(plugin):
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.MODS_CSS


@NODE
def test_served_script_parses(served, tmp_path):
    _, _, _, client = served
    target = tmp_path / "loader.js"
    target.write_text(client.get("/static/loader.js").text, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@NODE
def test_starter_code_parses(plugin, tmp_path):
    runner = tmp_path / "starters.js"
    runner.write_text(
        "const AsyncFunction = (async () => {}).constructor;\nconst starters = " + json.dumps(plugin.STARTERS) + ";\n"
        "for (const m of starters) new AsyncFunction('mod', m.code);\nconsole.log('ok');\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.stdout.strip() == "ok", result.stderr


def _lines(script, *starts):
    return "\n".join(line for line in script.split("\n") if line.lstrip().startswith(starts))


def _function_source(script, start):
    begin = script.index(start)
    return script[begin : script.index("\n  }\n", begin) + 4]


@NODE
def test_reading_mods_and_cleaning_styles(plugin, tmp_path):
    script = plugin.LOADER_SCRIPT
    runner = tmp_path / "logic.js"
    runner.write_text(
        "const assert = require('assert');\nconst document = {documentElement: {dataset: {}}};\nlet lang = 'en';\nconst t = (key, vars) => key + (vars ? JSON.stringify(vars) : '');\n"
        + _lines(script, "const PERMS = ", "const CODE_LIMIT", "const slug = ", "const clip = ", "const clamp = ", "const modText = ", "const CSS_FORBIDDEN")
        + "\n" + _function_source(script, "  function parseMod(")
        + _function_source(script, "  function cleanCss(")
        + _function_source(script, "  function machine(")
        + r"""
// The mod format: settings, ---, code; fenced or not; and back again
const text = 'name: Hello World\ndescription: Says hi\nicon: 👋\nversion: 3\npermissions: ui.status, composer.read\n---\nmod.status.set("hi");';
const mod = parseMod(text);
assert.deepStrictEqual([mod.id, mod.name, mod.version, mod.icon, mod.permissions], ['hello-world', 'Hello World', 3, '👋', ['ui.status', 'composer.read']]);
assert.deepStrictEqual(parseMod('Sure!\n\n```mod\n' + text + '\n```\nEnjoy.'), mod, 'a fenced answer from a model reads the same');
assert.deepStrictEqual(parseMod(modText(mod)), mod, 'exported text reads back the same');
assert.strictEqual(parseMod('name: Évé Café\n---\nx').id, 'eve-cafe');
for (const [bad, key] of [['mod.toast(1)', 'e_header'], ['---\nx', 'e_name'], ['name: a\n---\n', 'e_code'], ['name: a\npermissions: root\n---\nx', 'e_perm'], ['name: a\nowner: me\n---\nx', 'e_key'], ['name: a\n---\n' + 'x'.repeat(60001), 'e_long']]) {
  assert.throws(() => parseMod(bad), e => e.message.startsWith(key), key);
}
// Styles: nothing loads from elsewhere, nothing touches Mods, no escapes to hide words
assert.strictEqual(cleanCss('body{color:red}'), 'body{color:red}');
assert.strictEqual(cleanCss('a{background:url(https://evil.example/x)}'), 'a{background:none}');
assert.strictEqual(cleanCss('a{background:u/**/rl(https://evil.example/x)}'), 'a{background:none}');
assert.ok(cleanCss('a{background:url("data:image/png;base64,AAAA")}').includes('data:image/png'));
assert.strictEqual(cleanCss('a{background:url(data:text/html;base64,AAAA)}'), 'a{background:none}');
for (const bad of ['@import "x.css";', 'a{background:image-set("https://e/x" 1x)}', 'a{background:\\75rl(https://e/x)}', '@\\69mport "x";', '#owui-mods-bar{display:none}', '[class*=md-ui]{display:none}', '.md-dialog{opacity:0}', 'a{behavior:url(x.htc)}', 'a{-moz-binding:url(x)}', 'a{background:src("x")}']) {
  assert.throws(() => cleanCss(bad), /not allowed/, bad);
}
assert.ok(cleanCss('#messages .max-w-\\[58rem\\]{max-width:none}').includes('\\['), 'escaped brackets in class names still work');
// The store machine
const store = machine('store', 'idle', {'*': {load: 'loading'}, loading: {done: 'ready', fail: 'failed'}, ready: {save: 'saving'}, saving: {done: 'ready', fail: 'ready'}});
assert.strictEqual(store.send('save'), false, 'nothing is saved before it is loaded');
assert.ok(store.send('load') && store.send('done') && store.send('save') && store.send('fail') && store.is('ready'));
assert.strictEqual(document.documentElement.dataset.mdStore, 'ready');
console.log('ok');
""",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stdout + result.stderr


def test_studio_guide_documents_every_permission(plugin):
    script = plugin.LOADER_SCRIPT
    guide = script[script.index("const GUIDE = [") : script.index("].join('\\n');")]
    for perm in plugin.PERMISSIONS:
        assert f"'- {perm}:" in guide, perm


def test_shortcut_is_free_in_open_webui(plugin):
    # Open WebUI 0.13 uses Cmd/Ctrl+Shift+M for its model selector; Mods uses Cmd/Ctrl+Shift+.
    script = plugin.LOADER_SCRIPT
    assert "event.code !== 'Period'" in script and "key.toLowerCase() === 'm'" not in script
