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
    assert sinks and all(re.fullmatch(r"sprite\(pet\)|ICON(\.\w+|\[name\])", s.strip()) for s in sinks), sinks
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
    assert "send-message-button" not in script and "requestSubmit" not in script and ".submit(" not in script
    assert "Enter" not in insert


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
