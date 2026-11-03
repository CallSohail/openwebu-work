import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from demos import all_demos, hostile_demo, load_module  # noqa: E402

gui = load_module()


def run(coro):
    return asyncio.run(coro)


def offline_tool():
    tool = gui.Tools()
    tool.valves.live_weather = tool.valves.resolve_images = tool.valves.geocode_places = False
    return tool


def embedded_payload(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="gui-data">(.*?)</script>', html, re.S)
    assert m, "data block missing"
    return json.loads(m.group(1))


# ---------------------------------------------------------------- lenient parsing
@pytest.mark.parametrize("raw", [
    '```json\n{"components":[{"type":"text","md":"hi"}]}\n```',
    '{"components":[{"type":"text","md":"hi"},],}',
    '{\u201ccomponents\u201d:[{\u201ctype\u201d:\u201ctext\u201d,\u201cmd\u201d:\u201chi\u201d}]}',
    '{"components":[{"type":"text","md":"hi"',
    json.dumps(json.dumps({"components": [{"type": "text", "md": "hi"}]})),
    "{'components': [{'type': 'text', 'md': 'hi', 'x': True}]}",
    'Sure! Here is the UI: {"components":[{"type":"text","md":"hi"}]} Hope it helps.',
])
def test_parse_repairs_common_model_mistakes(raw):
    spec, _ = gui.normalize_spec(gui.parse_spec(raw))
    assert spec["components"][0] == {"type": "text", "md": "hi"}


def test_parse_reports_position_for_unrecoverable_json():
    with pytest.raises(gui.SpecError, match="invalid at character"):
        gui.parse_spec('{"components": [ {"type": "text" "md": "x"} ]}')
    with pytest.raises(gui.SpecError, match="empty"):
        gui.parse_spec("  ")


# ---------------------------------------------------------------- normalization
def test_aliases_and_shapes_are_accepted():
    spec, ctx = gui.normalize_spec({"blocks": [
        {"type": "kpi", "label": "VRAM", "value": "192", "unit": "GB", "icon": "gpu"},
        {"component": "bar_chart", "props": {"labels": ["a", "b"], "series": [{"name": "s", "data": ["1", "2.5"]}]}},
        {"type": "note", "text": "careful"},
        {"type": "dropdown", "label": "Region", "options": ["EU", "US"]},
    ]})
    m, c, n, s = spec["components"]
    assert m == {"type": "metric", "label": "VRAM", "value": "192", "unit": "GB", "icon": "microchip"}
    assert c["type"] == "chart" and c["kind"] == "bar" and c["series"][0]["data"] == [1, 2.5]
    assert n["type"] == "callout" and n["md"] == "careful"
    assert s["bind"] == "region" and spec["state"]["region"] == "EU"
    assert "used 'blocks' as components" in ctx.notes


def test_unknown_types_degrade_gracefully():
    spec, ctx = gui.normalize_spec({"components": [
        {"type": "hologram", "text": "falls back to text"},
        {"type": "hologram", "children": [{"type": "text", "md": "inner"}]},
        {"type": "hologram", "size": 3},
    ]})
    assert [c["type"] for c in spec["components"]] == ["text", "card"]
    assert any("dropped" in n for n in ctx.notes)


def test_nothing_usable_raises():
    with pytest.raises(gui.SpecError):
        gui.normalize_spec({"components": [{"type": "hologram"}]})
    with pytest.raises(gui.SpecError, match="components"):
        gui.normalize_spec({"title": "no components"})


def test_depth_node_and_list_limits():
    deep = {"type": "text", "md": "leaf"}
    for _ in range(20):
        deep = {"type": "card", "children": [deep]}
    spec, ctx = gui.normalize_spec({"components": [deep]})
    depth, c = 0, spec["components"][0]
    while c.get("children"):
        depth += 1
        c = c["children"][0]
    assert depth <= gui.LIMITS["max_depth"] + 1
    assert any("nesting" in n for n in ctx.notes)

    many = [{"type": "text", "md": str(i)} for i in range(500)]
    spec, ctx = gui.normalize_spec({"components": [{"type": "grid", "children": many[i:i + 50]} for i in range(0, 500, 50)]})
    total = sum(len(g.get("children", [])) for g in spec["components"])
    assert total + len(spec["components"]) <= gui.LIMITS["max_nodes"]

    rows = [{"a": i} for i in range(gui.LIMITS["max_rows"] + 50)]
    spec, ctx = gui.normalize_spec({"components": [{"type": "table", "rows": rows}]})
    assert len(spec["components"][0]["rows"]) == gui.LIMITS["max_rows"]


def test_duplicate_ids_are_renamed():
    spec, ctx = gui.normalize_spec({"components": [{"type": "text", "id": "x", "md": "a"},
                                                   {"type": "text", "id": "x", "md": "b"}]})
    assert [c["id"] for c in spec["components"]] == ["x", "x_2"]


def test_non_finite_and_bad_numbers():
    spec, _ = gui.normalize_spec({"components": [
        {"type": "chart", "labels": ["a", "b", "c"], "series": [{"name": "s", "data": [1, float("nan"), "inf"]}]},
        {"type": "metric", "label": "x", "value": float("inf")},
        {"type": "slider", "label": "Price", "min": "$1,000", "max": 5000, "value": 99999}]})
    assert spec["components"][0]["series"][0]["data"] == [1, None, None]
    assert "value" not in spec["components"][1]
    s = spec["components"][2]
    assert s["min"] == 1000 and spec["state"][s["bind"]] == 5000  # clamped into range


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>", "http://x/a.png", "//evil.com/a.png",
                                 "https://user:pw@evil.com/a.png", "https://ok.com/a.png\" onerror=\"x"])
def test_image_urls_are_restricted(url):
    spec, ctx = gui.normalize_spec({"components": [{"type": "image", "src": url}, {"type": "text", "md": "x"}]})
    assert spec["components"][0]["type"] == "text"


def test_link_urls_and_sources_are_restricted():
    spec, ctx = gui.normalize_spec({
        "components": [{"type": "button", "label": "go", "action": {"type": "link", "url": "javascript:alert(1)"}},
                       {"type": "text", "md": "x"}],
        "sources": [{"title": "bad", "url": "javascript:alert(1)"}, {"title": "good", "url": "https://example.org/a"}]})
    assert "action" not in spec["components"][0]
    assert spec["sources"] == [{"title": "good", "url": "https://example.org/a"}]


@pytest.mark.parametrize("expr", ["=__proto__", "=a`b`", "=x;alert(1)", "={}", "=" + "1+" * 300 + "1"])
def test_dangerous_or_huge_expressions_are_rejected(expr):
    spec, _ = gui.normalize_spec({"components": [{"type": "metric", "label": "x", "value": expr}]})
    assert "value" not in spec["components"][0]


def test_state_and_computed_validation():
    spec, ctx = gui.normalize_spec({"state": {"ok": 1, "bad key": 2, "__proto__": 3, "n": 4},
                                    "computed": {"double": "ok * 2", "evil": "x;y"},
                                    "components": [{"type": "metric", "label": "d", "value": "=double"}]})
    assert spec["state"]["ok"] == 1 and "n" not in spec["state"] and "__proto__" not in spec["state"]
    assert spec["state"]["bad_key"] == 2
    assert spec["computed"] == {"double": "ok * 2"}


def test_table_columns_inferred_from_header_row_and_objects():
    spec, _ = gui.normalize_spec({"components": [
        {"type": "table", "rows": [["Name", "Score"], ["a", 1], ["b", 2]]},
        {"type": "table", "rows": [{"first_name": "a", "score": 1}]}]})
    t1, t2 = spec["components"]
    assert [c["label"] for c in t1["columns"]] == ["Name", "Score"] and len(t1["rows"]) == 2
    assert [c["label"] for c in t2["columns"]] == ["First name", "Score"]


def test_legacy_frontier_blocks_are_migrated():
    spec, ctx = gui.normalize_spec({"title": "Trip", "blocks": [
        {"type": "budget", "currency": "$", "items": [{"label": "Hotel", "amount": 180, "qty": 3}]},
        {"type": "calculator", "inputs": [{"id": "price", "value": 100, "min": 0, "max": 500}],
         "outputs": [{"label": "Tax", "expr": "price*0.2", "format": "money"}]},
        {"type": "stock", "symbols": ["AAPL"]},
        {"type": "stats", "items": [{"label": "A", "value": 1}]}]})
    kinds = [c["type"] for c in spec["components"]]
    assert kinds == ["card", "card", "metrics"]
    assert spec["state"]["b1"] == 180 and spec["state"]["price"] == 100
    assert any("not supported" in n for n in ctx.notes)


def test_all_examples_are_clean():
    for name, ex in gui.EXAMPLES.items():
        spec, ctx = gui.normalize_spec(json.loads(json.dumps(ex)))
        assert not ctx.notes, (name, ctx.notes)
        assert spec["components"], name


# ---------------------------------------------------------------- HTML safety
def test_html_is_ascii_has_csp_and_escapes_script_breakouts():
    spec, ctx = gui.normalize_spec(hostile_demo())
    html = gui.build_html(spec, ctx, {"theme": "light"})
    assert html.isascii()
    assert 'http-equiv="Content-Security-Policy"' in html and "connect-src 'none'" in html
    data_block = re.search(r'id="gui-data">(.*?)</script>', html, re.S).group(1)
    assert "</script" not in data_block.lower() and "<" not in data_block
    # placeholders in model text never pull code in (single-pass substitution)
    assert html.count("use strict") == 1
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    assert "<script>" not in title and "&lt;/script&gt;" in title
    payload = embedded_payload(html)
    assert payload["spec"]["subtitle"].startswith("__JS__")


def test_icons_are_subset_and_unknown_icons_dropped():
    spec, ctx = gui.normalize_spec({"components": [{"type": "metric", "label": "x", "value": 1, "icon": "cpu"},
                                                   {"type": "metric", "label": "y", "value": 1, "icon": "<svg onload=1>"}]})
    html = gui.build_html(spec, ctx, {})
    icons = embedded_payload(html)["icons"]
    assert "cpu" in icons and len(icons) < 80
    assert "icon" not in spec["components"][1]


# ---------------------------------------------------------------- tool behaviour
def test_render_ui_returns_inline_embed_and_context():
    tool = offline_tool()
    res = run(tool.render_ui({"title": "T", "components": [{"type": "text", "md": "hello"}]}))
    resp, context = res
    assert resp.headers["content-disposition"] == "inline"
    assert resp.status_code == 200
    assert "visible to the user" in context and "hello" in context


def test_render_ui_errors_are_returned_to_the_model():
    tool = offline_tool()
    msg = run(tool.render_ui('{"components": [ {"type": "text" "md": "x"} ]}'))
    assert isinstance(msg, str) and "could not use the spec" in msg
    tool.valves.max_spec_kb = 16
    big = {"components": [{"type": "text", "md": "x" * 20000}]}
    assert "limit" in run(tool.render_ui(big))


def test_status_events_are_emitted():
    tool = offline_tool()
    events = []

    async def emit(e):
        events.append(e)

    run(tool.render_ui({"title": "T", "components": [{"type": "text", "md": "x"}]}, __event_emitter__=emit))
    assert events[0]["data"]["done"] is False and events[-1]["data"]["done"] is True


def test_theme_detected_from_browser_and_failures_tolerated():
    tool = offline_tool()

    async def dark_call(_):
        return json.dumps({"dark": True, "lang": "fr-FR"})

    resp, _ = run(tool.render_ui({"components": [{"type": "text", "md": "x"}]}, __event_call__=dark_call))
    cfg = embedded_payload(resp.body.decode())["cfg"]
    assert cfg["theme"] == "dark" and cfg["lang"] == "fr-FR"

    async def broken_call(_):
        raise RuntimeError("socket closed")

    resp, _ = run(tool.render_ui({"components": [{"type": "text", "md": "x"}]}, __event_call__=broken_call))
    assert embedded_payload(resp.body.decode())["cfg"]["theme"] == "auto"

    user = {"valves": tool.UserValves(theme="light")}
    resp, _ = run(tool.render_ui({"components": [{"type": "text", "md": "x"}]}, __user__=user, __event_call__=dark_call))
    assert embedded_payload(resp.body.decode())["cfg"]["theme"] == "light"


def test_custom_apps_can_be_disabled():
    tool = offline_tool()
    tool.valves.allow_custom_apps = False
    resp, _ = run(tool.render_ui({"components": [{"type": "card", "children": [{"type": "app", "html": "<b>x</b>"}]}]}))
    spec = embedded_payload(resp.body.decode())["spec"]
    assert spec["components"][0]["children"][0]["type"] == "notice"


def test_weather_live_success_and_failure(monkeypatch):
    tool = gui.Tools()

    async def ok(session, location, days, units, timeout):
        return {"place": "Paris, France", "temp_unit": "\u00b0C", "wind_unit": "km/h",
                "current": {"temp": 11, "label": "Rain", "icon": "cloud-rain"}, "hourly": {"time": [], "temp": []},
                "daily": [{"date": "2026-11-03", "label": "Rain", "icon": "cloud-rain", "min": 8, "max": 12}],
                "as_of": "2026-11-03 10:00 UTC", "source": {"title": "Open-Meteo", "url": "https://open-meteo.com/"}}

    monkeypatch.setattr(gui, "fetch_weather", ok)
    resp, ctx = run(tool.show_weather("Paris, France", 3))
    comp = embedded_payload(resp.body.decode())["spec"]["components"][0]
    assert comp["data"]["place"] == "Paris, France" and "fetched live" in ctx

    async def fail(*a, **k):
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(gui, "fetch_weather", fail)
    resp, ctx = run(tool.show_weather("Paris"))
    comp = embedded_payload(resp.body.decode())["spec"]["components"][0]
    assert "unavailable" in comp["error"] and "data" not in comp and "failed" in ctx

    tool.valves.live_weather = False
    resp, _ = run(tool.show_weather("Paris"))
    assert "turned off" in embedded_payload(resp.body.decode())["spec"]["components"][0]["error"]


def test_unresolved_image_queries_are_pruned(monkeypatch):
    tool = gui.Tools()

    async def none(*a, **k):
        return None

    monkeypatch.setattr(gui, "wiki_image", none)
    resp, ctx = run(tool.render_ui({"components": [
        {"type": "gallery", "images": [{"query": "nothing"}, {"src": "https://example.org/a.jpg"}]},
        {"type": "image", "query": "nothing"}]}))
    comps = embedded_payload(resp.body.decode())["spec"]["components"]
    assert comps[0]["images"] == [{"src": "https://example.org/a.jpg"}]
    assert comps[1]["type"] == "notice"


def test_ui_examples_tool():
    tool = gui.Tools()
    assert json.loads(run(tool.ui_examples("budget")))["title"] == "Monthly budget"
    assert "Available examples" in run(tool.ui_examples("nope"))


def test_markdown_fallback_summarizes_content():
    spec, _ = gui.normalize_spec(json.loads(json.dumps(gui.EXAMPLES["table"])))
    md = gui.to_markdown(spec)
    assert "| Model |" in md and "Llama 3.3 70B" in md


def test_every_demo_renders():
    tool = offline_tool()
    for name, spec in all_demos(gui).items():
        res = run(tool.render_ui(json.loads(json.dumps(spec))))
        assert not isinstance(res, str), (name, res)
        assert len(res[0].body) < 400_000, name


def test_tool_metadata_and_signatures():
    src = Path(gui.__file__).read_text(encoding="utf-8")
    assert re.search(r"^version: \d+\.\d+\.\d+$", src, re.M)
    import inspect

    for name in ("render_ui", "show_weather", "ui_examples"):
        fn = getattr(gui.Tools, name)
        assert fn.__doc__ and ":param" in fn.__doc__
        for p in inspect.signature(fn).parameters.values():
            if p.name != "self" and not p.name.startswith("__"):
                assert p.annotation is not inspect.Parameter.empty, (name, p.name)


def test_legacy_multi_day_map_and_compare_points():
    spec, ctx = gui.normalize_spec({"blocks": [
        {"type": "map", "mode": "walk", "days": [
            {"label": "Day 1", "places": [{"name": "A", "lat": 1, "lng": 2}, {"name": "B", "lat": 1.1, "lng": 2.1}]},
            {"label": "Day 2", "places": [{"name": "C", "lat": 1.2, "lng": 2.2}]}]},
        {"type": "compare", "items": [{"title": "X", "points": ["fast", "cheap"], "verdict": "Pick X"}]},
        {"type": "timeline", "items": [{"time": "9:00", "title": "Go", "day": "Day 1"}]}]})
    tabs, cmp_, tl = spec["components"]
    assert tabs["type"] == "tabs" and [t["label"] for t in tabs["tabs"]] == ["Day 1", "Day 2"]
    assert tabs["tabs"][0]["children"][0]["places"][1]["name"] == "B"
    assert cmp_["items"][0]["pros"] == ["fast", "cheap"] and cmp_["items"][0]["md"] == "Pick X"
    assert tl["items"][0]["group"] == "Day 1"
