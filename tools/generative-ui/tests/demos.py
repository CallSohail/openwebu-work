"""Demo specs for the gallery and browser tests (the ten ui_examples plus extra scenarios)."""

import importlib.util
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parents[1] / "generative_ui.py"


def load_module():
    spec = importlib.util.spec_from_file_location("generative_ui", _MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def extra_demos():
    return {
        "weather": {"components": [{"type": "weather", "location": "Paris, France", "days": 5}],
                    "followups": ["What should I wear this week?"]},
        "mortgage": {
            "title": "Mortgage calculator", "icon": "house",
            "state": {"price": 450000, "down": 20, "rate": 3.6, "years": 25},
            "computed": {"loan": "price * (1 - down / 100)", "pay": "-pmt(rate / 100 / 12, years * 12, loan)",
                         "interest": "pay * years * 12 - loan"},
            "components": [
                {"type": "grid", "columns": 2, "children": [
                    {"type": "number", "bind": "price", "label": "Home price", "min": 50000, "max": 3000000, "step": 1000,
                     "unit": "EUR"},
                    {"type": "slider", "bind": "down", "label": "Down payment", "min": 0, "max": 60, "unit": "%"},
                    {"type": "slider", "bind": "rate", "label": "Interest rate", "min": 0.5, "max": 9, "step": 0.1,
                     "unit": "%"},
                    {"type": "segmented", "bind": "years", "label": "Term", "options": [
                        {"label": "15 y", "value": 15}, {"label": "20 y", "value": 20}, {"label": "25 y", "value": 25},
                        {"label": "30 y", "value": 30}]}]},
                {"type": "metrics", "columns": 3, "items": [
                    {"label": "Monthly payment", "value": "=pay", "format": "money", "currency": "EUR", "icon": "calendar"},
                    {"label": "Loan amount", "value": "=loan", "format": "money", "currency": "EUR", "icon": "landmark"},
                    {"label": "Total interest", "value": "=interest", "format": "money", "currency": "EUR",
                     "icon": "percent"}]},
                {"type": "chart", "kind": "area", "title": "Remaining balance",
                 "labels": [str(y) for y in range(0, 31, 5)],
                 "series": [{"name": "Balance", "expr": "x > years ? null : loan * (pow(1 + rate/1200, years*12) - pow(1 + rate/1200, x*12)) / (pow(1 + rate/1200, years*12) - 1)"}],
                 "format": "money", "currency": "EUR"},
                {"type": "text", "size": "sm", "tone": "muted",
                 "md": "Fixed-rate annuity loan. {{years}} years at {{rate}}% gives {{pay|money}} per month."}]},
        "carousel": {"title": "Kyoto highlights", "icon": "camera",
                     "components": [{"type": "gallery", "layout": "carousel", "images": [
                         {"query": "Fushimi Inari-taisha", "caption": "Fushimi Inari"},
                         {"query": "Kinkaku-ji", "caption": "Kinkaku-ji"},
                         {"query": "Kiyomizu-dera", "caption": "Kiyomizu-dera"},
                         {"src": "https://example.invalid/broken.jpg", "caption": "Broken image (fallback demo)"}]}]},
        "science": {
            "title": "Wave interference", "icon": "atom", "subtitle": "Two sine waves and their sum",
            "state": {"f1": 1, "f2": 1.5, "phase": 0},
            "components": [
                {"type": "grid", "columns": 3, "children": [
                    {"type": "slider", "bind": "f1", "label": "Frequency A", "min": 0.5, "max": 4, "step": 0.1, "unit": "Hz"},
                    {"type": "slider", "bind": "f2", "label": "Frequency B", "min": 0.5, "max": 4, "step": 0.1, "unit": "Hz"},
                    {"type": "slider", "bind": "phase", "label": "Phase B", "min": 0, "max": 6.28, "step": 0.01, "unit": "rad"}]},
                {"type": "chart", "kind": "line", "labels": [str(round(t * 0.05, 2)) for t in range(0, 61)],
                 "series": [{"name": "A", "expr": "sin(2*PI*f1*x)"},
                            {"name": "B", "expr": "sin(2*PI*f2*x + phase)"},
                            {"name": "A + B", "expr": "sin(2*PI*f1*x) + sin(2*PI*f2*x + phase)"}],
                 "y_min": -2, "y_max": 2, "x_label": "t (s)", "caption": "Computed in your browser from the sliders."},
                {"type": "grid", "columns": 2, "children": [
                    {"type": "chart", "kind": "scatter", "title": "Sample measurements",
                     "series": [{"name": "Run 1", "data": [[1, 2.1], [2, 3.9], [3, 6.2], [4, 8.1], [5, 9.8]]},
                                {"name": "Run 2", "data": [[1, 1.8], [2, 4.3], [3, 5.7], [4, 8.4], [5, 10.4]]}],
                     "x_label": "Voltage", "y_label": "Current"},
                    {"type": "heatmap", "title": "Correlation", "x_labels": ["T", "P", "V", "n"],
                     "y_labels": ["T", "P", "V", "n"],
                     "values": [[1, 0.8, 0.6, 0.1], [0.8, 1, -0.4, 0.3], [0.6, -0.4, 1, 0.5], [0.1, 0.3, 0.5, 1]]}]}]},
        "errors": {
            "title": "Error handling and fallbacks", "icon": "shield-check",
            "components": [
                {"type": "text", "md": "Each item below is deliberately broken. The rest of the card keeps working."},
                {"type": "metric", "label": "Unknown variable", "value": "=does_not_exist * 2"},
                {"type": "metric", "label": "Division by zero", "value": "=1/0"},
                {"type": "chart", "kind": "line", "series": [{"name": "All null", "data": [None, None]}]},
                {"type": "fancy_widget", "text": "An unknown component type with text becomes a text block."},
                {"type": "image", "src": "javascript:alert(1)", "query": ""},
                {"type": "text", "md": "<img src=x onerror=alert(1)> [click](javascript:alert(1)) **still safe**"},
                {"type": "app", "title": "Sandboxed mini app", "height": 160, "html":
                    "<button id=b>Clicked 0 times</button><p id=n></p><script>var c=0;b.onclick=function(){c++;b.textContent='Clicked '+c+' times'};"
                    "fetch('https://example.com').then(function(){n.textContent='network allowed'}).catch(function(){n.textContent='Network blocked by sandbox (expected)'});</script>"}]},
    }


def all_demos(mod, include_tests=True):
    demos = dict(mod.EXAMPLES)
    demos.update(extra_demos())
    if include_tests:
        demos["hostile"] = hostile_demo()
        demos["perf"] = perf_demo()
    return demos


def hostile_demo():
    """Injection attempts. Nothing here may execute; everything must render as inert text."""
    payload = "</script><script>window.top.__pwned=1;window.__pwned=1</script><img src=x onerror=\"window.__pwned=1\">"
    return {
        "title": payload, "subtitle": "__JS__ __DATA__ __CSS__ placeholders must stay text",
        "components": [
            {"type": "text", "md": payload + " [x](javascript:window.__pwned=1) {{constructor}}"},
            {"type": "metric", "label": payload, "value": "=constructor.constructor('window.__pwned=1')()"},
            {"type": "metric", "label": "proto", "value": "=__proto__"},
            {"type": "table", "columns": [{"key": "a", "label": payload, "format": "link"}],
             "rows": [{"a": "javascript:window.__pwned=1"}, {"a": {"label": payload, "url": "javascript:alert(1)"}}]},
            {"type": "button", "label": "link", "action": {"type": "link", "url": "javascript:window.__pwned=1"}},
            {"type": "image", "src": "https://example.invalid/x.png\" onerror=\"window.__pwned=1"},
            {"type": "code", "code": payload},
            {"type": "chart", "kind": "bar", "labels": [payload], "series": [{"name": payload, "data": [1]}]},
            {"type": "diagram", "nodes": [{"id": "a", "label": payload}, {"id": "b", "label": "b"}],
             "edges": [{"from": "a", "to": "b", "label": payload[:40]}]}],
        "sources": [{"title": payload, "url": "javascript:window.__pwned=1"}, {"title": "ok", "url": "https://example.org"}],
        "followups": [payload],
    }


def perf_demo(rows=2000, charts=8):
    return {
        "title": "Performance: large table and many charts",
        "components": [
            {"type": "table", "page_size": 25, "columns": [
                {"key": "id", "label": "ID"}, {"key": "name", "label": "Name"}, {"key": "v", "label": "Value", "format": "number"},
                {"key": "s", "label": "Status", "format": "badge", "filterable": True}],
             "rows": [{"id": i, "name": f"row {i}", "v": (i * 7919) % 1000 / 10,
                       "s": {"label": ["ok", "warn", "down"][i % 3], "tone": ["success", "warning", "danger"][i % 3]}}
                      for i in range(rows)]},
            {"type": "grid", "columns": 2, "children": [
                {"type": "chart", "kind": ["line", "bar", "area", "stacked_bar"][k % 4], "labels": [str(i) for i in range(200)],
                 "series": [{"name": f"s{j}", "data": [((i * (j + 3) * 31) % 97) for i in range(200)]} for j in range(3)]}
                for k in range(charts)]}]}
