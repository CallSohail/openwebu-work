"""Server-side tests for Chat Files. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_files.py

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
SOURCE_PATH = HERE.parent / "chat_files.py"
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
    """Execute the source as module "function_chat_files_<hex>", as Open WebUI does."""
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_files_{uuid.uuid4().hex}"
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
    start, end = "// owui-chat-files:start", "// owui-chat-files:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Chat Files\n')
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
    assert plugin.RELOAD_FUNCTION_ID == "chat_files"
    assert plugin.ASSET_KEY == "function_chat_files"


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
        "maxZipMb": 300,
        "includeImages": "auto",
        "showReferences": True,
        "allowRename": True,
        "allowDelete": True,
        "headerButton": True,
        "buttonBesideMenu": False,
        "defaultView": "list",
        "thumbnails": True,
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
    event.valves = event.Valves(max_zip_mb=50, include_images="yes", allow_delete=False, language="fr")
    after = client.get("/static/loader.js")
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", after.text).group(1))
    assert (config["maxZipMb"], config["includeImages"], config["allowDelete"], config["language"]) == (50, "yes", False, "fr")
    assert after.headers["etag"] != before.headers["etag"]
    assert event._loader_fragment() is event._loader_fragment(), "the script must be rebuilt only when a valve changes"


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-chat-files" not in client.get("/static/loader.js").text
    plugin.reload_state(app)["active"] = True
    assert "owui-chat-files:start" in client.get("/static/loader.js").text


def test_registering_twice_keeps_one_route(served):
    plugin, _, app, _ = served
    plugin.register(app)
    plugin.register(app)
    assert sum(getattr(route, "path", "") == "/static/loader.js" for route in app.routes) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"max_zip_mb": 0},
        {"max_zip_mb": 5000},
        {"include_images": "maybe"},
        {"language": "de"},
        {"default_view": "tiles"},
    ],
)
def test_out_of_range_valves_are_refused(plugin, bad):
    with pytest.raises(ValidationError):
        plugin.Event.Valves(**bad)


def test_every_word_has_a_translation(plugin):
    script = plugin.LOADER_SCRIPT
    english = script[script.index("    en: {") : script.index("    fr: {")]
    french = script[script.index("    fr: {") : script.index("  const language = ")]
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
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.FILES_CSS


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_served_script_parses(served, tmp_path):
    _, _, _, client = served
    target = tmp_path / "loader.js"
    target.write_text(client.get("/static/loader.js").text, encoding="utf-8")
    result = subprocess.run(["node", "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_text_is_never_written_as_html(plugin):
    # File names, extracted text and CSV cells come from users; they must reach the page as text
    script = plugin.LOADER_SCRIPT
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "srcdoc"):
        assert risky not in script, risky


def test_only_real_pdfs_reach_the_pdf_viewer(plugin):
    script = plugin.LOADER_SCRIPT
    assert "magic.includes('%PDF-')" in script
    assert "new Blob([blob], {type: 'application/pdf'})" in script


def test_shares_the_tab_protocol_with_chat_gallery(plugin):
    script = plugin.LOADER_SCRIPT
    # The two take turns through one event, and Files places itself after Gallery's tab and beside its button
    assert "'owui-panel-tab-front'" in script and "owner: 'chat-files'" in script
    assert "'owui-chat-gallery-tabs'" in script and "'owui-chat-gallery-header'" in script
    # Each hides what lies under it with its own marker, so one never uncovers the other's
    assert "data-cg-covered" not in script and "data-cf-covered" in script


def _function_source(script, start, end):
    return script[script.index(start) : script.index(end, script.index(start))]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_state_machines_and_names(plugin, tmp_path):
    script = plugin.LOADER_SCRIPT
    # Every state that changes over time is an explicit machine
    for name in ("tab", "chat", "library", "select", "job", "view", "preview"):
        assert f"machine('{name}'" in script, name
    runner = tmp_path / "logic.js"
    runner.write_text(
        "const assert = require('assert');\nconst document = {documentElement: {dataset: {}}};\n"
        + _function_source(script, "  function machine(", "\n  }\n") + "\n  }\n"
        + "\n".join(line for line in script.split("\n") if line.lstrip().startswith(("const BIDI_RE", "const cleanName")))
        + r"""
const seen = [];
const tab = machine('tab', 'closed', {closed: {open: 'front'}, front: {hide: 'behind', close: 'closed'}, behind: {show: 'front', close: 'closed'}}, (next, from, event) => seen.push(from + '>' + next));
assert.strictEqual(tab.send('hide'), false, 'a closed tab cannot be set aside');
assert.ok(tab.send('open') && tab.send('hide') && tab.is('behind'));
assert.strictEqual(tab.send('open'), false, 'an open tab is shown, not opened again');
assert.ok(tab.send('show') && tab.send('close'));
assert.deepStrictEqual(seen, ['closed>front', 'front>behind', 'behind>front', 'front>closed']);
assert.strictEqual(document.documentElement.dataset.cfTab, 'closed');
const job = machine('job', 'idle', {idle: {zip: 'zipping', delete: 'deleting'}, zipping: {done: 'idle'}, deleting: {done: 'idle'}});
assert.ok(job.send('zip')); assert.strictEqual(job.send('delete'), false, 'one long job at a time');
// Direction controls never reach a name on screen or on disk
assert.strictEqual(cleanName('invoice\u202Efdp.exe'), 'invoicefdp.exe');
assert.strictEqual(cleanName('\u2067a\u2069\u200Fb\u061C'), 'ab');
assert.strictEqual(cleanName('تقرير.txt'), 'تقرير.txt');
assert.strictEqual(cleanName(null), '');
console.log('ok');
""",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(runner)], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stdout + result.stderr


def test_downloads_and_renames_drop_direction_controls(plugin):
    script = plugin.LOADER_SCRIPT
    assert "let clean = cleanName(name)" in script  # safeName, used for every download and zip entry
    assert "const name = cleanName(input.value).trim();" in script  # rename
    assert "item.name = cleanName(meta.name" in script  # every record read


def test_a_zip_never_needs_its_64_bit_form(plugin):
    script = plugin.LOADER_SCRIPT
    assert "list.length > 65535" in script
    # The largest zip a valve allows stays under 4 GB, the limit of a 32-bit size
    assert plugin.Event.Valves.model_fields["max_zip_mb"].metadata[-1].le * 1048576 < 2**32


def test_files_is_chosen_from_the_plus_menu_where_there_is_one(plugin):
    script = plugin.LOADER_SCRIPT
    assert "const menuThere = !!(found?.list && plusOf(found.list));" in script
    assert "(!menuThere || CONFIG.buttonBesideMenu)" in script
