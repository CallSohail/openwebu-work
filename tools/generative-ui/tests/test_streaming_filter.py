"""Tests for the streaming Filter mode (inline ```genui blocks)."""

import asyncio
import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from demos import load_module  # noqa: E402

gui = load_module()

SPEC = {"title": "Expenses", "icon": "wallet", "state": {"rent": 768, "food": 220},
        "computed": {"total": "rent + food"},
        "components": [{"type": "metric", "label": "Total", "value": "=total", "format": "money", "currency": "EUR"},
                       {"type": "slider", "bind": "rent", "label": "Rent", "min": 0, "max": 2000}]}
BLOCK = "```genui\n" + json.dumps(SPEC, indent=1) + "\n```\n"
ANSWER = ("Here is your spending at a glance.\n\n" + BLOCK +
          "Rent is **67%** of the total.\n\n```python\nprint('``` not a fence end')\n```\n\n"
          "```json\n{\"shown\": true}\n```\nDone.")


def run(c):
    return asyncio.run(c)


class Recorder:
    def __init__(self):
        self.events = []

    async def __call__(self, e):
        self.events.append(e)

    def embeds(self):
        return [e for e in self.events if e["type"] == "embeds"]

    def statuses(self):
        return [e["data"]["description"] for e in self.events if e["type"] == "status"]


def offline_filter(**valves):
    f = gui.Filter()
    f.valves.live_weather = f.valves.resolve_images = f.valves.geocode_places = False
    f.valves.status_updates = True
    for k, v in valves.items():
        setattr(f.valves, k, v)
    return f


def stream_answer(f, text, seed=0, meta=None, rec=None, finish=True):
    meta = meta or {"chat_id": "c1", "message_id": f"m{seed}"}
    rec = rec or Recorder()
    run(f.inlet({"messages": [{"role": "user", "content": "show my expenses"}]}, None, meta))
    rnd = random.Random(seed)
    out, i = [], 0
    while i < len(text):
        n = rnd.randint(1, 9)
        ev = {"choices": [{"delta": {"content": text[i:i + n]}, "finish_reason": None}]}
        out.append(run(f.stream(ev, meta, None, rec, None))["choices"][0]["delta"]["content"])
        i += n
    if finish:
        ev = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        out.append(run(f.stream(ev, meta, None, rec, None))["choices"][0]["delta"].get("content", ""))
    return "".join(out), rec


@pytest.mark.parametrize("seed", range(20))
def test_block_is_hidden_rendered_and_text_keeps_streaming(seed):
    f = offline_filter()
    visible, rec = stream_answer(f, ANSWER, seed)
    assert '"components"' not in visible and "```genui" not in visible
    assert visible.startswith("Here is your spending at a glance.\n\n")
    assert "*\U0001F4CA Expenses — interactive view shown above*" in visible
    assert "Rent is **67%** of the total." in visible
    assert "```python\nprint('``` not a fence end')\n```" in visible
    assert '```json\n{"shown": true}\n```\nDone.' in visible
    emb = rec.embeds()
    assert len(emb) == 1 and emb[0]["data"]["replace"] is True and len(emb[0]["data"]["embeds"]) == 1
    html = emb[0]["data"]["embeds"][0]
    assert "Content-Security-Policy" in html and "Expenses" in html
    st = rec.statuses()
    assert st[0].startswith("Building interactive view") and st[-1] == "“Expenses” ready"


@pytest.mark.parametrize("seed", range(10))
def test_plain_text_and_other_fences_are_byte_identical(seed):
    text = ("Intro\n   indented line\n~~~\ntilde fence\n~~~\n``inline`` code\n````md\n```genui\n{}\n```\n````\n"
            "\tTabbed\n```\nunterminated code at end")
    f = offline_filter()
    visible, rec = stream_answer(f, text, seed)
    assert visible == text
    assert rec.embeds() == []


def test_invalid_block_falls_back_to_a_readable_warning():
    f = offline_filter()
    visible, rec = stream_answer(f, "A\n```genui\n{\"components\": [ {\"type\": \"text\" \"md\": 1} ]}\n```\nB", 1)
    assert "[!WARNING]" in visible and "could not be displayed" in visible
    assert visible.startswith("A\n") and visible.endswith("B")
    assert rec.embeds() == []


def test_unterminated_block_at_end_is_repaired():
    f = offline_filter()
    truncated = 'Intro\n```genui\n{"title": "Cut", "components": [{"type": "text", "md": "hello"}'
    visible, rec = stream_answer(f, truncated, 2)
    assert visible.startswith("Intro\n") and "Cut" in visible
    assert len(rec.embeds()) == 1


def test_lone_opening_fence_at_end():
    f = offline_filter()
    visible, rec = stream_answer(f, "Intro\n```genui", 3)
    assert visible == "Intro\n" and rec.embeds() == []


def test_multiple_cards_accumulate_and_limit_applies():
    f = offline_filter(max_cards_per_message=2)
    text = "x\n" + BLOCK + "y\n" + BLOCK.replace("Expenses", "Second") + "z\n" + BLOCK.replace("Expenses", "Third")
    visible, rec = stream_answer(f, text, 4)
    emb = rec.embeds()
    assert [len(e["data"]["embeds"]) for e in emb] == [1, 2]
    assert "More visuals were requested" in visible


def test_disabled_by_user_passes_everything_through():
    f = offline_filter()
    meta = {"chat_id": "c", "message_id": "u"}
    run(f.inlet({"messages": [{"role": "user", "content": "chart"}]}, {"valves": {"enabled": False}}, meta))
    ev = {"choices": [{"delta": {"content": BLOCK}, "finish_reason": "stop"}]}
    assert run(f.stream(ev, meta, None, Recorder(), None))["choices"][0]["delta"]["content"] == BLOCK


def test_prompt_injection_modes():
    f = offline_filter()
    body = {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "Explain gravity"}]}
    run(f.inlet(body, None, {"chat_id": "c", "message_id": "1"}))
    assert body["messages"][0]["content"] == "S"  # auto: not a visual request
    body = {"messages": [{"role": "user", "content": "Compare my monthly expenses visually"}]}
    run(f.inlet(body, None, {"chat_id": "c", "message_id": "2"}))
    guide = body["messages"][0]["content"]
    assert "[GENERATIVE_UI_INLINE_V1]" in guide and "```genui" in guide and "slider{bind" in guide
    run(f.inlet(body, None, {"chat_id": "c", "message_id": "2"}))
    assert sum("[GENERATIVE_UI_INLINE_V1]" in m["content"] for m in body["messages"]) == 1
    f.valves.prompt_mode = "always"
    body = {"messages": [{"role": "user", "content": "Explain gravity"}]}
    run(f.inlet(body, None, {"chat_id": "c", "message_id": "3"}))
    assert body["messages"][0]["role"] == "system"
    f.valves.prompt_mode = "off"
    body = {"messages": [{"role": "user", "content": "dashboard please"}]}
    run(f.inlet(body, None, {"chat_id": "c", "message_id": "4"}))
    assert len(body["messages"]) == 1


def test_outlet_renders_blocks_for_non_streaming_responses():
    f = offline_filter()
    rec = Recorder()
    body = {"messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": ANSWER}]}
    run(f.outlet(body, {"chat_id": "c", "message_id": "n"}, None, rec, None))
    content = body["messages"][-1]["content"]
    assert '"components"' not in content and "interactive view shown above" in content
    assert len(rec.embeds()) == 1 and len(body["messages"][-1]["embeds"]) == 1
    # idempotent: nothing left to do on a second pass
    rec2 = Recorder()
    run(f.outlet(body, {"chat_id": "c", "message_id": "n"}, None, rec2, None))
    assert rec2.embeds() == []


def test_responses_api_deltas():
    f = offline_filter()
    meta = {"chat_id": "c", "message_id": "r"}
    rec = Recorder()
    run(f.inlet({"messages": [{"role": "user", "content": "chart"}]}, None, meta))
    out = []
    for i in range(0, len(ANSWER), 11):
        ev = {"type": "response.output_text.delta", "delta": ANSWER[i:i + 11]}
        out.append(run(f.stream(ev, meta, None, rec, None))["delta"])
    assert "Rent is **67%**" in "".join(out) and len(rec.embeds()) == 1


def test_placeholder_none_and_streams_are_isolated():
    f = offline_filter(placeholder="none")
    a, _ = stream_answer(f, "A\n" + BLOCK + "B", 5, {"chat_id": "c", "message_id": "iso1"})
    assert a == "A\nB"
    assert "c|iso1" not in f._streams


def test_module_is_both_tool_and_filter():
    assert hasattr(gui, "Tools") and hasattr(gui, "Filter")
    assert gui.Filter.Valves().prompt_mode == "auto"
