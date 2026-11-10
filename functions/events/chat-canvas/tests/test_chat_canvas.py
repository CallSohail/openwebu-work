"""Server-side tests for Chat Canvas. No running Open WebUI is needed.

    python -m pip install "pydantic>=2,<3" starlette httpx pytest
    python -m pytest -q tests/test_chat_canvas.py

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
SOURCE_PATH = HERE.parent / "chat_canvas.py"
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
    """Execute the source as module "function_chat_canvas_<hex>", as Open WebUI does."""
    codec = types.ModuleType("open_webui.utils.json_codec")
    codec.JSONCodec = types.SimpleNamespace(dumps=json.dumps, loads=json.loads)
    sys.modules.setdefault("open_webui", types.ModuleType("open_webui"))
    sys.modules.setdefault("open_webui.utils", types.ModuleType("open_webui.utils"))
    sys.modules["open_webui.utils.json_codec"] = codec
    name = f"function_chat_canvas_{uuid.uuid4().hex}"
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
    start, end = "// owui-chat-canvas:start", "// owui-chat-canvas:end"
    assert text.count(start) == 1 and text.count(end) == 1
    return text[text.index(start) + len(start) : text.index(end)]


def test_metadata_and_versions_agree():
    version = re.search(r"^version: (\S+)$", SOURCE, re.M).group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert SOURCE.startswith('"""\ntitle: Chat Canvas\n')
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
    assert plugin.RELOAD_FUNCTION_ID == "chat_canvas"
    assert plugin.ASSET_KEY == "function_chat_canvas"


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
        "minCodeLines": 3,
        "minDocumentChars": 1200,
        "runScripts": True,
        "keepDrafts": True,
        "allowSave": True,
        "headerButton": True,
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
    event.valves = event.Valves(min_code_lines=1, run_scripts=False, keep_drafts=False, language="fr")
    after = client.get("/static/loader.js")
    config = json.loads(re.search(r"const CONFIG = (\{.*?\});", after.text).group(1))
    assert (config["minCodeLines"], config["runScripts"], config["keepDrafts"], config["language"]) == (1, False, False, "fr")
    assert after.headers["etag"] != before.headers["etag"]
    assert event._loader_fragment() is event._loader_fragment(), "the script must be rebuilt only when a valve changes"


def test_disabling_the_function_withdraws_the_script(served):
    plugin, _, app, client = served
    plugin.reload_state(app)["active"] = False
    assert "owui-chat-canvas" not in client.get("/static/loader.js").text
    plugin.reload_state(app)["active"] = True
    assert "owui-chat-canvas:start" in client.get("/static/loader.js").text


def test_registering_twice_keeps_one_route(served):
    plugin, _, app, _ = served
    plugin.register(app)
    plugin.register(app)
    assert sum(getattr(route, "path", "") == "/static/loader.js" for route in app.routes) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"min_code_lines": 0},
        {"min_code_lines": 5000},
        {"min_document_chars": -1},
        {"language": "de"},
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
    assert '"""' not in plugin.LOADER_SCRIPT and '"""' not in plugin.CANVAS_CSS


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


def test_html_preview_is_always_sandboxed_without_same_origin(plugin):
    script = plugin.LOADER_SCRIPT
    # With allow-same-origin a previewed page could read the session; it must never be granted
    assert "allow-same-origin" not in script
    assert "frame.setAttribute('sandbox', CONFIG.runScripts ? 'allow-scripts' : '')" in script
    assert script.count("srcdoc") == 1 and script.count("createElement('iframe')") + script.count("el('iframe'") == 1


def test_drawings_are_shown_as_images(plugin):
    # An SVG inside <img> can't run scripts or load from elsewhere
    assert "new Blob([item.text], {type: 'image/svg+xml'})" in plugin.LOADER_SCRIPT


def test_nothing_is_sent_to_the_model_for_the_person(plugin):
    script = plugin.LOADER_SCRIPT
    # Canvas only fills the message box; these two message types would submit it
    assert "input:prompt:submit" not in script and "action:submit" not in script
    assert script.count("type: 'input:prompt'") == 1
    assert "location.origin)" in script


def test_templates_are_kept_out_of_the_message_box(plugin):
    # Open WebUI fills {{...}} in text set through its message box hook
    assert "!text.includes('{{')" in plugin.LOADER_SCRIPT


def test_shares_the_tab_protocol_with_gallery_and_files(plugin):
    script = plugin.LOADER_SCRIPT
    assert "'owui-panel-tab-front'" in script and "owner: 'chat-canvas'" in script
    assert "'owui-chat-gallery-tabs'" in script and "'owui-chat-files-tabs'" in script
    assert "data-cg-covered" not in script and "data-cf-covered" not in script and "data-cv-covered" in script
