"""Server-side tests for Chat Inspector. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_inspector.py

The function is loaded the way Open WebUI loads an Event Function, with the
few Open WebUI modules it imports replaced by stand-ins.
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
SOURCE_PATH = HERE.parent / "chat_inspector.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

# The two protocol blocks must stay byte for byte what Classic298 publishes at
# https://github.com/Classic298/open-webui-plugins/tree/main/shared-assets-protocol
# (commit 917c88f). Every plugin on a server shares one copy of this code, so a
# local edit here would change behaviour for the others.
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


def load_plugin():
    """Execute the source as module "function_chat_inspector_<hex>", as Open WebUI does."""
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_inspector_{uuid.uuid4().hex}"
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
    """An app on which the function is enabled, and a client for it."""
    app = Starlette()
    event = plugin.Event()
    plugin.reload_mark_loaded(app)
    return plugin, event, app, TestClient(app)


def script_of(text):
    start, end = "// owui-chat-inspector:start", "// owui-chat-inspector:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Chat Inspector\n')
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
    assert plugin.RELOAD_FUNCTION_ID == "chat_inspector"
    assert plugin.ASSET_KEY == "function_chat_inspector"


def test_script_is_served_with_the_default_settings(served):
    _, _, _, client = served
    response = client.get("/static/loader.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "no-cache" in response.headers["cache-control"]
    script = script_of(response.text)
    assert "__CONFIG__" not in script and "__CSS__" not in script
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", script).group(1))
    assert config == {
        "contextWindows": "",
        "warnPercent": 80,
        "showCapabilities": True,
        "headerButton": False,
        "platformName": "",
        "openAs": "popup",
        "chatMenu": True,
        "plusMenu": False,
        "language": "auto",
    }


def test_a_repeat_request_is_answered_with_304(served):
    _, _, _, client = served
    first = client.get("/static/loader.js")
    again = client.get("/static/loader.js", headers={"If-None-Match": first.headers["etag"]})
    weak = client.get("/static/loader.js", headers={"If-None-Match": "W/" + first.headers["etag"]})
    assert again.status_code == 304 and weak.status_code == 304 and not again.content


def test_a_valve_change_reaches_the_next_request(served):
    plugin, event, _, client = served
    before = client.get("/static/loader.js")
    # Open WebUI's dispatcher assigns a new Valves object before each event
    event.valves = event.Valves(context_windows="gpt-4o* = 128k", warn_percent=60, header_button=True, language="fr")
    after = client.get("/static/loader.js")
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", after.text).group(1))
    assert (config["contextWindows"], config["warnPercent"], config["headerButton"], config["language"]) == ("gpt-4o* = 128k", 60, True, "fr")
    assert after.headers["etag"] != before.headers["etag"]
    assert event._loader_fragment() is event._loader_fragment(), "the script must be rebuilt only when a valve changes"


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-chat-inspector" not in client.get("/static/loader.js").text
    plugin.reload_state(app)["active"] = True
    assert "owui-chat-inspector:start" in client.get("/static/loader.js").text


def test_registering_twice_keeps_one_route(served):
    plugin, _, app, _ = served
    plugin.register(app)
    plugin.register(app)
    assert sum(getattr(route, "path", "") == "/static/loader.js" for route in app.routes) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"warn_percent": 10},
        {"warn_percent": 100},
        {"language": "de"},
        {"open_as": "window"},
    ],
)
def test_out_of_range_valves_are_refused(plugin, bad):
    with pytest.raises(ValidationError):
        plugin.Event.Valves(**bad)


def test_every_word_has_a_translation(plugin):
    script = plugin.LOADER_SCRIPT
    english = script[script.index("    en: {") : script.index("    fr: {")]
    french = script[script.index("    fr: {") : script.index("  const icons = {")]
    keys = lambda block: set(re.findall(r"(?:^|[{,]\s*)(\w+): '", block, re.M)) - {"en", "fr"}
    assert keys(english) and keys(english) == keys(french)
    # Keys written out in full; the few built from a prefix (kind_, src_) are covered by the comparison above
    used = set(re.findall(r"\bt\('(\w+)'(?=[,)])", script)) | {
        key + suffix for key in re.findall(r"\btn\('(\w+)'(?=[,)])", script) for suffix in ("_one", "_other")
    }
    assert used <= keys(english), sorted(used - keys(english))


def test_sign_in_token_is_only_sent_to_this_site(plugin):
    script = plugin.LOADER_SCRIPT
    # Every request goes through request(), which adds the token only after checking where the address really leads
    assert "new URL(url, location.href).origin === location.origin" in script
    assert "own ? authHeaders() : {}" in script
    assert script.count("fetch(") == 1, "only request() may call fetch()"


def test_python_string_cannot_be_ended_by_the_script(plugin):
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.INSPECTOR_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_served_script_parses(served, tmp_path):
    _, _, _, client = served
    target = tmp_path / "loader.js"
    target.write_text(client.get("/static/loader.js").text, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_text_is_never_written_as_html(plugin):
    # Titles, code and documents come from the model and from users; they must reach the page as text
    script = plugin.LOADER_SCRIPT
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in script, risky


def test_a_valve_cannot_end_the_script(served):
    # Valve text lands inside the served script; "</script>" in it must not close a script tag
    _, event, _, client = served
    event.valves = event.Valves(context_windows="x = 1</script><script>alert(1)</script>")
    text = client.get("/static/loader.js").text
    assert "</script>" not in text and "<\\/script>" in text


def test_opens_from_the_chat_menu_as_a_popup_by_default(plugin):
    # Every account has the chat's ⋯ menu; the + menu and the button beside the tabs are opt-in
    valves = plugin.Event.Valves()
    assert valves.show_in_chat_menu is True and valves.open_as == "popup"
    assert valves.show_in_plus_menu is False and valves.header_button is False
    script = plugin.LOADER_SCRIPT
    assert "'owui-chat-inspector-menu-item'" in script and "#chat-context-menu-button[aria-expanded=\"true\"]" in script


def test_wording_uses_the_platform_name(plugin):
    # The words come from the server's own name (WEBUI_NAME), so EveGPT reads "EveGPT", not "Open WebUI"
    script = plugin.LOADER_SCRIPT
    strings = script[script.index("  const STRINGS = {") : script.index("  const icons = {")]
    assert "Open WebUI" not in strings and strings.count("{app}") >= 6
    assert "api('/api/config')" in script and r"\(Open WebUI\)" in script


def test_platform_name_valve_is_trimmed_and_bounded(served):
    _, event, _, client = served
    event.valves = event.Valves(platform_name="  EveGPT  ")
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", client.get("/static/loader.js").text).group(1))
    assert config["platformName"] == "EveGPT"


def test_reads_only(plugin):
    script = plugin.LOADER_SCRIPT
    # Every request goes through request() and api(), and none of them changes anything
    for verb in ("method: 'POST'", "method: 'DELETE'", "method: 'PUT'", "method: 'PATCH'"):
        assert verb not in script, verb
    assert "setInterval(" in script and script.count("setInterval(") == 1, "one cheap page watcher, no polling of the server"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_token_counts_are_close_to_real_tokenizers(plugin, tmp_path):
    # Real counts from the GPT-4o, GPT-4, Llama 3.1, Qwen 2.5 and Mistral tokenizers, for passages the tuning never saw
    samples = json.loads((HERE / "token_samples.json").read_text(encoding="utf-8"))
    script = plugin.LOADER_SCRIPT
    start = script.index("  const TUNING")
    end = script.index("\n  }\n", script.index("  function countTokens")) + 4
    runner = tmp_path / "count.js"
    runner.write_text(
        script[start:end]
        + "\nconst samples = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));"
        + "\nprocess.stdout.write(JSON.stringify(samples.map(s => Object.fromEntries(Object.keys(s.tokens).map(f => [f, countTokens(s.text, f)])))));",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(runner), str(HERE / "token_samples.json")], capture_output=True, text=True, check=True)
    estimates = json.loads(result.stdout)
    errors = {family: [] for family in samples[0]["tokens"]}
    for sample, estimate in zip(samples, estimates):
        for family, real in sample["tokens"].items():
            errors[family].append(abs(estimate[family] / real - 1))
    for family, values in errors.items():
        mean = sum(values) / len(values)
        assert mean < 0.15, f"{family}: mean error {mean:.0%}"
        assert max(values) < 0.45, f"{family}: worst error {max(values):.0%}"


def test_unknown_families_use_the_safest_tuning(plugin):
    assert "const DEFAULT_FAMILY = 'Llama';" in plugin.LOADER_SCRIPT


def test_html_is_never_written(plugin):
    script = plugin.LOADER_SCRIPT
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in script, risky


def test_shares_the_tab_protocol_with_the_others(plugin):
    script = plugin.LOADER_SCRIPT
    assert "'owui-panel-tab-front'" in script and "owner: 'chat-inspector'" in script
    for other in ("owui-chat-gallery-tabs", "owui-chat-files-tabs", "owui-chat-canvas-tabs", "owui-chat-canvas-header"):
        assert other in script, other
    assert "data-ci-covered" in script and not any(m in script for m in ("data-cg-covered", "data-cf-covered", "data-cv-covered"))


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_tool_activity_is_classified_and_counted(plugin, tmp_path):
    script = plugin.LOADER_SCRIPT
    tokens = script[script.index("  const TUNING") : script.index("\n  }\n", script.index("  function countTokens")) + 4]
    helpers = [line for line in script.split("\n") if line.lstrip().startswith(("const decodeEntities =", "const isNum ="))]
    activity = script[script.index("  /* ---- tools, searches and functions") : script.index("  const CAPS = [")]
    helpers.append(script[script.index("  // What of a saved output item") : script.index("  const textOf = ")])
    runner = tmp_path / "activity.js"
    runner.write_text(tokens + "\n".join(helpers) + "\n" + activity + r"""
const assert = require('assert');
const catalog = catalogOf([
  {id: 'calc', name: 'Calculator', specs: [{name: 'add'}]},
  {id: 'other', name: 'Other', specs: [{name: 'add'}]},
  {id: 'server:mcp:docs', name: 'Docs MCP'},
  {id: 'server:mcp:docs_v2', name: 'Docs v2'},
  {id: 'server:shop', name: 'Shop API', specs: [{name: 'get_pet'}]},
  null, {}, {id: 'broken', specs: 'nope'}
]);
const c = name => classify(name, catalog, false);
assert.deepStrictEqual(c('add'), {kind: 'workspace', source: 'Calculator'});
assert.deepStrictEqual(c('other_add'), {kind: 'workspace', source: 'Other'}, 'a shared name gets the tool id in front');
assert.deepStrictEqual(c('docs_v2_search'), {kind: 'mcp', source: 'Docs v2', tool: 'search'}, 'the longest server id wins');
assert.deepStrictEqual(c('docs_search'), {kind: 'mcp', source: 'Docs MCP', tool: 'search'});
assert.strictEqual(c('docs_').kind, 'other', 'a bare prefix is not a tool');
assert.deepStrictEqual(c('get_pet'), {kind: 'server', source: 'Shop API'});
assert.deepStrictEqual(c('search_web'), {kind: 'builtin', category: 'web'});
assert.deepStrictEqual(classify('recall_memories', catalog, true), {kind: 'auto', category: 'memory'});
assert.deepStrictEqual(classify('mystery', catalogOf(null), false), {kind: 'other'});
// One answer with every shape Open WebUI saves
const message = {done: true, output: [
  null, 'junk', {type: 'reasoning', duration: 2},
  {type: 'function_call', call_id: 'h', name: 'view_skill', hidden: true, arguments: '{}', status: 'completed'},
  {type: 'function_call_output', call_id: 'h', output: [{type: 'input_text', text: 'skill'}]},
  {type: 'function_call', call_id: 'a', name: 'search_web', arguments: '{"query": "q1"}', status: 'completed', started_at: 10, ended_at: 11.5},
  {type: 'function_call', call_id: 'b', name: 'add', arguments: {a: 1}, status: 'completed'},
  {type: 'function_call', call_id: 'x', name: 'lost'},
  {type: 'function_call_output', call_id: 'a', output: 'plain string result'},
  {type: 'function_call_output', call_id: 'b', output: [{type: 'input_text', text: '{"error": "boom"}'}]},
  {type: 'function_call_output', call_id: 'orphan', output: [{type: 'input_text', text: 'no call'}]},
  {type: 'message', content: [{type: 'output_text', text: 'thinking more'}]},
  {type: 'function_call', call_id: 'd', name: 'fetch_url', arguments: '{"url": "https://e.test/x"}', status: 'rejected'},
  {type: 'function_call_output', call_id: 'd', output: [], status: 'rejected'},
  {type: 'web_search_call', status: 'completed', action: {query: 'provider q'}},
  {type: 'open_webui:code_interpreter', code: 'print(1)', status: 'completed'},
  {type: 'message', content: [{type: 'output_text', text: 'done'}]}
], statusHistory: [null, {action: 'web_search_queries_generated', queries: ['q0', 'q0', '']}, {action: 'web_search', urls: ['https://e.test/x', 'https://e.test/y']}, {action: 'knowledge_search'}, {action: 'tool_connection', error: true, description: 'down'}],
  sources: [null, {source: {name: 'Doc'}, document: ['a', 'b']}, {source: {name: 'calc/add'}, document: ['3'], metadata: [{parameters: {a: 1, b: 2}}], tool_result: true}],
  files: [{type: 'image'}, {type: 'file'}]};
const a = activityOf(message, catalog, 'Llama');
const byName = name => a.calls.filter(call => call.name === name);
assert.strictEqual(byName('view_skill')[0].kind, 'auto');
assert.strictEqual(byName('search_web')[0].seconds, 1.5);
assert.strictEqual(byName('add')[0].status, 'failed', 'an {"error": ...} result is a failure');
assert.strictEqual(byName('add')[1].prompted, true, 'a call made through the prompt is found in the sources');
assert.strictEqual(byName('lost')[0].status, 'noresult');
assert.strictEqual(byName('?').length, 1, 'a result without its call still counts');
assert.strictEqual(byName('fetch_url')[0].status, 'denied');
assert.strictEqual(byName('web_search')[0].kind, 'provider');
assert.strictEqual(a.code, 1);
assert.strictEqual(a.rounds, 3, 'two batches of calls, then the final answer');
assert.deepStrictEqual(a.searches, ['q0', 'q1', 'provider q']);
assert.strictEqual(a.pages, 2, 'the same page twice counts once');
assert.strictEqual(a.knowledge, 1); assert.strictEqual(a.retrieved, 1); assert.strictEqual(a.images, 1);
assert.deepStrictEqual(a.connectionErrors, ['down']);
assert.strictEqual(a.thinking, 2);
// A streaming answer: calls without a result are still running
assert.strictEqual(activityOf({done: false, output: [{type: 'function_call', call_id: 'z', name: 'add'}]}, catalog).calls[0].status, 'running');
// Older chats: tool calls inside the text
const old = activityOf({content: 'x <details type="tool_calls" done="true" name="add" arguments="{&quot;a&quot;:1}" result="&quot;2&quot;"></details> <details type="reasoning" duration="4"></details>'}, catalog, 'Llama');
assert.strictEqual(old.calls.length, 1); assert.strictEqual(old.calls[0].kind, 'workspace'); assert.strictEqual(old.reasoning, 1);
// Nothing breaks on odd messages
for (const odd of [{}, {output: 'x'}, {output: [{}]}, {content: 5}, {sources: 'x', statusHistory: {}}]) activityOf(odd, catalog);
const sum = tally([a, old, activityOf({}, catalog)]);
assert.strictEqual(sum.calls, 9); assert.strictEqual(sum.hidden, 1); assert.strictEqual(sum.failed, 2); assert.strictEqual(sum.denied, 1);
assert.strictEqual(sum.tools.find(row => row.name === 'add').count, 3);
assert.strictEqual(sum.tools[sum.tools.length - 1].kind, 'auto');
assert.ok(toolTokensOf(message, 'Llama') > 0 && toolTokensOf({content: 'plain'}, 'Llama') === 0);
// Filters, actions and pipes
const functions = [
  {id: 'g', type: 'filter', name: 'Global', is_active: true, is_global: true},
  {id: 'm', type: 'filter', name: 'Mine', is_active: true, is_global: false},
  {id: 'off', type: 'filter', name: 'Off', is_active: false, is_global: true},
  {id: 'nope', type: 'filter', name: 'Not here', is_active: true, is_global: false},
  {id: 'act', type: 'action', name: 'Act', is_active: true, is_global: false},
  {id: 'pipey', type: 'pipe', name: 'Pipey', is_active: true}
];
const fn = functionsFor({id: 'pipey.sub', pipe: {type: 'pipe'}, filters: [{id: 'm'}], info: {meta: {filterIds: ['m', 'off'], actionIds: ['act']}}}, functions);
assert.deepStrictEqual(fn.filters.map(f => f.name + ':' + f.toggle), ['Global:false', 'Mine:true']);
assert.deepStrictEqual(fn.actions.map(f => f.name), ['Act']);
assert.strictEqual(fn.pipe, 'Pipey');
assert.strictEqual(functionsFor({id: 'x'}, null), null, 'without the list, nothing is claimed');
console.log('ok');
""", encoding="utf-8")
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr[-2000:]
