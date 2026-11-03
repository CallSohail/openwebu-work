import asyncio
import importlib.util
import random
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("rich_responses", Path(__file__).with_name("rich_responses.py"))
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def run(c):
    return asyncio.run(c)


def stream_through(text, seed=0, **kw):
    """Feed text in random-size chunks, like a model stream, and return the visible output."""
    it = rr.LineIconizer(**kw)
    rnd = random.Random(seed)
    out, i = [], 0
    while i < len(text):
        n = rnd.randint(1, 7)
        out.append(it.feed(text[i:i + n]))
        i += n
    out.append(it.flush())
    return "".join(out)


SAMPLE = """Direct answer first.

## Overview
Some text with **bold** words.

### Pros and cons
**Pros:** fast
**Cons**: costly
**Just bold text** in a sentence.
* bullet one
- bullet two

## 🧭 Already has icon
#hashtag is not a heading

```python
# Steps comment stays untouched
## not a heading in code
print("**Tip:** no")
```

## Next steps
## Something unmatched
## Summary"""


@pytest.mark.parametrize("seed", range(25))
def test_streaming_icons_are_stable_for_any_chunking(seed):
    out = stream_through(SAMPLE, seed)
    assert out == stream_through(SAMPLE, 999)  # chunk boundaries never change the result
    assert "## 🧭 Overview\n" in out
    assert "### ✅ Pros and cons\n" in out
    assert "✅ **Pros:** fast" in out and "❌ **Cons**: costly" in out
    assert "\n**Just bold text** in a sentence." in out
    assert "## 🧭 Already has icon" in out and "🧭 🧭" not in out
    assert "#hashtag is not a heading" in out
    assert "# Steps comment stays untouched\n## not a heading in code\nprint(\"**Tip:** no\")" in out
    assert "## ➡️ Next steps" in out and "## Something unmatched" in out
    assert out.endswith("## 📝 Summary")  # unterminated last line is flushed


def test_text_without_markup_is_unchanged():
    plain = "Hello there.\nJust a normal answer with `code` and 3 * 4 = 12.\n"
    assert stream_through(plain, 3) == plain


def test_disabled_icons_are_passthrough():
    assert stream_through(SAMPLE, 1, headings=False, labels=False) == SAMPLE


def test_french_and_spanish_headings():
    out = stream_through("## Résumé\n## Étapes\n## Ventajas\n", 4)
    assert out == "## 📝 Résumé\n## 🛠️ Étapes\n## ✅ Ventajas\n"


@pytest.mark.parametrize("text,files,intent", [
    ("hi", False, "chat"),
    ("Merci !", False, "chat"),
    ("What is 25% of 480?", False, "explain"),
    ("Capital of Peru?", False, "quick"),
    ("Explain how transformers work", False, "explain"),
    ("Explique-moi la photosynthèse", False, "explain"),
    ("", True, "summarize"),
    ("Summarize this PDF", True, "summarize"),
    ("Résume ce document", False, "summarize"),
    ("What does this file say about pricing and refunds in general", True, "summarize"),
    ("Compare PostgreSQL vs MySQL", False, "compare"),
    ("How do I install Docker on Ubuntu?", False, "howto"),
    ("My script fails with ModuleNotFoundError", False, "troubleshoot"),
    ("Plan a 3 day trip to Rome", False, "plan"),
    ("Write a python function to parse dates", False, "code"),
    ("Give me a python function to parse dates", False, "code"),
    ("Write a poem about autumn", False, "creative"),
    ("Recommend some sci-fi books", False, "recommend"),
])
def test_intent_detection(text, files, intent):
    assert rr.Filter.detect_intent(text, files) == intent


def test_prompt_shapes_and_length_hints():
    f = rr.Filter()
    p = f.build_prompt("Explain RAG briefly", False, "chatgpt")
    assert rr.MARKER in p and "## 🧭 Overview" in p and "brief answer" in p
    assert "render_ui" in p
    assert "In detail" not in p
    assert "thorough" in f.build_prompt("Explain RAG in detail", False, "chatgpt")
    chat = f.build_prompt("hello", False, "chatgpt")
    assert "No headings" in chat and "render_ui" not in chat
    assert "TL;DR" in f.build_prompt("", True, "chatgpt")
    f.valves.visual_tool_hint = False
    assert "render_ui" not in f.build_prompt("Explain RAG", False, "chatgpt")


def test_inlet_injects_once_and_respects_off_and_study_mode():
    f = rr.Filter()
    body = {"messages": [{"role": "system", "content": "Be nice."}, {"role": "user", "content": "Explain DNS"}]}
    meta = {"chat_id": "c", "message_id": "m"}
    run(f.inlet(body, None, meta))
    run(f.inlet(body, None, meta))
    assert body["messages"][0]["content"].count(rr.MARKER) == 1 and body["messages"][0]["content"].startswith("Be nice.")
    off = {"messages": [{"role": "user", "content": "Explain DNS"}]}
    run(f.inlet(off, {"valves": {"style": "off"}}, {"chat_id": "c", "message_id": "x"}))
    assert len(off["messages"]) == 1
    sm = {"messages": [{"role": "user", "content": "Explain DNS"}]}
    run(f.inlet(sm, None, {"chat_id": "c", "message_id": "y", "study_mode": {"enabled": True}}))
    assert len(sm["messages"]) == 1
    f.valves.system_prompt_integration = "separate"
    sep = {"messages": [{"role": "system", "content": "A"}, {"role": "user", "content": "Explain DNS"}]}
    run(f.inlet(sep, None, {"chat_id": "c", "message_id": "z"}))
    assert sep["messages"][0]["content"] == "A" and rr.MARKER in sep["messages"][1]["content"]


def test_stream_hook_chat_completions_and_isolation():
    f = rr.Filter()
    ma, mb = {"chat_id": "c", "message_id": "a"}, {"chat_id": "c", "message_id": "b"}
    run(f.inlet({"messages": [{"role": "user", "content": "Explain DNS"}]}, None, ma))
    run(f.inlet({"messages": [{"role": "user", "content": "Explain TCP"}]}, None, mb))

    def ev(text, finish=None):
        return {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}

    outs = {"a": [], "b": []}
    for chunk_a, chunk_b in [("## Over", "## Summ"), ("view\nx", "ary"), ("", "")]:
        outs["a"].append(run(f.stream(ev(chunk_a), ma))["choices"][0]["delta"]["content"])
        outs["b"].append(run(f.stream(ev(chunk_b), mb))["choices"][0]["delta"]["content"])
    fin = run(f.stream({"choices": [{"delta": {}, "finish_reason": "stop"}]}, mb))
    outs["b"].append(fin["choices"][0]["delta"].get("content", ""))
    assert "".join(outs["a"]) == "## 🧭 Overview\nx"
    assert "".join(outs["b"]) == "## 📝 Summary"  # held heading is flushed on finish_reason
    assert "c|b" not in f._streams
    run(f.outlet({}, ma))
    assert "c|a" not in f._streams


def test_stream_without_inlet_or_metadata_is_passthrough():
    f = rr.Filter()
    e = {"choices": [{"delta": {"content": "## Overview\n"}}]}
    assert run(f.stream(e, None)) == e
    assert run(f.stream(e, {"chat_id": "nope", "message_id": "x"})) == e


def test_user_can_disable_live_icons():
    f = rr.Filter()
    meta = {"chat_id": "c", "message_id": "q"}
    run(f.inlet({"messages": [{"role": "user", "content": "Explain DNS"}]}, {"valves": {"live_icons": False}}, meta))
    e = {"choices": [{"delta": {"content": "## Overview\n"}}]}
    assert run(f.stream(e, meta))["choices"][0]["delta"]["content"] == "## Overview\n"


def test_mid_line_hash_is_not_a_heading():
    assert stream_through("Plain ## Summary and C# code\n", 2) == "Plain ## Summary and C# code\n"
