"""Server-side and logic tests for Artifacts. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_artifacts.py

The function is loaded the way Open WebUI loads an Event Function, with the
few Open WebUI modules it imports replaced by stand-ins. The parsing, version
and spreadsheet code is run in Node where Node is installed.
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
SOURCE_PATH = HERE.parent / "chat_artifacts.py"
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
DEFAULT_CONFIG = {
    "autoOpen": True, "cardsInChat": True, "collapseCode": False, "minCodeLines": 12, "minDocumentChars": 1500, "tablesAsSheets": True,
    "network": "libraries", "esmCdn": "https://esm.sh", "babelUrl": "https://cdn.jsdelivr.net/npm/@babel/standalone@7.29.10/babel.min.js",
    "tailwindUrl": "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.3.3/dist/index.global.js", "mermaidUrl": "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js",
    "extraHosts": "https://cdn.jsdelivr.net, https://cdnjs.cloudflare.com, https://unpkg.com, https://fonts.googleapis.com, https://fonts.gstatic.com",
    "allowAi": True, "aiPerMinute": 10, "allowStorage": True, "keepDrafts": True, "allowSave": True, "allowShare": True, "templates": True, "replaceBuiltin": True,
    "headerButton": True, "chatMenu": True, "platformName": "", "language": "auto",
}


def load_plugin():
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_artifacts_{uuid.uuid4().hex}"
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
    start, end = "// owui-chat-artifacts:start", "// owui-chat-artifacts:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def config_of(text):
    return json.loads(re.search(r"const CONFIG = (\{.*?\});", text).group(1))


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Artifacts\n')
    assert "required_open_webui_version: 0.13.0" in SOURCE
    assert f"## [{version}]" in (HERE.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"Version v{version}" in (HERE.parent / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("title", PROTOCOL_BLOCKS)
def test_protocol_blocks_are_untouched(title):
    end_marker, expected = PROTOCOL_BLOCKS[title]
    start = SOURCE.rindex("# ====", 0, SOURCE.index(title))
    block = SOURCE[start : SOURCE.index(end_marker) + len(end_marker)] + "\n"
    assert hashlib.sha256(block.encode("utf-8")).hexdigest() == expected


def test_function_id_and_key_drop_the_random_suffix(plugin):
    assert plugin.RELOAD_FUNCTION_ID == "chat_artifacts"
    assert plugin.ASSET_KEY == "function_chat_artifacts"


def test_script_is_served_with_the_default_settings(served):
    _, _, _, client = served
    response = client.get("/static/loader.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    script = script_of(response.text)
    assert "__CONFIG__" not in script and "__CSS__" not in script
    assert config_of(script) == DEFAULT_CONFIG


def test_a_repeat_request_is_answered_with_304(served):
    _, _, _, client = served
    first = client.get("/static/loader.js")
    again = client.get("/static/loader.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and not again.content


def test_a_valve_change_reaches_the_next_request(served):
    _, event, _, client = served
    before = client.get("/static/loader.js")
    event.valves = event.Valves(network="off", auto_open=False, language="fr", esm_cdn="https://esm.example.edu/")
    after = client.get("/static/loader.js")
    config = config_of(after.text)
    assert (config["network"], config["autoOpen"], config["language"], config["esmCdn"]) == ("off", False, "fr", "https://esm.example.edu")
    assert after.headers["etag"] != before.headers["etag"]
    assert event._loader_fragment() is event._loader_fragment()


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-chat-artifacts" not in client.get("/static/loader.js").text
    plugin.reload_state(app)["active"] = True
    assert "owui-chat-artifacts:start" in client.get("/static/loader.js").text


def test_registering_twice_keeps_one_route(served):
    plugin, _, app, _ = served
    plugin.register(app)
    plugin.register(app)
    assert sum(getattr(route, "path", "") == "/static/loader.js" for route in app.routes) == 1


@pytest.mark.parametrize("bad", [{"network": "everything"}, {"min_code_lines": 0}, {"min_document_chars": -1}, {"ai_calls_per_minute": 0}, {"language": "de"}])
def test_out_of_range_valves_are_refused(plugin, bad):
    with pytest.raises(ValidationError):
        plugin.Event.Valves(**bad)


@pytest.mark.parametrize("value", ["javascript:alert(1)", "https://x.test/a\"onload=1", "data:text/javascript,1", "https://x.test/<script>", "ftp://x.test/", " "])
def test_library_addresses_must_be_plain_web_addresses(served, value):
    _, event, _, client = served
    event.valves = event.Valves(esm_cdn=value, babel_url=value, mermaid_url=value, tailwind_url=value, extra_hosts=value + ", https://ok.test")
    config = config_of(client.get("/static/loader.js").text)
    assert config["esmCdn"] == config["babelUrl"] == config["mermaidUrl"] == config["tailwindUrl"] == ""
    assert config["extraHosts"] == "https://ok.test"


def test_a_valve_cannot_end_the_script(served):
    _, event, _, client = served
    event.valves = event.Valves(platform_name="x</script><script>alert(1)</script>")
    text = client.get("/static/loader.js").text
    assert "</script>" not in text and "<\\/script>" in text


def test_every_word_has_a_translation(plugin):
    script = plugin.LOADER_SCRIPT
    english = script[script.index("    en: {") : script.index("    fr: {")]
    french = script[script.index("    fr: {") : script.index("  const language = () =>")]
    keys = lambda block: set(re.findall(r"(?:^|[{,]\s*)(\w+): '", block, re.M)) - {"en", "fr"}
    assert keys(english) and keys(english) == keys(french), sorted(keys(english) ^ keys(french))
    used = set(re.findall(r"\bt\('(\w+)'(?=[,)])", script))
    assert used <= keys(english), sorted(used - keys(english))
    # Keys built from a prefix: every kind has a name, a description and a ready request
    for kind in ("document", "slides", "sheet", "diagram", "html", "react", "svg", "data", "code", "design"):
        assert {"t_" + kind, "d_" + kind, "tp_" + kind} <= keys(english), kind


def test_python_string_cannot_be_ended_by_the_script(plugin):
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.ARTIFACTS_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_served_script_parses(served, tmp_path):
    _, _, _, client = served
    target = tmp_path / "loader.js"
    target.write_text(client.get("/static/loader.js").text, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def panel_code(script):
    """The script without the source of the documents it builds for sandboxed frames."""
    for start, end in (("const BOOT = `", "`;"), ("const SHIM = `", "`;"), ("const run = `", "`;"), ("const run = `", "`;")):
        while start in script:
            i = script.index(start)
            script = script[:i] + script[script.index(end, i + len(start)) + len(end):]
    return script


def test_the_page_never_writes_model_text_as_html(plugin):
    # Titles, code and documents come from the model and from other people; in this page they are only ever text.
    # (Inside a sandboxed frame an artifact is meant to run, so the frame documents are left out.)
    code = panel_code(plugin.LOADER_SCRIPT)
    for risky in ("innerHTML =", "innerHTML=", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in code, risky
    assert "box.innerHTML" in code, "rendered() reads back the safe DOM it built"


def test_sign_in_token_is_only_sent_to_this_site(plugin):
    script = plugin.LOADER_SCRIPT
    assert "if (!own) throw new Error('Refusing a request to another site')" in script
    assert panel_code(script).count("fetch(") == 1, "only request() may call fetch()"


def test_frames_never_get_this_site(plugin):
    script = plugin.LOADER_SCRIPT
    sandbox = re.search(r"const SANDBOX = '([^']+)'", script).group(1)
    assert "allow-scripts" in sandbox
    for flag in ("allow-same-origin", "allow-top-navigation", "allow-popups-to-escape-sandbox"):
        assert flag not in sandbox
    # Messages from a frame count only from that frame's own window with its own nonce
    assert "const record = frames.get(event.source);" in script and "data.__ar !== record.nonce" in script
    # Printing uses a frame that may not run scripts
    assert "frame.setAttribute('sandbox', 'allow-modals allow-same-origin')" in script and "<script[\\s\\S]*?<\\/script>/gi, ''" in script


def test_content_policy_keeps_frames_to_the_listed_libraries(plugin):
    script = plugin.LOADER_SCRIPT
    strict = re.search(r"return `default-src 'none'; script-src ([^`]+)`;", script).group(1)
    assert "connect-src blob: data: ${hosts}" in strict and "img-src data: blob: ${hosts}" in strict and "form-action 'none'" in strict
    assert "https:" not in strict and "*" not in strict
    off = re.search(r"if \(CONFIG.network === 'off'\) return \"([^\"]+)\"", script).group(1)
    assert "connect-src 'none'" in off and "http" not in off


def test_ai_requests_need_consent_and_are_limited(plugin):
    script = plugin.LOADER_SCRIPT
    body = script[script.index("async function aiCall") : script.index("async function storageCall")]
    assert body.index("confirmBox(") < body.index("api('/api/chat/completions'"), "the person is asked before the first request"
    assert "aiTimes.length >= CONFIG.aiPerMinute" in body and "if (!CONFIG.allowAi" in body
    # Only the prompt is sent: no chat id, so nothing is saved to a chat
    assert "messages: [{role: 'user', content: prompt}]" in body and "chat_id" not in body


NODE_HARNESS = r"""
const assert = require('assert');
const CONFIG = {minCodeLines: 12, minDocumentChars: 600, tablesAsSheets: true};
let lang = 'en';
const t = (k, v) => k;
const FENCE = '```';
""" + "\n"


def logic(script):
    take = lambda a, b: script[script.index(a) : script.index(b)]
    return (
        NODE_HARNESS
        + "\n".join(line for line in script.split("\n") if line.lstrip().startswith(("const countLines =", "const decodeEntities =", "const clamp =", "const toMs =")))
        + "\n" + take("  /* ---- finding artifacts in a chat ---- */", "  /* ---- what is kept in this browser")
        + "\n" + take("  function parseDelimited(", "  function sheetView(")
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_artifacts_versions_and_sheets(plugin, tmp_path):
    runner = tmp_path / "logic.js"
    runner.write_text(logic(plugin.LOADER_SCRIPT) + r"""
const chat = (turns, branch) => { const messages = {}; let prev = null; turns.forEach(([role, content], i) => { const id = 'm' + i; messages[id] = {id, parentId: prev, role, content, timestamp: i, model: 'x'}; prev = id; }); return {chat: {history: {messages, currentId: branch || prev}}}; };
const page = (title, body) => FENCE + 'html\n<!doctype html>\n<html><head>' + (title ? '<title>' + title + '</title>' : '') + '</head>\n<body>\n<h1>' + body + '</h1>\n<p>x</p>\n</body>\n</html>\n' + FENCE;
// The same <title> in a later answer is the next version; a different one is a new artifact
let list = artifactsOf(chat([['user', 'a'], ['assistant', page('Budget', 'One')], ['user', 'b'], ['assistant', 'Sure.\n\n' + page('Budget', 'Two')], ['user', 'c'], ['assistant', page('Other', 'Three')]]));
assert.deepStrictEqual(list.map(a => [a.title, a.versions.length]), [['Budget', 2], ['Other', 1]]);
assert.strictEqual(list[0].key, 'am1-0');
// Untitled, one after the other: versions; a reply in between without artifacts keeps the chain
list = artifactsOf(chat([['user', 'a'], ['assistant', page('', 'First')], ['user', 'thanks'], ['assistant', 'You are welcome'], ['user', 'b'], ['assistant', page('', 'Second')]]));
assert.deepStrictEqual(list.map(a => a.versions.length), [2]);
assert.strictEqual(list[0].title, 'Second', 'a guessed title follows the latest version');
// Two blocks of one kind in one answer are two artifacts, and the next answer's one isn't guessed onto either
list = artifactsOf(chat([['user', 'a'], ['assistant', page('', 'A') + '\n' + page('', 'B')], ['user', 'b'], ['assistant', page('', 'C')]]));
assert.strictEqual(list.length, 3);
// A repeated identical version isn't added twice
list = artifactsOf(chat([['user', 'a'], ['assistant', page('Same', 'X')], ['user', 'b'], ['assistant', page('Same', 'X')]]));
assert.strictEqual(list[0].versions.length, 1);
// Kinds
const kinds = text => candidatesOf({content: text}).map(c => c.type);
assert.deepStrictEqual(kinds(FENCE + 'mermaid\nflowchart TD\nA-->B\n' + FENCE), ['diagram']);
assert.deepStrictEqual(kinds(FENCE + 'xml\n<svg viewBox="0 0 1 1"></svg>\n' + FENCE), ['svg']);
assert.deepStrictEqual(kinds(FENCE + 'jsx\nexport default function App(){return <div/>}\n' + FENCE), ['react']);
assert.deepStrictEqual(kinds(FENCE + 'javascript\nimport React from "react";\nexport default function A(){ return <p>x</p> }\n' + FENCE), ['react']);
assert.deepStrictEqual(kinds(FENCE + 'csv\na,b\n1,2\n' + FENCE), ['sheet']);
assert.deepStrictEqual(kinds(FENCE + 'markdown\n# Deck\n---\n# Two\n---\n# Three\n' + FENCE), ['slides']);
assert.deepStrictEqual(kinds(FENCE + 'python\nprint(1)\n' + FENCE), [], 'short code is not an artifact');
assert.deepStrictEqual(kinds('Some text `inline` only'), []);
assert.deepStrictEqual(kinds('<details type="reasoning">\n' + FENCE + 'html\n<html><body>x</body></html>\n\n\n\n\n' + FENCE + '\n</details>'), [], 'reasoning is not part of the answer');
const open = FENCE + 'html\n<html>\n<head></head>\n<body>\n<h1>Still</h1>\nbeing written';
assert.deepStrictEqual(kinds(open), ['html']);
assert.strictEqual(candidatesOf({content: open})[0].unfinished, true, 'a block left open runs to the end');
const table = '## Prices\n\n| Item | Cost |\n| --- | --- |\n| A | 1 |\n| B, big | 2 |\n| C | 3 |\n';
const sheet = candidatesOf({content: table})[0];
assert.strictEqual(sheet.type, 'sheet'); assert.strictEqual(sheet.title, 'Prices'); assert.strictEqual(sheet.text.split('\n')[2], '"B, big",2');
// Names
assert.strictEqual(candidatesOf({content: FENCE + 'python title="grades.py"\n' + 'x = 1\n'.repeat(12) + FENCE})[0].title, 'grades.py');
assert.strictEqual(candidatesOf({content: FENCE + 'jsx\n// artifact: Habit tracker\nexport default function X(){return <div/>}\n' + FENCE})[0].title, 'Habit tracker');
assert.strictEqual(candidatesOf({content: FENCE + 'jsx\nexport default function BudgetPlanner(){return <div/>}\n' + FENCE})[0].title, 'Budget Planner');
assert.strictEqual(withoutMarker('// artifact: X\n{"a":1}'), '{"a":1}');
// The branch shown, not the other branches
const forked = chat([['user', 'a'], ['assistant', page('Left', 'L')]]);
forked.chat.history.messages.alt = {id: 'alt', parentId: 'm0', role: 'assistant', content: page('Right', 'R'), timestamp: 5};
assert.deepStrictEqual(artifactsOf(forked).map(a => a.title), ['Left']);
forked.chat.history.currentId = 'alt';
assert.deepStrictEqual(artifactsOf(forked).map(a => a.title), ['Right']);
// Broken history doesn't throw
for (const odd of [null, {}, {chat: {history: {messages: {a: {id: 'a', parentId: 'a', role: 'assistant', content: 5}}, currentId: 'a'}}}]) artifactsOf(odd);
// Numbers and formulas
assert.strictEqual(toNumber('1,234.5'), 1234.5); assert.strictEqual(toNumber('1 234,5'), 1234.5); assert.strictEqual(toNumber('12%'), 0.12); assert.strictEqual(toNumber('€5'), 5); assert.strictEqual(toNumber('abc'), null);
const rows = [['Item', 'Cost', 'N', 'Total'], ['a', '2', '3', '=B2*C2'], ['b', '4', '5', '=B3*C3'], ['', '', '=SUM(C2:C3)', '=SUM(D:D)'], ['=A9', '=1/0', '=D5', '=C5+1'], ['=IF(B2>1,"big","small")', '=ROUND(2/3,2)', '=AVERAGE(B2:B3)', '=CONCAT(A2,"-",A3)'], ['=FOO(1)', '=(1+2', '=D5', '=MEDIAN(B2:C3)']];
const e = sheetEngine(rows), v = (r, c) => e.value(r, c);
assert.strictEqual(v(1, 3), 6); assert.strictEqual(v(2, 3), 20); assert.strictEqual(v(3, 2), 8); assert.strictEqual(v(3, 3), '#CYCLE!', 'a total inside its own column refers to itself');
assert.strictEqual(v(4, 0), '#REF!'); assert.strictEqual(v(4, 1), '#DIV/0!');
assert.strictEqual(v(4, 2), '#CYCLE!', 'C5 and D5 refer to each other');
assert.strictEqual(v(5, 0), 'big'); assert.strictEqual(v(5, 1), 0.67); assert.strictEqual(v(5, 2), 3); assert.strictEqual(v(5, 3), 'a-b');
assert.strictEqual(v(6, 0), '#NAME?'); assert.strictEqual(v(6, 1), '#ERROR!'); assert.strictEqual(v(6, 3), 3.5);
// Nothing in a formula reaches eval: an attempt is just an unknown name
assert.strictEqual(sheetEngine([['=constructor.constructor("return 1")()']]).value(0, 0), '#NAME?');
const csv = gridOf('a;b\n"x;y";2\n', 'csv');
assert.deepStrictEqual(csv.rows, [['a', 'b'], ['x;y', '2']]);
const Q = '"'; assert.strictEqual(toCsv([['a', 'b,c'], [Q + 'q' + Q, ' s']]), 'a,' + Q + 'b,c' + Q + '\n' + Q + Q + Q + 'q' + Q + Q + Q + ',' + Q + ' s' + Q + '\n');
console.log('ok');
""", encoding="utf-8")
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", (result.stdout + result.stderr)[-3000:]
