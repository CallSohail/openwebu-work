"""Tests for Rich Visuals. No Open WebUI needed.

    python -m pip install "pydantic>=2,<3" pytest
    python -m pytest -q test_rich_visuals.py
"""

import asyncio
import importlib.util
import inspect
import json
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("rich_visuals", HERE / "rich_visuals.py")
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)

VIZ_MODEL = {"id": "m", "info": {"meta": {"builtinTools": {"visualizations": True}, "capabilities": {}}}}
PLAIN_MODEL = {"id": "m", "info": {"meta": {}}}
META = {"chat_id": "c1", "message_id": "m1", "session_id": "s1"}


def run(coro):
    return asyncio.run(coro)


def body(text, system=None):
    messages = [{"role": "system", "content": system}] if system else []
    return {"messages": messages + [{"role": "user", "content": text}]}


def system_of(b):
    return next((m["content"] for m in b["messages"] if m["role"] == "system"), "")


# ------------------------------------------------------------- when it acts

@pytest.mark.parametrize("text", ["hi", "Hello!", "hey there", "Bonjour !", "bonsoir à tous", "salut", "coucou toi", "merci", "merci beaucoup",
                                  "thanks a lot", "thank you so much", "ok", "OK thanks", "d'accord", "super, merci", "how are you?", "ça va ?",
                                  "hola", "gracias", "danke", "ciao", "bye", "au revoir", "à bientôt", "👍", "!!!", "good morning", "salam"])
def test_greetings_and_small_talk_are_left_alone(text):
    assert rv.classify(text)["mode"] == "skip", text


@pytest.mark.parametrize("text, topic", [
    ("What is the temperature in Paris this week?", "weather"),
    ("Quel temps fait-il à Lyon demain ?", "weather"),
    ("Will it rain in London tomorrow?", "weather"),
    ("Compare Python and Java for beginners", "compare"),
    ("How much does a 250,000 euro mortgage cost at 3.5%?", "money"),
    ("Show the growth of renewable energy since 2010", "data"),
    ("Plan my week of revision before the exam", "time"),
    ("How do I install Docker on Ubuntu, step by step?", "process"),
    ("How many calories should I eat to lose weight?", "health"),
    ("Explain photosynthesis", "science"),
])
def test_questions_that_benefit_from_a_visual(text, topic):
    decision = rv.classify(text)
    assert decision["mode"] == "visual" and topic in decision["topics"], (text, decision)


@pytest.mark.parametrize("text", ["Explain how DNS works", "Write a poem about autumn", "What is the capital of Australia?", "Tell me a joke"])
def test_other_substantive_questions_get_rich_text(text):
    assert rv.classify(text)["mode"] == "rich"


@pytest.mark.parametrize("text", ["Weather in Lyon, text only please", "Compare these, no charts", "Météo à Paris, juste du texte", "plain text: budget"])
def test_asking_for_plain_text_is_respected(text):
    assert rv.classify(text)["mode"] == "skip"


def test_greeting_followed_by_a_real_question_is_not_skipped():
    assert rv.classify("thanks! can you compare the iPhone 16 and the Pixel 9?")["mode"] == "visual"
    assert rv.classify("Bonjour, compare le prix de l'essence en France et en Espagne")["mode"] == "visual"


def test_asking_for_emojis_is_noticed():
    assert rv.classify("Give me the weather with emojis")["emoji"] is True
    assert rv.classify("Give me the weather")["emoji"] is False


def test_icons_offered_match_the_topic_and_exist():
    decision = rv.classify("What's the temperature and the chance of rain in Paris?")
    assert decision["icons"][:3] == ["thermometer", "thermometer-sun", "thermometer-snowflake"]
    assert "cloud-rain" in decision["icons"] and all(name in rv.ICONS for name in decision["icons"])


# ------------------------------------------------------------- what the model is told

def test_inlet_asks_for_a_visual_with_icons_when_visualization_is_on():
    f = rv.Filter()
    b = run(f.inlet(body("What is the temperature in Paris this week?", "You are helpful."), __metadata__=META, __model__=VIZ_MODEL))
    system = system_of(b)
    assert system.startswith("You are helpful.") and rv.MARKER in system
    assert "render_visualization" in system and "[[icon:" in system and "thermometer" in system and "No emojis." in system
    assert "accent" not in system  # icons follow the text colour; colour is only for meaning


def test_inlet_without_visualization_only_tidies_text():
    f = rv.Filter()
    b = run(f.inlet(body("What is the temperature in Paris this week?"), __metadata__=META, __model__=PLAIN_MODEL))
    system = system_of(b)
    assert "render_visualization" not in system and "[[icon" not in system and "Markdown" in system


def test_legacy_function_calling_counts_as_no_visualization():
    f = rv.Filter()
    b = run(f.inlet(body("Compare Python and Java"), __metadata__={**META, "params": {"function_calling": "legacy"}}, __model__=VIZ_MODEL))
    assert "render_visualization" not in system_of(b)


def test_inlet_leaves_greetings_tasks_and_opted_out_people_untouched():
    f = rv.Filter()
    for b, kwargs in [
        (body("Bonjour !"), {}),
        (body("Compare Python and Java"), {"__metadata__": {**META, "task": "title_generation"}}),
        (body("Compare Python and Java"), {"__user__": {"valves": {"enabled": False}}}),
    ]:
        before = json.dumps(b)
        run(f.inlet(b, __model__=VIZ_MODEL, **{"__metadata__": META, **kwargs}))
        assert json.dumps(b) == before


def test_injection_is_not_repeated():
    f = rv.Filter()
    b = body("Compare Python and Java", "Base.")
    run(f.inlet(b, __metadata__=META, __model__=VIZ_MODEL))
    run(f.inlet(b, __metadata__=META, __model__=VIZ_MODEL))
    assert system_of(b).count(rv.MARKER) == 1


def test_skip_phrases_and_rich_text_only_setting():
    f = rv.Filter()
    f.valves.skip_phrases = "status report"
    b = body("Status report")
    run(f.inlet(b, __metadata__=META, __model__=VIZ_MODEL))
    assert system_of(b) == ""
    f.valves.visuals = "rich_text_only"
    b = body("Compare Python and Java")
    run(f.inlet(b, __metadata__=META, __model__=VIZ_MODEL))
    assert "render_visualization" not in system_of(b)


def test_it_never_emits_events():
    # No status lines, no messages: the hooks are not even given an event emitter
    for name in ("inlet", "stream", "outlet"):
        assert "__event_emitter__" not in inspect.signature(getattr(rv.Filter, name)).parameters
    assert "event_emitter" not in (HERE / "rich_visuals.py").read_text()


# ------------------------------------------------------------- streaming

def chunks_for(arguments: str, sizes):
    """Split a tool call's arguments into Chat Completions chunks of the given sizes."""
    out, i = [], 0
    first = True
    for size in sizes + [len(arguments)]:
        piece = arguments[i:i + size]
        i += size
        if not piece:
            break
        call = {"index": 0, "function": {"arguments": piece}}
        if first:
            call.update(id="call_1", type="function", function={"name": "render_visualization", "arguments": piece})
            first = False
        out.append({"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]})
    out.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    return out


def stream_all(f, chunks, meta=META):
    args, text = "", ""
    for chunk in chunks:
        chunk = run(f.stream(chunk, __metadata__=meta))
        for choice in chunk.get("choices", []):
            for call in choice.get("delta", {}).get("tool_calls") or []:
                args += call["function"].get("arguments", "")
            text += choice.get("delta", {}).get("content") or ""
    return args, text


def armed_filter(text="What is the temperature in Paris this week?"):
    f = rv.Filter()
    run(f.inlet(body(text), __metadata__=META, __model__=VIZ_MODEL))
    return f


@pytest.mark.parametrize("sizes", [[5], [12, 3, 1, 1, 1, 7], [40, 2, 2, 2, 2, 2, 2, 2], [1] * 120])
def test_icon_tokens_become_svg_even_when_cut_between_chunks(sizes):
    html = '<div class="s">[[icon:thermometer]] <b>18 °C</b> [[icon:rain|Chance of rain]] 40%</div>'
    arguments = json.dumps({"title": "Paris", "html": html})
    f = armed_filter()
    args, _ = stream_all(f, chunks_for(arguments, sizes))
    parsed = json.loads(args)  # the tool call stays valid JSON
    out = parsed["html"]
    assert "[[icon" not in out and out.count("<svg class='owui-icon") == 2
    assert "owui-icon-thermometer" in out and "owui-icon-cloud-rain" in out
    assert "role='img' aria-label='Chance of rain'" in out and rv.ICONS["thermometer"] in out


@pytest.mark.parametrize("ascii_only", [True, False])
@pytest.mark.parametrize("sizes", [[7, 7, 7], [1] * 90, [3, 5, 2, 9, 4]])
def test_emojis_in_a_visual_become_icons_and_unknown_tokens_disappear(ascii_only, sizes):
    # Some providers send arguments with non-ASCII characters as \uXXXX escapes, others as they are
    html = "<p>🌡️ 18 °C ☀️ sunny 🦄 [[icon:does-not-exist]]</p>"
    f = armed_filter()
    args, _ = stream_all(f, chunks_for(json.dumps({"title": "t", "html": html}, ensure_ascii=ascii_only), sizes))
    out = json.loads(args)["html"]
    assert "owui-icon-thermometer" in out and "owui-icon-sun" in out
    assert not rv.EMOJI_RUN_RE.search(out) and "does-not-exist" not in out


def test_emojis_stay_when_asked_for():
    f = armed_filter("Weather in Paris with emojis please")
    _, text = stream_all(f, [{"choices": [{"index": 0, "delta": {"content": "Sunny ☀️ today"}, "finish_reason": "stop"}]}])
    assert text == "Sunny ☀️ today"


def test_text_loses_emojis_and_tokens_but_code_blocks_are_kept():
    f = armed_filter()
    pieces = ["## 🌡️ Tempe", "rature\nIt is warm ✅ [[icon:s", "un]] today.\n`", "``python\nprint('✅')\n``", "`\nDone 👍"]
    _, text = stream_all(f, [{"choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]} for p in pieces]
                         + [{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}])
    assert text.startswith("## Temperature\nIt is warm  today.")
    assert "print('✅')" in text and text.endswith("Done")
    assert "[[icon" not in text and "🌡" not in text


def test_chunks_for_other_answers_pass_untouched():
    f = rv.Filter()
    chunk = {"choices": [{"index": 0, "delta": {"content": "Hello ✅ [[icon:sun]]"}}]}
    assert run(f.stream(json.loads(json.dumps(chunk)), __metadata__={"message_id": "other"})) == chunk


def test_responses_api_text_events_lose_emojis_and_tool_events_pass():
    f = armed_filter()
    text = run(f.stream({"type": "response.output_text.delta", "item_id": "t", "delta": "Warm ✅ day"}, __metadata__=META))
    assert text["delta"] == "Warm day"
    event = {"type": "response.function_call_arguments.delta", "item_id": "fc1", "delta": '{"html":"[[icon:sun]]'}
    assert run(f.stream(dict(event), __metadata__=META)) == event


def test_a_broken_chunk_is_passed_through():
    f = armed_filter()
    weird = {"choices": [{"delta": {"tool_calls": [{"function": None}]}}]}
    assert run(f.stream(weird, __metadata__=META)) is weird


# ------------------------------------------------------------- outlet

def test_outlet_tidies_text_and_leaves_tool_call_blocks_alone():
    f = armed_filter()
    details = '<details type="tool_calls" done="true" arguments="{&quot;html&quot;:&quot;☀️&quot;}"><summary>Tool</summary></details>'
    b = {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": f"Warm ✅ [[icon:sun]]\n{details}\n```\n🙂\n```"}]}
    run(f.outlet(b, __metadata__=META))
    content = b["messages"][-1]["content"]
    assert content.startswith("Warm \n") and details in content and "🙂" in content
    assert f._streams == {}


# ------------------------------------------------------------- icons

def test_every_icon_is_safe_inside_a_json_string_and_uses_plain_svg():
    allowed = {"path", "circle", "line", "rect", "polyline", "polygon", "ellipse"}
    for name, inner in rv.ICONS.items():
        assert '"' not in inner and "\\" not in inner and "<script" not in inner.lower(), name
        assert set(re.findall(r"<(\w+)", inner)) <= allowed, name
    for alias, target in rv.ALIASES.items():
        assert target in rv.ICONS, alias
    for emoji, target in rv.EMOJI_ICONS.items():
        assert target in rv.ICONS, emoji
    assert len(rv.ICONS) >= 200


def test_icon_labels_cannot_break_out():
    svg = rv.icon_svg("sun", "x' onload='alert(1)")
    assert "onload" not in svg.split(">", 1)[0].split("aria-label=")[1][:40] or "'" not in svg.split("aria-label='")[1].split("'")[0]
    assert rv.icon_svg("nope") == ""


def test_joiners_in_ordinary_text_are_kept():
    malayalam = "\u0d28\u0d4d\u200d"  # a chillu letter written with a zero-width joiner
    assert rv.strip_emojis(malayalam + " ok") == malayalam + " ok"
    assert rv.strip_emojis("family 👨\u200d👩\u200d👧 done") == "family done"


@pytest.mark.parametrize("before, after", [("Hello 👋 How can I help?", "Hello How can I help?"), ("in Paris 🌤️:", "in Paris:"),
                                           ("Done ✅.", "Done."), ("✅ done", "done"), ("## 🌡️ Temperature", "## Temperature"),
                                           ("a 👍🏽 👍 b", "a b"), ("x → y ✓", "x → y ✓")])
def test_removing_emojis_keeps_the_spacing_right(before, after):
    assert rv.strip_emojis(before) == after


@pytest.mark.parametrize("element, icon", [
    ("<span class='icon'>wallet</span>", "wallet"),
    ('<i class="icon">sun</i>', "sun"),
    ('<span class="kpi-icon icon">rent</span>', None),
    ('<i data-icon="thermometer"></i>', "thermometer"),
    ("<span class='icons'>food</span>", "utensils"),
    ("<span class='label'>wallet</span>", None),
])
@pytest.mark.parametrize("sizes", [[4], [1] * 80, [13, 2, 7]])
def test_icon_names_written_inside_elements_become_icons(element, icon, sizes):
    # What small models write instead of the token, seen in testing with a real 3B model
    f = armed_filter("Show my monthly budget: rent 700, food 300")
    html = "<li>" + element + " Rent: 700</li>"
    args, _ = stream_all(f, chunks_for(json.dumps({"title": "t", "html": html}), sizes))
    out = json.loads(args)["html"]
    if icon:
        assert f"owui-icon-{icon}" in out and element not in out
    else:
        assert element in out and "owui-icon" not in out


@pytest.mark.parametrize("written, icon", [
    ("<img src='https://static.example.dev/icons/thermometer.svg' alt='Thermometer'>", "thermometer"),
    ('<img class="i" src="/icons/wallet.png" alt="Rent">', "wallet"),
    ("<[icon:coins|Price]]", "coins"),
    ("[icon:battery]", "battery"),
    ("[[icon:sun]", "sun"),
    ("{{icon:wind}}", "wind"),
    ("<img src='https://example.com/photo.jpg' alt='Paris'>", None),
    ("<img src='/path/to/icon-scale.png' class='owui-icon'>", "scale"),
    ("<img src='/path/to/icon-battery-check.png' class='owui-icon'>", "battery"),
    ("<img src='https://icons.example.com/png/256/weather/icon-weather-sunny.png' alt='Sunny'>", "sun"),
    ("<span class='owui-icon owui-landmark'></span>", "landmark"),
    ("<span class='owui-icon owui-food'></span>", "utensils"),
    ('<i class="lucide lucide-cloud-rain"></i>', "cloud-rain"),
    ('<i class="fa fa-wallet"></i>', "wallet"),
    ("<span class='owui-icon owui-unicorn'></span>", None),
])
@pytest.mark.parametrize("sizes", [[5], [1] * 120])
def test_loose_icon_forms_from_real_models(written, icon, sizes):
    # Forms a real 3B model produced in testing; an image pointing at an icon file could never load in the sandbox
    f = armed_filter("Show my monthly budget")
    html = "<div>" + written + " 700</div>"
    args, _ = stream_all(f, chunks_for(json.dumps({"title": "t", "html": html}), sizes))
    out = json.loads(args)["html"]
    if icon:
        assert f"owui-icon-{icon}" in out and "icon:" not in out and ".svg'" not in out
    else:
        assert written in out and "<svg" not in out



# ------------------------------------------------------------- the visual is never broken

def tool_chunks(name, arguments, sizes, finish=True):
    chunks = chunks_for(arguments, sizes)
    chunks[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] = name
    return chunks if finish else chunks[:-1]


SCRIPT_HTML = (
    "<div id='c'>[[icon:battery]] Battery</div><canvas id='x'></canvas>"
    "<script>const labels = ['[[icon:battery]] Battery', '📷 Camera', \"{{icon:cpu}} Chip\"];"
    "new Chart(document.getElementById('x'), {data: {labels}});</script>"
    "<style>.k::before{content:'[[icon:sun]]'}</style><!-- [[icon:sun]] -->"
)


@pytest.mark.parametrize("sizes", [[9], [1] * 50, [300]])
def test_tokens_and_emojis_in_scripts_and_styles_never_become_markup(sizes):
    # The bug people saw: an icon put into a single-quoted JavaScript string broke the chart's script
    f = armed_filter("Compare the iPhone 16 and the Pixel 9")
    args, _ = stream_all(f, chunks_for(json.dumps({"title": "Phones", "html": SCRIPT_HTML}), sizes))
    html = json.loads(args)["html"]
    script = re.search(r"<script>([\s\S]*?)</script>", html).group(1)
    assert "<svg" not in script and "[[icon" not in script and "{{icon" not in script and "📷" not in script
    assert "'Battery'" in script and "'Camera'" in script
    assert "<svg" not in re.search(r"<style>([\s\S]*?)</style>", html).group(1)
    assert "<!-- [[icon:sun]] -->" in html  # comments are left as they are
    assert html.startswith("<div id='c'><svg class='owui-icon owui-icon-battery")


def test_tokens_inside_attributes_are_removed_not_drawn():
    out = rv.transform_html('<td title="[[icon:sun]] Sunny ☀️" data-x=\'{{icon:sun}}\'>[[icon:sun]] Sunny</td>', True)
    tag = out.split(">", 1)[0]
    assert "<svg" not in tag and "icon:" not in tag and "☀" not in tag
    assert "owui-icon-sun" in out


def test_comparison_signs_in_text_are_kept():
    out = rv.transform_html("<p>a < b and 3 > 2 [[icon:check]]</p>", True)
    assert "a < b and 3 > 2" in out and "<svg" in out


def test_the_arguments_are_held_until_complete_then_sent_once():
    f = armed_filter()
    arguments = json.dumps({"title": "t", "html": "<p>[[icon:sun]] hi</p>"})
    sent = []
    for chunk in chunks_for(arguments, [4] * 20):
        chunk = run(f.stream(chunk, __metadata__=META))
        for call in chunk["choices"][0]["delta"].get("tool_calls") or []:
            sent.append(call["function"].get("arguments", ""))
    assert sum(1 for piece in sent if piece) == 1
    assert "owui-icon-sun" in json.loads("".join(sent))["html"]


def test_complete_arguments_are_sent_even_without_a_finish_reason():
    f = armed_filter()
    args, _ = stream_all(f, tool_chunks("render_visualization", json.dumps({"title": "t", "html": "[[icon:sun]] x"}), [6] * 10, finish=False))
    assert "owui-icon-sun" in json.loads(args)["html"]


@pytest.mark.parametrize("name", ["web_search", "execute_code", "my_tool"])
def test_other_tools_pass_through_untouched(name):
    f = armed_filter()
    arguments = json.dumps({"query": "[[icon:sun]] ☀️ weather", "html": "[[icon:sun]]"})
    chunks = tool_chunks(name, arguments, [5] * 30)
    sent = []
    for chunk in chunks:
        before = json.dumps(chunk)
        out = run(f.stream(chunk, __metadata__=META))
        assert json.dumps(out) == before
        for call in out["choices"][0]["delta"].get("tool_calls") or []:
            sent.append(call["function"].get("arguments", ""))
    assert "".join(sent) == arguments


def test_two_tool_calls_in_one_answer_are_kept_apart():
    f = armed_filter()
    viz = json.dumps({"title": "t", "html": "[[icon:sun]] a"})
    other = json.dumps({"query": "[[icon:sun]]"})
    stream = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "render_visualization", "arguments": viz[:10]}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 1, "id": "b", "function": {"name": "web_search", "arguments": other}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": viz[10:]}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    got = {0: "", 1: ""}
    for chunk in stream:
        for call in run(f.stream(chunk, __metadata__=META))["choices"][0]["delta"].get("tool_calls") or []:
            got[call["index"]] += call["function"].get("arguments", "")
    assert "owui-icon-sun" in json.loads(got[0])["html"] and got[1] == other


def test_arguments_that_are_not_json_go_out_exactly_as_they_came():
    f = armed_filter()
    broken = '{"title": "t", "html": "[[icon:sun]] unfinished'
    args, _ = stream_all(f, chunks_for(broken, [7] * 10))
    assert args == broken


def test_oversized_arguments_are_passed_through():
    f = armed_filter()
    big = json.dumps({"title": "t", "html": "[[icon:sun]]" + "x" * (rv.MAX_ARGUMENTS + 10)})
    args, _ = stream_all(f, chunks_for(big, [50_000] * 10))
    assert args == big


def test_the_title_loses_emojis_and_tokens():
    out = json.loads(rv.transform_arguments(json.dumps({"title": "☀️ [[icon:sun]] Paris", "html": "x"}), True))
    assert out["title"] == "Paris"


def test_the_instructions_stay_small():
    # Every message pays for these tokens, so they must stay short (about 4 characters per token)
    f = rv.Filter()
    b = run(f.inlet(body("Compare the iPhone 16 and the Pixel 9 on price, battery and camera"), __metadata__=META, __model__=VIZ_MODEL))
    added = system_of(b)
    assert len(added) < 1100, len(added)
    plain = run(rv.Filter().inlet(body("Explain how DNS works"), __metadata__=META, __model__=PLAIN_MODEL))
    assert len(system_of(plain)) < 80
