"""
title: Generative UI
author: Muhammad Sohail
author_url: https://github.com/CallSohail/openwebu-work
version: 1.0.0
license: MIT
required_open_webui_version: 0.12.0
requirements: aiohttp
description: ChatGPT-style generative UI for any model. The model writes one validated JSON spec and the Tool renders native-looking, fully interactive cards, dashboards, charts, tables, calculators, checklists, comparisons, timelines, diagrams, maps and live weather inline in the chat. Self-contained renderer (no CDN), strict validation, sandboxed custom apps, light and dark themes.
"""

import asyncio
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

log = logging.getLogger("generative_ui")

VERSION = "1.0.0"
PROTOCOL_VERSION = "1"
USER_AGENT = "OpenWebUI-GenerativeUI/1.0 (+https://github.com/CallSohail/openwebu-work)"

# --------------------------------------------------------------------------------------------------------------------
# Limits. Every spec is bounded before it reaches the browser so a runaway model can't freeze the chat.
# --------------------------------------------------------------------------------------------------------------------
LIMITS = {
    "max_depth": 8,  # nesting of layout components
    "max_nodes": 300,  # total components in one spec
    "max_children": 60,
    "max_items": 80,  # items in a list-like component
    "max_rows": 2000,  # table rows
    "max_columns": 24,
    "max_series": 8,
    "max_points": 1000,  # points per chart series
    "max_str": 600,
    "max_md": 12000,
    "max_code": 20000,
    "max_html": 120000,
    "max_expr": 400,
    "max_state": 64,
    "max_followups": 6,
    "max_sources": 20,
}

ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$")
VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
EXPR_RE = re.compile(r"""^[\w\s+\-*/%^().,<>=!?:&|'"\[\]]*$""")
DATA_IMG_RE = re.compile(r"^data:image/(png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$")
RESERVED_VARS = {"true", "false", "and", "or", "not", "PI", "E", "i", "x", "n"}

TONES = ("neutral", "info", "success", "warning", "danger", "accent")
TONE_ALIASES = {
    "good": "success", "ok": "success", "green": "success", "positive": "success", "tip": "success",
    "blue": "info", "note": "info", "primary": "accent", "highlight": "accent",
    "warn": "warning", "caution": "warning", "yellow": "warning", "orange": "warning",
    "bad": "danger", "error": "danger", "red": "danger", "critical": "danger", "negative": "danger",
    "gray": "neutral", "grey": "neutral", "default": "neutral", "muted": "neutral",
}
FORMATS = ("number", "integer", "money", "percent", "compact", "duration", "text", "date")


class SpecError(ValueError):
    """The spec could not be used; the message tells the model exactly what to fix."""


# --------------------------------------------------------------------------------------------------------------------
# Lenient JSON parsing: bounded repairs for the mistakes local models actually make.
# --------------------------------------------------------------------------------------------------------------------
def _close_truncated(s: str) -> str:
    """Close strings and brackets left open by output that was cut off."""
    stack: list[str] = []
    in_str = esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}" and stack:
            stack.pop()
    s = s.rstrip()
    if in_str:
        s += '"'
    s = re.sub(r",\s*$", "", s)
    s = re.sub(r":\s*$", ": null", s)
    return s + "".join(reversed(stack))


def parse_spec(spec: Any, _depth: int = 0) -> Any:
    """Return a Python object from a dict or a JSON-ish string. Raises SpecError with a precise location."""
    if isinstance(spec, (dict, list)):
        return spec
    s = str(spec or "").strip().lstrip("﻿")
    if not s:
        raise SpecError('spec is empty. Pass one JSON object such as {"title": "...", "components": [...]}.')
    if len(s) > 2_000_000:
        raise SpecError("spec is far too large. Keep it under 250 KB.")
    s = re.sub(r"^```[A-Za-z0-9_-]*\s*|\s*```\s*$", "", s)
    candidates = [s]
    a, b = s.find("{"), s.rfind("}")
    if a > 0 or (b != -1 and b < len(s) - 1):
        candidates.append(s[a: b + 1] if a != -1 and b > a else s[a:] if a != -1 else s)
    first_err = None
    for t in candidates:
        t2 = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        t3 = re.sub(r",\s*([}\]])", r"\1", t2)
        for cand in dict.fromkeys([t, t2, t3, _close_truncated(t3)]):
            try:
                value = json.loads(cand, strict=False)
            except json.JSONDecodeError as exc:
                first_err = first_err or (exc, cand)
                continue
            if isinstance(value, str) and _depth < 2:
                return parse_spec(value, _depth + 1)
            return value
        try:  # Python literals: single quotes, True/False/None
            import ast

            value = ast.literal_eval(t3)
            if isinstance(value, (dict, list)):
                return value
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
    if first_err is None:
        raise SpecError("spec is not JSON.")
    exc, cand = first_err
    lo = max(0, exc.pos - 60)
    raise SpecError(
        f"spec JSON is invalid at character {exc.pos} ({exc.msg}) near: ...{cand[lo:exc.pos + 40]}... "
        "Fix the quotes or commas there and call the tool again with ONE JSON object."
    )


# --------------------------------------------------------------------------------------------------------------------
# Component protocol. Field kinds are validated and coerced; anything not listed is dropped.
# --------------------------------------------------------------------------------------------------------------------
COMMON = {"id": "id", "visible_if": "expr", "span": "int:1:4"}
FMT = {"format": "format", "currency": "str:12", "decimals": "int:0:6", "unit": "str:24"}

SCHEMAS: dict[str, dict[str, str]] = {
    # layout
    "grid": {"columns": "int:1:4", "gap": "enum:sm|md|lg", "children": "children"},
    "card": {"title": "str", "subtitle": "str", "icon": "icon", "badge": "badge", "tone": "tone",
             "children": "children", "footer": "str"},
    "section": {"title": "str", "subtitle": "str", "icon": "icon", "badge": "badge", "children": "children",
                "controls": "children"},
    "tabs": {"bind": "var", "style": "enum:segmented|underline", "tabs": "list:tab"},
    "accordion": {"items": "list:accordion_item"},
    "divider": {"label": "str"},
    # text
    "heading": {"text": "str", "level": "int:1:4", "icon": "icon", "subtitle": "str"},
    "text": {"md": "md", "size": "enum:sm|md|lg", "tone": "enum:default|muted"},
    "callout": {"tone": "tone", "title": "str", "md": "md", "icon": "icon"},
    "quote": {"md": "md", "by": "str"},
    "code": {"code": "code", "language": "str:24", "title": "str"},
    "kv": {"title": "str", "columns": "int:1:3", "items": "list:kv_item"},
    "tags": {"items": "list:tag"},
    "notice": {"md": "md", "tone": "tone"},
    # media
    "image": {"src": "img", "alt": "str", "caption": "str", "credit": "str", "link": "url", "query": "str:120",
              "ratio": "enum:wide|square|tall|auto"},
    "gallery": {"title": "str", "layout": "enum:grid|carousel", "images": "list:image"},
    # data display
    "metric": {"label": "str", "value": "val", "icon": "icon", "caption": "str", "delta": "val",
               "delta_tone": "enum:auto|good|bad|neutral", "trend": "nums", "progress": "obj:progress_item",
               "size": "enum:sm|md|lg", **FMT},
    "metrics": {"columns": "int:1:4", "items": "list:metric_item"},
    "progress": {"label": "str", "value": "val", "max": "val", "tone": "tone", "caption": "str",
                 "show_value": "bool", **FMT},
    "gauge": {"label": "str", "value": "val", "min": "num", "max": "num", "caption": "str",
              "thresholds": "list:threshold", **FMT},
    "chart": {"title": "str", "subtitle": "str", "kind": "enum:line|area|bar|stacked_bar|hbar|pie|donut|scatter",
              "labels": "labels", "series": "list:series", "y_min": "num", "y_max": "num", "height": "int:140:520",
              "caption": "str", "legend": "bool", "table_view": "bool", "x_label": "str", "y_label": "str",
              "bind": "var", **FMT},
    "heatmap": {"title": "str", "x_labels": "labels", "y_labels": "labels", "values": "matrix", "caption": "str",
                **FMT},
    "timeline": {"title": "str", "items": "list:timeline_item"},
    "steps": {"title": "str", "interactive": "bool", "items": "list:step"},
    "flow": {"title": "str", "direction": "enum:vertical|horizontal", "items": "list:flow_item"},
    "diagram": {"title": "str", "direction": "enum:LR|TB", "nodes": "list:node", "edges": "list:edge",
                "caption": "str"},
    "status": {"title": "str", "items": "list:status_item"},
    "table": {"title": "str", "columns": "list:column", "rows": "rows", "searchable": "bool", "sortable": "bool",
              "page_size": "int:3:100", "download": "bool", "caption": "str", "empty": "str"},
    "list": {"title": "str", "bind": "var", "selectable": "bool", "items": "list:list_item"},
    "checklist": {"title": "str", "bind": "var", "items": "list:check_item", "total_label": "str",
                  "copy": "bool", "copy_label": "str", **FMT},
    "compare": {"title": "str", "columns": "int:1:4", "items": "list:product", "spec_table": "bool"},
    "map": {"title": "str", "places": "list:place", "caption": "str", "height": "int:160:560",
            "mode": "enum:walking|driving|bicycling|transit"},
    "weather": {"location": "str:120", "days": "int:1:7", "units": "enum:metric|imperial"},
    "app": {"title": "str", "html": "html", "height": "int:120:900", "caption": "str"},
    # inputs (all write to `state`)
    "slider": {"bind": "var", "label": "str", "min": "num", "max": "num", "step": "num", "value": "num", **FMT},
    "number": {"bind": "var", "label": "str", "min": "num", "max": "num", "step": "num", "value": "num",
               "placeholder": "str", **FMT},
    "select": {"bind": "var", "label": "str", "options": "options", "value": "scalar"},
    "segmented": {"bind": "var", "label": "str", "options": "options", "value": "scalar", "full": "bool"},
    "toggle": {"bind": "var", "label": "str", "detail": "str", "value": "bool"},
    "text_input": {"bind": "var", "label": "str", "placeholder": "str", "value": "str", "multiline": "bool"},
    "date": {"bind": "var", "label": "str", "value": "str:10", "min": "str:10", "max": "str:10"},
    "button": {"label": "str", "icon": "icon", "variant": "enum:primary|secondary|ghost", "action": "action",
               "full": "bool"},
    "actions": {"items": "list:button_item", "align": "enum:left|center|right|stretch"},
}
CONTAINERS = {"grid", "card", "section"}

ITEMS: dict[str, dict[str, str]] = {
    "tab": {"label": "str", "icon": "icon", "value": "scalar", "children": "children"},
    "accordion_item": {"title": "str", "icon": "icon", "md": "md", "open": "bool", "children": "children"},
    "kv_item": {"label": "str", "value": "val", "icon": "icon", "hint": "str", **FMT},
    "tag": {"label": "str", "tone": "tone", "icon": "icon"},
    "image": {"src": "img", "alt": "str", "caption": "str", "credit": "str", "link": "url", "query": "str:120"},
    "metric_item": {"label": "str", "value": "val", "icon": "icon", "caption": "str", "delta": "val",
                    "delta_tone": "enum:auto|good|bad|neutral", "trend": "nums", "progress": "obj:progress_item",
                    "visible_if": "expr", **FMT},
    "progress_item": {"value": "val", "max": "val", "tone": "tone"},
    "threshold": {"value": "num", "tone": "tone"},
    "series": {"name": "str", "data": "points", "expr": "expr", "color": "int:1:8"},
    "timeline_item": {"time": "str", "date": "str", "title": "str", "detail": "md", "icon": "icon", "tone": "tone",
                      "tag": "str", "group": "str"},
    "step": {"title": "str", "detail": "md", "icon": "icon", "tone": "tone"},
    "flow_item": {"title": "str", "detail": "str", "icon": "icon", "tone": "tone"},
    "node": {"id": "id", "label": "str", "sublabel": "str", "icon": "icon", "tone": "tone", "detail": "md"},
    "edge": {"from": "id", "to": "id", "label": "str:40", "dashed": "bool"},
    "status_item": {"name": "str", "state": "enum:ok|warn|down|info|unknown", "detail": "str", "value": "val",
                    "icon": "icon"},
    "column": {"key": "str:64", "label": "str", "align": "enum:left|center|right",
               "format": "enum:text|number|integer|money|percent|compact|badge|link|progress|date|bool",
               "currency": "str:12", "decimals": "int:0:6", "sortable": "bool", "filterable": "bool"},
    "list_item": {"id": "id", "title": "str", "subtitle": "str", "icon": "icon", "badge": "badge",
                  "detail": "obj:list_detail"},
    "list_detail": {"title": "str", "badge": "badge", "md": "md", "highlight": "str", "highlight_icon": "icon",
                    "tags": "strs", "button": "obj:button_item"},
    "check_item": {"id": "id", "label": "str", "detail": "str", "value": "range", "checked": "bool"},
    "product": {"title": "str", "subtitle": "str", "image": "img", "query": "str:120", "icon": "icon",
                "price": "val", "rating": "num", "reviews": "int:0:100000000", "badge": "badge",
                "pros": "strs", "cons": "strs", "specs": "pairs", "md": "md", "url": "url", "url_label": "str",
                "highlight": "bool", **FMT},
    "place": {"name": "str", "lat": "num", "lng": "num", "note": "str", "time": "str", "icon": "icon", "url": "url"},
    "button_item": {"label": "str", "icon": "icon", "variant": "enum:primary|secondary|ghost", "action": "action",
                    "full": "bool"},
}

TYPE_ALIASES = {
    "kpi": "metric", "stat": "metric", "stats": "metrics", "kpis": "metrics", "kpi_grid": "metrics",
    "markdown": "text", "paragraph": "text", "prose": "text", "md": "text", "rich_text": "text",
    "note": "callout", "tip": "callout", "warning": "callout", "alert": "callout", "info": "callout",
    "header": "heading", "title": "heading", "subheading": "heading",
    "columns": "grid", "row": "grid", "stack": "grid", "layout": "grid",
    "container": "card", "panel": "card", "box": "card",
    "comparison": "compare", "products": "compare", "product_cards": "compare", "cards": "compare",
    "product": "compare", "places": "compare",
    "itinerary": "timeline", "schedule": "timeline", "agenda": "timeline",
    "workflow": "flow", "process": "flow", "pipeline": "flow",
    "flowchart": "diagram", "architecture": "diagram", "graph_diagram": "diagram", "network": "diagram",
    "wizard": "steps", "howto": "steps", "checklist_steps": "steps",
    "images": "gallery", "carousel": "gallery", "photos": "gallery", "photo": "image", "img": "image",
    "dropdown": "select", "range": "slider", "switch": "toggle", "input": "text_input", "textbox": "text_input",
    "radio": "segmented", "radio_group": "segmented", "toggle_group": "segmented", "buttons": "actions",
    "datatable": "table", "data_table": "table", "dataframe": "table",
    "status_board": "status", "health": "status",
    "todo": "checklist", "checkbox_list": "checklist", "shopping_list": "checklist", "checkboxes": "checklist",
    "menu": "list", "explorer": "list", "selectable_list": "list", "picker": "list",
    "iframe": "app", "html": "app", "custom": "app", "mini_app": "app", "widget": "app",
    "forecast": "weather", "key_value": "kv", "properties": "kv", "specs": "kv", "details": "kv",
    "badges": "tags", "chips": "tags", "progress_bar": "progress", "meter": "gauge", "route": "map", "maps": "map",
    "line_chart": "chart", "bar_chart": "chart", "pie_chart": "chart", "area_chart": "chart", "donut_chart": "chart",
    "scatter_chart": "chart", "graph": "chart", "linechart": "chart", "barchart": "chart", "piechart": "chart",
    "histogram": "chart", "sparkline": "metric", "line": "chart", "bar": "chart", "pie": "chart", "area": "chart",
    "donut": "chart", "scatter": "chart", "hbar": "chart", "stacked_bar": "chart", "toc": "list", "expandable": "accordion", "collapse": "accordion",
}
CHART_KIND_FROM_ALIAS = {"line_chart": "line", "linechart": "line", "bar_chart": "bar", "barchart": "bar",
                         "histogram": "bar", "pie_chart": "pie", "piechart": "pie", "area_chart": "area",
                         "donut_chart": "donut", "scatter_chart": "scatter", "line": "line", "bar": "bar",
                         "pie": "pie", "area": "area", "donut": "donut", "scatter": "scatter", "hbar": "hbar",
                         "stacked_bar": "stacked_bar"}
CALLOUT_TONE_FROM_ALIAS = {"tip": "success", "warning": "warning", "alert": "danger", "note": "info", "info": "info"}
TEXT_KEYS = ("md", "text", "content", "body", "markdown", "description", "value")
CHILD_KEYS = ("children", "components", "items", "content", "blocks")


class _Ctx:
    def __init__(self) -> None:
        self.notes: list[str] = []
        self.nodes = 0
        self.ids: set[str] = set()
        self.icons: set[str] = set()
        self.state: dict[str, Any] = {}
        self.binds: list[tuple[str, Any]] = []
        self.weather: list[dict] = []
        self.images: list[dict] = []
        self.places: list[dict] = []
        self.kinds: list[str] = []

    def note(self, msg: str) -> None:
        if len(self.notes) < 40:
            self.notes.append(msg)


_DROP = object()


def _finite(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        t = v.strip().replace(",", "").replace(" ", "").replace(" ", "")
        t = re.sub(r"^[^\d\-+.]+|[^\d.]+$", "", t)  # currency symbols / trailing units
        try:
            f = float(t)
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _num_out(f: float) -> int | float:
    return int(f) if f.is_integer() and abs(f) < 1e15 else round(f, 10)


def _str(v: Any, cap: int) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v)
    if isinstance(v, str) and "\\n" in s and "\n" not in s:  # double-escaped newlines from small models
        s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "  ")
    s = s.replace("\x00", "")
    return s[:cap]


def _safe_url(v: Any, image: bool = False) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if image and s.startswith("data:"):
        return s if DATA_IMG_RE.match(s) and len(s) < 300_000 else None
    if len(s) > 2000 or any(c in s for c in "\x00\r\n\t<>\"'` "):
        return None
    try:
        u = urlparse(s)
    except ValueError:
        return None
    allowed = ("https",) if image else ("https", "http", "mailto")
    if u.scheme.lower() not in allowed:
        return None
    if u.scheme.lower() != "mailto" and (not u.netloc or "@" in u.netloc):
        return None
    return s


def _expr_ok(s: str) -> bool:
    return len(s) <= LIMITS["max_expr"] and bool(EXPR_RE.match(s)) and "__" not in s


def _clean_value(v: Any, kind: str, ctx: _Ctx, path: str, depth: int) -> Any:
    """Validate one field. Returns _DROP when the value is unusable."""
    if v is None:
        return _DROP
    name, _, arg = kind.partition(":")
    if name == "str":
        return _str(v, int(arg) if arg else LIMITS["max_str"]) or _DROP
    if name == "md":
        return _str(v, LIMITS["max_md"]) or _DROP
    if name == "code":
        return _str(v, LIMITS["max_code"]) or _DROP
    if name == "html":
        s = _str(v, LIMITS["max_html"])
        return s or _DROP
    if name == "num":
        f = _finite(v)
        return _DROP if f is None else _num_out(f)
    if name == "int":
        lo, hi = (int(x) for x in arg.split(":"))
        f = _finite(v)
        return _DROP if f is None else max(lo, min(hi, int(round(f))))
    if name == "bool":
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.lower() in ("true", "false", "yes", "no", "1", "0"):
            return v.lower() in ("true", "yes", "1")
        return bool(v) if isinstance(v, (int, float)) else _DROP
    if name == "enum":
        opts = arg.split("|")
        s = str(v).strip()
        for o in opts:
            if s.lower() == o.lower():
                return o
        return _DROP
    if name == "tone":
        s = str(v).strip().lower()
        s = TONE_ALIASES.get(s, s)
        return s if s in TONES else _DROP
    if name == "format":
        s = str(v).strip().lower()
        s = {"currency": "money", "usd": "money", "eur": "money", "%": "percent", "pct": "percent", "int": "integer",
             "float": "number", "decimal": "number", "string": "text", "years": "number", "minutes": "duration"}.get(s, s)
        return s if s in FORMATS else _DROP
    if name == "icon":
        s = str(v).strip().lower().replace("_", "-").replace(" ", "-")
        s = ICON_ALIASES.get(s, s)
        if s in ICONS:
            ctx.icons.add(s)
            return s
        return _DROP
    if name == "id":
        s = re.sub(r"[^A-Za-z0-9_\-]", "_", str(v).strip())[:64]
        if s and not s[0].isalpha() and s[0] != "_":
            s = "n" + s
        return s if ID_RE.match(s or "") and "__" not in s else _DROP
    if name == "var":
        s = re.sub(r"[^A-Za-z0-9_]", "_", str(v).strip())[:40]
        if s and s[0].isdigit():
            s = "v" + s
        return s if VAR_RE.match(s or "") and s not in RESERVED_VARS and "__" not in s else _DROP
    if name == "expr":
        s = str(v).strip().lstrip("=")
        if not s or not _expr_ok(s):
            ctx.note(f"{path}: expression rejected")
            return _DROP
        return s
    if name == "url":
        return _safe_url(v) or _DROP
    if name == "img":
        u = _safe_url(v, image=True)
        if u is None:
            ctx.note(f"{path}: image URL rejected (https only)")
        return u or _DROP
    if name == "scalar":
        if isinstance(v, bool) or isinstance(v, str):
            return v[:200] if isinstance(v, str) else v
        f = _finite(v)
        return _DROP if f is None else _num_out(f)
    if name == "val":  # number, bool, text or "=expression"
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            f = _finite(v)
            return _DROP if f is None else _num_out(f)
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("="):
                return s if _expr_ok(s[1:]) else _DROP
            return s[:200]
        return _DROP
    if name == "range":  # number or [min, max]
        if isinstance(v, (list, tuple)) and len(v) == 2:
            a, b = _finite(v[0]), _finite(v[1])
            return [_num_out(a), _num_out(b)] if a is not None and b is not None else _DROP
        f = _finite(v)
        return _DROP if f is None else _num_out(f)
    if name == "nums":
        if not isinstance(v, list):
            return _DROP
        out = [_finite(x) for x in v[: LIMITS["max_points"]]]
        return [_num_out(x) if x is not None else None for x in out]
    if name == "points":  # numbers, nulls, or [x, y] pairs for scatter
        if not isinstance(v, list):
            return _DROP
        out: list[Any] = []
        for x in v[: LIMITS["max_points"]]:
            if isinstance(x, (list, tuple)) and len(x) >= 2:
                a, b = _finite(x[0]), _finite(x[1])
                out.append([_num_out(a), _num_out(b)] if a is not None and b is not None else None)
            elif isinstance(x, dict) and "x" in x and "y" in x:
                a, b = _finite(x["x"]), _finite(x["y"])
                out.append([_num_out(a), _num_out(b)] if a is not None and b is not None else None)
            else:
                f = _finite(x)
                out.append(_num_out(f) if f is not None else None)
        return out
    if name == "labels":
        if not isinstance(v, list):
            return _DROP
        return [(_str(x, 80) or "") for x in v[: LIMITS["max_points"]]]
    if name == "strs":
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return _DROP
        return [s for s in (_str(x, 300) for x in v[: LIMITS["max_items"]]) if s]
    if name == "pairs":  # specs: {"VRAM": "24 GB"} or [["VRAM","24 GB"]] or [{"label","value"}]
        out_pairs: list[list[str]] = []
        if isinstance(v, dict):
            seq = list(v.items())
        elif isinstance(v, list):
            seq = []
            for x in v:
                if isinstance(x, (list, tuple)) and len(x) >= 2:
                    seq.append((x[0], x[1]))
                elif isinstance(x, dict):
                    seq.append((x.get("label") or x.get("name") or x.get("key"), x.get("value")))
        else:
            return _DROP
        for k, val in seq[:40]:
            ks, vs = _str(k, 80), _str(val if not isinstance(val, bool) else ("Yes" if val else "No"), 200)
            if ks and vs is not None:
                out_pairs.append([ks, vs])
        return out_pairs or _DROP
    if name == "matrix":
        if not isinstance(v, list):
            return _DROP
        rows = []
        for r in v[:60]:
            if isinstance(r, list):
                rows.append([(_num_out(f) if (f := _finite(x)) is not None else None) for x in r[:60]])
        return rows or _DROP
    if name == "options":
        if isinstance(v, dict):
            v = [{"label": k, "value": val} for k, val in v.items()]
        if not isinstance(v, list):
            return _DROP
        opts = []
        for o in v[:30]:
            if isinstance(o, dict):
                label = _str(o.get("label") or o.get("name") or o.get("value"), 80)
                val = o.get("value", label)
                ic = _clean_value(o.get("icon"), "icon", ctx, path, depth) if o.get("icon") else _DROP
            else:
                label, val, ic = _str(o, 80), o, _DROP
            if label is None:
                continue
            if not isinstance(val, (str, int, float, bool)) or (isinstance(val, float) and not math.isfinite(val)):
                val = label
            item = {"label": label, "value": val[:200] if isinstance(val, str) else val}
            if ic is not _DROP:
                item["icon"] = ic
            opts.append(item)
        return opts or _DROP
    if name == "badge":
        if isinstance(v, str):
            v = {"label": v}
        if not isinstance(v, dict):
            return _DROP
        b = _clean_obj(v, {"label": "str:60", "tone": "tone", "icon": "icon"}, ctx, path, depth)
        return b if b.get("label") else _DROP
    if name == "action":
        if isinstance(v, str):
            v = {"type": "prompt", "text": v}
        if not isinstance(v, dict):
            return _DROP
        a = _clean_obj(v, {"type": "enum:prompt|set|reset|copy|link|download", "text": "str:2000", "var": "var",
                           "value": "val", "url": "url", "filename": "str:80", "target": "id"}, ctx, path, depth)
        t = a.get("type")
        if not t:
            t = a["type"] = "link" if a.get("url") else "prompt" if a.get("text") else ""
        need = {"prompt": "text", "set": "var", "copy": "text", "link": "url"}.get(t)
        if not t or (need and need not in a) or (t == "download" and "target" not in a and "text" not in a):
            ctx.note(f"{path}: action dropped (incomplete)")
            return _DROP
        return a
    if name == "rows":
        if not isinstance(v, list):
            return _DROP
        if len(v) > LIMITS["max_rows"]:
            ctx.note(f"{path}: table truncated to {LIMITS['max_rows']} rows")
        rows_out = []
        for r in v[: LIMITS["max_rows"]]:
            if isinstance(r, dict):
                rows_out.append({str(k)[:64]: _cell(x) for k, x in list(r.items())[: LIMITS["max_columns"]]})
            elif isinstance(r, (list, tuple)):
                rows_out.append([_cell(x) for x in r[: LIMITS["max_columns"]]])
        return rows_out
    if name == "children":
        return _clean_children(v, ctx, path, depth + 1)
    if name == "obj":
        if not isinstance(v, dict):
            return _DROP
        o = _clean_obj(v, ITEMS[arg], ctx, path, depth)
        return o or _DROP
    if name == "list":
        schema = ITEMS[arg]
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            return _DROP
        if len(v) > LIMITS["max_items"]:
            ctx.note(f"{path}: list truncated to {LIMITS['max_items']} items")
        out_list = []
        for i, it in enumerate(v[: LIMITS["max_items"]]):
            if isinstance(it, str) and arg in ("tag", "check_item", "step", "flow_item", "timeline_item",
                                                 "list_item", "status_item", "accordion_item", "place"):
                key = {"tag": "label", "check_item": "label", "status_item": "name", "place": "name",
                       "accordion_item": "title"}.get(arg, "title")
                it = {key: it}
            if isinstance(it, str) and arg == "image":
                it = {"src": it}
            if isinstance(it, str) and arg == "column":
                it = {"key": it, "label": it}
            if not isinstance(it, dict):
                continue
            o = _clean_obj(it, schema, ctx, f"{path}[{i}]", depth)
            if o:
                out_list.append(o)
        return out_list
    return _DROP


def _cell(x: Any) -> Any:
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return _num_out(float(x)) if math.isfinite(float(x)) else None
    if isinstance(x, dict):  # {"label","url"} link cells or {"value","tone"} badges
        out = {}
        for k in ("label", "value", "tone", "url"):
            if k in x:
                val = x[k]
                if k == "url":
                    val = _safe_url(val)
                elif k == "tone":
                    t = TONE_ALIASES.get(str(val).lower(), str(val).lower())
                    val = t if t in TONES else None
                elif isinstance(val, (int, float)) and not isinstance(val, bool):
                    val = _num_out(float(val)) if math.isfinite(float(val)) else None
                else:
                    val = _str(val, 400)
                if val is not None:
                    out[k] = val
        return out or None
    return _str(x, 400)


FIELD_ALIASES = {
    "md": ("text", "content", "body", "markdown", "description", "verdict"),
    "pros": ("points", "advantages"),
    "cons": ("disadvantages",),
    "group": ("day",),
    "title": ("heading", "name"),
    "label": ("name", "title", "text"),
    "children": ("components", "content", "blocks", "items"),
    "items": ("data", "entries", "list", "options_list"),
    "src": ("url", "image", "href"),
    "lng": ("lon", "long", "longitude"),
    "lat": ("latitude",),
    "detail": ("description", "details", "text", "body"),
    "subtitle": ("description", "sub", "secondary"),
    "value": ("amount", "val"),
    "bind": ("var", "state", "name_var"),
    "code": ("content", "source"),
    "html": ("content", "source", "code"),
    "images": ("items", "photos", "pictures"),
    "places": ("items", "stops", "locations", "markers"),
    "nodes": ("items",),
    "tabs": ("items", "panels"),
}


def _clean_obj(raw: dict, schema: dict[str, str], ctx: _Ctx, path: str, depth: int) -> dict:
    out: dict[str, Any] = {}
    used: set[str] = set()
    for key, kind in schema.items():
        val = raw.get(key)
        if val is None:
            for alt in FIELD_ALIASES.get(key, ()):  # tolerate common synonyms
                if alt in raw and alt not in schema and alt not in used and alt != "type":
                    val = raw[alt]
                    used.add(alt)
                    break
        if val is None:
            continue
        c = _clean_value(val, kind, ctx, f"{path}.{key}", depth)
        if c is not _DROP and c != [] and c != "":
            out[key] = c
    return out


def _clean_children(v: Any, ctx: _Ctx, path: str, depth: int) -> list:
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return []
    if depth > LIMITS["max_depth"]:
        ctx.note(f"{path}: nesting deeper than {LIMITS['max_depth']} levels removed")
        return []
    if len(v) > LIMITS["max_children"]:
        ctx.note(f"{path}: only the first {LIMITS['max_children']} components kept")
    out = []
    for i, c in enumerate(v[: LIMITS["max_children"]]):
        comp = _clean_component(c, ctx, f"{path}[{i}]", depth)
        if comp:
            out.append(comp)
    return out


def _clean_component(raw: Any, ctx: _Ctx, path: str, depth: int) -> dict | None:
    if isinstance(raw, str):
        raw = {"type": "text", "md": raw}
    if not isinstance(raw, dict):
        ctx.note(f"{path}: dropped (not an object)")
        return None
    if ctx.nodes >= LIMITS["max_nodes"]:
        ctx.note(f"component limit ({LIMITS['max_nodes']}) reached; the rest was dropped")
        return None
    raw = dict(raw)
    if isinstance(raw.get("props"), dict):  # {"type","props":{...}} style
        props = raw.pop("props")
        raw = {**props, **raw}
    t0 = str(raw.get("type") or raw.get("component") or raw.get("kind") or "").strip().lower().replace("-", "_")
    t0 = t0.replace(" ", "_")
    t = TYPE_ALIASES.get(t0, t0)
    if t == "chart":
        k = str(raw.get("kind") or raw.get("chart_type") or raw.get("chartType") or CHART_KIND_FROM_ALIAS.get(t0, "")).lower()
        k = {"column": "bar", "columns": "bar", "bars": "bar", "stacked": "stacked_bar", "stacked_column": "stacked_bar",
             "horizontal_bar": "hbar", "bar_horizontal": "hbar", "doughnut": "donut", "lines": "line",
             "chart": "line", "": "line"}.get(k, CHART_KIND_FROM_ALIAS.get(k, k))
        raw["kind"] = k
    if t0 in CALLOUT_TONE_FROM_ALIAS and not raw.get("tone"):
        raw["tone"] = CALLOUT_TONE_FROM_ALIAS[t0]
    if t == "chart" and isinstance(raw.get("nodes"), list):
        t = "diagram"
    if t == "compare" and t0 in ("places",) and isinstance(raw.get("items"), list) and raw["items"] and all(
            isinstance(x, dict) and ("lat" in x or "latitude" in x) for x in raw["items"]):
        t, raw["places"] = "map", raw.pop("items")
    if t == "compare" and t0 == "product" and "items" not in raw:
        raw = {"type": "compare", "items": [raw]}
    if t == "metric" and t0 == "sparkline" and "trend" not in raw:
        raw["trend"] = raw.get("data") or raw.get("values")
    legacy = _migrate_legacy(t, raw, ctx, path)
    if legacy is not None:
        return _clean_component(legacy, ctx, path, depth) if isinstance(legacy, dict) else None
    if t not in SCHEMAS:
        txt = next((raw.get(k) for k in TEXT_KEYS if isinstance(raw.get(k), str) and raw.get(k).strip()), None)
        kids = next((raw.get(k) for k in CHILD_KEYS if isinstance(raw.get(k), list)), None)
        if kids:
            ctx.note(f"{path}: unknown type '{t0 or '?'}' rendered as a card")
            raw = {"type": "card", "title": raw.get("title"), "children": kids}
            t = "card"
        elif txt:
            ctx.note(f"{path}: unknown type '{t0 or '?'}' rendered as text")
            title = _str(raw.get("title"), 200)
            raw = {"type": "text", "md": (f"**{title}**\n\n" if title else "") + txt}
            t = "text"
        else:
            ctx.note(f"{path}: unknown type '{t0 or '?'}' dropped")
            return None
    if t == "weather" and "location" not in raw:
        raw["location"] = raw.get("city") or raw.get("place") or raw.get("query")
    ctx.nodes += 1
    schema = {**SCHEMAS[t], **COMMON}
    out = {"type": t, **_clean_obj(raw, schema, ctx, path, depth)}
    if "id" in out:
        base, n = out["id"], 2
        while out["id"] in ctx.ids:
            out["id"] = f"{base}_{n}"
            n += 1
        if out["id"] != base:
            ctx.note(f"{path}: duplicate id '{base}' renamed to '{out['id']}'")
        ctx.ids.add(out["id"])
    if not _post_check(out, ctx, path):
        return None
    ctx.kinds.append(t)
    return out


def _post_check(c: dict, ctx: _Ctx, path: str) -> bool:
    """Type-specific required fields and state registration."""
    t = c["type"]
    required = {"text": "md", "callout": "md", "quote": "md", "code": "code", "heading": "text", "app": "html",
                "weather": "location", "notice": "md"}
    if t in required and required[t] not in c:
        if t == "callout" and c.get("title"):
            c["md"] = ""
            return True
        ctx.note(f"{path}: {t} without '{required[t]}' dropped")
        return False
    if t in ("chart",):
        if not c.get("series"):
            ctx.note(f"{path}: chart without series dropped")
            return False
        c["series"] = [s for s in c["series"][: LIMITS["max_series"]] if s.get("data") or s.get("expr")]
        if not c["series"]:
            ctx.note(f"{path}: chart series had no data")
            return False
        c.setdefault("kind", "line")
    if t == "table":
        rows = c.get("rows") or []
        if not c.get("columns"):
            if rows and isinstance(rows[0], dict):
                keys: list[str] = []
                for r in rows[:50]:
                    for k in r:
                        if k not in keys:
                            keys.append(k)
                c["columns"] = [{"key": k, "label": k.replace("_", " ").strip().capitalize()} for k in keys[:24]]
            elif rows and isinstance(rows[0], list):  # first row is a header when it is all text
                if all(isinstance(x, str) for x in rows[0]) and len(rows) > 1:
                    c["columns"] = [{"key": str(i), "label": x} for i, x in enumerate(rows[0])]
                    c["rows"] = rows[1:]
                else:
                    c["columns"] = [{"key": str(i), "label": f"Column {i + 1}"} for i in range(len(rows[0]))]
            else:
                ctx.note(f"{path}: table without columns or rows dropped")
                return False
        for i, col in enumerate(c["columns"]):
            col.setdefault("key", str(i))
            col.setdefault("label", col["key"])
        c.setdefault("rows", [])
    if t in ("timeline", "steps", "flow", "status", "list", "checklist", "compare", "accordion", "metrics", "kv",
             "tags", "gallery", "actions") and not c.get("items") and not c.get("images"):
        ctx.note(f"{path}: {t} without items dropped")
        return False
    if t == "tabs":
        if not c.get("tabs"):
            ctx.note(f"{path}: tabs without tabs dropped")
            return False
        for i, tab in enumerate(c["tabs"]):
            tab.setdefault("label", f"Tab {i + 1}")
            tab.setdefault("value", tab["label"])
        if c.get("bind"):
            _register(ctx, c["bind"], c["tabs"][0]["value"])
    if t == "diagram":
        nodes = c.get("nodes") or []
        ids = {n.get("id") for n in nodes if n.get("id")}
        for i, n in enumerate(nodes):
            if not n.get("id"):
                n["id"] = f"n{i}"
                ids.add(n["id"])
            n.setdefault("label", n["id"])
        c["edges"] = [e for e in c.get("edges") or [] if e.get("from") in ids and e.get("to") in ids][:200]
        if not nodes:
            ctx.note(f"{path}: diagram without nodes dropped")
            return False
    if t == "map":
        if not c.get("places"):
            ctx.note(f"{path}: map without places dropped")
            return False
        for p in c["places"]:
            if "lat" in p and "lng" in p and not (-90 <= p["lat"] <= 90 and -180 <= p["lng"] <= 180):
                p.pop("lat")
                p.pop("lng")
            if "lat" not in p or "lng" not in p:
                ctx.places.append(p)
    if t == "weather":
        ctx.weather.append(c)
    if t in ("image",) and not c.get("src"):
        if c.get("query"):
            ctx.images.append((c, "src"))
        else:
            ctx.note(f"{path}: image without src dropped")
            return False
    if t == "gallery":
        imgs = c.get("images") or []
        keep = []
        for im in imgs:
            if im.get("src"):
                keep.append(im)
            elif im.get("query"):
                keep.append(im)
                ctx.images.append((im, "src"))
        if not keep:
            ctx.note(f"{path}: gallery without usable images dropped")
            return False
        c["images"] = keep
    if t == "compare":
        for p in c.get("items") or []:
            if not p.get("image") and p.get("query"):
                ctx.images.append((p, "image"))
    # inputs bind state
    if t in ("slider", "number"):
        c.setdefault("min", 0)
        c.setdefault("max", max(100, c.get("value", 0) or 0))
        if c["max"] <= c["min"]:
            c["max"] = c["min"] + 1
        c.setdefault("step", 1 if (c["max"] - c["min"]) >= 10 else round((c["max"] - c["min"]) / 100, 6) or 1)
        init = c.get("value", c["min"])
        init = max(c["min"], min(c["max"], init))
        if "bind" not in c:
            c["bind"] = _auto_var(ctx, c.get("label") or t)
        _register(ctx, c["bind"], init)
    if t in ("select", "segmented"):
        if not c.get("options"):
            ctx.note(f"{path}: {t} without options dropped")
            return False
        if "bind" not in c:
            c["bind"] = _auto_var(ctx, c.get("label") or t)
        vals = [o["value"] for o in c["options"]]
        _register(ctx, c["bind"], c["value"] if c.get("value") in vals else vals[0])
    if t == "toggle":
        if "bind" not in c:
            c["bind"] = _auto_var(ctx, c.get("label") or t)
        _register(ctx, c["bind"], bool(c.get("value", False)))
    if t in ("text_input", "date"):
        if "bind" not in c:
            c["bind"] = _auto_var(ctx, c.get("label") or t)
        _register(ctx, c["bind"], c.get("value", ""))
    if t == "list":
        for i, it in enumerate(c.get("items") or []):
            it.setdefault("id", f"item{i + 1}")
            it.setdefault("title", it["id"])
        if c.get("bind"):
            _register(ctx, c["bind"], c["items"][0]["id"] if c.get("selectable", True) else "")
    if t == "checklist":
        for i, it in enumerate(c.get("items") or []):
            it.setdefault("id", f"c{i + 1}")
            it.setdefault("label", f"Item {i + 1}")
        if c.get("bind"):
            _register(ctx, c["bind"], sum(1 for it in c["items"] if it.get("checked")))
            _register(ctx, c["bind"] + "_total", 0)
    return True


def _auto_var(ctx: _Ctx, label: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(label).lower()).strip("_")[:30] or "value"
    if base[0].isdigit() or base in RESERVED_VARS:
        base = "v_" + base
    name, n = base, 2
    while name in ctx.state:
        name = f"{base}_{n}"
        n += 1
    return name


def _register(ctx: _Ctx, var: str, value: Any) -> None:
    if var not in ctx.state and len(ctx.state) < LIMITS["max_state"]:
        ctx.state[var] = value


# --------------------------------------------------------------------------------------------------------------------
# Backward compatibility with Frontier UI 1.x "blocks" specs.
# --------------------------------------------------------------------------------------------------------------------
def _migrate_legacy(t: str, raw: dict, ctx: _Ctx, path: str) -> Any:
    """Convert Frontier UI block types that no longer exist. Returns None when no migration applies."""
    if t == "budget":
        items = [x for x in raw.get("items") or [] if isinstance(x, dict)][:20]
        cur = str(raw.get("currency") or "$")[:4]
        kids: list[dict] = []
        terms = []
        for i, it in enumerate(items):
            var = f"b{i + 1}"
            amount = _finite(it.get("amount")) or 0
            qty = _finite(it.get("qty")) or 1
            kids.append({"type": "slider", "bind": var, "label": str(it.get("label") or f"Item {i + 1}"),
                         "min": _finite(it.get("min")) or 0, "max": _finite(it.get("max")) or max(amount * 3, 10),
                         "value": amount, "format": "money", "currency": cur})
            terms.append(f"{var}*{_num_out(qty)}")
        ctx.note(f"{path}: legacy 'budget' converted to sliders + total")
        return {"type": "card", "title": raw.get("title") or "Budget", "icon": "wallet", "children": [
            {"type": "grid", "columns": 2, "children": kids},
            {"type": "metric", "label": "Total", "value": "=" + ("+".join(terms) or "0"), "format": "money",
             "currency": cur, "icon": "calculator"}]}
    if t == "calculator" and isinstance(raw.get("inputs"), list):
        kids = []
        for it in raw["inputs"][:12]:
            if isinstance(it, dict) and it.get("id"):
                kids.append({"type": "slider", "bind": it["id"], "label": it.get("label") or it["id"],
                             "min": it.get("min"), "max": it.get("max"), "step": it.get("step"),
                             "value": it.get("value"), "format": "money" if raw.get("currency") else None,
                             "currency": raw.get("currency")})
        outs = []
        for o in raw.get("outputs") or []:
            if isinstance(o, dict) and o.get("expr"):
                outs.append({"label": o.get("label"), "value": "=" + str(o["expr"]),
                             "format": {"money": "money", "percent": "percent"}.get(o.get("format"), "number"),
                             "currency": raw.get("currency")})
        ctx.note(f"{path}: legacy 'calculator' converted to sliders + metrics")
        return {"type": "card", "title": raw.get("title") or "Calculator", "icon": "calculator",
                "children": [{"type": "grid", "columns": 2, "children": kids},
                             {"type": "metrics", "items": outs}]}
    if t == "map" and isinstance(raw.get("days"), list) and not raw.get("places"):
        days = [d for d in raw["days"] if isinstance(d, dict) and isinstance(d.get("places"), list)][:7]
        if not days:
            return None
        ctx.note(f"{path}: multi-day map converted to day tabs")
        return {"type": "tabs", "tabs": [{"label": str(d.get("label") or f"Day {i + 1}"), "children": [
            {"type": "map", "places": d["places"], "mode": raw.get("mode")}]} for i, d in enumerate(days)]}
    if t in ("stock", "apps", "email", "links", "hero"):
        if t == "links":
            lines = [f"- [{x.get('title') or x.get('url')}]({x.get('url')})" + (f" - {x['note']}" if x.get("note") else "")
                     for x in raw.get("items") or [] if isinstance(x, dict) and _safe_url(x.get("url"))]
            return {"type": "text", "md": "\n".join(lines)} if lines else False
        if t == "hero":
            kids = []
            if raw.get("image"):
                kids.append({"type": "image", "query": raw["image"]} if not _safe_url(raw["image"], True)
                            else {"type": "image", "src": raw["image"]})
            kids.append({"type": "heading", "text": raw.get("title") or raw.get("eyebrow") or ""})
            if raw.get("md"):
                kids.append({"type": "text", "md": raw["md"]})
            return {"type": "card", "children": kids}
        if t == "email":
            body = f"To: {raw.get('to', '')}\nSubject: {raw.get('subject', '')}\n\n{raw.get('body', '')}"
            return {"type": "code", "title": "Email draft", "language": "text", "code": body}
        ctx.note(f"{path}: '{t}' blocks are not supported in Generative UI and were removed")
        return False
    return None


# --------------------------------------------------------------------------------------------------------------------
# Top-level spec normalization
# --------------------------------------------------------------------------------------------------------------------
def normalize_spec(data: Any) -> tuple[dict, _Ctx]:
    ctx = _Ctx()
    if isinstance(data, dict) and isinstance(data.get("spec"), (dict, str)) and not any(
            k in data for k in ("components", "blocks")):
        data = parse_spec(data["spec"])
    if isinstance(data, list):
        data = {"components": data}
    if not isinstance(data, dict):
        raise SpecError('spec must be a JSON object with a "components" array.')
    if data.get("type") and not any(k in data for k in ("components", "blocks")):  # a single component
        data = {"components": [data]}
    comps = None
    for k in ("components", "blocks", "children", "sections", "content", "items", "ui"):
        if isinstance(data.get(k), (list, dict)):
            comps = data[k]
            if k not in ("components",):
                ctx.note(f"used '{k}' as components")
            break
    if comps is None:
        text = next((data.get(k) for k in ("md", "text", "markdown") if isinstance(data.get(k), str)), None)
        if not text:
            raise SpecError('spec has no "components" array. Example: {"title":"GPU status","components":'
                            '[{"type":"metric","label":"VRAM","value":192,"unit":"GB","icon":"hard-drive"}]}')
        comps = [{"type": "text", "md": text}]
    if isinstance(data.get("state"), dict):
        for k, v in list(data["state"].items())[: LIMITS["max_state"]]:
            var = _clean_value(k, "var", ctx, "state", 0)
            if var is _DROP:
                ctx.note(f"state key '{k}' rejected")
                continue
            val = _clean_value(v, "scalar", ctx, "state", 0)
            if val is not _DROP:
                ctx.state[var] = val
    computed: dict[str, str] = {}
    if isinstance(data.get("computed"), dict):
        for k, v in list(data["computed"].items())[:32]:
            var = _clean_value(k, "var", ctx, "computed", 0)
            ex = _clean_value(v, "expr", ctx, f"computed.{k}", 0)
            if var is _DROP or ex is _DROP:
                ctx.note(f"computed '{k}' rejected")
                continue
            computed[var] = ex
            ctx.state.setdefault(var, 0)
    components = _clean_children(comps, ctx, "components", 0)
    if not components:
        raise SpecError("none of the components could be used: " + "; ".join(ctx.notes[:8]))
    spec: dict[str, Any] = {"version": PROTOCOL_VERSION, "components": components}
    top = _clean_obj(data, {"title": "str:160", "subtitle": "str:400", "icon": "icon", "badge": "badge",
                            "footnote": "str:600", "frame": "enum:card|plain"}, ctx, "spec", 0)
    spec.update(top)
    ds = data.get("data_source") or data.get("source_kind")
    if isinstance(ds, str):
        ds = {"kind": ds}
    if isinstance(ds, dict):
        d = _clean_obj(ds, {"kind": "enum:live|cached|user|demo|static|estimate", "label": "str:200",
                            "as_of": "str:40"}, ctx, "data_source", 0)
        if d.get("kind"):
            spec["data_source"] = d
    srcs = data.get("sources") or data.get("citations") or []
    if isinstance(srcs, list):
        clean_src = []
        for s in srcs[: LIMITS["max_sources"]]:
            if isinstance(s, str):
                s = {"url": s}
            if isinstance(s, dict):
                u = _safe_url(s.get("url") or s.get("href") or s.get("link"))
                if u and not u.startswith("mailto"):
                    clean_src.append({"title": _str(s.get("title") or s.get("name"), 200) or urlparse(u).netloc,
                                      "url": u})
                elif s.get("url"):
                    ctx.note("a source URL was rejected (http/https only)")
        if clean_src:
            spec["sources"] = clean_src
    fu = data.get("followups") or data.get("suggestions") or data.get("follow_ups")
    fu_clean = _clean_value(fu, "strs", ctx, "followups", 0) if fu else _DROP
    if fu_clean is not _DROP:
        spec["followups"] = [s[:200] for s in fu_clean][: LIMITS["max_followups"]]
    spec["state"] = ctx.state
    if computed:
        spec["computed"] = computed
    return spec, ctx


# --------------------------------------------------------------------------------------------------------------------
# Plain-text fallback (noscript + model context)
# --------------------------------------------------------------------------------------------------------------------
def _fmt_plain(v: Any, c: dict | None = None) -> str:
    c = c or {}
    if isinstance(v, str) and v.startswith("="):
        return "(interactive)"
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (int, float)):
        f = c.get("format")
        cur = c.get("currency") or ""
        s = f"{v:,.0f}" if f in ("integer", "money") and abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
        if f == "money":
            s = f"{cur or '$'}{s}" if len(cur) <= 1 else f"{s} {cur}"
        if f == "percent":
            s += "%"
        if c.get("unit"):
            s += f" {c['unit']}"
        return s
    return str(v)


def to_markdown(spec: dict, limit: int = 6000) -> str:
    out: list[str] = []
    if spec.get("title"):
        out.append(f"### {spec['title']}")
    if spec.get("subtitle"):
        out.append(spec["subtitle"])

    def walk(comps: list, level: int = 0) -> None:
        for c in comps:
            t = c["type"]
            if t in ("text", "callout", "quote", "notice"):
                if c.get("title"):
                    out.append(f"**{c['title']}**")
                out.append(c.get("md", ""))
            elif t == "heading":
                out.append(f"#### {c['text']}")
            elif t in ("metric",):
                out.append(f"- {c.get('label', '')}: {_fmt_plain(c.get('value'), c)}")
            elif t == "metrics":
                out.extend(f"- {m.get('label', '')}: {_fmt_plain(m.get('value'), m)}" for m in c["items"])
            elif t == "progress":
                out.append(f"- {c.get('label', '')}: {_fmt_plain(c.get('value'), c)} / {_fmt_plain(c.get('max', 100))}")
            elif t == "gauge":
                out.append(f"- {c.get('label', '')}: {_fmt_plain(c.get('value'), c)}")
            elif t == "kv":
                out.extend(f"- {i.get('label', '')}: {_fmt_plain(i.get('value'), i)}" for i in c["items"])
            elif t == "table":
                cols = c["columns"]
                out.append("| " + " | ".join(col["label"] for col in cols) + " |")
                out.append("|" + "---|" * len(cols))
                for r in c["rows"][:15]:
                    cells = []
                    for i, col in enumerate(cols):
                        v = r.get(col["key"]) if isinstance(r, dict) else (r[i] if i < len(r) else "")
                        if isinstance(v, dict):
                            v = v.get("label", v.get("value", ""))
                        cells.append(str("" if v is None else v).replace("|", "/"))
                    out.append("| " + " | ".join(cells) + " |")
                if len(c["rows"]) > 15:
                    out.append(f"... {len(c['rows']) - 15} more rows")
            elif t == "chart":
                labels = c.get("labels") or []
                for s in c["series"]:
                    if s.get("data"):
                        pts = ", ".join(f"{labels[i] if i < len(labels) else i}: {v}" for i, v in
                                        enumerate(s["data"][:24]))
                        out.append(f"- {s.get('name', 'Series')}: {pts}")
            elif t in ("timeline", "steps", "flow"):
                for i, it in enumerate(c["items"], 1):
                    head = " ".join(x for x in (it.get("time"), it.get("title")) if x)
                    out.append(f"{i}. {head}" + (f" - {it['detail']}" if it.get("detail") else ""))
            elif t == "status":
                out.extend(f"- {it.get('name', '')}: {it.get('state', '')} {it.get('detail', '')}".rstrip()
                           for it in c["items"])
            elif t == "list":
                out.extend(f"- {it.get('title', '')}" + (f" ({it['subtitle']})" if it.get("subtitle") else "")
                           for it in c["items"])
            elif t == "checklist":
                out.extend(f"- [{'x' if it.get('checked') else ' '}] {it.get('label', '')}" for it in c["items"])
            elif t == "compare":
                for p in c["items"]:
                    out.append(f"- **{p.get('title', '')}** {_fmt_plain(p['price'], p) if 'price' in p else ''}".rstrip())
                    for k, v in p.get("specs") or []:
                        out.append(f"  - {k}: {v}")
            elif t == "map":
                out.extend(f"- {p.get('time', '')} {p.get('name', '')}".strip() for p in c["places"])
            elif t == "diagram":
                names = {n["id"]: n.get("label", n["id"]) for n in c["nodes"]}
                out.extend(f"- {names[e['from']]} -> {names[e['to']]}" for e in c.get("edges") or [])
            elif t == "weather" and isinstance(c.get("data"), dict):
                d = c["data"]
                cur = d.get("current") or {}
                out.append(f"- Weather {d.get('place', '')}: {cur.get('temp')}{d.get('temp_unit', '')}, {cur.get('label', '')}")
                for day in (d.get("daily") or [])[:7]:
                    out.append(f"  - {day.get('date')}: {day.get('label')}, {day.get('min')} to {day.get('max')}")
            elif t == "code":
                out.append(f"```{c.get('language', '')}\n{c['code'][:1500]}\n```")
            for key in ("children", "controls"):
                if c.get(key):
                    walk(c[key], level + 1)
            if t == "tabs":
                for tab in c["tabs"]:
                    out.append(f"**{tab['label']}**")
                    walk(tab.get("children") or [], level + 1)
            if t == "accordion":
                for it in c["items"]:
                    out.append(f"**{it.get('title', '')}**")
                    if it.get("md"):
                        out.append(it["md"])
                    walk(it.get("children") or [], level + 1)

    walk(spec["components"])
    if spec.get("sources"):
        out.append("Sources: " + "; ".join(f"[{i}] {s['title']} {s['url']}" for i, s in
                                            enumerate(spec["sources"], 1)))
    text = "\n".join(x for x in out if x is not None and str(x).strip())
    return text if len(text) <= limit else text[:limit] + "\n..."


# --------------------------------------------------------------------------------------------------------------------
# Live data (server-side, keyless, bounded). Nothing here is fabricated: failures render a clear error state.
# --------------------------------------------------------------------------------------------------------------------
WMO = {0: ("Clear", "sun"), 1: ("Mainly clear", "cloud-sun"), 2: ("Partly cloudy", "cloud-sun"),
       3: ("Overcast", "cloud"), 45: ("Fog", "cloud-fog"), 48: ("Rime fog", "cloud-fog"),
       51: ("Light drizzle", "cloud-drizzle"), 53: ("Drizzle", "cloud-drizzle"), 55: ("Dense drizzle", "cloud-drizzle"),
       56: ("Freezing drizzle", "cloud-drizzle"), 57: ("Freezing drizzle", "cloud-drizzle"),
       61: ("Light rain", "cloud-rain"), 63: ("Rain", "cloud-rain"), 65: ("Heavy rain", "cloud-rain"),
       66: ("Freezing rain", "cloud-rain"), 67: ("Freezing rain", "cloud-rain"), 71: ("Light snow", "cloud-snow"),
       73: ("Snow", "cloud-snow"), 75: ("Heavy snow", "cloud-snow"), 77: ("Snow grains", "cloud-snow"),
       80: ("Rain showers", "cloud-rain"), 81: ("Rain showers", "cloud-rain"), 82: ("Violent showers", "cloud-rain"),
       85: ("Snow showers", "cloud-snow"), 86: ("Snow showers", "cloud-snow"), 95: ("Thunderstorm", "cloud-lightning"),
       96: ("Thunderstorm, hail", "cloud-lightning"), 99: ("Thunderstorm, hail", "cloud-lightning")}

_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> Any:
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    if len(_CACHE) > 500:
        for k in list(_CACHE)[:100]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.monotonic(), value)


async def _get_json(session: Any, url: str, params: dict, timeout: float) -> Any:
    import aiohttp

    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=timeout),
                           headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status}")
        return await r.json(content_type=None)


async def geocode(session: Any, query: str, timeout: float) -> dict | None:
    """City-level geocoding through Open-Meteo (keyless)."""
    q = re.sub(r"\s+", " ", query).strip()[:120]
    if not q:
        return None
    key = "geo:" + q.lower()
    cached = _cache_get(key, 86400)
    if cached is not None:
        return cached or None
    parts = [p.strip() for p in q.split(",") if p.strip()]
    name, hints = parts[0], [p.lower() for p in parts[1:]]
    data = await _get_json(session, "https://geocoding-api.open-meteo.com/v1/search",
                           {"name": name, "count": 8, "language": "en", "format": "json"}, timeout)
    results = data.get("results") or []
    best = None
    for r in results:
        hay = " ".join(str(r.get(k, "")) for k in ("country", "country_code", "admin1", "admin2")).lower()
        if not hints or any(h in hay for h in hints):
            best = r
            break
    best = best or (results[0] if results else None)
    out = None
    if best:
        label = ", ".join(x for x in (best.get("name"), best.get("admin1"), best.get("country")) if x)
        out = {"lat": float(best["latitude"]), "lng": float(best["longitude"]), "label": label,
               "timezone": best.get("timezone")}
    _cache_put(key, out or {})
    return out


async def fetch_weather(session: Any, location: str, days: int, units: str, timeout: float) -> dict:
    geo = await geocode(session, location, timeout)
    if not geo:
        raise RuntimeError(f"location '{location}' not found")
    imperial = units == "imperial"
    key = f"wx:{geo['lat']:.3f},{geo['lng']:.3f}:{days}:{units}"
    cached = _cache_get(key, 600)
    if cached:
        return cached
    params = {
        "latitude": f"{geo['lat']:.4f}", "longitude": f"{geo['lng']:.4f}", "timezone": "auto", "forecast_days": days,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,is_day",
        "hourly": "temperature_2m,precipitation_probability",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    if imperial:
        params.update({"temperature_unit": "fahrenheit", "wind_speed_unit": "mph"})
    d = await _get_json(session, "https://api.open-meteo.com/v1/forecast", params, timeout)
    cur = d.get("current") or {}
    code = int(cur.get("weather_code") or 0)
    label, icon = WMO.get(code, ("Unknown", "cloud"))
    if code in (0, 1) and cur.get("is_day") == 0:
        icon = "moon"
    hourly = d.get("hourly") or {}
    times = hourly.get("time") or []
    now_idx = 0
    cur_time = str(cur.get("time") or "")
    for i, t in enumerate(times):
        if t[:13] >= cur_time[:13]:
            now_idx = i
            break
    h_times = [t[11:16] for t in times[now_idx: now_idx + 24]]
    h_temp = [_finite(x) for x in (hourly.get("temperature_2m") or [])[now_idx: now_idx + 24]]
    h_rain = [_finite(x) for x in (hourly.get("precipitation_probability") or [])[now_idx: now_idx + 24]]
    daily = d.get("daily") or {}
    days_out = []
    for i, t in enumerate(daily.get("time") or []):
        c = int((daily.get("weather_code") or [0])[i] or 0)
        lab, ic = WMO.get(c, ("Unknown", "cloud"))
        days_out.append({"date": t, "label": lab, "icon": ic,
                         "max": _finite((daily.get("temperature_2m_max") or [None])[i]),
                         "min": _finite((daily.get("temperature_2m_min") or [None])[i]),
                         "rain": _finite((daily.get("precipitation_probability_max") or [None])[i])})
    out = {
        "place": geo["label"], "lat": geo["lat"], "lng": geo["lng"],
        "temp_unit": "°F" if imperial else "°C", "wind_unit": "mph" if imperial else "km/h",
        "current": {"temp": _finite(cur.get("temperature_2m")), "feels": _finite(cur.get("apparent_temperature")),
                    "humidity": _finite(cur.get("relative_humidity_2m")), "wind": _finite(cur.get("wind_speed_10m")),
                    "label": label, "icon": icon, "time": cur_time},
        "hourly": {"time": h_times, "temp": h_temp, "rain": h_rain},
        "daily": days_out,
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": {"title": "Open-Meteo", "url": "https://open-meteo.com/"},
    }
    _cache_put(key, out)
    return out


async def wiki_image(session: Any, query: str, timeout: float) -> dict | None:
    key = "img:" + query.lower()
    cached = _cache_get(key, 86400)
    if cached is not None:
        return cached or None
    data = await _get_json(session, "https://en.wikipedia.org/w/api.php", {
        "action": "query", "format": "json", "formatversion": "2", "generator": "search", "gsrsearch": query,
        "gsrlimit": "4", "prop": "pageimages|info", "piprop": "thumbnail", "pithumbsize": "960", "inprop": "url"},
        timeout)
    pages = sorted((data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0))
    out = None
    skip = re.compile(r"\.svg|logo|icon|flag|map|seal|coat_of_arms|signature", re.I)
    for p in pages:
        src = (p.get("thumbnail") or {}).get("source") or ""
        if src.startswith("https://") and not skip.search(src):
            out = {"src": src, "link": p.get("fullurl", ""), "credit": f"Wikipedia: {p.get('title', '')}"}
            break
    _cache_put(key, out or {})
    return out


# --------------------------------------------------------------------------------------------------------------------
# HTML assembly
# --------------------------------------------------------------------------------------------------------------------
RENDERER_ICONS = {"check", "copy", "chevron-down", "chevron-left", "chevron-right", "chevron-up", "circle-check",
                  "circle-check-big", "circle", "triangle-alert", "circle-x", "info", "circle-alert",
                  "message-circle", "arrow-down", "arrow-right", "arrow-up", "search", "table", "chart-line",
                  "download", "external-link", "rotate-ccw", "map-pin", "star", "x", "link", "sparkles",
                  "circle-help", "trending-up", "trending-down", "droplets", "wind", "thermometer", "minus", "plus",
                  "arrow-up-down", "lightbulb", "image", "award", "route", "map", "workflow", "receipt"}

CSP = ("default-src 'none'; img-src https: data: blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "font-src data:; frame-src https://www.openstreetmap.org; connect-src 'none'; form-action 'none'; "
       "base-uri 'none'; media-src 'none'; object-src 'none'")


def _json_for_html(value: Any) -> str:
    """ASCII-only JSON safe to embed inside <script type=application/json>."""
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def _collect_icons(spec: dict, ctx: _Ctx) -> dict:
    names = set(ctx.icons) | RENDERER_ICONS
    for c in spec["components"]:
        if c.get("type") == "weather":
            names |= {"sun", "moon", "cloud", "cloud-sun", "cloud-fog", "cloud-drizzle", "cloud-rain",
                      "cloud-snow", "cloud-lightning"}
    return {n: ICONS[n] for n in sorted(names) if n in ICONS}


def build_html(spec: dict, ctx: _Ctx, cfg: dict) -> str:
    payload = {"spec": spec, "cfg": cfg, "icons": _collect_icons(spec, ctx)}
    parts = {
        "__CSP__": CSP,
        "__TITLE__": _html_escape(spec.get("title") or "Generative UI"),
        "__CSS__": _CSS,
        "__FALLBACK__": _html_escape(to_markdown(spec, 4000)),
        "__JS__": _JS,
        "__DATA__": _json_for_html(payload),
    }
    # single pass, so model-written text can never inject a later placeholder
    return re.sub(r"__(CSP|TITLE|CSS|FALLBACK|JS|DATA)__", lambda m: parts[m.group(0)], _TEMPLATE)


# --------------------------------------------------------------------------------------------------------------------
# Open WebUI Tool
# --------------------------------------------------------------------------------------------------------------------
class Tools:
    class Valves(BaseModel):
        theme: Literal["auto", "light", "dark"] = Field(
            default="auto", description="auto follows the user's Open WebUI theme (and live theme switches when "
                                        "'iframe same-origin' is enabled); light/dark force one.")
        followup_action: Literal["submit", "fill"] = Field(
            default="submit", description="Follow-up chips and prompt buttons: submit (Open WebUI asks the user to "
                                          "confirm, then sends) or fill (put the text in the chat input).")
        live_weather: bool = Field(default=True, description="Fetch real forecasts from Open-Meteo for weather "
                                                             "components (keyless; sends the location name).")
        resolve_images: bool = Field(default=True, description="Turn image 'query' fields into real photos from "
                                                               "Wikipedia (keyless; sends the query text).")
        geocode_places: bool = Field(default=True, description="Look up coordinates for map places that have none "
                                                               "(Open-Meteo geocoding, city level).")
        map_embeds: bool = Field(default=True, description="Show an OpenStreetMap embed in map components. "
                                                           "When off, maps show the stop list and links only.")
        allow_custom_apps: bool = Field(default=True, description="Allow 'app' components: model-written HTML/JS "
                                                                  "run in a nested sandbox with no network access.")
        network_timeout: float = Field(default=6.0, ge=1.0, le=20.0, description="Seconds per external request.")
        max_spec_kb: int = Field(default=256, ge=16, le=1024, description="Largest accepted spec, in KB.")
        status_updates: bool = Field(default=True, description="Show 'Building...' status lines while rendering.")

    class UserValves(BaseModel):
        theme: Literal["default", "auto", "light", "dark"] = Field(
            default="default", description="Your theme for generated UI. default = the administrator setting.")
        units: Literal["auto", "metric", "imperial"] = Field(
            default="auto", description="Units for live weather. auto = from your browser locale.")

    def __init__(self) -> None:
        self.valves = self.Valves()
        self.citation = False

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _user_valve(user: Any, name: str, default: Any) -> Any:
        uv = (user or {}).get("valves") if isinstance(user, dict) else None
        if uv is None:
            return default
        val = uv.get(name) if isinstance(uv, dict) else getattr(uv, name, None)
        return default if val in (None, "", "default") else val

    async def _browser_info(self, event_call: Any) -> dict:
        """Ask the user's browser for the Open WebUI theme and locale (bounded; never required)."""
        if not event_call:
            return {}
        code = ("return JSON.stringify({dark: document.documentElement.classList.contains('dark'), "
                "lang: (navigator.language || '').slice(0, 16)})")
        try:
            res = await asyncio.wait_for(event_call({"type": "execute", "data": {"code": code}}), 2.5)
            if isinstance(res, dict):
                res = res.get("result") or res.get("value") or res.get("data")
            info = json.loads(res) if isinstance(res, str) else {}
            return info if isinstance(info, dict) else {}
        except Exception as exc:  # the browser may be gone or execute may be disabled
            log.debug("browser info unavailable: %s", exc)
            return {}

    async def _status(self, emitter: Any, text: str, done: bool = False) -> None:
        if emitter and self.valves.status_updates:
            try:
                await emitter({"type": "status", "data": {"description": text, "done": done}})
            except Exception as exc:
                log.debug("status emit failed: %s", exc)

    async def _resolve(self, ctx: _Ctx, units: str) -> list[str]:
        """Fill live data in place. Returns notes for the model. Never raises."""
        jobs = []
        notes: list[str] = []
        v = self.valves
        if not (ctx.weather or (ctx.images and v.resolve_images) or (ctx.places and v.geocode_places)):
            return notes
        try:
            import aiohttp
        except ImportError:
            for w in ctx.weather:
                w["error"] = "Live weather needs the aiohttp package on the server."
            return ["aiohttp is not installed; live data skipped"]
        timeout = float(v.network_timeout)
        async with aiohttp.ClientSession() as session:
            async def do_weather(c: dict) -> None:
                if not v.live_weather:
                    c["error"] = "Live weather is turned off by the administrator."
                    return
                try:
                    c["data"] = await fetch_weather(session, c["location"], int(c.get("days", 5)),
                                                    c.get("units") or units, timeout)
                    notes.append(f"weather for {c['data']['place']} fetched live from Open-Meteo at "
                                 f"{c['data']['as_of']}")
                except Exception as exc:
                    c["error"] = f"Live weather for {c['location']} is unavailable right now."
                    notes.append(f"weather for {c['location']} failed: {type(exc).__name__}: {str(exc)[:80]}")

            async def do_image(target: tuple) -> None:
                obj, key = target
                try:
                    hit = await wiki_image(session, obj["query"], timeout)
                except Exception as exc:
                    hit = None
                    log.debug("image lookup failed: %s", exc)
                if hit:
                    obj[key] = hit["src"]
                    if key == "src":
                        obj.setdefault("credit", hit["credit"])
                        obj.setdefault("link", hit["link"])
                else:
                    obj["missing"] = True

            async def do_place(p: dict) -> None:
                try:
                    g = await geocode(session, p["name"], timeout)
                except Exception as exc:
                    g = None
                    log.debug("geocode failed: %s", exc)
                if g:
                    p["lat"], p["lng"], p["approx"] = round(g["lat"], 5), round(g["lng"], 5), True

            jobs += [do_weather(c) for c in ctx.weather[:3]]
            if v.resolve_images:
                jobs += [do_image(o) for o in ctx.images[:10]]
            if v.geocode_places:
                jobs += [do_place(p) for p in ctx.places[:10]]
            try:
                await asyncio.wait_for(asyncio.gather(*jobs, return_exceptions=True), timeout + 4)
            except asyncio.TimeoutError:
                notes.append("some live lookups timed out")
        for w in ctx.weather:
            if "data" not in w and "error" not in w:
                w["error"] = f"Live weather for {w['location']} timed out."
        if any(obj.get("missing") for obj, _ in ctx.images):
            notes.append("some image queries found no photo; those images were left out")
        return notes

    async def _render(self, raw: Any, user: Any, emitter: Any, event_call: Any, extra_notes: list[str] | None = None):
        v = self.valves
        try:
            size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(json.dumps(raw, default=str))
        except (TypeError, ValueError):
            size = 0
        if size > v.max_spec_kb * 1024:
            return (f"Generative UI: the spec is {size // 1024} KB, above the {v.max_spec_kb} KB limit. "
                    "Summarize the data (fewer rows/points) and call render_ui again.")
        try:
            spec, ctx = normalize_spec(parse_spec(raw))
        except SpecError as exc:
            return f"Generative UI could not use the spec: {exc}"
        except RecursionError:
            return "Generative UI: the spec is nested too deeply. Flatten it and call render_ui again."
        title = spec.get("title") or "Interactive view"
        await self._status(emitter, f"Building “{title}”…")
        theme_pref = self._user_valve(user, "theme", v.theme)
        units_pref = self._user_valve(user, "units", "auto")
        info = await self._browser_info(event_call) if (theme_pref == "auto" or units_pref == "auto") else {}
        lang = str(info.get("lang") or "en-US")
        if units_pref == "auto":
            units_pref = "imperial" if lang.lower().endswith(("-us", "-lr", "-mm")) else "metric"
        live_notes = await self._resolve(ctx, units_pref)
        if not v.allow_custom_apps:
            _disable_apps(spec["components"])
        _prune_images(spec["components"])
        theme = theme_pref if theme_pref in ("light", "dark") else ("dark" if info.get("dark") else
                                                                     "light" if "dark" in info else "auto")
        cfg = {"theme": theme, "follow": theme_pref == "auto", "followup": v.followup_action, "lang": lang,
               "maps": v.map_embeds, "version": VERSION}
        try:
            html = build_html(spec, ctx, cfg)
        except (TypeError, ValueError) as exc:
            log.exception("render failed")
            return f"Generative UI failed to render the spec ({type(exc).__name__}). Simplify it and try again."
        await self._status(emitter, f"Showing “{title}”", done=True)
        kinds = ", ".join(dict.fromkeys(ctx.kinds))
        notes = (extra_notes or []) + ctx.notes + live_notes
        context = (
            f"The interactive UI “{title}” is rendered and visible to the user (components: {kinds}). "
            "Do not repeat its contents, do not print JSON or code for it, and do not describe it field by field. "
            "Reply with one to three short sentences that add insight, a caveat or a next step."
        )
        if notes:
            context += "\nRenderer notes: " + "; ".join(notes[:12])
        context += "\nWhat the user sees (summary):\n" + to_markdown(spec, 1500)
        return HTMLResponse(content=html, headers={"Content-Disposition": "inline"}), context

    # ---------------------------------------------------------------- tool methods
    async def render_ui(
        self,
        spec: dict,
        __user__: dict | None = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> Any:
        """
        Show the answer as a native, interactive visual (ChatGPT-style generative UI) inside the chat.
        USE IT when a visual clearly helps: comparisons, dashboards, metrics, trends or any numbers over time, tables
        to sort/search, calculators and budgets (sliders that recalculate), checklists, itineraries and schedules,
        step-by-step processes, architectures and flows, option explorers, maps, weather. DO NOT use it for simple
        facts, short definitions, casual chat or one-number answers. Never invent live readings, prices or ratings:
        use numbers from tools, the user or well-known facts, and set data_source.kind="demo" for illustrative data.

        spec = {"title","subtitle"?,"icon"?,"badge"?:{"label","tone"},"data_source"?:{"kind":"live|user|demo|static|estimate","label"?},
                "state"?:{"var":initial},"components":[...],"sources"?:[{"title","url"}],"followups"?:["2-4 short next questions"]}
        Any number/text value may be an expression string starting with "=" over state vars, e.g. "=gpus*vram".
        Functions: min max round(x,d) abs sqrt pow floor ceil clamp(x,a,b) if(c,a,b) pmt(rate,n,pv) fv pv sum avg; ops + - * / % ^ == != < > && || ? :
        "computed":{"var":"expr"} defines derived values (recomputed in order on every change).
        Text (md, labels) may embed {{expr}} or {{expr|money}}. Inputs write their "bind" var; everything recalculates instantly.
        Components (all accept "id", "visible_if":"view=='table'", "span":1-4 inside grid):
          layout: grid{columns:1-4,children} card{title,subtitle,icon,badge,children} section{title,subtitle,icon,controls,children}
                  tabs{bind?,tabs:[{label,icon?,children}]} accordion{items:[{title,md|children}]} divider
          text: heading{text} text{md} callout{tone:info|success|warning|danger,title,md} code{code,language} quote{md,by}
                kv{items:[{label,value,icon}]} tags{items:[str|{label,tone}]}
          data: metric{label,value,unit,icon,caption,format:number|integer|money|percent|compact,currency,delta,trend:[n],progress:{value,max}}
                metrics{columns,items:[metric]} progress{label,value,max,tone} gauge{label,value,min,max,unit,thresholds:[{value,tone}]}
                chart{kind:line|area|bar|stacked_bar|hbar|pie|donut|scatter,labels,series:[{name,data:[n]|expr:"per-point expr using i,x"}],unit,format,caption}
                heatmap{x_labels,y_labels,values} table{columns:[{key,label,format,align}],rows:[{...}],searchable,page_size}
                timeline{items:[{time,title,detail,icon,group}]} steps{interactive?,items:[{title,detail}]}
                flow{items:[{title,detail,icon,tone}]} diagram{nodes:[{id,label,sublabel,icon,tone,detail}],edges:[{from,to,label}],direction:LR|TB}
                status{items:[{name,state:ok|warn|down|info,detail,value}]}
                compare{items:[{title,subtitle,price,rating,badge,pros,cons,specs:{k:v},url,highlight}]}
                list{bind?,items:[{id,title,subtitle,icon,detail:{title,badge,md,highlight,tags,button}}]}  (select one to see details)
                checklist{title,bind?,items:[{label,detail,value:n|[min,max]}],format:money,currency,total_label,copy:true}
                map{places:[{name,lat,lng,time,note}]} weather{location,days} (LIVE, never write numbers) gallery{images:[{src|query,caption}]}
          inputs: slider{bind,label,min,max,step,value,format,unit} number{...} select{bind,label,options} segmented{bind,options}
                  toggle{bind,label} text_input{bind,label} date{bind,label}
                  button{label,icon,action:{type:prompt|set|reset|copy|link|download,text|var+value|url|target}} actions{items:[button]}
          app{html,height}  (last resort: self-contained HTML/JS mini app, sandboxed, no network)
        Icons: lucide names, e.g. cpu hard-drive timer thermometer server gauge chart-line database cloud brain bot code-xml
        monitor-check workflow plane hotel utensils map-pin calendar wallet shopping-cart tag star zap shield users.
        After it returns, reply with 1-3 sentences only; the user already sees the UI. Call ui_examples(topic) for a full example.

        :param spec: The UI spec object described above (a JSON object, not a string).
        :return: An interactive embed rendered inline in the chat, plus a short summary for you.
        """
        return await self._render(spec, __user__, __event_emitter__, __event_call__)

    async def show_weather(
        self,
        location: str,
        days: int = 5,
        __user__: dict | None = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> Any:
        """
        Show a LIVE weather forecast card (current conditions, 24-hour temperature chart, daily forecast) for a place.
        Use it whenever the user asks about the weather or a forecast. Data comes from Open-Meteo at call time.

        :param location: City or place name, optionally with country, e.g. "Paris, France".
        :param days: Number of forecast days, 1 to 7.
        :return: A live weather card rendered inline in the chat.
        """
        days = max(1, min(7, int(days or 5)))
        spec = {"components": [{"type": "weather", "location": location, "days": days}],
                "followups": [f"What should I wear in {location.split(',')[0]} this week?",
                              f"Best day for outdoor plans in {location.split(',')[0]}?"]}
        return await self._render(spec, __user__, __event_emitter__, __event_call__)

    async def ui_examples(self, topic: str = "dashboard") -> str:
        """
        Return a complete, valid example spec for render_ui to copy and adapt. Use it when unsure of the format.

        :param topic: one of dashboard, calculator, comparison, table, itinerary, diagram, workflow, checklist, explorer, chart, status.
        :return: A JSON spec example.
        """
        key = str(topic or "").strip().lower()
        key = {"budget": "calculator", "compare": "comparison", "products": "comparison", "trip": "itinerary",
               "travel": "itinerary", "architecture": "diagram", "flow": "workflow", "steps": "workflow",
               "shopping": "checklist", "models": "explorer", "list": "explorer", "server": "status",
               "monitoring": "dashboard", "data": "table"}.get(key, key)
        if key not in EXAMPLES:
            return "Available examples: " + ", ".join(sorted(EXAMPLES))
        return json.dumps(EXAMPLES[key], ensure_ascii=False)


def _walk(comps: list):
    for c in comps:
        yield c
        for k in ("children", "controls"):
            if c.get(k):
                yield from _walk(c[k])
        for tab in c.get("tabs") or []:
            yield from _walk(tab.get("children") or [])
        if c.get("type") == "accordion":
            for it in c.get("items") or []:
                yield from _walk(it.get("children") or [])


def _prune_images(comps: list) -> None:
    """Drop photos whose query found nothing, instead of showing empty frames."""
    for c in list(_walk(comps)):
        if c.get("type") == "gallery":
            c["images"] = [im for im in c.get("images") or [] if im.get("src")]
            for im in c["images"]:
                im.pop("query", None)
            if not c["images"]:
                c.clear()
                c.update({"type": "notice", "md": "Photos are unavailable right now."})
        elif c.get("type") == "image" and not c.get("src"):
            c.clear()
            c.update({"type": "notice", "md": "Photo unavailable right now."})
        elif c.get("type") == "compare":
            for p in c.get("items") or []:
                p.pop("missing", None)
                p.pop("query", None)
        c.pop("missing", None)


def _disable_apps(comps: list) -> None:
    for c in comps:
        if c.get("type") == "app":
            c.clear()
            c.update({"type": "notice", "tone": "warning", "md": "Custom app components are disabled on this server."})
        for k in ("children", "controls"):
            if c.get(k):
                _disable_apps(c[k])
        for tab in c.get("tabs") or []:
            _disable_apps(tab.get("children") or [])
        if c.get("type") == "accordion":
            for it in c.get("items") or []:
                _disable_apps(it.get("children") or [])


# --------------------------------------------------------------------------------------------------------------------
# Examples (served by ui_examples and used by the demo gallery and tests)
# --------------------------------------------------------------------------------------------------------------------
_HOURS = [f"{h}:00" for h in range(0, 24, 2)]
_DAYS = [f"Day {d}" for d in range(1, 13)]


def _perf_chart(metric: str, rng: str, expr: str, unit: str, fmt: str = "number") -> dict:
    return {"type": "chart", "kind": "line", "labels": _HOURS if rng == "24h" else _DAYS,
            "series": [{"name": {"load": "GPU utilization", "lat": "Latency", "req": "Requests"}[metric], "expr": expr}],
            "unit": unit, "format": fmt, "height": 220, "y_min": 0,
            "visible_if": f"metric=='{metric}' && range=='{rng}'",
            "caption": "Illustrative performance series, not live server telemetry"}


EXAMPLES: dict[str, dict] = {
    "dashboard": {
        "title": "Server monitor", "icon": "server",
        "badge": {"label": "Demo online", "tone": "success", "icon": "circle-check"},
        "data_source": {"kind": "demo", "label": "Simulated monitoring data for an LLM inference server"},
        "state": {"server": "b200", "range": "24h", "metric": "load", "rps": 80, "replicas": 2, "gpus": 4},
        "computed": {
            "cap": "replicas * (server == 'b200' ? 60 : 28)",
            "util": "clamp(rps / cap * 100, 0, 100)",
            "lat": "round(180 + 900 * pow(min(rps / cap, 0.98), 3))",
            "vram": "gpus * (server == 'b200' ? 192 : 48)",
        },
        "components": [
            {"type": "segmented", "bind": "server", "full": True, "options": [
                {"label": "gpu-node-1 (Blackwell)", "value": "b200"}, {"label": "gpu-node-2 (L40S)", "value": "l40s"}]},
            {"type": "grid", "columns": 2, "children": [
                {"type": "metric", "icon": "cpu", "label": "GPU utilization", "value": "=util", "format": "percent",
                 "decimals": 0, "progress": {"value": "=util", "max": 100}},
                {"type": "metric", "icon": "hard-drive", "label": "GPU VRAM", "value": "=vram", "unit": "GB",
                 "caption": "Total installed memory"},
                {"type": "metric", "icon": "timer", "label": "Est. latency", "value": "=lat", "unit": "ms",
                 "caption": "Simulated request latency"},
                {"type": "metric", "icon": "thermometer", "label": "Temperature", "value": "=round(38 + util * 0.3)",
                 "unit": "°C", "caption": "Example reading"}]},
            {"type": "section", "title": "Performance analytics", "controls": [
                {"type": "segmented", "bind": "range", "options": [{"label": "24 hours", "value": "24h"},
                                                                   {"label": "12 days", "value": "12d"}]}],
             "children": [
                 {"type": "segmented", "bind": "metric", "full": True, "options": [
                     {"label": "GPU load", "value": "load"}, {"label": "Latency", "value": "lat"},
                     {"label": "Requests", "value": "req"}]},
                 _perf_chart("load", "24h", "clamp(round(util * (0.55 + 0.6 * noise(i, 1))), 0, 100)", "%", "percent"),
                 _perf_chart("load", "12d", "clamp(round(util * (0.6 + 0.5 * noise(i, 7))), 0, 100)", "%", "percent"),
                 _perf_chart("lat", "24h", "round(lat * (0.75 + 0.5 * noise(i, 2)))", "ms"),
                 _perf_chart("lat", "12d", "round(lat * (0.8 + 0.4 * noise(i, 8)))", "ms"),
                 _perf_chart("req", "24h", "round(rps * (0.5 + 0.8 * noise(i, 3)))", "req/s"),
                 _perf_chart("req", "12d", "round(rps * (0.7 + 0.5 * noise(i, 9)))", "req/s")]},
            {"type": "section", "title": "Inference load simulator", "badge": {"label": "Interactive"},
             "subtitle": "Adjust traffic and replicas to see the estimated impact on GPU load and latency.",
             "children": [
                 {"type": "grid", "columns": 3, "children": [
                     {"type": "slider", "bind": "rps", "label": "Traffic", "min": 10, "max": 400, "step": 10,
                      "unit": "req/s"},
                     {"type": "slider", "bind": "replicas", "label": "Replicas", "min": 1, "max": 8},
                     {"type": "slider", "bind": "gpus", "label": "GPUs per node", "min": 1, "max": 8}]},
                 {"type": "callout", "tone": "warning", "title": "Over capacity",
                  "md": "Utilization is **{{round(util)}}%**. Add replicas or reduce traffic to keep latency under 500 ms.",
                  "visible_if": "util >= 85"},
                 {"type": "callout", "tone": "success", "title": "Healthy headroom",
                  "md": "Capacity is about **{{cap}} req/s**; you are using {{round(util)}}% of it.",
                  "visible_if": "util < 85"},
                 {"type": "actions", "items": [
                     {"label": "Reset", "icon": "rotate-ccw", "action": {"type": "reset"}},
                     {"label": "Ask how to scale", "icon": "message-circle",
                      "action": {"type": "prompt", "text": "How many replicas do I need for {{rps}} requests per second?"}}]}]}],
        "followups": ["Compare Blackwell and L40S for 70B models", "How do I export real metrics from Prometheus?"],
    },
    "calculator": {
        "title": "Monthly budget", "icon": "wallet", "subtitle": "Move the sliders; totals update instantly.",
        "data_source": {"kind": "user", "label": "Based on the amounts you entered"},
        "state": {"income": 3200, "rent": 1100, "food": 420, "transport": 90, "fun": 200, "save_pct": 15},
        "computed": {"spent": "rent + food + transport + fun", "saving": "income * save_pct / 100",
                     "left": "income - spent - saving"},
        "components": [
            {"type": "grid", "columns": 2, "children": [
                {"type": "slider", "bind": "income", "label": "Net income", "min": 500, "max": 10000, "step": 50,
                 "format": "money", "currency": "€"},
                {"type": "slider", "bind": "rent", "label": "Rent", "min": 0, "max": 4000, "step": 25,
                 "format": "money", "currency": "€"},
                {"type": "slider", "bind": "food", "label": "Groceries", "min": 0, "max": 1500, "step": 10,
                 "format": "money", "currency": "€"},
                {"type": "slider", "bind": "transport", "label": "Transport", "min": 0, "max": 600, "step": 5,
                 "format": "money", "currency": "€"},
                {"type": "slider", "bind": "fun", "label": "Leisure", "min": 0, "max": 1500, "step": 10,
                 "format": "money", "currency": "€"},
                {"type": "slider", "bind": "save_pct", "label": "Savings rate", "min": 0, "max": 50, "unit": "%"}]},
            {"type": "metrics", "columns": 3, "items": [
                {"label": "Spent", "icon": "receipt", "value": "=spent", "format": "money", "currency": "€"},
                {"label": "Saved", "icon": "piggy-bank", "value": "=saving", "format": "money", "currency": "€"},
                {"label": "Left over", "icon": "wallet", "value": "=left", "format": "money", "currency": "€",
                 "caption": "after savings"}]},
            {"type": "chart", "kind": "donut", "labels": ["Rent", "Groceries", "Transport", "Leisure", "Savings"],
             "series": [{"name": "Share", "expr": "i == 0 ? rent : i == 1 ? food : i == 2 ? transport : i == 3 ? fun : saving"}],
             "format": "money", "currency": "€", "unit": "per month"},
            {"type": "callout", "tone": "danger", "title": "Over budget",
             "md": "You are spending {{-left|money}} more than you earn after savings.", "visible_if": "left < 0"},
            {"type": "actions", "items": [{"label": "Reset", "icon": "rotate-ccw", "action": {"type": "reset"}}]}],
        "followups": ["How can I cut groceries by 15%?", "Is a 15% savings rate enough?"],
    },
    "comparison": {
        "title": "RTX 4090 vs L40S vs RTX 6000 Ada", "icon": "cpu",
        "data_source": {"kind": "static", "label": "Published manufacturer specifications"},
        "components": [
            {"type": "compare", "spec_table": True, "items": [
                {"title": "GeForce RTX 4090", "subtitle": "Consumer flagship", "icon": "monitor",
                 "badge": {"label": "Best value", "tone": "success"},
                 "pros": ["Fastest per euro", "Easy to buy"], "cons": ["No ECC", "Blower-less, 450 W"],
                 "specs": {"VRAM": "24 GB GDDR6X", "FP16 (dense)": "165 TFLOPS", "TDP": "450 W", "ECC": "No"}},
                {"title": "NVIDIA L40S", "subtitle": "Data-center inference", "icon": "server", "highlight": True,
                 "badge": {"label": "Recommended", "tone": "info"},
                 "pros": ["48 GB with ECC", "Passive server cooling"], "cons": ["Server chassis required"],
                 "specs": {"VRAM": "48 GB GDDR6", "FP16 (dense)": "362 TFLOPS", "TDP": "350 W", "ECC": "Yes"}},
                {"title": "RTX 6000 Ada", "subtitle": "Workstation", "icon": "laptop",
                 "pros": ["48 GB with ECC", "Fits a workstation"], "cons": ["Highest price"],
                 "specs": {"VRAM": "48 GB GDDR6", "FP16 (dense)": "182 TFLOPS", "TDP": "300 W", "ECC": "Yes"}}]},
            {"type": "callout", "tone": "info", "md": "For a 70B model in 4-bit you need about 40 GB of VRAM, "
                                                      "so the 48 GB cards fit it on one GPU."}],
        "followups": ["Which one for a 4-GPU server?", "Show power cost per year"],
    },
    "table": {
        "title": "Open-weight models", "icon": "database",
        "data_source": {"kind": "static", "label": "Model cards"},
        "components": [{"type": "table", "searchable": True, "page_size": 6, "columns": [
            {"key": "model", "label": "Model"}, {"key": "params", "label": "Params (B)", "format": "number"},
            {"key": "ctx", "label": "Context", "format": "compact"},
            {"key": "license", "label": "License", "format": "badge", "filterable": True},
            {"key": "arch", "label": "Architecture", "filterable": True}],
            "rows": [
                {"model": "Llama 3.3 70B", "params": 70, "ctx": 128000, "license": {"label": "Llama", "tone": "warning"}, "arch": "Dense"},
                {"model": "Qwen3 32B", "params": 32, "ctx": 128000, "license": {"label": "Apache-2.0", "tone": "success"}, "arch": "Dense"},
                {"model": "Qwen3 30B-A3B", "params": 30, "ctx": 128000, "license": {"label": "Apache-2.0", "tone": "success"}, "arch": "MoE"},
                {"model": "gpt-oss-20b", "params": 21, "ctx": 128000, "license": {"label": "Apache-2.0", "tone": "success"}, "arch": "MoE"},
                {"model": "gpt-oss-120b", "params": 117, "ctx": 128000, "license": {"label": "Apache-2.0", "tone": "success"}, "arch": "MoE"},
                {"model": "Mistral Small 3.1", "params": 24, "ctx": 128000, "license": {"label": "Apache-2.0", "tone": "success"}, "arch": "Dense"},
                {"model": "Gemma 3 27B", "params": 27, "ctx": 128000, "license": {"label": "Gemma", "tone": "warning"}, "arch": "Dense"},
                {"model": "Phi-4", "params": 14, "ctx": 16000, "license": {"label": "MIT", "tone": "success"}, "arch": "Dense"}]}],
    },
    "itinerary": {
        "title": "Two days in Paris", "icon": "plane",
        "components": [
            {"type": "tabs", "bind": "day", "tabs": [
                {"label": "Day 1", "value": "d1", "children": [
                    {"type": "timeline", "items": [
                        {"time": "09:00", "title": "Louvre Museum", "detail": "Book the 9:00 slot to avoid queues.", "icon": "landmark"},
                        {"time": "12:30", "title": "Lunch in Le Marais", "detail": "Falafel on Rue des Rosiers.", "icon": "utensils"},
                        {"time": "15:00", "title": "Notre-Dame", "detail": "Reopened in December 2024.", "icon": "landmark"},
                        {"time": "19:00", "title": "Seine walk", "detail": "Sunset from Pont des Arts.", "icon": "waves"}]},
                    {"type": "map", "mode": "walking", "places": [
                        {"name": "Louvre Museum", "lat": 48.8606, "lng": 2.3376, "time": "09:00"},
                        {"name": "Le Marais", "lat": 48.8575, "lng": 2.3592, "time": "12:30"},
                        {"name": "Notre-Dame de Paris", "lat": 48.853, "lng": 2.3499, "time": "15:00"},
                        {"name": "Pont des Arts", "lat": 48.8583, "lng": 2.3375, "time": "19:00"}]}]},
                {"label": "Day 2", "value": "d2", "children": [
                    {"type": "timeline", "items": [
                        {"time": "10:00", "title": "Eiffel Tower", "detail": "Summit tickets sell out; book ahead.", "icon": "tower-control"},
                        {"time": "13:00", "title": "Musée d'Orsay", "detail": "Impressionists on the 5th floor.", "icon": "image"},
                        {"time": "17:00", "title": "Montmartre", "detail": "Sacré-Cœur and the view over Paris.", "icon": "mountain"}]},
                    {"type": "map", "mode": "transit", "places": [
                        {"name": "Eiffel Tower", "lat": 48.8584, "lng": 2.2945, "time": "10:00"},
                        {"name": "Musée d'Orsay", "lat": 48.86, "lng": 2.3266, "time": "13:00"},
                        {"name": "Sacré-Cœur", "lat": 48.8867, "lng": 2.3431, "time": "17:00"}]}]}]},
            {"type": "checklist", "title": "Trip costs", "format": "money", "currency": "€",
             "total_label": "Estimated total, all items", "items": [
                 {"label": "Louvre ticket", "detail": "Timed entry", "value": 22},
                 {"label": "Eiffel Tower summit", "detail": "Lift ticket", "value": 36},
                 {"label": "Musée d'Orsay", "value": 16},
                 {"label": "Meals", "detail": "Two days", "value": [60, 140]}]}],
        "followups": ["Add a rainy-day plan", "Where should I stay?"],
    },
    "diagram": {
        "title": "RAG architecture", "icon": "workflow",
        "components": [
            {"type": "diagram", "direction": "LR", "nodes": [
                {"id": "docs", "label": "Documents", "sublabel": "PDF, HTML, wiki", "icon": "file-text",
                 "detail": "Source files are split into chunks of ~500 tokens with overlap."},
                {"id": "embed", "label": "Embedder", "sublabel": "text → vectors", "icon": "brain-circuit", "tone": "info",
                 "detail": "An embedding model turns each chunk and each query into a vector."},
                {"id": "vdb", "label": "Vector DB", "sublabel": "ANN index", "icon": "database",
                 "detail": "Stores vectors and returns the nearest chunks for a query."},
                {"id": "user", "label": "User question", "icon": "message-circle"},
                {"id": "ret", "label": "Retriever", "sublabel": "top-k + rerank", "icon": "search", "tone": "info",
                 "detail": "Finds the most relevant chunks and optionally reranks them."},
                {"id": "llm", "label": "LLM", "sublabel": "answer with context", "icon": "bot", "tone": "success",
                 "detail": "Generates the answer grounded in the retrieved chunks, with citations."}],
             "edges": [{"from": "docs", "to": "embed", "label": "chunks"}, {"from": "embed", "to": "vdb"},
                       {"from": "user", "to": "ret"}, {"from": "vdb", "to": "ret", "label": "top-k"},
                       {"from": "ret", "to": "llm", "label": "context"}]},
            {"type": "accordion", "items": [
                {"title": "Why rerank?", "md": "A cross-encoder reranker reorders the top 20-50 hits so the best 3-5 reach the LLM."},
                {"title": "Typical failure modes", "md": "- Chunks too large or too small\n- Missing metadata filters\n- No citation checks"}]}],
        "followups": ["How do I evaluate retrieval quality?", "Which embedding model should I use?"],
    },
    "workflow": {
        "title": "How visual generation works", "icon": "sparkles",
        "components": [
            {"type": "flow", "items": [
                {"title": "1. User sends a prompt", "detail": "“Show me an AI server dashboard”", "icon": "message-circle"},
                {"title": "2. Model selects the presentation", "detail": "Charts, cards, tables, maps, buttons and layout",
                 "icon": "brain-circuit", "tone": "info"},
                {"title": "3. Generates a structured UI spec", "detail": "Components + data + interaction logic", "icon": "code-xml"},
                {"title": "4. Tool validates and renders", "detail": "Interactive visual inside the chat", "icon": "monitor-check",
                 "tone": "success"}]},
            {"type": "steps", "title": "Deploy checklist", "interactive": True, "items": [
                {"title": "Import the Tool", "detail": "Workspace → Tools → Import, paste `generative_ui.py`."},
                {"title": "Enable it for a model", "detail": "Workspace → Models → edit → Tools → Generative UI."},
                {"title": "Turn on native function calling", "detail": "Model → Advanced params → Function Calling: Native."},
                {"title": "Ask for a dashboard", "detail": "Try “Show me a GPU dashboard”."}]}],
    },
    "checklist": {
        "title": "Apartment shopping plan", "icon": "shopping-cart",
        "components": [
            {"type": "text", "md": "Split purchases across the two sale periods."},
            {"type": "checklist", "title": "Shopping checklist", "format": "money", "currency": "€",
             "total_label": "Total target budget, all items", "copy_label": "Copy selected shopping list", "items": [
                 {"label": "Bedding and duvet covers", "detail": "November · Target budget €20–50", "value": [20, 50]},
                 {"label": "Pillows and blankets", "detail": "November · Target budget €15–35", "value": [15, 35]},
                 {"label": "Wardrobe or storage", "detail": "Compare now · Target budget €40–90", "value": [40, 90]},
                 {"label": "Washing machine or washer-dryer", "detail": "Black Friday · Target budget €150–250", "value": [150, 250]},
                 {"label": "Winter jackets and shoes", "detail": "January · Target budget €40–90", "value": [40, 90]}]},
            {"type": "text", "size": "sm", "tone": "muted", "md": "These budgets are suggested spending limits, not verified sale prices."}],
    },
    "explorer": {
        "title": "AI model explorer", "subtitle": "Example model catalog with expandable details.", "icon": "bot",
        "components": [{"type": "list", "bind": "model", "items": [
            {"id": "qwen", "title": "Qwen3.5", "subtitle": "35B-A3B", "icon": "brain-circuit",
             "detail": {"title": "Qwen3.5-35B-A3B", "badge": {"label": "Mixture of Experts"},
                        "md": "Efficient reasoning, coding and multilingual tasks.",
                        "highlight": "Best suited for: reasoning and agent workflows",
                        "tags": ["Text generation", "Multilingual support", "Tool calling"],
                        "button": {"label": "Explore this model", "icon": "arrow-right",
                                   "action": {"type": "prompt", "text": "Tell me more about Qwen3.5-35B-A3B"}}}},
            {"id": "gptoss", "title": "GPT-OSS", "subtitle": "20B", "icon": "bot",
             "detail": {"title": "gpt-oss-20b", "badge": {"label": "Open weights"},
                        "md": "Compact reasoning model that runs on a single 16 GB GPU.",
                        "highlight": "Best suited for: local assistants", "tags": ["Reasoning", "Tool calling"],
                        "button": {"label": "Explore this model", "icon": "arrow-right",
                                   "action": {"type": "prompt", "text": "Tell me more about gpt-oss-20b"}}}},
            {"id": "llama", "title": "Llama 3.3", "subtitle": "70B", "icon": "network",
             "detail": {"title": "Llama-3.3-70B-Instruct", "badge": {"label": "Dense"},
                        "md": "Strong general model with broad ecosystem support.",
                        "highlight": "Best suited for: general chat and RAG", "tags": ["Text generation", "Multilingual"],
                        "button": {"label": "Explore this model", "icon": "arrow-right",
                                   "action": {"type": "prompt", "text": "Tell me more about Llama 3.3 70B"}}}}]}],
    },
    "chart": {
        "title": "Sales analytics", "icon": "chart-column",
        "data_source": {"kind": "user", "label": "From the uploaded sales.csv"},
        "state": {"view": "chart"},
        "components": [
            {"type": "metrics", "columns": 3, "items": [
                {"label": "Revenue", "value": 482300, "format": "money", "delta": "+12.4%", "trend": [31, 34, 33, 38, 41, 40, 45]},
                {"label": "Orders", "value": 6120, "format": "integer", "delta": "+5.1%"},
                {"label": "Avg. order", "value": 78.8, "format": "money", "delta": "-2.0%", "delta_tone": "bad"}]},
            {"type": "segmented", "bind": "view", "options": [{"label": "Chart", "value": "chart", "icon": "chart-column"},
                                                              {"label": "By region", "value": "region", "icon": "chart-pie"}]},
            {"type": "chart", "kind": "bar", "title": "Monthly revenue by channel", "visible_if": "view=='chart'",
             "labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun"], "format": "money",
             "series": [{"name": "Online", "data": [42000, 45500, 47800, 51200, 55900, 60100]},
                        {"name": "Retail", "data": [31000, 29800, 30500, 28900, 30100, 29500]}]},
            {"type": "chart", "kind": "pie", "title": "Revenue share by region", "visible_if": "view=='region'",
             "labels": ["Europe", "North America", "Asia", "Other"], "series": [{"name": "Revenue", "data": [46, 31, 17, 6]}],
             "unit": "%"}],
    },
    "status": {
        "title": "Infrastructure status", "icon": "server",
        "data_source": {"kind": "demo", "label": "Example status board"},
        "components": [
            {"type": "status", "items": [
                {"name": "Open WebUI", "state": "ok", "detail": "v0.12.5 · 3 replicas", "value": "99.98%"},
                {"name": "vLLM gateway", "state": "ok", "detail": "p95 410 ms", "value": "99.9%"},
                {"name": "Embedding service", "state": "warn", "detail": "Queue backlog 1.2k", "value": "97.1%"},
                {"name": "Vector database", "state": "ok", "detail": "12.4 M vectors", "value": "100%"},
                {"name": "Backup job", "state": "down", "detail": "Failed at 02:00 · disk full", "value": "—"}]},
            {"type": "grid", "columns": 3, "children": [
                {"type": "gauge", "label": "CPU", "value": 62, "unit": "%", "thresholds": [{"value": 70, "tone": "warning"}, {"value": 90, "tone": "danger"}]},
                {"type": "gauge", "label": "Memory", "value": 81, "unit": "%", "thresholds": [{"value": 70, "tone": "warning"}, {"value": 90, "tone": "danger"}]},
                {"type": "gauge", "label": "Disk", "value": 96, "unit": "%", "thresholds": [{"value": 70, "tone": "warning"}, {"value": 90, "tone": "danger"}]}]}],
        "followups": ["Why did the backup fail?", "How do I alert on queue backlog?"],
    },
}

ICON_ALIASES = {
    "home": "house", "bar-chart": "chart-column", "bar-chart-2": "chart-column", "bar-chart-3": "chart-column",
    "line-chart": "chart-line", "pie-chart": "chart-pie", "area-chart": "chart-area", "chart": "chart-line",
    "check-circle": "circle-check", "check-circle-2": "circle-check", "alert-triangle": "triangle-alert",
    "x-circle": "circle-x", "alert-circle": "circle-alert", "help-circle": "circle-help", "dollar": "dollar-sign",
    "money": "banknote", "temperature": "thermometer", "gpu": "microchip", "chip": "cpu", "processor": "cpu",
    "ram": "memory-stick", "memory": "memory-stick", "storage": "hard-drive", "disk": "hard-drive",
    "latency": "timer", "time": "clock", "weather": "cloud-sun", "location": "map-pin", "pin": "map-pin",
    "restaurant": "utensils", "food": "utensils", "flight": "plane", "airplane": "plane", "cart": "shopping-cart",
    "code-2": "code-xml", "model": "brain", "ai": "sparkles", "chat": "message-circle", "warning": "triangle-alert",
    "error": "circle-x", "success": "circle-check", "train": "train-front", "building": "building-2",
    "trash": "trash-2", "edit": "pen-line", "pencil": "pen-line", "sliders": "sliders-horizontal",
    "dashboard": "layout-dashboard", "grid": "layout-grid", "document": "file-text", "doc": "file-text",
    "spreadsheet": "file-spreadsheet", "rain": "cloud-rain", "snow": "cloud-snow", "fog": "cloud-fog",
    "storm": "cloud-lightning", "check-square": "list-checks", "todo": "list-checks", "robot": "bot",
    "llm": "bot", "settings-2": "settings", "cog": "settings", "gear": "settings", "email": "mail",
    "hospital": "plus", "price": "tag", "budget": "wallet", "savings": "piggy-bank", "growth": "trending-up",
    "decline": "trending-down", "temperature-high": "thermometer", "tower-control": "landmark", "museum": "landmark",
    "monument": "landmark", "beach": "umbrella", "park": "tree-pine", "hike": "mountain", "walk": "footprints",
    "drive": "car", "bus-stop": "bus", "lab": "flask-conical", "science": "atom", "education": "graduation-cap",
    "university": "school", "help": "circle-help", "question": "circle-help", "idea": "lightbulb",
}

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script>document.documentElement.className = "js";</script>
<style>__CSS__
.js .g-fb{display:none}.g-fb{white-space:pre-wrap;font:13px/1.5 ui-sans-serif,system-ui,sans-serif;margin:0;padding:12px;border:1px solid #ddd;border-radius:12px}
</style>
</head>
<body>
<div id="gui-root"></div>
<pre id="gui-fallback" class="g-fb">__FALLBACK__</pre>
<script type="application/json" id="gui-data">__DATA__</script>
<script>__JS__</script>
</body>
</html>"""


# --------------------------------------------------------------------------------------------------------------------
# Icons: subset of Lucide v0.460.0 (ISC License, Copyright (c) Lucide Contributors; portions MIT, Feather).
# --------------------------------------------------------------------------------------------------------------------
ICONS: dict[str, str] = {
    "activity": "<path d=\"M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2\"/>",
    "alarm-clock": "<circle cx=\"12\" cy=\"13\" r=\"8\"/><path d=\"M12 9v4l2 2\"/><path d=\"M5 3 2 6\"/><path d=\"m22 6-3-3\"/><path d=\"M6.38 18.7 4 21\"/><path d=\"M17.64 18.67 20 21\"/>",
    "arrow-down": "<path d=\"M12 5v14\"/><path d=\"m19 12-7 7-7-7\"/>",
    "arrow-left": "<path d=\"m12 19-7-7 7-7\"/><path d=\"M19 12H5\"/>",
    "arrow-right": "<path d=\"M5 12h14\"/><path d=\"m12 5 7 7-7 7\"/>",
    "arrow-up": "<path d=\"m5 12 7-7 7 7\"/><path d=\"M12 19V5\"/>",
    "arrow-up-down": "<path d=\"m21 16-4 4-4-4\"/><path d=\"M17 20V4\"/><path d=\"m3 8 4-4 4 4\"/><path d=\"M7 4v16\"/>",
    "atom": "<circle cx=\"12\" cy=\"12\" r=\"1\"/><path d=\"M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z\"/><path d=\"M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z\"/>",
    "award": "<path d=\"m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 2.686a.5.5 0 0 1-.81-.469l1.514-8.526\"/><circle cx=\"12\" cy=\"8\" r=\"6\"/>",
    "banknote": "<rect width=\"20\" height=\"12\" x=\"2\" y=\"6\" rx=\"2\"/><circle cx=\"12\" cy=\"12\" r=\"2\"/><path d=\"M6 12h.01M18 12h.01\"/>",
    "battery": "<rect width=\"16\" height=\"10\" x=\"2\" y=\"7\" rx=\"2\" ry=\"2\"/><line x1=\"22\" x2=\"22\" y1=\"11\" y2=\"13\"/>",
    "bed": "<path d=\"M2 4v16\"/><path d=\"M2 8h18a2 2 0 0 1 2 2v10\"/><path d=\"M2 17h20\"/><path d=\"M6 8v9\"/>",
    "bell": "<path d=\"M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9\"/><path d=\"M10.3 21a1.94 1.94 0 0 0 3.4 0\"/>",
    "bike": "<circle cx=\"18.5\" cy=\"17.5\" r=\"3.5\"/><circle cx=\"5.5\" cy=\"17.5\" r=\"3.5\"/><circle cx=\"15\" cy=\"5\" r=\"1\"/><path d=\"M12 17.5V14l-3-3 4-3 2 3h2\"/>",
    "book-open": "<path d=\"M12 7v14\"/><path d=\"M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z\"/>",
    "bot": "<path d=\"M12 8V4H8\"/><rect width=\"16\" height=\"12\" x=\"4\" y=\"8\" rx=\"2\"/><path d=\"M2 14h2\"/><path d=\"M20 14h2\"/><path d=\"M15 13v2\"/><path d=\"M9 13v2\"/>",
    "box": "<path d=\"M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z\"/><path d=\"m3.3 7 8.7 5 8.7-5\"/><path d=\"M12 22V12\"/>",
    "brain": "<path d=\"M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z\"/><path d=\"M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z\"/><path d=\"M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4\"/><path d=\"M17.599 6.5a3 3 0 0 0 .399-1.375\"/><path d=\"M6.003 5.125A3 3 0 0 0 6.401 6.5\"/><path d=\"M3.477 10.896a4 4 0 0 1 .585-.396\"/><path d=\"M19.938 10.5a4 4 0 0 1 .585.396\"/><path d=\"M6 18a4 4 0 0 1-1.967-.516\"/><path d=\"M19.967 17.484A4 4 0 0 1 18 18\"/>",
    "brain-circuit": "<path d=\"M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z\"/><path d=\"M9 13a4.5 4.5 0 0 0 3-4\"/><path d=\"M6.003 5.125A3 3 0 0 0 6.401 6.5\"/><path d=\"M3.477 10.896a4 4 0 0 1 .585-.396\"/><path d=\"M6 18a4 4 0 0 1-1.967-.516\"/><path d=\"M12 13h4\"/><path d=\"M12 18h6a2 2 0 0 1 2 2v1\"/><path d=\"M12 8h8\"/><path d=\"M16 8V5a2 2 0 0 1 2-2\"/><circle cx=\"16\" cy=\"13\" r=\".5\"/><circle cx=\"18\" cy=\"3\" r=\".5\"/><circle cx=\"20\" cy=\"21\" r=\".5\"/><circle cx=\"20\" cy=\"8\" r=\".5\"/>",
    "briefcase": "<path d=\"M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16\"/><rect width=\"20\" height=\"14\" x=\"2\" y=\"6\" rx=\"2\"/>",
    "bug": "<path d=\"m8 2 1.88 1.88\"/><path d=\"M14.12 3.88 16 2\"/><path d=\"M9 7.13v-1a3.003 3.003 0 1 1 6 0v1\"/><path d=\"M12 20c-3.3 0-6-2.7-6-6v-3a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v3c0 3.3-2.7 6-6 6\"/><path d=\"M12 20v-9\"/><path d=\"M6.53 9C4.6 8.8 3 7.1 3 5\"/><path d=\"M6 13H2\"/><path d=\"M3 21c0-2.1 1.7-3.9 3.8-4\"/><path d=\"M20.97 5c0 2.1-1.6 3.8-3.5 4\"/><path d=\"M22 13h-4\"/><path d=\"M17.2 17c2.1.1 3.8 1.9 3.8 4\"/>",
    "building-2": "<path d=\"M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z\"/><path d=\"M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2\"/><path d=\"M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2\"/><path d=\"M10 6h4\"/><path d=\"M10 10h4\"/><path d=\"M10 14h4\"/><path d=\"M10 18h4\"/>",
    "bus": "<path d=\"M8 6v6\"/><path d=\"M15 6v6\"/><path d=\"M2 12h19.6\"/><path d=\"M18 18h3s.5-1.7.8-2.8c.1-.4.2-.8.2-1.2 0-.4-.1-.8-.2-1.2l-1.4-5C20.1 6.8 19.1 6 18 6H4a2 2 0 0 0-2 2v10h3\"/><circle cx=\"7\" cy=\"18\" r=\"2\"/><path d=\"M9 18h5\"/><circle cx=\"16\" cy=\"18\" r=\"2\"/>",
    "cake": "<path d=\"M20 21v-8a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8\"/><path d=\"M4 16s.5-1 2-1 2.5 2 4 2 2.5-2 4-2 2.5 2 4 2 2-1 2-1\"/><path d=\"M2 21h20\"/><path d=\"M7 8v3\"/><path d=\"M12 8v3\"/><path d=\"M17 8v3\"/><path d=\"M7 4h.01\"/><path d=\"M12 4h.01\"/><path d=\"M17 4h.01\"/>",
    "calculator": "<rect width=\"16\" height=\"20\" x=\"4\" y=\"2\" rx=\"2\"/><line x1=\"8\" x2=\"16\" y1=\"6\" y2=\"6\"/><line x1=\"16\" x2=\"16\" y1=\"14\" y2=\"18\"/><path d=\"M16 10h.01\"/><path d=\"M12 10h.01\"/><path d=\"M8 10h.01\"/><path d=\"M12 14h.01\"/><path d=\"M8 14h.01\"/><path d=\"M12 18h.01\"/><path d=\"M8 18h.01\"/>",
    "calendar": "<path d=\"M8 2v4\"/><path d=\"M16 2v4\"/><rect width=\"18\" height=\"18\" x=\"3\" y=\"4\" rx=\"2\"/><path d=\"M3 10h18\"/>",
    "calendar-days": "<path d=\"M8 2v4\"/><path d=\"M16 2v4\"/><rect width=\"18\" height=\"18\" x=\"3\" y=\"4\" rx=\"2\"/><path d=\"M3 10h18\"/><path d=\"M8 14h.01\"/><path d=\"M12 14h.01\"/><path d=\"M16 14h.01\"/><path d=\"M8 18h.01\"/><path d=\"M12 18h.01\"/><path d=\"M16 18h.01\"/>",
    "camera": "<path d=\"M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z\"/><circle cx=\"12\" cy=\"13\" r=\"3\"/>",
    "car": "<path d=\"M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9C18.7 10.6 16 10 16 10s-1.3-1.4-2.2-2.3c-.5-.4-1.1-.7-1.8-.7H5c-.6 0-1.1.4-1.4.9l-1.4 2.9A3.7 3.7 0 0 0 2 12v4c0 .6.4 1 1 1h2\"/><circle cx=\"7\" cy=\"17\" r=\"2\"/><path d=\"M9 17h6\"/><circle cx=\"17\" cy=\"17\" r=\"2\"/>",
    "chart-area": "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\"/><path d=\"M7 11.207a.5.5 0 0 1 .146-.353l2-2a.5.5 0 0 1 .708 0l3.292 3.292a.5.5 0 0 0 .708 0l4.292-4.292a.5.5 0 0 1 .854.353V16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1z\"/>",
    "chart-column": "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\"/><path d=\"M18 17V9\"/><path d=\"M13 17V5\"/><path d=\"M8 17v-3\"/>",
    "chart-line": "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\"/><path d=\"m19 9-5 5-4-4-3 3\"/>",
    "chart-pie": "<path d=\"M21 12c.552 0 1.005-.449.95-.998a10 10 0 0 0-8.953-8.951c-.55-.055-.998.398-.998.95v8a1 1 0 0 0 1 1z\"/><path d=\"M21.21 15.89A10 10 0 1 1 8 2.83\"/>",
    "chart-bar": "<path d=\"M3 3v16a2 2 0 0 0 2 2h16\"/><path d=\"M7 16h8\"/><path d=\"M7 11h12\"/><path d=\"M7 6h3\"/>",
    "check": "<path d=\"M20 6 9 17l-5-5\"/>",
    "check-check": "<path d=\"M18 6 7 17l-5-5\"/><path d=\"m22 10-7.5 7.5L13 16\"/>",
    "chevron-down": "<path d=\"m6 9 6 6 6-6\"/>",
    "chevron-left": "<path d=\"m15 18-6-6 6-6\"/>",
    "chevron-right": "<path d=\"m9 18 6-6-6-6\"/>",
    "chevron-up": "<path d=\"m18 15-6-6-6 6\"/>",
    "circle": "<circle cx=\"12\" cy=\"12\" r=\"10\"/>",
    "circle-alert": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><line x1=\"12\" x2=\"12\" y1=\"8\" y2=\"12\"/><line x1=\"12\" x2=\"12.01\" y1=\"16\" y2=\"16\"/>",
    "circle-check": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m9 12 2 2 4-4\"/>",
    "circle-check-big": "<path d=\"M21.801 10A10 10 0 1 1 17 3.335\"/><path d=\"m9 11 3 3L22 4\"/>",
    "circle-dot": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><circle cx=\"12\" cy=\"12\" r=\"1\"/>",
    "circle-help": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3\"/><path d=\"M12 17h.01\"/>",
    "circle-x": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"m15 9-6 6\"/><path d=\"m9 9 6 6\"/>",
    "clipboard-list": "<rect width=\"8\" height=\"4\" x=\"8\" y=\"2\" rx=\"1\" ry=\"1\"/><path d=\"M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2\"/><path d=\"M12 11h4\"/><path d=\"M12 16h4\"/><path d=\"M8 11h.01\"/><path d=\"M8 16h.01\"/>",
    "clock": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><polyline points=\"12 6 12 12 16 14\"/>",
    "cloud": "<path d=\"M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z\"/>",
    "cloud-drizzle": "<path d=\"M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242\"/><path d=\"M8 19v1\"/><path d=\"M8 14v1\"/><path d=\"M16 19v1\"/><path d=\"M16 14v1\"/><path d=\"M12 21v1\"/><path d=\"M12 16v1\"/>",
    "cloud-fog": "<path d=\"M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242\"/><path d=\"M16 17H7\"/><path d=\"M17 21H9\"/>",
    "cloud-lightning": "<path d=\"M6 16.326A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 .5 8.973\"/><path d=\"m13 12-3 5h4l-3 5\"/>",
    "cloud-rain": "<path d=\"M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242\"/><path d=\"M16 14v6\"/><path d=\"M8 14v6\"/><path d=\"M12 16v6\"/>",
    "cloud-snow": "<path d=\"M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242\"/><path d=\"M8 15h.01\"/><path d=\"M8 19h.01\"/><path d=\"M12 17h.01\"/><path d=\"M12 21h.01\"/><path d=\"M16 15h.01\"/><path d=\"M16 19h.01\"/>",
    "cloud-sun": "<path d=\"M12 2v2\"/><path d=\"m4.93 4.93 1.41 1.41\"/><path d=\"M20 12h2\"/><path d=\"m19.07 4.93-1.41 1.41\"/><path d=\"M15.947 12.65a4 4 0 0 0-5.925-4.128\"/><path d=\"M13 22H7a5 5 0 1 1 4.9-6H13a3 3 0 0 1 0 6Z\"/>",
    "code": "<polyline points=\"16 18 22 12 16 6\"/><polyline points=\"8 6 2 12 8 18\"/>",
    "code-xml": "<path d=\"m18 16 4-4-4-4\"/><path d=\"m6 8-4 4 4 4\"/><path d=\"m14.5 4-5 16\"/>",
    "coffee": "<path d=\"M10 2v2\"/><path d=\"M14 2v2\"/><path d=\"M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1\"/><path d=\"M6 2v2\"/>",
    "coins": "<circle cx=\"8\" cy=\"8\" r=\"6\"/><path d=\"M18.09 10.37A6 6 0 1 1 10.34 18\"/><path d=\"M7 6h1v4\"/><path d=\"m16.71 13.88.7.71-2.82 2.82\"/>",
    "compass": "<path d=\"m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z\"/><circle cx=\"12\" cy=\"12\" r=\"10\"/>",
    "copy": "<rect width=\"14\" height=\"14\" x=\"8\" y=\"8\" rx=\"2\" ry=\"2\"/><path d=\"M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2\"/>",
    "cpu": "<rect width=\"16\" height=\"16\" x=\"4\" y=\"4\" rx=\"2\"/><rect width=\"6\" height=\"6\" x=\"9\" y=\"9\" rx=\"1\"/><path d=\"M15 2v2\"/><path d=\"M15 20v2\"/><path d=\"M2 15h2\"/><path d=\"M2 9h2\"/><path d=\"M20 15h2\"/><path d=\"M20 9h2\"/><path d=\"M9 2v2\"/><path d=\"M9 20v2\"/>",
    "credit-card": "<rect width=\"20\" height=\"14\" x=\"2\" y=\"5\" rx=\"2\"/><line x1=\"2\" x2=\"22\" y1=\"10\" y2=\"10\"/>",
    "database": "<ellipse cx=\"12\" cy=\"5\" rx=\"9\" ry=\"3\"/><path d=\"M3 5V19A9 3 0 0 0 21 19V5\"/><path d=\"M3 12A9 3 0 0 0 21 12\"/>",
    "dollar-sign": "<line x1=\"12\" x2=\"12\" y1=\"2\" y2=\"22\"/><path d=\"M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6\"/>",
    "download": "<path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\"/><polyline points=\"7 10 12 15 17 10\"/><line x1=\"12\" x2=\"12\" y1=\"15\" y2=\"3\"/>",
    "droplets": "<path d=\"M7 16.3c2.2 0 4-1.83 4-4.05 0-1.16-.57-2.26-1.71-3.19S7.29 6.75 7 5.3c-.29 1.45-1.14 2.84-2.29 3.76S3 11.1 3 12.25c0 2.22 1.8 4.05 4 4.05z\"/><path d=\"M12.56 6.6A10.97 10.97 0 0 0 14 3.02c.5 2.5 2 4.9 4 6.5s3 3.5 3 5.5a6.98 6.98 0 0 1-11.91 4.97\"/>",
    "dumbbell": "<path d=\"M14.4 14.4 9.6 9.6\"/><path d=\"M18.657 21.485a2 2 0 1 1-2.829-2.828l-1.767 1.768a2 2 0 1 1-2.829-2.829l6.364-6.364a2 2 0 1 1 2.829 2.829l-1.768 1.767a2 2 0 1 1 2.828 2.829z\"/><path d=\"m21.5 21.5-1.4-1.4\"/><path d=\"M3.9 3.9 2.5 2.5\"/><path d=\"M6.404 12.768a2 2 0 1 1-2.829-2.829l1.768-1.767a2 2 0 1 1-2.828-2.829l2.828-2.828a2 2 0 1 1 2.829 2.828l1.767-1.768a2 2 0 1 1 2.829 2.829z\"/>",
    "euro": "<path d=\"M4 10h12\"/><path d=\"M4 14h9\"/><path d=\"M19 6a7.7 7.7 0 0 0-5.2-2A7.9 7.9 0 0 0 6 12c0 4.4 3.5 8 7.8 8 2 0 3.8-.8 5.2-2\"/>",
    "external-link": "<path d=\"M15 3h6v6\"/><path d=\"M10 14 21 3\"/><path d=\"M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6\"/>",
    "eye": "<path d=\"M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
    "file": "<path d=\"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z\"/><path d=\"M14 2v4a2 2 0 0 0 2 2h4\"/>",
    "file-spreadsheet": "<path d=\"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z\"/><path d=\"M14 2v4a2 2 0 0 0 2 2h4\"/><path d=\"M8 13h2\"/><path d=\"M14 13h2\"/><path d=\"M8 17h2\"/><path d=\"M14 17h2\"/>",
    "file-text": "<path d=\"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z\"/><path d=\"M14 2v4a2 2 0 0 0 2 2h4\"/><path d=\"M10 9H8\"/><path d=\"M16 13H8\"/><path d=\"M16 17H8\"/>",
    "film": "<rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\"/><path d=\"M7 3v18\"/><path d=\"M3 7.5h4\"/><path d=\"M3 12h18\"/><path d=\"M3 16.5h4\"/><path d=\"M17 3v18\"/><path d=\"M17 7.5h4\"/><path d=\"M17 16.5h4\"/>",
    "filter": "<polygon points=\"22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3\"/>",
    "flag": "<path d=\"M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z\"/><line x1=\"4\" x2=\"4\" y1=\"22\" y2=\"15\"/>",
    "flame": "<path d=\"M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z\"/>",
    "flask-conical": "<path d=\"M10 2v7.527a2 2 0 0 1-.211.896L4.72 20.55a1 1 0 0 0 .9 1.45h12.76a1 1 0 0 0 .9-1.45l-5.069-10.127A2 2 0 0 1 14 9.527V2\"/><path d=\"M8.5 2h7\"/><path d=\"M7 16h10\"/>",
    "folder": "<path d=\"M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z\"/>",
    "gauge": "<path d=\"m12 14 4-4\"/><path d=\"M3.34 19a10 10 0 1 1 17.32 0\"/>",
    "gem": "<path d=\"M6 3h12l4 6-10 13L2 9Z\"/><path d=\"M11 3 8 9l4 13 4-13-3-6\"/><path d=\"M2 9h20\"/>",
    "gift": "<rect x=\"3\" y=\"8\" width=\"18\" height=\"4\" rx=\"1\"/><path d=\"M12 8v13\"/><path d=\"M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7\"/><path d=\"M7.5 8a2.5 2.5 0 0 1 0-5A4.8 8 0 0 1 12 8a4.8 8 0 0 1 4.5-5 2.5 2.5 0 0 1 0 5\"/>",
    "git-branch": "<line x1=\"6\" x2=\"6\" y1=\"3\" y2=\"15\"/><circle cx=\"18\" cy=\"6\" r=\"3\"/><circle cx=\"6\" cy=\"18\" r=\"3\"/><path d=\"M18 9a9 9 0 0 1-9 9\"/>",
    "git-fork": "<circle cx=\"12\" cy=\"18\" r=\"3\"/><circle cx=\"6\" cy=\"6\" r=\"3\"/><circle cx=\"18\" cy=\"6\" r=\"3\"/><path d=\"M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9\"/><path d=\"M12 12v3\"/>",
    "globe": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20\"/><path d=\"M2 12h20\"/>",
    "graduation-cap": "<path d=\"M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832l8.57 3.908a2 2 0 0 0 1.66 0z\"/><path d=\"M22 10v6\"/><path d=\"M6 12.5V16a6 3 0 0 0 12 0v-3.5\"/>",
    "hard-drive": "<line x1=\"22\" x2=\"2\" y1=\"12\" y2=\"12\"/><path d=\"M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z\"/><line x1=\"6\" x2=\"6.01\" y1=\"16\" y2=\"16\"/><line x1=\"10\" x2=\"10.01\" y1=\"16\" y2=\"16\"/>",
    "hash": "<line x1=\"4\" x2=\"20\" y1=\"9\" y2=\"9\"/><line x1=\"4\" x2=\"20\" y1=\"15\" y2=\"15\"/><line x1=\"10\" x2=\"8\" y1=\"3\" y2=\"21\"/><line x1=\"16\" x2=\"14\" y1=\"3\" y2=\"21\"/>",
    "heart": "<path d=\"M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z\"/>",
    "house": "<path d=\"M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8\"/><path d=\"M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z\"/>",
    "hotel": "<path d=\"M10 22v-6.57\"/><path d=\"M12 11h.01\"/><path d=\"M12 7h.01\"/><path d=\"M14 15.43V22\"/><path d=\"M15 16a5 5 0 0 0-6 0\"/><path d=\"M16 11h.01\"/><path d=\"M16 7h.01\"/><path d=\"M8 11h.01\"/><path d=\"M8 7h.01\"/><rect x=\"4\" y=\"2\" width=\"16\" height=\"20\" rx=\"2\"/>",
    "image": "<rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\" ry=\"2\"/><circle cx=\"9\" cy=\"9\" r=\"2\"/><path d=\"m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21\"/>",
    "info": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><path d=\"M12 16v-4\"/><path d=\"M12 8h.01\"/>",
    "key": "<path d=\"m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4\"/><path d=\"m21 2-9.6 9.6\"/><circle cx=\"7.5\" cy=\"15.5\" r=\"5.5\"/>",
    "landmark": "<line x1=\"3\" x2=\"21\" y1=\"22\" y2=\"22\"/><line x1=\"6\" x2=\"6\" y1=\"18\" y2=\"11\"/><line x1=\"10\" x2=\"10\" y1=\"18\" y2=\"11\"/><line x1=\"14\" x2=\"14\" y1=\"18\" y2=\"11\"/><line x1=\"18\" x2=\"18\" y1=\"18\" y2=\"11\"/><polygon points=\"12 2 20 7 4 7\"/>",
    "languages": "<path d=\"m5 8 6 6\"/><path d=\"m4 14 6-6 2-3\"/><path d=\"M2 5h12\"/><path d=\"M7 2h1\"/><path d=\"m22 22-5-10-5 10\"/><path d=\"M14 18h6\"/>",
    "laptop": "<path d=\"M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16\"/>",
    "layers": "<path d=\"m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z\"/><path d=\"m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65\"/><path d=\"m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65\"/>",
    "layout-dashboard": "<rect width=\"7\" height=\"9\" x=\"3\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"5\" x=\"14\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"9\" x=\"14\" y=\"12\" rx=\"1\"/><rect width=\"7\" height=\"5\" x=\"3\" y=\"16\" rx=\"1\"/>",
    "layout-grid": "<rect width=\"7\" height=\"7\" x=\"3\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"7\" x=\"14\" y=\"3\" rx=\"1\"/><rect width=\"7\" height=\"7\" x=\"14\" y=\"14\" rx=\"1\"/><rect width=\"7\" height=\"7\" x=\"3\" y=\"14\" rx=\"1\"/>",
    "leaf": "<path d=\"M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z\"/><path d=\"M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12\"/>",
    "lightbulb": "<path d=\"M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5\"/><path d=\"M9 18h6\"/><path d=\"M10 22h4\"/>",
    "link": "<path d=\"M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71\"/><path d=\"M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71\"/>",
    "list": "<path d=\"M3 12h.01\"/><path d=\"M3 18h.01\"/><path d=\"M3 6h.01\"/><path d=\"M8 12h13\"/><path d=\"M8 18h13\"/><path d=\"M8 6h13\"/>",
    "list-checks": "<path d=\"m3 17 2 2 4-4\"/><path d=\"m3 7 2 2 4-4\"/><path d=\"M13 6h8\"/><path d=\"M13 12h8\"/><path d=\"M13 18h8\"/>",
    "list-ordered": "<path d=\"M10 12h11\"/><path d=\"M10 18h11\"/><path d=\"M10 6h11\"/><path d=\"M4 10h2\"/><path d=\"M4 6h1v4\"/><path d=\"M6 18H4c0-1 2-2 2-3s-1-1.5-2-1\"/>",
    "lock": "<rect width=\"18\" height=\"11\" x=\"3\" y=\"11\" rx=\"2\" ry=\"2\"/><path d=\"M7 11V7a5 5 0 0 1 10 0v4\"/>",
    "mail": "<rect width=\"20\" height=\"16\" x=\"2\" y=\"4\" rx=\"2\"/><path d=\"m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7\"/>",
    "map": "<path d=\"M14.106 5.553a2 2 0 0 0 1.788 0l3.659-1.83A1 1 0 0 1 21 4.619v12.764a1 1 0 0 1-.553.894l-4.553 2.277a2 2 0 0 1-1.788 0l-4.212-2.106a2 2 0 0 0-1.788 0l-3.659 1.83A1 1 0 0 1 3 19.381V6.618a1 1 0 0 1 .553-.894l4.553-2.277a2 2 0 0 1 1.788 0z\"/><path d=\"M15 5.764v15\"/><path d=\"M9 3.236v15\"/>",
    "map-pin": "<path d=\"M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0\"/><circle cx=\"12\" cy=\"10\" r=\"3\"/>",
    "memory-stick": "<path d=\"M6 19v-3\"/><path d=\"M10 19v-3\"/><path d=\"M14 19v-3\"/><path d=\"M18 19v-3\"/><path d=\"M8 11V9\"/><path d=\"M16 11V9\"/><path d=\"M12 11V9\"/><path d=\"M2 15h20\"/><path d=\"M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v1.1a2 2 0 0 0 0 3.837V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-5.1a2 2 0 0 0 0-3.837Z\"/>",
    "message-circle": "<path d=\"M7.9 20A9 9 0 1 0 4 16.1L2 22Z\"/>",
    "message-square": "<path d=\"M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z\"/>",
    "microchip": "<path d=\"M18 12h2\"/><path d=\"M18 16h2\"/><path d=\"M18 20h2\"/><path d=\"M18 4h2\"/><path d=\"M18 8h2\"/><path d=\"M4 12h2\"/><path d=\"M4 16h2\"/><path d=\"M4 20h2\"/><path d=\"M4 4h2\"/><path d=\"M4 8h2\"/><path d=\"M8 2a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2h-1.5c-.276 0-.494.227-.562.495a2 2 0 0 1-3.876 0C9.994 2.227 9.776 2 9.5 2z\"/>",
    "monitor": "<rect width=\"20\" height=\"14\" x=\"2\" y=\"3\" rx=\"2\"/><line x1=\"8\" x2=\"16\" y1=\"21\" y2=\"21\"/><line x1=\"12\" x2=\"12\" y1=\"17\" y2=\"21\"/>",
    "monitor-check": "<path d=\"m9 10 2 2 4-4\"/><rect width=\"20\" height=\"14\" x=\"2\" y=\"3\" rx=\"2\"/><path d=\"M12 17v4\"/><path d=\"M8 21h8\"/>",
    "moon": "<path d=\"M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z\"/>",
    "mountain": "<path d=\"m8 3 4 8 5-5 5 15H2L8 3z\"/>",
    "music": "<path d=\"M9 18V5l12-2v13\"/><circle cx=\"6\" cy=\"18\" r=\"3\"/><circle cx=\"18\" cy=\"16\" r=\"3\"/>",
    "network": "<rect x=\"16\" y=\"16\" width=\"6\" height=\"6\" rx=\"1\"/><rect x=\"2\" y=\"16\" width=\"6\" height=\"6\" rx=\"1\"/><rect x=\"9\" y=\"2\" width=\"6\" height=\"6\" rx=\"1\"/><path d=\"M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3\"/><path d=\"M12 12V8\"/>",
    "newspaper": "<path d=\"M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2\"/><path d=\"M18 14h-8\"/><path d=\"M15 18h-5\"/><path d=\"M10 6h8v4h-8V6Z\"/>",
    "package": "<path d=\"M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z\"/><path d=\"M12 22V12\"/><path d=\"m3.3 7 7.703 4.734a2 2 0 0 0 1.994 0L20.7 7\"/><path d=\"m7.5 4.27 9 5.15\"/>",
    "palette": "<circle cx=\"13.5\" cy=\"6.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"17.5\" cy=\"10.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"8.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\"/><circle cx=\"6.5\" cy=\"12.5\" r=\".5\" fill=\"currentColor\"/><path d=\"M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z\"/>",
    "paperclip": "<path d=\"m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48\"/>",
    "pause": "<rect x=\"14\" y=\"4\" width=\"4\" height=\"16\" rx=\"1\"/><rect x=\"6\" y=\"4\" width=\"4\" height=\"16\" rx=\"1\"/>",
    "pen-line": "<path d=\"M12 20h9\"/><path d=\"M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62l.838-2.872a2 2 0 0 1 .506-.854z\"/>",
    "percent": "<line x1=\"19\" x2=\"5\" y1=\"5\" y2=\"19\"/><circle cx=\"6.5\" cy=\"6.5\" r=\"2.5\"/><circle cx=\"17.5\" cy=\"17.5\" r=\"2.5\"/>",
    "phone": "<path d=\"M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z\"/>",
    "piggy-bank": "<path d=\"M19 5c-1.5 0-2.8 1.4-3 2-3.5-1.5-11-.3-11 5 0 1.8 0 3 2 4.5V20h4v-2h3v2h4v-4c1-.5 1.7-1 2-2h2v-4h-2c0-1-.5-1.5-1-2V5z\"/><path d=\"M2 9v1c0 1.1.9 2 2 2h1\"/><path d=\"M16 11h.01\"/>",
    "pill": "<path d=\"m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z\"/><path d=\"m8.5 8.5 7 7\"/>",
    "plane": "<path d=\"M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z\"/>",
    "play": "<polygon points=\"6 3 20 12 6 21 6 3\"/>",
    "plug": "<path d=\"M12 22v-5\"/><path d=\"M9 8V2\"/><path d=\"M15 8V2\"/><path d=\"M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z\"/>",
    "plus": "<path d=\"M5 12h14\"/><path d=\"M12 5v14\"/>",
    "minus": "<path d=\"M5 12h14\"/>",
    "pound-sterling": "<path d=\"M18 7c0-5.333-8-5.333-8 0\"/><path d=\"M10 7v14\"/><path d=\"M6 21h12\"/><path d=\"M6 13h10\"/>",
    "power": "<path d=\"M12 2v10\"/><path d=\"M18.4 6.6a9 9 0 1 1-12.77.04\"/>",
    "puzzle": "<path d=\"M15.39 4.39a1 1 0 0 0 1.68-.474 2.5 2.5 0 1 1 3.014 3.015 1 1 0 0 0-.474 1.68l1.683 1.682a2.414 2.414 0 0 1 0 3.414L19.61 15.39a1 1 0 0 1-1.68-.474 2.5 2.5 0 1 0-3.014 3.015 1 1 0 0 1 .474 1.68l-1.683 1.682a2.414 2.414 0 0 1-3.414 0L8.61 19.61a1 1 0 0 0-1.68.474 2.5 2.5 0 1 1-3.014-3.015 1 1 0 0 0 .474-1.68l-1.683-1.682a2.414 2.414 0 0 1 0-3.414L4.39 8.61a1 1 0 0 1 1.68.474 2.5 2.5 0 1 0 3.014-3.015 1 1 0 0 1-.474-1.68l1.683-1.682a2.414 2.414 0 0 1 3.414 0z\"/>",
    "receipt": "<path d=\"M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z\"/><path d=\"M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8\"/><path d=\"M12 17.5v-11\"/>",
    "refresh-cw": "<path d=\"M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8\"/><path d=\"M21 3v5h-5\"/><path d=\"M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16\"/><path d=\"M8 16H3v5\"/>",
    "rocket": "<path d=\"M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z\"/><path d=\"m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z\"/><path d=\"M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0\"/><path d=\"M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5\"/>",
    "rotate-ccw": "<path d=\"M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8\"/><path d=\"M3 3v5h5\"/>",
    "route": "<circle cx=\"6\" cy=\"19\" r=\"3\"/><path d=\"M9 19h8.5a3.5 3.5 0 0 0 0-7h-11a3.5 3.5 0 0 1 0-7H15\"/><circle cx=\"18\" cy=\"5\" r=\"3\"/>",
    "ruler": "<path d=\"M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.41 2.41 0 0 1 0-3.4l2.6-2.6a2.41 2.41 0 0 1 3.4 0Z\"/><path d=\"m14.5 12.5 2-2\"/><path d=\"m11.5 9.5 2-2\"/><path d=\"m8.5 6.5 2-2\"/><path d=\"m17.5 15.5 2-2\"/>",
    "scale": "<path d=\"m16 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z\"/><path d=\"m2 16 3-8 3 8c-.87.65-1.92 1-3 1s-2.13-.35-3-1Z\"/><path d=\"M7 21h10\"/><path d=\"M12 3v18\"/><path d=\"M3 7h2c2 0 5-1 7-2 2 1 5 2 7 2h2\"/>",
    "school": "<path d=\"M14 22v-4a2 2 0 1 0-4 0v4\"/><path d=\"m18 10 3.447 1.724a1 1 0 0 1 .553.894V20a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7.382a1 1 0 0 1 .553-.894L6 10\"/><path d=\"M18 5v17\"/><path d=\"m4 6 7.106-3.553a2 2 0 0 1 1.788 0L20 6\"/><path d=\"M6 5v17\"/><circle cx=\"12\" cy=\"9\" r=\"2\"/>",
    "search": "<circle cx=\"11\" cy=\"11\" r=\"8\"/><path d=\"m21 21-4.3-4.3\"/>",
    "send": "<path d=\"M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z\"/><path d=\"m21.854 2.147-10.94 10.939\"/>",
    "server": "<rect width=\"20\" height=\"8\" x=\"2\" y=\"2\" rx=\"2\" ry=\"2\"/><rect width=\"20\" height=\"8\" x=\"2\" y=\"14\" rx=\"2\" ry=\"2\"/><line x1=\"6\" x2=\"6.01\" y1=\"6\" y2=\"6\"/><line x1=\"6\" x2=\"6.01\" y1=\"18\" y2=\"18\"/>",
    "settings": "<path d=\"M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>",
    "share": "<path d=\"M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8\"/><polyline points=\"16 6 12 2 8 6\"/><line x1=\"12\" x2=\"12\" y1=\"2\" y2=\"15\"/>",
    "shield": "<path d=\"M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z\"/>",
    "shield-check": "<path d=\"M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z\"/><path d=\"m9 12 2 2 4-4\"/>",
    "ship": "<path d=\"M2 21c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1 .6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1\"/><path d=\"M19.38 20A11.6 11.6 0 0 0 21 14l-9-4-9 4c0 2.9.94 5.34 2.81 7.76\"/><path d=\"M19 13V7a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v6\"/><path d=\"M12 10v4\"/><path d=\"M12 2v3\"/>",
    "shirt": "<path d=\"M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z\"/>",
    "shopping-bag": "<path d=\"M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z\"/><path d=\"M3 6h18\"/><path d=\"M16 10a4 4 0 0 1-8 0\"/>",
    "shopping-cart": "<circle cx=\"8\" cy=\"21\" r=\"1\"/><circle cx=\"19\" cy=\"21\" r=\"1\"/><path d=\"M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12\"/>",
    "sigma": "<path d=\"M18 7V5a1 1 0 0 0-1-1H6.5a.5.5 0 0 0-.4.8l4.5 6a2 2 0 0 1 0 2.4l-4.5 6a.5.5 0 0 0 .4.8H17a1 1 0 0 0 1-1v-2\"/>",
    "sliders-horizontal": "<line x1=\"21\" x2=\"14\" y1=\"4\" y2=\"4\"/><line x1=\"10\" x2=\"3\" y1=\"4\" y2=\"4\"/><line x1=\"21\" x2=\"12\" y1=\"12\" y2=\"12\"/><line x1=\"8\" x2=\"3\" y1=\"12\" y2=\"12\"/><line x1=\"21\" x2=\"16\" y1=\"20\" y2=\"20\"/><line x1=\"12\" x2=\"3\" y1=\"20\" y2=\"20\"/><line x1=\"14\" x2=\"14\" y1=\"2\" y2=\"6\"/><line x1=\"8\" x2=\"8\" y1=\"10\" y2=\"14\"/><line x1=\"16\" x2=\"16\" y1=\"18\" y2=\"22\"/>",
    "smartphone": "<rect width=\"14\" height=\"20\" x=\"5\" y=\"2\" rx=\"2\" ry=\"2\"/><path d=\"M12 18h.01\"/>",
    "snowflake": "<line x1=\"2\" x2=\"22\" y1=\"12\" y2=\"12\"/><line x1=\"12\" x2=\"12\" y1=\"2\" y2=\"22\"/><path d=\"m20 16-4-4 4-4\"/><path d=\"m4 8 4 4-4 4\"/><path d=\"m16 4-4 4-4-4\"/><path d=\"m8 20 4-4 4 4\"/>",
    "sofa": "<path d=\"M20 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v3\"/><path d=\"M2 16a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-5a2 2 0 0 0-4 0v1.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5V11a2 2 0 0 0-4 0z\"/><path d=\"M4 18v2\"/><path d=\"M20 18v2\"/><path d=\"M12 4v9\"/>",
    "sparkles": "<path d=\"M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z\"/><path d=\"M20 3v4\"/><path d=\"M22 5h-4\"/><path d=\"M4 17v2\"/><path d=\"M5 18H3\"/>",
    "square": "<rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\"/>",
    "star": "<path d=\"M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z\"/>",
    "store": "<path d=\"m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7\"/><path d=\"M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8\"/><path d=\"M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4\"/><path d=\"M2 7h20\"/><path d=\"M22 7v3a2 2 0 0 1-2 2a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 16 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 12 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 8 12a2.7 2.7 0 0 1-1.59-.63.7.7 0 0 0-.82 0A2.7 2.7 0 0 1 4 12a2 2 0 0 1-2-2V7\"/>",
    "sun": "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2\"/><path d=\"M12 20v2\"/><path d=\"m4.93 4.93 1.41 1.41\"/><path d=\"m17.66 17.66 1.41 1.41\"/><path d=\"M2 12h2\"/><path d=\"M20 12h2\"/><path d=\"m6.34 17.66-1.41 1.41\"/><path d=\"m19.07 4.93-1.41 1.41\"/>",
    "sunrise": "<path d=\"M12 2v8\"/><path d=\"m4.93 10.93 1.41 1.41\"/><path d=\"M2 18h2\"/><path d=\"M20 18h2\"/><path d=\"m19.07 10.93-1.41 1.41\"/><path d=\"M22 22H2\"/><path d=\"m8 6 4-4 4 4\"/><path d=\"M16 18a4 4 0 0 0-8 0\"/>",
    "sunset": "<path d=\"M12 10V2\"/><path d=\"m4.93 10.93 1.41 1.41\"/><path d=\"M2 18h2\"/><path d=\"M20 18h2\"/><path d=\"m19.07 10.93-1.41 1.41\"/><path d=\"M22 22H2\"/><path d=\"m16 6-4 4-4-4\"/><path d=\"M16 18a4 4 0 0 0-8 0\"/>",
    "table": "<path d=\"M12 3v18\"/><rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\"/><path d=\"M3 9h18\"/><path d=\"M3 15h18\"/>",
    "tablet": "<rect width=\"16\" height=\"20\" x=\"4\" y=\"2\" rx=\"2\" ry=\"2\"/><line x1=\"12\" x2=\"12.01\" y1=\"18\" y2=\"18\"/>",
    "tag": "<path d=\"M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z\"/><circle cx=\"7.5\" cy=\"7.5\" r=\".5\" fill=\"currentColor\"/>",
    "target": "<circle cx=\"12\" cy=\"12\" r=\"10\"/><circle cx=\"12\" cy=\"12\" r=\"6\"/><circle cx=\"12\" cy=\"12\" r=\"2\"/>",
    "terminal": "<polyline points=\"4 17 10 11 4 5\"/><line x1=\"12\" x2=\"20\" y1=\"19\" y2=\"19\"/>",
    "thermometer": "<path d=\"M14 4v10.54a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0Z\"/>",
    "thumbs-up": "<path d=\"M7 10v12\"/><path d=\"M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z\"/>",
    "ticket": "<path d=\"M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z\"/><path d=\"M13 5v2\"/><path d=\"M13 17v2\"/><path d=\"M13 11v2\"/>",
    "timer": "<line x1=\"10\" x2=\"14\" y1=\"2\" y2=\"2\"/><line x1=\"12\" x2=\"15\" y1=\"14\" y2=\"11\"/><circle cx=\"12\" cy=\"14\" r=\"8\"/>",
    "train-front": "<path d=\"M8 3.1V7a4 4 0 0 0 8 0V3.1\"/><path d=\"m9 15-1-1\"/><path d=\"m15 15 1-1\"/><path d=\"M9 19c-2.8 0-5-2.2-5-5v-4a8 8 0 0 1 16 0v4c0 2.8-2.2 5-5 5Z\"/><path d=\"m8 19-2 3\"/><path d=\"m16 19 2 3\"/>",
    "trash-2": "<path d=\"M3 6h18\"/><path d=\"M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6\"/><path d=\"M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2\"/><line x1=\"10\" x2=\"10\" y1=\"11\" y2=\"17\"/><line x1=\"14\" x2=\"14\" y1=\"11\" y2=\"17\"/>",
    "tree-pine": "<path d=\"m17 14 3 3.3a1 1 0 0 1-.7 1.7H4.7a1 1 0 0 1-.7-1.7L7 14h-.3a1 1 0 0 1-.7-1.7L9 9h-.2A1 1 0 0 1 8 7.3L12 3l4 4.3a1 1 0 0 1-.8 1.7H15l3 3.3a1 1 0 0 1-.7 1.7H17Z\"/><path d=\"M12 22v-3\"/>",
    "trending-down": "<polyline points=\"22 17 13.5 8.5 8.5 13.5 2 7\"/><polyline points=\"16 17 22 17 22 11\"/>",
    "trending-up": "<polyline points=\"22 7 13.5 15.5 8.5 10.5 2 17\"/><polyline points=\"16 7 22 7 22 13\"/>",
    "triangle-alert": "<path d=\"m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3\"/><path d=\"M12 9v4\"/><path d=\"M12 17h.01\"/>",
    "trophy": "<path d=\"M6 9H4.5a2.5 2.5 0 0 1 0-5H6\"/><path d=\"M18 9h1.5a2.5 2.5 0 0 0 0-5H18\"/><path d=\"M4 22h16\"/><path d=\"M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22\"/><path d=\"M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22\"/><path d=\"M18 2H6v7a6 6 0 0 0 12 0V2Z\"/>",
    "truck": "<path d=\"M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2\"/><path d=\"M15 18H9\"/><path d=\"M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14\"/><circle cx=\"17\" cy=\"18\" r=\"2\"/><circle cx=\"7\" cy=\"18\" r=\"2\"/>",
    "tv": "<rect width=\"20\" height=\"15\" x=\"2\" y=\"7\" rx=\"2\" ry=\"2\"/><polyline points=\"17 2 12 7 7 2\"/>",
    "umbrella": "<path d=\"M22 12a10.06 10.06 1 0 0-20 0Z\"/><path d=\"M12 12v8a2 2 0 0 0 4 0\"/><path d=\"M12 2v1\"/>",
    "user": "<path d=\"M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2\"/><circle cx=\"12\" cy=\"7\" r=\"4\"/>",
    "users": "<path d=\"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2\"/><circle cx=\"9\" cy=\"7\" r=\"4\"/><path d=\"M22 21v-2a4 4 0 0 0-3-3.87\"/><path d=\"M16 3.13a4 4 0 0 1 0 7.75\"/>",
    "utensils": "<path d=\"M3 2v7c0 1.1.9 2 2 2h4a2 2 0 0 0 2-2V2\"/><path d=\"M7 2v20\"/><path d=\"M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3Zm0 0v7\"/>",
    "video": "<path d=\"m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5\"/><rect x=\"2\" y=\"6\" width=\"14\" height=\"12\" rx=\"2\"/>",
    "wallet": "<path d=\"M19 7V4a1 1 0 0 0-1-1H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2a1 1 0 0 0-1-1\"/><path d=\"M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4\"/>",
    "washing-machine": "<path d=\"M3 6h3\"/><path d=\"M17 6h.01\"/><rect width=\"18\" height=\"20\" x=\"3\" y=\"2\" rx=\"2\"/><circle cx=\"12\" cy=\"13\" r=\"5\"/><path d=\"M12 18a2.5 2.5 0 0 0 0-5 2.5 2.5 0 0 1 0-5\"/>",
    "waves": "<path d=\"M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1\"/><path d=\"M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1\"/><path d=\"M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1\"/>",
    "wifi": "<path d=\"M12 20h.01\"/><path d=\"M2 8.82a15 15 0 0 1 20 0\"/><path d=\"M5 12.859a10 10 0 0 1 14 0\"/><path d=\"M8.5 16.429a5 5 0 0 1 7 0\"/>",
    "wind": "<path d=\"M12.8 19.6A2 2 0 1 0 14 16H2\"/><path d=\"M17.5 8a2.5 2.5 0 1 1 2 4H2\"/><path d=\"M9.8 4.4A2 2 0 1 1 11 8H2\"/>",
    "workflow": "<rect width=\"8\" height=\"8\" x=\"3\" y=\"3\" rx=\"2\"/><path d=\"M7 11v4a2 2 0 0 0 2 2h4\"/><rect width=\"8\" height=\"8\" x=\"13\" y=\"13\" rx=\"2\"/>",
    "wrench": "<path d=\"M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z\"/>",
    "x": "<path d=\"M18 6 6 18\"/><path d=\"m6 6 12 12\"/>",
    "zap": "<path d=\"M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z\"/>",
}

# --------------------------------------------------------------------------------------------------------------------
# Renderer stylesheet
# --------------------------------------------------------------------------------------------------------------------
_CSS = r""":root{
--card:#ffffff;--surface:#ffffff;--text:#0d0d0d;--muted:#5d5d5d;--faint:#8f8f8f;--line:#e5e5e5;--line2:#efefef;
--chip:#f2f2f2;--chip2:#e8e8e8;--hover:#f7f7f7;--focus:#2a78d6;--accent:#2a78d6;--accent-ink:#ffffff;
--ok:#16a34a;--ok-bg:#eaf7ef;--ok-line:#b7e4c7;--ok-ink:#15803d;
--info:#2563eb;--info-bg:#eef4fe;--info-line:#bfd3f8;--info-ink:#1d4ed8;
--warn:#d97706;--warn-bg:#fdf4e4;--warn-line:#f3d7a5;--warn-ink:#a15c07;
--bad:#dc2626;--bad-bg:#fdeeee;--bad-line:#f5c2c2;--bad-ink:#b91c1c;
--acc-bg:#f1effd;--acc-line:#d5cff7;--acc-ink:#5b4ad1;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;--s8:#e34948;
--seq0:#cde2fb;--seq1:#104281;--shadow:0 1px 2px rgba(0,0,0,.04);--tip:#ffffff;--tip-line:#e3e3e3;
color-scheme:light}
:root[data-theme="dark"]{
--card:#212121;--surface:#212121;--text:#ececec;--muted:#b4b4b4;--faint:#8b8b8b;--line:#383838;--line2:#2f2f2f;
--chip:#2f2f2f;--chip2:#3a3a3a;--hover:#2a2a2a;--focus:#5aa1f0;--accent:#3987e5;--accent-ink:#ffffff;
--ok:#22c55e;--ok-bg:rgba(34,197,94,.12);--ok-line:rgba(34,197,94,.35);--ok-ink:#4ade80;
--info:#60a5fa;--info-bg:rgba(96,165,250,.12);--info-line:rgba(96,165,250,.35);--info-ink:#93c5fd;
--warn:#f59e0b;--warn-bg:rgba(245,158,11,.12);--warn-line:rgba(245,158,11,.35);--warn-ink:#fbbf24;
--bad:#f87171;--bad-bg:rgba(248,113,113,.12);--bad-line:rgba(248,113,113,.35);--bad-ink:#fca5a5;
--acc-bg:rgba(144,133,233,.14);--acc-line:rgba(144,133,233,.4);--acc-ink:#b4abf3;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767;
--seq0:#1f3554;--seq1:#86b6ef;--shadow:none;--tip:#2a2a2a;--tip-line:#444;
color-scheme:dark}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent}
body{font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
font-size:14px;line-height:1.5;color:var(--text);-webkit-font-smoothing:antialiased;overflow-x:hidden}
button,input,select,textarea{font:inherit;color:inherit}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:6px}
.ico{flex:none;display:inline-block;vertical-align:middle}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
[hidden]{display:none!important}
#gui-root{container-type:inline-size;container-name:gui;padding:2px 2px 6px}
.g-frame{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px 18px 18px;box-shadow:var(--shadow)}
.g-plain{padding:0}
.g-stack{display:flex;flex-direction:column;gap:14px;min-width:0}
.g-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.g-top-t{display:flex;align-items:center;gap:8px;font-weight:600;font-size:15px;min-width:0}
.g-top-t .ico{color:var(--text)}
.g-sub{color:var(--muted);font-size:13px;margin-top:2px}
.g-badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:2px 9px;font-size:12px;font-weight:500;
white-space:nowrap;background:var(--chip);color:var(--muted);border:1px solid transparent}
.t-success{background:var(--ok-bg);color:var(--ok-ink)}.t-info{background:var(--info-bg);color:var(--info-ink)}
.t-warning{background:var(--warn-bg);color:var(--warn-ink)}.t-danger{background:var(--bad-bg);color:var(--bad-ink)}
.t-accent{background:var(--acc-bg);color:var(--acc-ink)}
.g-hr{height:1px;background:var(--line);border:0;margin:2px 0}
.g-div{display:flex;align-items:center;gap:10px;color:var(--faint);font-size:12px}
.g-div::before,.g-div::after{content:"";flex:1;height:1px;background:var(--line)}
.g-div.nolabel::after{display:none}
.g-grid{display:grid;gap:14px;grid-template-columns:repeat(var(--cols,2),minmax(0,1fr))}
.g-grid.gap-sm{gap:8px}.g-grid.gap-lg{gap:20px}
.span-2{grid-column:span 2}.span-3{grid-column:span 3}.span-4{grid-column:span 4}
@container gui (max-width:560px){.g-grid.c3,.g-grid.c4{--cols:2!important}.span-3,.span-4{grid-column:1/-1}}
@container gui (max-width:380px){.g-grid{--cols:1!important}.g-grid>*{grid-column:1/-1}}
.g-card{border:1px solid var(--line);border-radius:14px;padding:14px;min-width:0;background:var(--card)}
.g-card.tone-info{background:var(--info-bg);border-color:var(--info-line)}
.g-card.tone-success{background:var(--ok-bg);border-color:var(--ok-line)}
.g-card.tone-warning{background:var(--warn-bg);border-color:var(--warn-line)}
.g-card.tone-danger{background:var(--bad-bg);border-color:var(--bad-line)}
.g-card.tone-accent{background:var(--acc-bg);border-color:var(--acc-line)}
.g-card-h{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.g-card-t{display:flex;align-items:center;gap:8px;font-weight:600;min-width:0}
.g-card-f{margin-top:10px;color:var(--faint);font-size:12px}
.g-sec{display:flex;flex-direction:column;gap:12px;min-width:0}
.g-sec+.g-sec,.g-stack>.g-sec:not(:first-child){border-top:1px solid var(--line);padding-top:16px}
.g-sec-h{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.g-sec-t{font-weight:600;display:flex;align-items:center;gap:8px}
.g-ctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.g-h{margin:0;font-weight:600;display:flex;align-items:center;gap:8px;line-height:1.3}
.g-h.l1{font-size:20px}.g-h.l2{font-size:17px}.g-h.l3{font-size:15px}.g-h.l4{font-size:14px;color:var(--muted)}
.md{min-width:0;overflow-wrap:anywhere}.md p{margin:0 0 8px}.md>:last-child{margin-bottom:0}
.md ul,.md ol{margin:0 0 8px;padding-left:20px}.md li{margin:2px 0}
.md h4,.md h5{margin:10px 0 6px;font-size:14px;font-weight:600}.md h4:first-child,.md h5:first-child{margin-top:0}
.md code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;background:var(--chip);
padding:1px 5px;border-radius:5px}
.md pre{background:var(--chip);padding:10px 12px;border-radius:10px;overflow:auto;margin:0 0 8px}
.md pre code{background:none;padding:0}
.md blockquote{margin:0 0 8px;padding-left:12px;border-left:3px solid var(--line);color:var(--muted)}
.md table{border-collapse:collapse;margin:0 0 8px;font-size:13px}.md th,.md td{border:1px solid var(--line);padding:4px 8px}
.md sup a{font-size:11px;background:var(--chip);border-radius:6px;padding:0 5px;color:var(--muted);margin-left:2px}
.txt-sm{font-size:13px}.txt-lg{font-size:15.5px}.txt-muted{color:var(--muted)}
.g-callout{display:flex;gap:10px;border-radius:12px;padding:11px 13px;border:1px solid var(--line);background:var(--hover)}
.g-callout .ico{margin-top:2px}
.g-callout.tone-info{background:var(--info-bg);border-color:var(--info-line)}.g-callout.tone-info .ico{color:var(--info)}
.g-callout.tone-success{background:var(--ok-bg);border-color:var(--ok-line)}.g-callout.tone-success .ico{color:var(--ok)}
.g-callout.tone-warning{background:var(--warn-bg);border-color:var(--warn-line)}.g-callout.tone-warning .ico{color:var(--warn)}
.g-callout.tone-danger{background:var(--bad-bg);border-color:var(--bad-line)}.g-callout.tone-danger .ico{color:var(--bad)}
.g-callout.tone-accent{background:var(--acc-bg);border-color:var(--acc-line)}.g-callout.tone-accent .ico{color:var(--acc-ink)}
.g-callout-t{font-weight:600;margin-bottom:2px}
.g-quote{border-left:3px solid var(--line);padding:2px 0 2px 14px;font-size:15px}
.g-quote-by{color:var(--muted);font-size:13px;margin-top:6px}
.g-code{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.g-code-h{display:flex;justify-content:space-between;align-items:center;padding:6px 8px 6px 12px;background:var(--hover);
border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}
.g-code pre{margin:0;padding:12px;overflow:auto;max-height:420px;font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.g-kv{display:grid;grid-template-columns:repeat(var(--cols,1),minmax(0,1fr));gap:0 18px}
.g-kv-r{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:7px 0;border-bottom:1px solid var(--line2)}
.g-kv-l{color:var(--muted);display:flex;align-items:center;gap:6px}.g-kv-v{font-weight:500;text-align:right}
.g-kv-h{color:var(--faint);font-size:12px}
.g-tags{display:flex;flex-wrap:wrap;gap:6px}
.g-tag{display:inline-flex;align-items:center;gap:4px;border-radius:7px;padding:2px 8px;font-size:12px;background:var(--chip);
color:var(--text);border:1px solid var(--line2)}
.g-tag.t-success,.g-tag.t-info,.g-tag.t-warning,.g-tag.t-danger,.g-tag.t-accent{border-color:transparent}
.g-notice{font-size:13px;color:var(--muted);border:1px dashed var(--line);border-radius:10px;padding:8px 12px}
.g-err{font-size:12.5px;color:var(--bad-ink);background:var(--bad-bg);border:1px solid var(--bad-line);border-radius:10px;
padding:8px 12px;display:flex;gap:8px;align-items:flex-start}
.g-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border-radius:999px;padding:7px 14px;
border:1px solid var(--line);background:var(--card);cursor:pointer;font-size:13.5px;font-weight:500;color:var(--text);
transition:background .15s,border-color .15s;text-decoration:none!important;min-height:34px}
.g-btn:hover{background:var(--hover)}
.g-btn.primary{background:var(--text);color:var(--card);border-color:var(--text)}.g-btn.primary:hover{opacity:.88}
.g-btn.ghost{border-color:transparent;background:transparent}.g-btn.ghost:hover{background:var(--chip)}
.g-btn.full{width:100%}.g-btn.sm{padding:3px 9px;min-height:26px;font-size:12px}
.g-btn.icon{padding:5px;min-height:28px;width:28px;border-color:transparent;background:transparent;color:var(--muted)}
.g-btn.icon:hover{background:var(--chip);color:var(--text)}
.g-btn[disabled]{opacity:.45;cursor:default}
.g-actions{display:flex;gap:8px;flex-wrap:wrap}.g-actions.center{justify-content:center}.g-actions.right{justify-content:flex-end}
.g-actions.stretch>*{flex:1}
.g-seg{display:inline-flex;gap:2px;padding:2px;border-radius:999px;max-width:100%;overflow-x:auto;scrollbar-width:none}
.g-seg.full{display:flex;width:100%}.g-seg.full>button{flex:1}
.g-seg>button{border:0;background:transparent;border-radius:999px;padding:5px 14px;cursor:pointer;color:var(--muted);
font-size:13px;white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;gap:6px;transition:background .15s,color .15s}
.g-seg>button:hover{color:var(--text)}
.g-seg>button[aria-selected="true"],.g-seg>button[aria-pressed="true"]{background:var(--chip);color:var(--text);font-weight:500}
.g-ul{display:flex;gap:18px;border-bottom:1px solid var(--line);overflow-x:auto}
.g-ul>button{border:0;background:none;padding:8px 0;cursor:pointer;color:var(--muted);border-bottom:2px solid transparent;
margin-bottom:-1px;white-space:nowrap;display:inline-flex;gap:6px;align-items:center}
.g-ul>button[aria-selected="true"]{color:var(--text);border-bottom-color:var(--text);font-weight:500}
.g-field{display:flex;flex-direction:column;gap:6px;min-width:0}
.g-field-h{display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13px}
.g-field-l{color:var(--muted)}.g-field-v{font-weight:600;font-variant-numeric:tabular-nums}
.g-field-e{color:var(--bad-ink);font-size:12px}
.g-in{border:1px solid var(--line);border-radius:10px;padding:7px 10px;background:var(--card);width:100%;min-height:36px}
.g-in:focus{outline:none;border-color:var(--focus);box-shadow:0 0 0 3px color-mix(in srgb,var(--focus) 22%,transparent)}
.g-in.bad{border-color:var(--bad)}
textarea.g-in{min-height:70px;resize:vertical}
input[type=range].g-range{-webkit-appearance:none;appearance:none;width:100%;height:20px;background:transparent;margin:0;cursor:pointer}
input[type=range].g-range::-webkit-slider-runnable-track{height:6px;border-radius:999px;
background:linear-gradient(to right,var(--text) 0,var(--text) var(--p,50%),var(--chip2) var(--p,50%))}
input[type=range].g-range::-moz-range-track{height:6px;border-radius:999px;background:var(--chip2)}
input[type=range].g-range::-moz-range-progress{height:6px;border-radius:999px;background:var(--text)}
input[type=range].g-range::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:var(--card);
border:1px solid var(--line);box-shadow:0 1px 3px rgba(0,0,0,.25);margin-top:-6px}
input[type=range].g-range::-moz-range-thumb{width:18px;height:18px;border-radius:50%;background:var(--card);border:1px solid var(--line);
box-shadow:0 1px 3px rgba(0,0,0,.25)}
.g-range-m{display:flex;justify-content:space-between;color:var(--faint);font-size:11.5px}
.g-tog{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer}
.g-tog-d{color:var(--muted);font-size:12.5px}
.g-sw{position:relative;width:36px;height:20px;border-radius:999px;background:var(--chip2);border:0;flex:none;cursor:pointer;transition:background .15s;padding:0}
.g-sw::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:#fff;
box-shadow:0 1px 2px rgba(0,0,0,.3);transition:transform .15s}
.g-sw[aria-checked="true"]{background:var(--ok)}.g-sw[aria-checked="true"]::after{transform:translateX(16px)}
.g-metric{display:flex;flex-direction:column;gap:3px;min-width:0}
.g-metric-l{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px}
.g-metric-l .ico{color:var(--muted)}
.g-metric-v{font-size:28px;font-weight:500;letter-spacing:-.01em;line-height:1.2;font-variant-numeric:tabular-nums;
display:flex;align-items:baseline;gap:5px;flex-wrap:wrap}
.g-metric.sm .g-metric-v{font-size:21px}.g-metric.lg .g-metric-v{font-size:36px}
.g-metric-u{font-size:14px;color:var(--muted);font-weight:500}
.g-metric-c{color:var(--muted);font-size:12.5px}
.g-delta{display:inline-flex;align-items:center;gap:2px;font-size:12.5px;font-weight:500;margin-left:4px}
.g-delta.good{color:var(--ok-ink)}.g-delta.bad{color:var(--bad-ink)}.g-delta.neutral{color:var(--muted)}
.g-bar{height:7px;border-radius:999px;background:var(--chip2);overflow:hidden;margin:6px 0 2px}
.g-bar>i{display:block;height:100%;border-radius:999px;background:var(--ok);transition:width .25s ease-out}
.g-bar.tone-info>i,.g-bar.tone-accent>i{background:var(--accent)}.g-bar.tone-warning>i{background:var(--warn)}
.g-bar.tone-danger>i{background:var(--bad)}.g-bar.tone-neutral>i{background:var(--text)}
.g-spark{display:block;margin-top:4px}
.g-metrics{display:grid;grid-template-columns:repeat(var(--cols,2),minmax(0,1fr));gap:20px 24px}
@container gui (max-width:560px){.g-metrics{--cols:2!important}}
@container gui (max-width:300px){.g-metrics{--cols:1!important}}
.g-prog-h{display:flex;justify-content:space-between;font-size:13px}.g-prog-h span:first-child{color:var(--muted)}
.g-gauge{display:flex;flex-direction:column;align-items:center}
.g-gauge-l{color:var(--muted);font-size:13px}.g-gauge svg{max-width:220px;width:100%}
.g-chart{min-width:0}
.g-chart-h{display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:8px}
.g-chart-t{font-weight:600}.g-chart-s{color:var(--muted);font-size:12.5px}
.g-plot{position:relative;border:1px solid var(--line);border-radius:14px;padding:12px 12px 8px;min-width:0}
.g-plot:focus-visible{outline-offset:-2px}
.g-svg{display:block;width:100%;overflow:visible}
.g-svg text{fill:var(--muted);font-size:11.5px;font-family:inherit}
.g-svg .gl{stroke:var(--line);stroke-dasharray:3 4;stroke-width:1}
.g-svg .ax{stroke:var(--line);stroke-width:1}
.g-svg .guide{stroke:var(--faint);stroke-width:1;stroke-dasharray:2 3}
.g-legend{display:flex;justify-content:center;flex-wrap:wrap;gap:4px 16px;margin-top:8px;font-size:12.5px;color:var(--muted)}
.g-legend span{display:inline-flex;align-items:center;gap:6px}
.g-legend i{width:9px;height:9px;border-radius:50%;display:inline-block}
.g-caption{color:var(--faint);font-size:12px;margin-top:8px}
.g-chart-f{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:6px}
.g-tip{position:absolute;pointer-events:none;background:var(--tip);border:1px solid var(--tip-line);border-radius:10px;
padding:7px 10px;font-size:12px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:5;white-space:nowrap;min-width:90px}
.g-tip b{display:block;font-weight:600;margin-bottom:3px;color:var(--text)}
.g-tip div{display:flex;align-items:center;gap:6px;color:var(--muted)}
.g-tip div span:last-child{margin-left:auto;padding-left:10px;color:var(--text);font-weight:600;font-variant-numeric:tabular-nums}
.g-tip i{width:8px;height:8px;border-radius:50%;display:inline-block}
.g-pie{display:flex;align-items:center;gap:18px;flex-wrap:wrap;justify-content:center}
.g-pie svg{width:180px;height:180px;flex:none}
.g-pie-l{display:flex;flex-direction:column;gap:6px;font-size:13px;min-width:140px}
.g-pie-l div{display:flex;align-items:center;gap:8px}.g-pie-l i{width:9px;height:9px;border-radius:50%}
.g-pie-l span:last-child{margin-left:auto;color:var(--muted);font-variant-numeric:tabular-nums;padding-left:10px}
.g-tbl-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.g-search{position:relative;flex:1;min-width:160px}.g-search .ico{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--faint)}
.g-search input{padding-left:32px}
.g-tbl-wrap{overflow:auto;border:1px solid var(--line);border-radius:12px}
table.g-tbl{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
.g-tbl th{text-align:left;font-weight:500;color:var(--muted);background:var(--hover);padding:0;border-bottom:1px solid var(--line);white-space:nowrap}
.g-tbl th button{all:unset;cursor:pointer;display:flex;align-items:center;gap:4px;padding:8px 12px;width:100%;box-sizing:border-box}
.g-tbl th button:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
.g-tbl th span.st{padding:8px 12px;display:block}
.g-tbl td{padding:8px 12px;border-bottom:1px solid var(--line2);vertical-align:middle}
.g-tbl tr:last-child td{border-bottom:0}.g-tbl tbody tr:hover td{background:var(--hover)}
.g-tbl .r{text-align:right}.g-tbl .c{text-align:center}.g-tbl th.r button{justify-content:flex-end}
.g-tbl .pb{display:flex;align-items:center;gap:8px;min-width:110px}.g-tbl .pb .g-bar{flex:1;margin:0}
.g-tbl-f{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:8px;color:var(--faint);font-size:12px;flex-wrap:wrap}
.g-pager{display:flex;align-items:center;gap:4px}
.g-empty{padding:22px;text-align:center;color:var(--faint)}
.g-tl{display:flex;flex-direction:column}
.g-tl-g{font-weight:600;margin:6px 0 8px;font-size:13px}
.g-tl-i{display:grid;grid-template-columns:72px 18px minmax(0,1fr);gap:0 10px}
.g-tl-time{color:var(--muted);font-size:12.5px;text-align:right;padding-top:1px;font-variant-numeric:tabular-nums}
.g-tl-rail{position:relative;display:flex;justify-content:center}
.g-tl-rail::before{content:"";position:absolute;top:18px;bottom:-4px;width:1.5px;background:var(--line)}
.g-tl-i:last-child .g-tl-rail::before{display:none}
.g-tl-dot{width:18px;height:18px;border-radius:50%;background:var(--card);border:1.5px solid var(--line);display:flex;
align-items:center;justify-content:center;color:var(--muted);z-index:1}
.g-tl-dot.plain::after{content:"";width:6px;height:6px;border-radius:50%;background:var(--text)}
.g-tl-b{padding-bottom:16px;min-width:0}.g-tl-t{font-weight:500;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.g-tl-d{color:var(--muted);font-size:13px;margin-top:2px}
@container gui (max-width:420px){.g-tl-i{grid-template-columns:18px minmax(0,1fr)}.g-tl-time{display:none}.g-tl-tm{display:inline!important}}
.g-tl-tm{display:none;color:var(--muted);font-size:12px}
.g-steps{display:flex;flex-direction:column;gap:0}
.g-step{display:grid;grid-template-columns:26px minmax(0,1fr);gap:0 12px}
.g-step-n{width:26px;height:26px;border-radius:50%;border:1.5px solid var(--line);display:flex;align-items:center;justify-content:center;
font-size:12px;font-weight:600;color:var(--muted);background:var(--card);position:relative;z-index:1}
.g-step-rail{position:relative;display:flex;flex-direction:column;align-items:center}
.g-step-rail::after{content:"";flex:1;width:1.5px;background:var(--line);margin:2px 0}
.g-step:last-child .g-step-rail::after{display:none}
.g-step-b{padding:2px 0 16px;min-width:0}.g-step-t{font-weight:500}.g-step-d{color:var(--muted);font-size:13px;margin-top:3px}
.g-step.done .g-step-n{background:var(--ok);border-color:var(--ok);color:#fff}
.g-step.cur .g-step-n{border-color:var(--text);color:var(--text)}
.g-step.cur .g-step-t{font-weight:600}
.g-step.btn .g-step-b{cursor:pointer}
.g-flow{display:flex;flex-direction:column;align-items:stretch;gap:0}
.g-flow-i{border:1px solid transparent;border-radius:14px;padding:12px 14px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:3px}
.g-flow-i .ico{color:var(--text)}
.g-flow-t{font-weight:600}.g-flow-d{color:var(--muted);font-size:12.5px}
.g-flow-i.tone-info{background:var(--info-bg);border-color:var(--info-line)}.g-flow-i.tone-info .ico{color:var(--info)}
.g-flow-i.tone-success{background:var(--ok-bg);border-color:var(--ok-line)}.g-flow-i.tone-success .ico{color:var(--ok)}
.g-flow-i.tone-warning{background:var(--warn-bg);border-color:var(--warn-line)}.g-flow-i.tone-warning .ico{color:var(--warn)}
.g-flow-i.tone-danger{background:var(--bad-bg);border-color:var(--bad-line)}.g-flow-i.tone-danger .ico{color:var(--bad)}
.g-flow-i.tone-accent{background:var(--acc-bg);border-color:var(--acc-line)}.g-flow-i.tone-accent .ico{color:var(--acc-ink)}
.g-flow-a{display:flex;justify-content:center;color:var(--faint);padding:3px 0}
.g-flow.h{flex-direction:row;align-items:stretch}.g-flow.h .g-flow-i{flex:1;min-width:0}.g-flow.h .g-flow-a{align-items:center;padding:0 4px}
@container gui (max-width:560px){.g-flow.h{flex-direction:column}.g-flow.h .g-flow-a{transform:rotate(90deg);padding:3px 0}}
.g-status-h{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
.g-status-r{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid var(--line2)}
.g-status-r:last-child{border-bottom:0}
.g-status-n{font-weight:500}.g-status-d{color:var(--muted);font-size:12.5px}
.g-status-v{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--muted);font-size:13px;text-align:right}
.st-ok{color:var(--ok)}.st-warn{color:var(--warn)}.st-down{color:var(--bad)}.st-info{color:var(--info)}.st-unknown{color:var(--faint)}
.g-list{display:flex;flex-direction:column;gap:8px}
.g-li{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:14px;padding:10px 12px;background:var(--card);
cursor:pointer;text-align:left;width:100%;transition:background .15s,border-color .15s}
.g-li:hover{background:var(--hover)}
.g-li[aria-selected="true"]{border-color:var(--chip2);background:var(--hover)}
.g-li.static{cursor:default}
.g-li-ic{width:36px;height:36px;border-radius:10px;background:var(--chip);display:flex;align-items:center;justify-content:center;flex:none}
.g-li-b{min-width:0;flex:1}.g-li-t{font-weight:600}.g-li-s{color:var(--muted);font-size:12.5px}
.g-li-e{color:var(--faint);display:flex;align-items:center;gap:8px}
.g-li[aria-selected="true"] .g-li-e .sel{color:var(--ok)}
.g-detail{border-top:1px solid var(--line);margin-top:6px;padding-top:14px;display:flex;flex-direction:column;gap:10px}
.g-detail-h{display:flex;justify-content:space-between;align-items:center;gap:10px}
.g-detail-t{font-weight:600}
.g-detail-hl{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:13px}
.g-check{border:1px solid var(--line);border-radius:16px;padding:14px 16px}
.g-check-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:6px}
.g-check-t{font-weight:600}.g-check-c{color:var(--muted);font-size:12px}
.g-ci{display:flex;gap:12px;align-items:flex-start;padding:7px 0;cursor:pointer;border:0;background:none;width:100%;text-align:left;border-radius:8px}
.g-ci-box{width:20px;height:20px;border-radius:50%;border:1.5px solid var(--chip2);flex:none;margin-top:1px;display:flex;align-items:center;
justify-content:center;color:#fff;transition:background .15s,border-color .15s}
.g-ci[aria-checked="true"] .g-ci-box{background:var(--ok);border-color:var(--ok)}
.g-ci-l{font-weight:500;font-size:15px}.g-ci-d{color:var(--muted);font-size:12.5px}
.g-ci[aria-checked="true"] .g-ci-l{text-decoration:line-through;text-decoration-color:var(--faint)}
.g-check-f{display:flex;justify-content:space-between;align-items:baseline;border-top:1px solid var(--line);margin-top:8px;padding-top:12px;gap:10px}
.g-check-f span:first-child{color:var(--muted);font-size:12.5px}.g-check-f b{font-size:16px;font-variant-numeric:tabular-nums}
.g-check .g-btn.full{margin-top:12px}
.g-cmp{display:grid;grid-template-columns:repeat(var(--cols,3),minmax(0,1fr));gap:12px}
@container gui (max-width:620px){.g-cmp{--cols:2!important}}@container gui (max-width:400px){.g-cmp{--cols:1!important}}
.g-prod{border:1px solid var(--line);border-radius:16px;overflow:hidden;display:flex;flex-direction:column;background:var(--card);min-width:0}
.g-prod.hl{border-color:var(--text);box-shadow:0 0 0 1px var(--text)}
.g-prod-img{aspect-ratio:16/10;background:var(--chip);overflow:hidden;display:flex;align-items:center;justify-content:center;color:var(--faint)}
.g-prod-img img{width:100%;height:100%;object-fit:cover;display:block}
.g-prod-b{padding:12px 14px 14px;display:flex;flex-direction:column;gap:6px;flex:1}
.g-prod-h{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
.g-prod-t{font-weight:600;line-height:1.3}.g-prod-s{color:var(--muted);font-size:12.5px}
.g-prod-p{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums}
.g-stars{display:inline-flex;align-items:center;gap:3px;color:var(--muted);font-size:12.5px}
.g-stars .ico{color:#e5a000;fill:#e5a000}
.g-pc{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:3px;font-size:13px}
.g-pc li{display:flex;gap:6px;align-items:flex-start}.g-pc .ico{margin-top:3px}
.g-pc .pro .ico{color:var(--ok)}.g-pc .con .ico{color:var(--bad)}
.g-prod .g-kv-r{padding:5px 0;font-size:13px}
.g-prod-f{margin-top:auto;padding-top:6px}
.g-img{margin:0;border-radius:14px;overflow:hidden;background:var(--chip);position:relative}
.g-img img{display:block;width:100%;height:100%;object-fit:cover}
.g-img.wide{aspect-ratio:16/9}.g-img.square{aspect-ratio:1}.g-img.tall{aspect-ratio:3/4}
.g-img figcaption{font-size:12.5px;color:var(--muted);padding:6px 2px 0;background:var(--card)}
.g-img .cr{font-size:11px;color:var(--faint)}
.g-ph{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:6px;color:var(--faint);font-size:12px;width:100%;height:100%;min-height:120px}
.g-gal{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
@container gui (max-width:480px){.g-gal{grid-template-columns:repeat(2,minmax(0,1fr))}}
.g-gal .g-img{aspect-ratio:4/3}
.g-gal figure{margin:0}
.g-car{position:relative}
.g-car-track{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:10px;scrollbar-width:none;border-radius:14px}
.g-car-track::-webkit-scrollbar{display:none}
.g-car-track>figure{flex:0 0 100%;scroll-snap-align:center;margin:0}
.g-car-track .g-img{aspect-ratio:16/9}
.g-car-nav{position:absolute;top:calc(50% - 30px);width:34px;height:34px;border-radius:50%;border:1px solid var(--line);background:var(--card);
display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.g-car-nav.l{left:8px}.g-car-nav.r{right:8px}
.g-dots{display:flex;justify-content:center;gap:6px;margin-top:8px}
.g-dots button{width:7px;height:7px;border-radius:50%;border:0;padding:0;background:var(--chip2);cursor:pointer}
.g-dots button[aria-current="true"]{background:var(--text)}
.g-map-f{border:0;width:100%;border-radius:12px;display:block;background:var(--chip)}
.g-stops{display:flex;flex-direction:column;margin-top:10px}
.g-stop{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-bottom:1px solid var(--line2)}
.g-stop:last-child{border-bottom:0}
.g-stop-n{width:22px;height:22px;border-radius:50%;background:var(--text);color:var(--card);font-size:11.5px;font-weight:600;
display:flex;align-items:center;justify-content:center;flex:none}
.g-stop-b{min-width:0;flex:1}.g-stop-t{font-weight:500}.g-stop-d{color:var(--muted);font-size:12.5px}
.g-wx-now{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.g-wx-temp{font-size:44px;font-weight:400;line-height:1;letter-spacing:-.02em}
.g-wx-ic{color:var(--text)}
.g-wx-meta{display:flex;gap:14px;color:var(--muted);font-size:13px;flex-wrap:wrap}
.g-wx-meta span{display:inline-flex;align-items:center;gap:5px}
.g-wx-days{display:flex;flex-direction:column}
.g-wx-d{display:grid;grid-template-columns:62px 26px minmax(0,1fr) 42px 100px 42px;gap:8px;align-items:center;padding:7px 0;
border-bottom:1px solid var(--line2);font-size:13.5px}
.g-wx-d:last-child{border-bottom:0}
.g-wx-d .lo{color:var(--muted);text-align:right}.g-wx-d .hi{text-align:left}
.g-wx-d .lab{color:var(--muted);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.g-wx-rng{height:5px;border-radius:999px;background:var(--chip2);position:relative}
.g-wx-rng i{position:absolute;top:0;bottom:0;border-radius:999px;background:linear-gradient(90deg,#60a5fa,#f59e0b)}
@container gui (max-width:440px){.g-wx-d{grid-template-columns:52px 24px 36px minmax(40px,1fr) 36px}.g-wx-d .lab{display:none}}
.g-app{border:1px solid var(--line);border-radius:12px;width:100%;display:block;background:#fff}
.g-acc{border:1px solid var(--line);border-radius:14px;overflow:hidden}
.g-acc-i+.g-acc-i{border-top:1px solid var(--line)}
.g-acc-b{all:unset;box-sizing:border-box;width:100%;display:flex;align-items:center;gap:8px;padding:11px 14px;cursor:pointer;font-weight:500}
.g-acc-b:hover{background:var(--hover)}.g-acc-b:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
.g-acc-b .chev{margin-left:auto;transition:transform .2s;color:var(--muted)}
.g-acc-b[aria-expanded="true"] .chev{transform:rotate(180deg)}
.g-acc-p{padding:0 14px 14px;display:flex;flex-direction:column;gap:10px}
.g-diag{position:relative}
.g-diag svg text{font-family:inherit}
.g-diag .nd{cursor:pointer}.g-diag .nd:focus{outline:none}.g-diag .nd:focus-visible rect{stroke:var(--focus);stroke-width:2}
.g-foot{display:flex;flex-direction:column;gap:10px}
.g-src{display:flex;flex-wrap:wrap;gap:6px}
.g-src a{display:inline-flex;align-items:center;gap:6px;font-size:12px;background:var(--chip);color:var(--muted);border-radius:999px;
padding:3px 10px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.g-src a:hover{color:var(--text);text-decoration:none}
.g-src a b{font-weight:600;color:var(--text)}
.g-fu{display:flex;flex-wrap:wrap;gap:8px}
.g-fu button{display:inline-flex;align-items:center;gap:7px;border:1px solid var(--line);background:var(--card);border-radius:999px;
padding:6px 12px;font-size:13px;cursor:pointer;color:var(--text);text-align:left;transition:background .15s}
.g-fu button:hover{background:var(--hover)}.g-fu .ico{color:var(--faint)}
.g-ds{color:var(--faint);font-size:12px;display:flex;align-items:center;gap:6px}
.g-toast{position:fixed;left:50%;bottom:10px;transform:translateX(-50%);background:var(--text);color:var(--card);font-size:12.5px;
padding:6px 12px;border-radius:999px;z-index:20;pointer-events:none}
.g-fade{animation:gin .28s ease-out both}
@keyframes gin{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
@keyframes shim{0%{background-position:-200px 0}100%{background-position:200px 0}}
.g-img.loading{background:linear-gradient(90deg,var(--chip) 0,var(--hover) 50%,var(--chip) 100%);background-size:400px 100%;animation:shim 1.2s linear infinite}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
@media (forced-colors:active){.g-bar>i,.g-sw[aria-checked="true"]{background:Highlight}.g-badge{border:1px solid CanvasText}}"""

# --------------------------------------------------------------------------------------------------------------------
# Renderer script: reactive state, safe expression engine, component registry. No eval, no innerHTML of user data.
# --------------------------------------------------------------------------------------------------------------------
_JS = r"""(function () {
"use strict";
var D = document, SVGNS = "http://www.w3.org/2000/svg";
var DATA = JSON.parse(D.getElementById("gui-data").textContent);
var SPEC = DATA.spec, CFG = DATA.cfg || {}, ICONS = DATA.icons || {};
var ROOT = D.getElementById("gui-root");
var LANG = CFG.lang || undefined;
var uid = 0;
function nid(p) { uid += 1; return (p || "g") + uid; }
/* ---------- theme ---------- */
function setTheme(t) { D.documentElement.setAttribute("data-theme", t === "dark" ? "dark" : "light"); }
(function theme() {
var t = CFG.theme;
var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
if (t !== "light" && t !== "dark") { t = mq && mq.matches ? "dark" : "light"; }
setTheme(t);
if (!CFG.follow) return;
try {
var pd = window.parent.document.documentElement;
var sync = function () { setTheme(pd.classList.contains("dark") ? "dark" : "light"); };
sync();
new MutationObserver(sync).observe(pd, { attributes: true, attributeFilter: ["class"] });
} catch (e) {
if (CFG.theme === "auto" && mq && mq.addEventListener) mq.addEventListener("change", function (ev) { setTheme(ev.matches ? "dark" : "light"); });
}
})();
/* ---------- DOM helpers ---------- */
function put(el, k) {
if (k == null || k === false) return;
if (Array.isArray(k)) { for (var i = 0; i < k.length; i++) put(el, k[i]); return; }
el.appendChild(k instanceof Node ? k : D.createTextNode(String(k)));
}
function attrs(el, a) {
if (!a) return;
for (var k in a) {
var v = a[k];
if (v == null || v === false) continue;
if (k === "class") el.setAttribute("class", v);
else if (k === "text") el.textContent = v;
else if (k.slice(0, 2) === "on" && typeof v === "function") el.addEventListener(k.slice(2), v);
else if (k === "style" && typeof v === "object") { for (var s in v) el.style.setProperty(s, v[s]); }
else el.setAttribute(k, v === true ? "" : String(v));
}
}
function h(tag, a) { var el = D.createElement(tag); attrs(el, a); for (var i = 2; i < arguments.length; i++) put(el, arguments[i]); return el; }
function sv(tag, a) { var el = D.createElementNS(SVGNS, tag); attrs(el, a); for (var i = 2; i < arguments.length; i++) put(el, arguments[i]); return el; }
function icon(name, size, cls) {
var m = ICONS[name]; if (!m) return null;
var s = D.createElementNS(SVGNS, "svg");
attrs(s, { viewBox: "0 0 24 24", width: size || 16, height: size || 16, fill: "none", stroke: "currentColor",
"stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", "class": "ico " + (cls || "") });
s.innerHTML = m; /* trusted constant markup from the Lucide subset shipped by the server */
return s;
}
function toneCls(t) { return t ? " t-" + t : ""; }
function badge(b) { if (!b) return null; return h("span", { "class": "g-badge" + toneCls(b.tone) }, b.icon ? icon(b.icon, 13) : null, b.label); }
function safeUrl(u) { return typeof u === "string" && /^(https?:\/\/|mailto:)/i.test(u) ? u : null; }
function toast(msg) {
var t = h("div", { "class": "g-toast", role: "status" }, msg); D.body.appendChild(t);
setTimeout(function () { t.remove(); }, 1600);
}
/* ---------- reactive state ---------- */
var state = Object.create(null); var initial = SPEC.state || {};
for (var k0 in initial) state[k0] = initial[k0];
var watchers = []; var queued = false;
function watch(fn) { watchers.push(fn); try { fn(); } catch (e) { console.warn("[gui]", e); } }
var COMPUTED = SPEC.computed || {};
function recompute() { for (var k in COMPUTED) { try { state[k] = evalExpr(COMPUTED[k]); } catch (e) { state[k] = null; } } }
function flush() { queued = false; recompute(); for (var i = 0; i < watchers.length; i++) { try { watchers[i](); } catch (e) { console.warn("[gui]", e); } } postHeight(); }
function setVar(k, v) { if (!k || state[k] === v) return; state[k] = v; if (!queued) { queued = true; Promise.resolve().then(flush); } }
function resetState() { for (var k in initial) if (!(k in COMPUTED)) state[k] = initial[k]; if (!queued) { queued = true; Promise.resolve().then(flush); } }
/* ---------- safe expression engine (no eval) ---------- */
function pmt(r, n, pv, fv, t) { fv = fv || 0; t = t || 0; if (!r) return -(pv + fv) / n; var p = Math.pow(1 + r, n); return -(r * (pv * p + fv)) / ((1 + r * t) * (p - 1)); }
var FN = {
min: Math.min, max: Math.max, abs: Math.abs, sqrt: Math.sqrt, pow: Math.pow, exp: Math.exp, ln: Math.log,
log: function (x, b) { return b ? Math.log(x) / Math.log(b) : Math.log10(x); }, log10: Math.log10, log2: Math.log2,
floor: Math.floor, ceil: Math.ceil, trunc: Math.trunc, sign: Math.sign, sin: Math.sin, cos: Math.cos, tan: Math.tan,
round: function (x, d) { var p = Math.pow(10, d || 0); return Math.round(x * p) / p; },
clamp: function (x, a, b) { return Math.min(Math.max(x, a), b); },
"if": function (c, a, b) { return c ? a : b; },
sum: function () { var s = 0; for (var i = 0; i < arguments.length; i++) s += +arguments[i] || 0; return s; },
avg: function () { var s = 0; for (var i = 0; i < arguments.length; i++) s += +arguments[i] || 0; return arguments.length ? s / arguments.length : 0; },
pmt: pmt,
fv: function (r, n, p, pv) { pv = pv || 0; if (!r) return -(pv + p * n); var q = Math.pow(1 + r, n); return -(pv * q + p * (q - 1) / r); },
pv: function (r, n, p) { if (!r) return -p * n; return -p * (1 - Math.pow(1 + r, -n)) / r; },
pct: function (a, b) { return b ? a / b * 100 : 0; }, mod: function (a, b) { return ((a % b) + b) % b; },
lerp: function (a, b, t) { return a + (b - a) * t; }, len: function (s) { return String(s).length; },
str: function (x) { return String(x); }, num: function (x) { return +x; },
fixed: function (x, d) { return Number(x).toFixed(d || 0); },
noise: function (i, s) { var x = Math.sin((i + 1) * 12.9898 + (s || 0) * 78.233) * 43758.5453; return x - Math.floor(x); }
};
var CONST = { PI: Math.PI, E: Math.E, "true": true, "false": false };
var BP = { "?": 1, "||": 2, "&&": 3, "==": 4, "!=": 4, "<": 5, ">": 5, "<=": 5, ">=": 5, "+": 6, "-": 6, "*": 7, "/": 7, "%": 7, "^": 9, "**": 9 };
var TOK = /\s*(?:(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")|(\*\*|==|!=|<=|>=|&&|\|\||[-+*\/%^(),?:<>!]))/y;
var CACHE = {};
function compile(src) {
if (CACHE[src]) return CACHE[src];
if (src.length > 400) throw new Error("expression too long");
var toks = [], m; TOK.lastIndex = 0;
while (TOK.lastIndex < src.length) {
var at = TOK.lastIndex; m = TOK.exec(src);
if (!m) { if (/^\s*$/.test(src.slice(at))) break; throw new Error("unexpected '" + src.slice(at, at + 8) + "'"); }
if (m[1]) toks.push({ t: "n", v: parseFloat(m[1]) });
else if (m[2]) { var w = m[2]; if (w === "and") toks.push({ t: "o", v: "&&" }); else if (w === "or") toks.push({ t: "o", v: "||" }); else if (w === "not") toks.push({ t: "o", v: "!" }); else toks.push({ t: "i", v: w }); }
else if (m[3]) toks.push({ t: "s", v: m[3].slice(1, -1).replace(/\\(.)/g, "$1") });
else if (m[4]) toks.push({ t: "o", v: m[4] });
}
var p = 0, depth = 0;
function peek() { return toks[p]; }
function eat(v) { var t = toks[p]; if (!t || t.v !== v) throw new Error("expected '" + v + "'"); p++; }
function nud() {
var t = toks[p++]; if (!t) throw new Error("unexpected end");
if (++depth > 60) throw new Error("too complex");
if (t.t === "n" || t.t === "s") { var c = t.v; return function () { return c; }; }
if (t.t === "i") {
if (peek() && peek().v === "(") {
p++; var fn = FN[t.v]; if (!fn || !Object.prototype.hasOwnProperty.call(FN, t.v)) throw new Error("unknown function " + t.v);
var args = [];
if (peek() && peek().v !== ")") { do { args.push(expr(0)); } while (peek() && peek().v === "," && ++p); }
eat(")");
return function (s) { var a = []; for (var i = 0; i < args.length; i++) a.push(args[i](s)); return fn.apply(null, a); };
}
var name = t.v;
if (Object.prototype.hasOwnProperty.call(CONST, name)) { var cv = CONST[name]; return function () { return cv; }; }
return function (s) { if (!Object.prototype.hasOwnProperty.call(s, name)) throw new Error("unknown value '" + name + "'"); return s[name]; };
}
if (t.v === "(") { var e = expr(0); eat(")"); return e; }
if (t.v === "-") { var a1 = expr(8); return function (s) { return -a1(s); }; }
if (t.v === "+") { var a2 = expr(8); return function (s) { return +a2(s); }; }
if (t.v === "!") { var a3 = expr(8); return function (s) { return !a3(s); }; }
throw new Error("unexpected '" + t.v + "'");
}
function expr(rbp) {
var left = nud();
while (true) {
var t = peek(); if (!t || t.t !== "o") break;
var bp = BP[t.v]; if (!bp || bp <= rbp) break;
p++;
if (t.v === "?") { var cond = left, a = expr(0); eat(":"); var b = expr(0); left = (function (c, x, y) { return function (s) { return c(s) ? x(s) : y(s); }; })(cond, a, b); continue; }
var right = expr(t.v === "^" || t.v === "**" ? bp - 1 : bp);
left = binop(t.v, left, right);
}
return left;
}
function binop(op, l, r) {
switch (op) {
case "+": return function (s) { var a = l(s), b = r(s); return (typeof a === "string" || typeof b === "string") ? String(a) + String(b) : a + b; };
case "-": return function (s) { return l(s) - r(s); };
case "*": return function (s) { return l(s) * r(s); };
case "/": return function (s) { return l(s) / r(s); };
case "%": return function (s) { return l(s) % r(s); };
case "^": case "**": return function (s) { return Math.pow(l(s), r(s)); };
case "==": return function (s) { return l(s) == r(s); }; /* eslint-disable-line eqeqeq */
case "!=": return function (s) { return l(s) != r(s); };
case "<": return function (s) { return l(s) < r(s); };
case ">": return function (s) { return l(s) > r(s); };
case "<=": return function (s) { return l(s) <= r(s); };
case ">=": return function (s) { return l(s) >= r(s); };
case "&&": return function (s) { return l(s) && r(s); };
case "||": return function (s) { return l(s) || r(s); };
}
throw new Error("bad operator " + op);
}
var fnc = expr(0);
if (p < toks.length) throw new Error("unexpected '" + toks[p].v + "'");
CACHE[src] = fnc;
return fnc;
}
function evalExpr(src, extra) {
var scope = state;
if (extra) { scope = Object.create(null); for (var k in state) scope[k] = state[k]; for (var j in extra) scope[j] = extra[j]; }
return compile(src)(scope);
}
function isExpr(v) { return typeof v === "string" && v.charAt(0) === "="; }
function val(v) { return isExpr(v) ? evalExpr(v.slice(1)) : v; }
/* ---------- formatting ---------- */
function nf(o) { try { return new Intl.NumberFormat(LANG, o); } catch (e) { return new Intl.NumberFormat("en-US", o); } }
function fmt(v, o) {
o = o || {};
if (v == null || v === "") return "\u2014";
if (typeof v === "boolean") return v ? "Yes" : "No";
if (typeof v === "string") { var n0 = Number(v); if (v.trim() === "" || isNaN(n0) || o.format == null) return v; v = n0; }
if (typeof v !== "number" || !isFinite(v)) return "\u2014";
var f = o.format || "number", d = o.decimals, a = Math.abs(v), out;
if (f === "integer") out = nf({ maximumFractionDigits: 0 }).format(v);
else if (f === "money") {
var cur = o.currency || "$", dd = d != null ? d : (Number.isInteger(v) || a >= 10000 ? 0 : 2);
if (/^[A-Z]{3}$/.test(cur)) out = nf({ style: "currency", currency: cur, minimumFractionDigits: dd, maximumFractionDigits: dd }).format(v);
else out = (v < 0 ? "-" : "") + cur + nf({ minimumFractionDigits: dd, maximumFractionDigits: dd }).format(a);
}
else if (f === "percent") out = nf({ maximumFractionDigits: d != null ? d : (a < 10 ? 1 : 0) }).format(v) + "%";
else if (f === "compact") out = nf({ notation: "compact", maximumFractionDigits: d != null ? d : 1 }).format(v);
else if (f === "duration") { var hh = Math.floor(a / 60), mm = Math.round(a % 60); out = (v < 0 ? "-" : "") + (hh ? hh + "h " : "") + mm + "m"; }
else if (f === "text") out = String(v);
else out = nf({ maximumFractionDigits: d != null ? d : (a >= 100 ? 0 : a >= 1 ? 2 : 3), minimumFractionDigits: d != null ? d : 0 }).format(v);
return out;
}
function fmtU(v, o) { var s = fmt(v, o); return o && o.unit && s !== "\u2014" ? s + (/^[%\u00b0]/.test(o.unit) ? "" : " ") + o.unit : s; }
/* ---------- markdown-lite (DOM only, never innerHTML) ---------- */
var INLINE = /\*\*([^*]+?)\*\*|__([^_]+?)__|\*([^*\s][^*]*?)\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|\{\{([^}]{1,400})\}\}|\[(\d{1,2})\]|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])/g;
var TPL_FMT = /\|\s*(money|percent|integer|number|compact|duration)(?::(\d))?\s*$/;
function tplNode(src) {
var m = src.match(TPL_FMT), opts = {}, ex = src;
if (m && src.charAt(m.index - 1) !== "|") { opts.format = m[1]; if (m[2]) opts.decimals = +m[2]; ex = src.slice(0, m.index); }
var cur = SPEC.currency; if (cur) opts.currency = cur;
var span = h("span", { "class": "tpl" });
watch(function () {
try { var r = evalExpr(ex.trim()); span.textContent = typeof r === "number" || opts.format ? fmt(r, opts) : String(r); span.removeAttribute("title"); }
catch (e) { span.textContent = "\u2014"; span.title = String(e.message || e); }
});
return span;
}
function inline(text, parent) {
var last = 0, m, re = new RegExp(INLINE.source, "g");
while ((m = re.exec(text))) {
if (m.index > last) parent.appendChild(D.createTextNode(text.slice(last, m.index)));
if (m[1] || m[2]) { var b = h("strong"); inline(m[1] || m[2], b); parent.appendChild(b); }
else if (m[3]) { var it = h("em"); inline(m[3], it); parent.appendChild(it); }
else if (m[4]) parent.appendChild(h("code", null, m[4]));
else if (m[5]) { var u = safeUrl(m[6]); if (u) { var a = h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }); inline(m[5], a); parent.appendChild(a); } else parent.appendChild(D.createTextNode(m[5])); }
else if (m[7]) parent.appendChild(tplNode(m[7]));
else if (m[8]) {
var src = (SPEC.sources || [])[+m[8] - 1];
if (src) parent.appendChild(h("sup", null, h("a", { href: src.url, target: "_blank", rel: "noopener noreferrer", title: src.title }, m[8])));
else parent.appendChild(D.createTextNode(m[0]));
}
else if (m[9]) parent.appendChild(h("a", { href: m[9], target: "_blank", rel: "noopener noreferrer" }, m[9].replace(/^https?:\/\//, "").slice(0, 60)));
last = re.lastIndex;
}
if (last < text.length) parent.appendChild(D.createTextNode(text.slice(last)));
return parent;
}
function md(src, cls) {
var root = h("div", { "class": "md " + (cls || "") });
var lines = String(src || "").replace(/\r\n?/g, "\n").split("\n"), i = 0, para = [];
function flushP() { if (para.length) { root.appendChild(inline(para.join(" "), h("p"))); para = []; } }
while (i < lines.length) {
var ln = lines[i], t = ln.trim(), mm;
if (!t) { flushP(); i++; continue; }
if (/^```/.test(t)) {
flushP(); var code = []; i++;
while (i < lines.length && !/^```/.test(lines[i].trim())) { code.push(lines[i]); i++; }
i++; root.appendChild(h("pre", null, h("code", null, code.join("\n")))); continue;
}
if ((mm = t.match(/^(#{1,6})\s+(.*)$/))) { flushP(); root.appendChild(inline(mm[2], h(mm[1].length <= 3 ? "h4" : "h5"))); i++; continue; }
if (/^(-{3,}|\*{3,})$/.test(t)) { flushP(); root.appendChild(h("hr", { "class": "g-hr" })); i++; continue; }
if (/^>\s?/.test(t)) { flushP(); var q = []; while (i < lines.length && /^>\s?/.test(lines[i].trim())) { q.push(lines[i].trim().replace(/^>\s?/, "")); i++; } root.appendChild(inline(q.join(" "), h("blockquote"))); continue; }
if (/^[-*+]\s+/.test(t) || /^\d+[.)]\s+/.test(t)) {
flushP(); var ordered = /^\d/.test(t), list = h(ordered ? "ol" : "ul");
while (i < lines.length) {
var l2 = lines[i].trim();
if (!(ordered ? /^\d+[.)]\s+/ : /^[-*+]\s+/).test(l2)) { if (l2 && list.lastChild && /^\s{2,}/.test(lines[i])) { list.lastChild.appendChild(D.createTextNode(" ")); inline(l2, list.lastChild); i++; continue; } break; }
var body = l2.replace(/^([-*+]|\d+[.)])\s+/, ""), li = h("li");
var cb = body.match(/^\[( |x|X)\]\s+(.*)$/); if (cb) { li.appendChild(D.createTextNode(cb[1] === " " ? "\u2610 " : "\u2611 ")); body = cb[2]; }
list.appendChild(inline(body, li)); i++;
}
root.appendChild(list); continue;
}
if (/^\|.*\|$/.test(t) && i + 1 < lines.length && /^\|?\s*:?-{2,}/.test(lines[i + 1].trim())) {
flushP(); var tbl = h("table"), cells = function (s) { return s.trim().replace(/^\||\|$/g, "").split("|"); };
var hr = h("tr"); cells(t).forEach(function (c) { hr.appendChild(inline(c.trim(), h("th"))); }); tbl.appendChild(h("thead", null, hr));
var tb = h("tbody"); i += 2;
while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) { var tr = h("tr"); cells(lines[i]).forEach(function (c) { tr.appendChild(inline(c.trim(), h("td"))); }); tb.appendChild(tr); i++; }
tbl.appendChild(tb); root.appendChild(h("div", { style: { overflow: "auto" } }, tbl)); continue;
}
para.push(t); i++;
}
flushP();
return root;
}
/* plain text (labels) that may contain {{expr}} */
function txt(s) {
if (s == null) return null;
s = String(s);
if (s.indexOf("{{") < 0) return s;
var span = h("span"); inline(s.replace(/\*/g, "\u2217"), span); return span;
}
/* ---------- clipboard and download ---------- */
function copyText(text, btn) {
function done(ok) {
if (btn) { var old = btn.getAttribute("data-label") || ""; btn.setAttribute("aria-label", ok ? "Copied" : "Copy failed"); }
toast(ok ? "Copied" : "Copy blocked by the browser. Select the text manually.");
}
function legacy() {
var ta = h("textarea", { style: { position: "fixed", opacity: "0", top: "0" } }); ta.value = text; D.body.appendChild(ta); ta.select();
var ok = false; try { ok = D.execCommand("copy"); } catch (e) { ok = false; }
ta.remove(); done(ok);
}
try {
if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done(true); }, legacy);
else legacy();
} catch (e) { legacy(); }
}
function download(name, text, mime) {
try {
var url = URL.createObjectURL(new Blob([text], { type: mime || "text/plain;charset=utf-8" }));
var a = h("a", { href: url, download: name }); D.body.appendChild(a); a.click(); a.remove();
setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
} catch (e) { toast("Download blocked by the browser"); }
}
var EXPORTERS = {};
function sendPrompt(text) {
if (!text) return;
var type = CFG.followup === "fill" ? "input:prompt" : "input:prompt:submit";
try { window.parent.postMessage({ type: type, text: String(text) }, "*"); toast(type === "input:prompt" ? "Added to the message box" : "Sent to chat"); }
catch (e) { copyText(String(text)); }
}
function runAction(a, btn) {
if (!a) return;
var text = a.text != null ? String(a.text).replace(/\{\{([^}]{1,400})\}\}/g, function (_, e) { try { return fmt(evalExpr(e.replace(TPL_FMT, "").trim()), { format: (e.match(TPL_FMT) || [])[1] }); } catch (x) { return "\u2014"; } }) : "";
if (a.type === "prompt") sendPrompt(text);
else if (a.type === "set") { var v = a.value; try { v = val(v); } catch (e) { v = a.value; } setVar(a["var"], v); }
else if (a.type === "reset") resetState();
else if (a.type === "copy") copyText(text, btn);
else if (a.type === "link") { var u = safeUrl(a.url); if (u) window.open(u, "_blank", "noopener"); }
else if (a.type === "download") {
if (a.target && EXPORTERS[a.target]) { var ex = EXPORTERS[a.target](); download(a.filename || ex.name, ex.text, ex.mime); }
else download(a.filename || "export.txt", text);
}
}
function button(b) {
var u = b.action && b.action.type === "link" ? safeUrl(b.action.url) : null;
var cls = "g-btn " + (b.variant || "secondary") + (b.full ? " full" : "");
var kids = [b.icon ? icon(b.icon, 15) : null, txt(b.label || ""), u ? icon("external-link", 13) : null];
if (u) return h("a", { "class": cls, href: u, target: "_blank", rel: "noopener noreferrer" }, kids);
var el = h("button", { type: "button", "class": cls }, kids);
el.addEventListener("click", function () { runAction(b.action, el); });
return el;
}
/* ---------- component registry ---------- */
var R = {};
function errBox(type, e) {
return h("div", { "class": "g-err", role: "alert" }, icon("triangle-alert", 15), h("div", null, "This " + (type || "component") + " could not be displayed: " + String((e && e.message) || e).slice(0, 160)));
}
function node(c) {
var el;
try {
var fn = R[c.type];
el = fn ? fn(c) : h("div", { "class": "g-notice" }, "Unsupported component: " + c.type);
} catch (e) { console.error("[gui]", c.type, e); el = errBox(c.type, e); }
if (!el) return null;
if (c.span && c.span > 1) el.classList.add("span-" + c.span);
if (c.id && !el.id) el.id = "c-" + c.id;
if (c.visible_if) {
var vi = c.visible_if;
watch(function () { var ok = true; try { ok = !!evalExpr(vi); } catch (e) { ok = true; } el.hidden = !ok; });
}
return el;
}
function nodes(list) { var out = []; (list || []).forEach(function (c) { var n = node(c); if (n) out.push(n); }); return out; }
function stack(list, cls) { return h("div", { "class": "g-stack " + (cls || "") }, nodes(list)); }
function title(t, ic, sub, bd, extra) {
if (!t && !sub && !bd && !extra) return null;
return h("div", { "class": "g-card-h" },
h("div", { style: { "min-width": "0" } }, t ? h("div", { "class": "g-card-t" }, ic ? icon(ic, 16) : null, txt(t)) : null, sub ? h("div", { "class": "g-sub" }, txt(sub)) : null),
h("div", { "class": "g-ctrl" }, extra || null, badge(bd)));
}
R.grid = function (c) { var cols = c.columns || Math.min(4, Math.max(1, (c.children || []).length)) || 2; return h("div", { "class": "g-grid c" + cols + (c.gap ? " gap-" + c.gap : ""), style: { "--cols": cols } }, nodes(c.children)); };
R.card = function (c) { return h("div", { "class": "g-card" + (c.tone ? " tone-" + c.tone : "") }, title(c.title, c.icon, c.subtitle, c.badge), stack(c.children), c.footer ? h("div", { "class": "g-card-f" }, txt(c.footer)) : null); };
R.section = function (c) {
var head = (c.title || c.controls || c.badge) ? h("div", { "class": "g-sec-h" },
h("div", null, c.title ? h("div", { "class": "g-sec-t" }, c.icon ? icon(c.icon, 16) : null, txt(c.title)) : null, c.subtitle ? h("div", { "class": "g-sub" }, txt(c.subtitle)) : null),
h("div", { "class": "g-ctrl" }, nodes(c.controls), badge(c.badge))) : null;
return h("section", { "class": "g-sec" }, head, nodes(c.children));
};
R.divider = function (c) { return c.label ? h("div", { "class": "g-div", role: "separator" }, c.label) : h("hr", { "class": "g-hr" }); };
R.heading = function (c) { var lv = c.level || 2; return h("div", null, h("h" + Math.min(6, lv + 1), { "class": "g-h l" + lv }, c.icon ? icon(c.icon, lv <= 2 ? 20 : 16) : null, txt(c.text)), c.subtitle ? h("div", { "class": "g-sub" }, txt(c.subtitle)) : null); };
R.text = function (c) { return md(c.md, (c.size ? "txt-" + c.size : "") + (c.tone === "muted" ? " txt-muted" : "")); };
R.notice = function (c) { return h("div", { "class": "g-notice" }, md(c.md)); };
R.callout = function (c) {
var ic = c.icon || { info: "info", success: "circle-check", warning: "triangle-alert", danger: "circle-alert", accent: "sparkles" }[c.tone || "info"] || "info";
return h("div", { "class": "g-callout tone-" + (c.tone || "info"), role: c.tone === "danger" || c.tone === "warning" ? "note" : null }, icon(ic, 17), h("div", { style: { "min-width": "0" } }, c.title ? h("div", { "class": "g-callout-t" }, txt(c.title)) : null, c.md ? md(c.md) : null));
};
R.quote = function (c) { return h("figure", { "class": "g-quote", style: { margin: "0" } }, md(c.md), c.by ? h("figcaption", { "class": "g-quote-by" }, "\u2014 " + c.by) : null); };
R.code = function (c) {
var b = h("button", { type: "button", "class": "g-btn sm ghost", "aria-label": "Copy code" }, icon("copy", 13), "Copy");
b.addEventListener("click", function () { copyText(c.code, b); });
return h("div", { "class": "g-code" }, h("div", { "class": "g-code-h" }, h("span", null, c.title || c.language || "code"), b), h("pre", { tabindex: "0" }, h("code", null, c.code)));
};
function kvRow(it) {
var v = h("span", { "class": "g-kv-v" });
watch(function () { var r; try { r = val(it.value); } catch (e) { r = null; } v.textContent = fmtU(r, it); });
return h("div", { "class": "g-kv-r" }, h("span", { "class": "g-kv-l" }, it.icon ? icon(it.icon, 14) : null, txt(it.label), it.hint ? h("span", { "class": "g-kv-h" }, it.hint) : null), v);
}
R.kv = function (c) { return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "4px" } }, txt(c.title)) : null, h("div", { "class": "g-kv", style: { "--cols": c.columns || 1 } }, c.items.map(kvRow))); };
R.tags = function (c) { return h("div", { "class": "g-tags" }, c.items.map(function (t) { return h("span", { "class": "g-tag" + toneCls(t.tone) }, t.icon ? icon(t.icon, 12) : null, t.label); })); };
/* tabs, accordion */
R.tabs = function (c) {
var bind = c.bind, tabs = c.tabs, wrap = h("div", { "class": "g-sec" });
var bar = h("div", { "class": c.style === "underline" ? "g-ul" : "g-seg full", role: "tablist" });
var local = { v: tabs[0].value };
function cur() { return bind ? state[bind] : local.v; }
var panels = [], btns = [];
tabs.forEach(function (t, i) {
var pid = nid("p"), bid = nid("t");
var b = h("button", { type: "button", role: "tab", id: bid, "aria-controls": pid }, t.icon ? icon(t.icon, 14) : null, t.label);
b.addEventListener("click", function () { if (bind) setVar(bind, t.value); else { local.v = t.value; sync(); } });
b.addEventListener("keydown", function (e) {
var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0; if (!d) return;
e.preventDefault(); var n = btns[(i + d + btns.length) % btns.length]; n.focus(); n.click();
});
btns.push(b); bar.appendChild(b);
panels.push(h("div", { role: "tabpanel", id: pid, "aria-labelledby": bid, "class": "g-stack" }, nodes(t.children)));
});
function sync() {
var v = cur(); var found = tabs.some(function (t) { return t.value === v; });
tabs.forEach(function (t, i) { var on = found ? t.value === v : i === 0; btns[i].setAttribute("aria-selected", on ? "true" : "false"); btns[i].tabIndex = on ? 0 : -1; panels[i].hidden = !on; });
postHeight();
}
if (bind) watch(sync); else sync();
wrap.appendChild(bar); panels.forEach(function (p) { wrap.appendChild(p); });
return wrap;
};
R.accordion = function (c) {
return h("div", { "class": "g-acc" }, c.items.map(function (it) {
var pid = nid("acc"), open = !!it.open;
var panel = h("div", { "class": "g-acc-p", id: pid }, it.md ? md(it.md) : null, nodes(it.children));
var btn = h("button", { type: "button", "class": "g-acc-b", "aria-expanded": open ? "true" : "false", "aria-controls": pid }, it.icon ? icon(it.icon, 15) : null, txt(it.title || ""), icon("chevron-down", 16, "chev"));
panel.hidden = !open;
btn.addEventListener("click", function () { open = !open; btn.setAttribute("aria-expanded", open ? "true" : "false"); panel.hidden = !open; postHeight(); });
return h("div", { "class": "g-acc-i" }, btn, panel);
}));
};
/* metrics */
function sparkline(data, w, hgt) {
var pts = data.filter(function (x) { return x != null; }); if (pts.length < 2) return null;
var mn = Math.min.apply(null, pts), mx = Math.max.apply(null, pts), rg = mx - mn || 1;
var d = data.map(function (v, i) { return v == null ? null : [i / (data.length - 1) * w, hgt - 2 - (v - mn) / rg * (hgt - 4)]; }).filter(Boolean);
return sv("svg", { "class": "g-spark", width: w, height: hgt, viewBox: "0 0 " + w + " " + hgt, "aria-hidden": "true" },
sv("polyline", { points: d.map(function (p) { return p[0].toFixed(1) + "," + p[1].toFixed(1); }).join(" "), fill: "none", stroke: "var(--s1)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
}
function metric(c) {
var vEl = h("span"), uEl = c.unit ? h("span", { "class": "g-metric-u" }, c.unit) : null, dEl = h("span", { "class": "g-delta" });
var bar = c.progress ? h("i") : null;
var box = h("div", { "class": "g-metric " + (c.size || "") },
h("div", { "class": "g-metric-l" }, c.icon ? icon(c.icon, 15) : null, txt(c.label || "")),
h("div", { "class": "g-metric-v" }, vEl, uEl, c.delta != null ? dEl : null),
bar ? h("div", { "class": "g-bar" + (c.progress.tone ? " tone-" + c.progress.tone : ""), role: "progressbar" }, bar) : null,
c.trend ? sparkline(c.trend, 120, 28) : null,
c.caption ? h("div", { "class": "g-metric-c" }, txt(c.caption)) : null);
watch(function () {
var r; try { r = val(c.value); vEl.textContent = fmt(r, c); vEl.removeAttribute("title"); } catch (e) { vEl.textContent = "\u2014"; vEl.title = e.message; }
if (c.delta != null) {
var dv; try { dv = val(c.delta); } catch (e) { dv = null; }
var n = typeof dv === "number" ? dv : parseFloat(String(dv).replace(/[^\d.\-+]/g, ""));
var tone = c.delta_tone && c.delta_tone !== "auto" ? c.delta_tone : (isNaN(n) || n === 0 ? "neutral" : n > 0 ? "good" : "bad");
dEl.className = "g-delta " + tone; dEl.textContent = "";
put(dEl, [isNaN(n) || n === 0 ? null : icon(n > 0 ? "trending-up" : "trending-down", 13), typeof dv === "number" ? (dv > 0 ? "+" : "") + fmt(dv, { format: c.format === "money" ? "money" : "number", currency: c.currency }) : String(dv)]);
}
if (bar) {
var pv, pm; try { pv = +val(c.progress.value); pm = +val(c.progress.max == null ? 100 : c.progress.max); } catch (e) { pv = 0; pm = 100; }
var pc = pm > 0 ? Math.max(0, Math.min(100, pv / pm * 100)) : 0; bar.style.width = pc + "%";
bar.parentNode.setAttribute("aria-valuenow", Math.round(pc)); bar.parentNode.setAttribute("aria-label", (c.label || "") + " " + Math.round(pc) + "%");
}
});
return box;
}
R.metric = metric;
R.metrics = function (c) {
var n = c.columns || Math.min(4, c.items.length);
return h("div", { "class": "g-metrics", style: { "--cols": n } }, c.items.map(function (m) {
var el = metric(m);
if (m.visible_if) watch(function () { var ok = true; try { ok = !!evalExpr(m.visible_if); } catch (e) { ok = true; } el.hidden = !ok; });
return el;
}));
};
R.progress = function (c) {
var bar = h("i"), right = h("span"), wrap = h("div", { "class": "g-bar tone-" + (c.tone || "success"), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100" }, bar);
watch(function () {
var v = +val(c.value), m = +val(c.max == null ? 100 : c.max); var p = m > 0 ? Math.max(0, Math.min(100, v / m * 100)) : 0;
bar.style.width = p + "%"; wrap.setAttribute("aria-valuenow", Math.round(p));
right.textContent = c.show_value === false ? "" : (c.format ? fmtU(v, c) : Math.round(p) + "%");
});
return h("div", null, h("div", { "class": "g-prog-h" }, h("span", null, txt(c.label || "")), right), wrap, c.caption ? h("div", { "class": "g-metric-c" }, txt(c.caption)) : null);
};
R.gauge = function (c) {
var mn = c.min != null ? c.min : 0, mx = c.max != null ? c.max : 100;
var holder = h("div");
function arc(a0, a1, r) { var p0 = [100 + r * Math.cos(a0), 100 + r * Math.sin(a0)], p1 = [100 + r * Math.cos(a1), 100 + r * Math.sin(a1)]; return "M" + p0[0].toFixed(2) + " " + p0[1].toFixed(2) + " A" + r + " " + r + " 0 " + (a1 - a0 > Math.PI ? 1 : 0) + " 1 " + p1[0].toFixed(2) + " " + p1[1].toFixed(2); }
watch(function () {
var v; try { v = +val(c.value); } catch (e) { v = NaN; }
var t = isFinite(v) ? Math.max(0, Math.min(1, (v - mn) / ((mx - mn) || 1))) : 0;
var tone = "var(--ok)";
(c.thresholds || []).slice().sort(function (a, b) { return a.value - b.value; }).forEach(function (th) { if (v >= th.value) tone = { success: "var(--ok)", warning: "var(--warn)", danger: "var(--bad)", info: "var(--info)", accent: "var(--accent)", neutral: "var(--text)" }[th.tone] || tone; });
holder.textContent = "";
holder.appendChild(sv("svg", { viewBox: "0 0 200 118", role: "img", "aria-label": (c.label || "Gauge") + ": " + fmtU(v, c) },
sv("path", { d: arc(Math.PI, 2 * Math.PI - 0.0001, 80), fill: "none", stroke: "var(--chip2)", "stroke-width": 14, "stroke-linecap": "round" }),
t > 0.003 ? sv("path", { d: arc(Math.PI, Math.PI + Math.PI * t, 80), fill: "none", stroke: tone, "stroke-width": 14, "stroke-linecap": "round" }) : null,
sv("text", { x: 100, y: 92, "text-anchor": "middle", style: "font-size:26px;font-weight:600;fill:var(--text)" }, fmt(v, c)),
sv("text", { x: 100, y: 112, "text-anchor": "middle", style: "font-size:11px;fill:var(--faint)" }, c.unit || ""),
sv("text", { x: 20, y: 114, "text-anchor": "middle", style: "font-size:10.5px;fill:var(--faint)" }, fmt(mn, c)),
sv("text", { x: 180, y: 114, "text-anchor": "middle", style: "font-size:10.5px;fill:var(--faint)" }, fmt(mx, c))));
});
return h("div", { "class": "g-gauge" }, c.label ? h("div", { "class": "g-gauge-l" }, txt(c.label)) : null, holder, c.caption ? h("div", { "class": "g-metric-c" }, txt(c.caption)) : null);
};
/* ---------- charts ---------- */
var SER = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)", "var(--s6)", "var(--s7)", "var(--s8)"];
function niceTicks(lo, hi, count) {
if (lo === hi) { hi = lo + 1; lo = lo > 0 ? 0 : lo - 1; }
var span = hi - lo, step = Math.pow(10, Math.floor(Math.log10(span / count))), err = span / count / step;
if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
var a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, out = [];
for (var v = a; v <= b + step / 2; v += step) out.push(+v.toFixed(10));
return out;
}
function seriesData(c) {
var labels = c.labels || [];
var n = labels.length || Math.max.apply(null, c.series.map(function (s) { return (s.data || []).length; }).concat([0]));
return c.series.map(function (s, si) {
var data;
if (s.expr) { data = []; for (var i = 0; i < n; i++) { var v; try { v = +evalExpr(s.expr, { i: i, x: isNaN(+labels[i]) ? i : +labels[i], n: n }); } catch (e) { v = null; } data.push(isFinite(v) ? v : null); } }
else data = (s.data || []).slice(0, Math.max(n, (s.data || []).length));
return { name: s.name || ("Series " + (si + 1)), data: data, color: SER[((s.color || si + 1) - 1) % 8] };
});
}
function chartTable(c, ser) {
var labels = c.labels || [];
var t = h("table", { "class": "g-tbl" }, h("thead", null, h("tr", null, h("th", null, h("span", { "class": "st" }, c.x_label || "")), ser.map(function (s) { return h("th", { "class": "r" }, h("span", { "class": "st" }, s.name)); }))));
var tb = h("tbody"); var n = Math.max.apply(null, ser.map(function (s) { return s.data.length; }));
for (var i = 0; i < n; i++) tb.appendChild(h("tr", null, h("td", null, labels[i] != null ? labels[i] : String(i + 1)), ser.map(function (s) { var v = s.data[i]; return h("td", { "class": "r" }, Array.isArray(v) ? v.join(", ") : fmtU(v, c)); })));
t.appendChild(tb); return h("div", { "class": "g-tbl-wrap" }, t);
}
function tipBox(plot) { var t = h("div", { "class": "g-tip", hidden: true, role: "status", "aria-live": "polite" }); plot.appendChild(t); return t; }
function showTip(tip, plot, x, y, head, rows) {
tip.textContent = ""; tip.appendChild(h("b", null, head));
rows.forEach(function (r) { tip.appendChild(h("div", null, h("i", { style: { background: r.color } }), h("span", null, r.name), h("span", null, r.value))); });
tip.hidden = false;
var pw = plot.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
var left = x + 14; if (left + tw > pw - 4) left = x - tw - 14; if (left < 4) left = 4;
var top = Math.max(4, Math.min(y - th / 2, plot.clientHeight - th - 4));
tip.style.left = left + "px"; tip.style.top = top + "px";
}
function cartesian(c, plot, W) {
var ser = seriesData(c), labels = c.labels || [], kind = c.kind, H = c.height || 240;
var horizontal = kind === "hbar", stacked = kind === "stacked_bar", bars = kind === "bar" || stacked || horizontal, scatter = kind === "scatter";
var n = scatter ? 0 : Math.max.apply(null, ser.map(function (s) { return s.data.length; }).concat([labels.length]));
var vals = [];
if (stacked) { for (var i = 0; i < n; i++) { var pos = 0, neg = 0; ser.forEach(function (s) { var v = s.data[i] || 0; if (v >= 0) pos += v; else neg += v; }); vals.push(pos, neg); } }
else ser.forEach(function (s) { s.data.forEach(function (v) { if (Array.isArray(v)) vals.push(v[1]); else if (v != null) vals.push(v); }); });
if (!vals.length) throw new Error("no numeric data");
var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
if (bars || kind === "area") lo = Math.min(0, lo);
if (c.y_min != null) lo = c.y_min; if (c.y_max != null) hi = c.y_max;
if (!bars && kind !== "area" && c.y_min == null && lo > 0 && (hi - lo) / hi < 0.5) { /* keep zoomed in */ } else if (!bars && c.y_min == null && lo > 0) lo = 0;
var ticks = niceTicks(lo, hi, horizontal ? Math.max(3, Math.floor(W / 110)) : 4); lo = c.y_min != null ? c.y_min : ticks[0]; hi = c.y_max != null ? c.y_max : ticks[ticks.length - 1];
var tf = function (v) { return fmt(v, { format: c.format === "money" ? "money" : c.format === "percent" ? "percent" : "compact", currency: c.currency, decimals: c.format === "percent" ? 0 : undefined }); };
var catLbl = function (i) { return labels[i] != null ? String(labels[i]) : String(i + 1); };
var padL, padB = 26, padT = 8, padR = 10;
if (horizontal) { padL = Math.min(W * 0.38, 12 + 6.6 * Math.max.apply(null, labels.map(function (l) { return String(l).length; }).concat([4]))); H = c.height || Math.max(120, n * 30 + 34); }
else padL = 10 + 6.8 * Math.max.apply(null, ticks.map(function (t) { return tf(t).length; }));
var pw = Math.max(40, W - padL - padR), ph = Math.max(40, H - padT - padB);
var svg = sv("svg", { "class": "g-svg", width: W, height: H, viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": (c.title || "Chart") + ": " + ser.map(function (s) { return s.name; }).join(", ") });
var vScale = function (v) { return horizontal ? padL + (v - lo) / ((hi - lo) || 1) * pw : padT + ph - (v - lo) / ((hi - lo) || 1) * ph; };
ticks.forEach(function (t) {
if (t < lo - 1e-9 || t > hi + 1e-9) return;
var p = vScale(t);
if (horizontal) { svg.appendChild(sv("line", { "class": "gl", x1: p, x2: p, y1: padT, y2: padT + ph })); svg.appendChild(sv("text", { x: p, y: H - 6, "text-anchor": "middle" }, tf(t))); }
else { svg.appendChild(sv("line", { "class": "gl", x1: padL, x2: padL + pw, y1: p, y2: p })); svg.appendChild(sv("text", { x: padL - 8, y: p + 4, "text-anchor": "end" }, tf(t))); }
});
var xmin = 0, xmax = 1, xticks = [];
if (scatter) {
var xs = []; ser.forEach(function (s) { s.data.forEach(function (v) { if (Array.isArray(v)) xs.push(v[0]); }); });
xticks = niceTicks(Math.min.apply(null, xs), Math.max.apply(null, xs), Math.max(3, Math.floor(pw / 90))); xmin = xticks[0]; xmax = xticks[xticks.length - 1];
}
var xPos = function (i) { return scatter ? padL + (i - xmin) / ((xmax - xmin) || 1) * pw : bars ? padL + (i + 0.5) * pw / n : padL + (n <= 1 ? pw / 2 : i * pw / (n - 1)); };
if (scatter) xticks.forEach(function (t) { svg.appendChild(sv("text", { x: xPos(t), y: H - 6, "text-anchor": "middle" }, fmt(t, { format: "compact" }))); });
else if (horizontal) { for (var hi2 = 0; hi2 < n; hi2++) { var yc = padT + (hi2 + 0.5) * ph / n; svg.appendChild(sv("text", { x: padL - 8, y: yc + 4, "text-anchor": "end" }, catLbl(hi2).length > 26 ? catLbl(hi2).slice(0, 25) + "\u2026" : catLbl(hi2))); } }
else {
var maxChars = Math.max.apply(null, labels.map(function (l) { return String(l).length; }).concat([3]));
var every = Math.max(1, Math.ceil(n * (maxChars * 6.6 + 10) / pw));
for (var xi = 0; xi < n; xi += every) svg.appendChild(sv("text", { x: xPos(xi), y: H - 6, "text-anchor": n > 1 && !bars && xi === 0 ? "start" : n > 1 && !bars && xi === n - 1 ? "end" : "middle" }, catLbl(xi)));
}
svg.appendChild(horizontal ? sv("line", { "class": "ax", x1: vScale(Math.max(lo, 0)), x2: vScale(Math.max(lo, 0)), y1: padT, y2: padT + ph }) : sv("line", { "class": "ax", x1: padL, x2: padL + pw, y1: vScale(Math.max(lo, Math.min(0, hi)) ), y2: vScale(Math.max(lo, Math.min(0, hi))) }));
var marks = sv("g");
if (bars) {
var band = (horizontal ? ph : pw) / Math.max(1, n), inner = band * 0.72, gap = 2;
var bw = stacked ? inner : Math.max(2, (inner - gap * (ser.length - 1)) / ser.length);
for (var b = 0; b < n; b++) {
var accP = 0, accN = 0;
ser.forEach(function (s, si) {
var v = s.data[b]; if (v == null) return;
var from, to;
if (stacked) { if (v >= 0) { from = accP; to = accP + v; accP = to; } else { from = accN; to = accN + v; accN = to; } } else { from = 0; to = v; }
var off = (horizontal ? padT : padL) + b * band + (band - inner) / 2 + (stacked ? 0 : si * (bw + gap));
var a1 = vScale(Math.max(lo, Math.min(from, to))), a2 = vScale(Math.max(from, to));
var x, y, w, hh;
if (horizontal) { x = a1; w = Math.max(1, a2 - a1 - (stacked ? gap : 0)); y = off; hh = bw; }
else { y = a2; hh = Math.max(1, a1 - a2 - (stacked && from !== 0 ? gap : 0)); x = off; w = bw; }
var r = Math.min(4, w / 2, hh / 2);
marks.appendChild(sv("rect", { x: x.toFixed(1), y: y.toFixed(1), width: w.toFixed(1), height: hh.toFixed(1), rx: r, fill: s.color }));
});
}
} else if (scatter) {
ser.forEach(function (s) { s.data.forEach(function (p) { if (Array.isArray(p)) marks.appendChild(sv("circle", { cx: xPos(p[0]), cy: vScale(p[1]), r: 4.5, fill: s.color, stroke: "var(--card)", "stroke-width": 1.5, "fill-opacity": 0.85 })); }); });
} else {
ser.forEach(function (s) {
var segs = [], cur = [];
s.data.forEach(function (v, i) { if (v == null) { if (cur.length) segs.push(cur); cur = []; } else cur.push([xPos(i), vScale(v)]); });
if (cur.length) segs.push(cur);
segs.forEach(function (seg) {
var d = seg.map(function (p, i) { return (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }).join("");
if (kind === "area") marks.appendChild(sv("path", { d: d + "L" + seg[seg.length - 1][0].toFixed(1) + " " + vScale(Math.max(lo, 0)).toFixed(1) + "L" + seg[0][0].toFixed(1) + " " + vScale(Math.max(lo, 0)).toFixed(1) + "Z", fill: s.color, "fill-opacity": 0.14, stroke: "none" }));
marks.appendChild(sv("path", { d: d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
if (seg.length <= 40) seg.forEach(function (p) { marks.appendChild(sv("circle", { cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: 3.5, fill: s.color, stroke: "var(--card)", "stroke-width": 1.5 })); });
});
});
}
svg.appendChild(marks);
var guide = sv("line", { "class": "guide", y1: padT, y2: padT + ph, visibility: "hidden" }); svg.appendChild(guide);
var hl = sv("g"); svg.appendChild(hl);
var tip = plot.__tip;
function show(i, px, py) {
if (scatter) return;
if (i < 0 || i >= n) { tip.hidden = true; guide.setAttribute("visibility", "hidden"); hl.textContent = ""; return; }
hl.textContent = "";
if (!bars) { var gx = xPos(i); guide.setAttribute("x1", gx); guide.setAttribute("x2", gx); guide.setAttribute("visibility", "visible"); ser.forEach(function (s) { if (s.data[i] != null) hl.appendChild(sv("circle", { cx: gx, cy: vScale(s.data[i]), r: 5, fill: s.color, stroke: "var(--card)", "stroke-width": 2 })); }); }
else { var band2 = (horizontal ? ph : pw) / n; hl.appendChild(sv("rect", horizontal ? { x: padL, y: padT + i * band2, width: pw, height: band2, fill: "var(--text)", "fill-opacity": 0.05 } : { x: padL + i * band2, y: padT, width: band2, height: ph, fill: "var(--text)", "fill-opacity": 0.05 })); }
showTip(tip, plot, px != null ? px : (horizontal ? padL + pw / 2 : xPos(i)) + 12, py != null ? py : (horizontal ? padT + (i + 0.5) * ph / n : padT + ph / 3) + 12, catLbl(i),
ser.filter(function (s) { return s.data[i] != null; }).map(function (s) { return { name: s.name, color: s.color, value: fmtU(s.data[i], c) }; }));
}
function idxAt(ev) {
var r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) * W / r.width, y = (ev.clientY - r.top) * H / r.height;
if (horizontal) return Math.floor((y - padT) / (ph / n));
if (bars) return Math.floor((x - padL) / (pw / n));
return n <= 1 ? 0 : Math.round((x - padL) / (pw / (n - 1)));
}
if (scatter) {
svg.addEventListener("mousemove", function (ev) {
var r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) * W / r.width, y = (ev.clientY - r.top) * H / r.height, best = null, bd = 400;
ser.forEach(function (s) { s.data.forEach(function (p) { if (!Array.isArray(p)) return; var dx = xPos(p[0]) - x, dy = vScale(p[1]) - y, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = { s: s, p: p }; } }); });
if (best) showTip(tip, plot, xPos(best.p[0]) + 12, vScale(best.p[1]) + 12, best.s.name, [{ name: c.x_label || "x", color: best.s.color, value: fmt(best.p[0]) }, { name: c.y_label || "y", color: best.s.color, value: fmtU(best.p[1], c) }]);
else tip.hidden = true;
});
} else svg.addEventListener("mousemove", function (ev) { var r = plot.getBoundingClientRect(); show(idxAt(ev), ev.clientX - r.left, ev.clientY - r.top); });
svg.addEventListener("mouseleave", function () { show(-1); });
plot.__key = function (e) {
if (scatter) return;
var cur = plot.__idx == null ? -1 : plot.__idx;
if (e.key === "ArrowRight" || e.key === "ArrowDown") cur = Math.min(n - 1, cur + 1); else if (e.key === "ArrowLeft" || e.key === "ArrowUp") cur = Math.max(0, cur - 1); else if (e.key === "Escape") cur = -1; else return;
e.preventDefault(); plot.__idx = cur; show(cur);
};
return { svg: svg, ser: ser };
}
function pie(c, plot) {
var ser = seriesData(c), s0 = ser[0], labels = c.labels || [], donut = c.kind === "donut";
var items = s0.data.map(function (v, i) { return { label: labels[i] != null ? labels[i] : "Item " + (i + 1), v: Math.max(0, +v || 0), color: SER[i % 8] }; });
if (items.length > 8) { var rest = items.slice(7).reduce(function (a, b) { return a + b.v; }, 0); items = items.slice(0, 7).concat([{ label: "Other", v: rest, color: "var(--faint)" }]); }
var total = items.reduce(function (a, b) { return a + b.v; }, 0); if (!total) throw new Error("all values are zero");
var svg = sv("svg", { viewBox: "0 0 200 200", role: "img", "aria-label": (c.title || "Chart") + ": " + items.map(function (it) { return it.label + " " + Math.round(it.v / total * 100) + "%"; }).join(", ") });
var a = -Math.PI / 2, R0 = 92, R1 = donut ? 56 : 0, tip = plot.__tip;
items.forEach(function (it) {
var frac = it.v / total, a2 = a + frac * Math.PI * 2; if (frac <= 0) return;
var large = a2 - a > Math.PI ? 1 : 0, P = function (r, t) { return (100 + r * Math.cos(t)).toFixed(2) + " " + (100 + r * Math.sin(t)).toFixed(2); };
var d = frac >= 0.9999 ? (donut ? "M100 8A92 92 0 1 1 99.99 8ZM100 44A56 56 0 1 0 100.01 44Z" : "M100 8A92 92 0 1 1 99.99 8Z")
: "M" + P(R0, a) + "A" + R0 + " " + R0 + " 0 " + large + " 1 " + P(R0, a2) + (donut ? "L" + P(R1, a2) + "A" + R1 + " " + R1 + " 0 " + large + " 0 " + P(R1, a) + "Z" : "L100 100Z");
var p = sv("path", { d: d, fill: it.color, stroke: "var(--card)", "stroke-width": 2, "fill-rule": "evenodd", tabindex: "0" });
var on = function (ev) { var r = plot.getBoundingClientRect(); showTip(tip, plot, ev && ev.clientX ? ev.clientX - r.left : 100, ev && ev.clientY ? ev.clientY - r.top : 80, it.label, [{ name: s0.name, color: it.color, value: fmtU(it.v, c) + " \u00b7 " + Math.round(frac * 1000) / 10 + "%" }]); p.setAttribute("opacity", "0.85"); };
var off = function () { tip.hidden = true; p.removeAttribute("opacity"); };
p.addEventListener("mousemove", on); p.addEventListener("mouseleave", off); p.addEventListener("focus", function () { on(); }); p.addEventListener("blur", off);
svg.appendChild(p); a = a2;
});
if (donut) { svg.appendChild(sv("text", { x: 100, y: 100, "text-anchor": "middle", style: "font-size:22px;font-weight:600;fill:var(--text)" }, fmt(total, { format: c.format === "money" ? "money" : "compact", currency: c.currency }))); svg.appendChild(sv("text", { x: 100, y: 118, "text-anchor": "middle", style: "font-size:11px;fill:var(--faint)" }, c.unit || "Total")); }
var leg = h("div", { "class": "g-pie-l" }, items.map(function (it) { return h("div", null, h("i", { style: { background: it.color } }), h("span", null, it.label), h("span", null, Math.round(it.v / total * 100) + "%")); }));
return { el: h("div", { "class": "g-pie" }, svg, leg), ser: ser };
}
R.chart = function (c) {
var plot = h("div", { "class": "g-plot", tabindex: "0", "aria-label": (c.title || "Chart") + ". Use arrow keys to read values." });
plot.__tip = tipBox(plot);
var body = h("div"), legend = h("div", { "class": "g-legend" }), tableHolder = h("div", { hidden: true });
var showTable = false, lastW = 0, ser = null;
var tBtn = c.table_view === false ? null : h("button", { type: "button", "class": "g-btn icon", "aria-label": "Show data table", title: "Show data table", "aria-pressed": "false" }, icon("table", 15));
function draw(force) {
var W = Math.floor(plot.clientWidth - 24); if (W <= 0) W = 520;
if (!force && W === lastW) return; lastW = W;
body.textContent = ""; legend.textContent = "";
try {
if (c.kind === "pie" || c.kind === "donut") { var p = pie(c, plot); body.appendChild(p.el); ser = p.ser; }
else {
var r = cartesian(c, plot, W); body.appendChild(r.svg); ser = r.ser;
if (ser.length > 1 || c.legend) ser.forEach(function (s) { legend.appendChild(h("span", null, h("i", { style: { background: s.color } }), s.name)); });
}
} catch (e) { body.appendChild(errBox("chart", e)); }
if (showTable && ser) { tableHolder.textContent = ""; tableHolder.appendChild(chartTable(c, ser)); }
}
if (tBtn) tBtn.addEventListener("click", function () {
showTable = !showTable; tBtn.setAttribute("aria-pressed", showTable ? "true" : "false");
tBtn.textContent = ""; tBtn.appendChild(icon(showTable ? "chart-line" : "table", 15)); tBtn.setAttribute("aria-label", showTable ? "Show chart" : "Show data table");
plot.hidden = showTable; tableHolder.hidden = !showTable; if (showTable && ser) { tableHolder.textContent = ""; tableHolder.appendChild(chartTable(c, ser)); } postHeight();
});
plot.addEventListener("keydown", function (e) { if (plot.__key) plot.__key(e); });
plot.appendChild(body); plot.appendChild(legend);
var hasHead = !!(c.title || c.subtitle);
var head = hasHead ? h("div", { "class": "g-chart-h" }, h("div", null, c.title ? h("div", { "class": "g-chart-t" }, txt(c.title)) : null, c.subtitle ? h("div", { "class": "g-chart-s" }, txt(c.subtitle)) : null), tBtn) : null;
var foot = c.caption || (!hasHead && tBtn) ? h("div", { "class": "g-chart-f" }, h("div", { "class": "g-caption", style: { "margin-top": "0" } }, c.caption ? txt(c.caption) : null), hasHead ? null : tBtn) : null;
var wrap = h("div", { "class": "g-chart" }, head, plot, tableHolder, foot);
var dyn = c.series.some(function (s) { return s.expr; });
afterMount(function () { draw(true); if (dyn) watch(function () { draw(true); }); });
if (window.ResizeObserver) new ResizeObserver(function () { draw(false); }).observe(plot);
return wrap;
};
R.heatmap = function (c) {
var plot = h("div", { "class": "g-plot", style: { overflow: "auto" } }); plot.__tip = tipBox(plot);
var xs = c.x_labels || [], ys = c.y_labels || [], vals = c.values, flat = [];
vals.forEach(function (r) { r.forEach(function (v) { if (v != null) flat.push(v); }); });
var mn = Math.min.apply(null, flat), mx = Math.max.apply(null, flat);
var cols = Math.max.apply(null, vals.map(function (r) { return r.length; })), padL = 10 + 6.6 * Math.max.apply(null, ys.map(function (y) { return String(y).length; }).concat([2]));
var cw = 34, chh = 26, W = padL + cols * cw, H = vals.length * chh + 24;
var svg = sv("svg", { "class": "g-svg", viewBox: "0 0 " + W + " " + H, style: "max-width:" + W + "px", role: "img", "aria-label": c.title || "Heatmap" });
vals.forEach(function (r, yi) {
svg.appendChild(sv("text", { x: padL - 8, y: yi * chh + chh / 2 + 4, "text-anchor": "end" }, ys[yi] != null ? String(ys[yi]) : ""));
r.forEach(function (v, xi) {
var t = v == null ? 0 : (v - mn) / ((mx - mn) || 1);
var rect = sv("rect", { x: padL + xi * cw + 1, y: yi * chh + 1, width: cw - 2, height: chh - 2, rx: 4, fill: v == null ? "var(--chip)" : "color-mix(in srgb, var(--seq1) " + Math.round(8 + t * 92) + "%, var(--seq0))" });
rect.addEventListener("mousemove", function (ev) { var b = plot.getBoundingClientRect(); showTip(plot.__tip, plot, ev.clientX - b.left, ev.clientY - b.top, (ys[yi] != null ? ys[yi] : "") + " \u00b7 " + (xs[xi] != null ? xs[xi] : ""), [{ name: "Value", color: rect.getAttribute("fill"), value: fmtU(v, c) }]); });
rect.addEventListener("mouseleave", function () { plot.__tip.hidden = true; });
svg.appendChild(rect);
});
});
xs.forEach(function (x, xi) { if (xi < cols) svg.appendChild(sv("text", { x: padL + xi * cw + cw / 2, y: H - 6, "text-anchor": "middle" }, String(x).slice(0, 5))); });
plot.appendChild(svg);
return h("div", { "class": "g-chart" }, c.title ? h("div", { "class": "g-chart-h" }, h("div", { "class": "g-chart-t" }, txt(c.title))) : null, plot, c.caption ? h("div", { "class": "g-caption" }, txt(c.caption)) : null);
};
/* ---------- table ---------- */
function cellText(v) { if (v && typeof v === "object") return v.label != null ? String(v.label) : v.value != null ? String(v.value) : ""; return v == null ? "" : String(v); }
function cellNum(v) { if (v && typeof v === "object") v = v.value != null ? v.value : v.label; if (typeof v === "number") return v; var n = parseFloat(String(v == null ? "" : v).replace(/[^\d.\-]/g, "")); return isNaN(n) ? null : n; }
function renderCell(v, col) {
var f = col.format || "text";
if (v == null || v === "") return h("span", { style: { color: "var(--faint)" } }, "\u2014");
if (f === "badge") { var tone = v && typeof v === "object" ? v.tone : null; return h("span", { "class": "g-badge" + toneCls(tone) }, cellText(v)); }
if (f === "link" || (v && typeof v === "object" && v.url)) { var u = safeUrl(v && typeof v === "object" ? v.url : v); return u ? h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, cellText(v && typeof v === "object" ? v : u.replace(/^https?:\/\//, "").slice(0, 40))) : cellText(v); }
if (f === "progress") { var n = cellNum(v) || 0; return h("div", { "class": "pb" }, h("div", { "class": "g-bar" }, h("i", { style: { width: Math.max(0, Math.min(100, n)) + "%" } })), h("span", null, Math.round(n) + "%")); }
if (f === "bool") return icon(v === true || v === "true" || v === "yes" ? "check" : "minus", 15);
if (f === "text" || f === "date") return cellText(v);
var num = cellNum(v); return num == null ? cellText(v) : fmt(num, { format: f, currency: col.currency, decimals: col.decimals });
}
R.table = function (c) {
var cols = c.columns, rows = c.rows.map(function (r, i) { return { r: r, i: i }; });
function get(row, col, ci) { return Array.isArray(row.r) ? row.r[+col.key === +col.key && !isNaN(+col.key) ? +col.key : ci] : row.r[col.key]; }
var numeric = cols.map(function (col, ci) { if (col.format && col.format !== "text" && col.format !== "badge" && col.format !== "link" && col.format !== "date") return true; var cnt = 0, num = 0; rows.slice(0, 30).forEach(function (r) { var v = get(r, col, ci); if (v != null && v !== "") { cnt++; if (typeof v === "number") num++; } }); return cnt > 0 && num / cnt > 0.8; });
var sortCol = -1, sortDir = 1, q = "", page = 0, size = c.page_size || 10, filters = {};
var searchable = c.searchable !== false && rows.length > 6;
var tools = h("div", { "class": "g-tbl-tools" });
if (searchable) {
var inp = h("input", { "class": "g-in", type: "search", placeholder: "Search", "aria-label": "Search table" });
inp.addEventListener("input", function () { q = inp.value.toLowerCase(); page = 0; draw(); });
tools.appendChild(h("div", { "class": "g-search" }, icon("search", 14), inp));
}
cols.forEach(function (col, ci) {
if (!col.filterable) return;
var vals = {}; rows.forEach(function (r) { var t = cellText(get(r, col, ci)); if (t) vals[t] = 1; });
var sel = h("select", { "class": "g-in", style: { width: "auto" }, "aria-label": "Filter " + col.label }, h("option", { value: "" }, "All " + col.label.toLowerCase()), Object.keys(vals).sort().slice(0, 60).map(function (v) { return h("option", { value: v }, v); }));
sel.addEventListener("change", function () { filters[ci] = sel.value; page = 0; draw(); });
tools.appendChild(sel);
});
if (c.download !== false) {
EXPORTERS[c.id || (c.id = nid("tbl"))] = function () { return { name: (c.title || "table").replace(/[^\w\-]+/g, "_") + ".csv", mime: "text/csv;charset=utf-8", text: csv(view()) }; };
}
function csv(list) {
var esc = function (s) { s = String(s == null ? "" : s); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
return [cols.map(function (c2) { return esc(c2.label); }).join(",")].concat(list.map(function (r) { return cols.map(function (col, ci) { return esc(cellText(get(r, col, ci))); }).join(","); })).join("\n");
}
var thead = h("tr"), tbody = h("tbody"), info = h("span"), pager = h("div", { "class": "g-pager" });
var sortable = c.sortable !== false;
var ths = cols.map(function (col, ci) {
var al = col.align || (numeric[ci] ? "right" : "left"), cls = al === "right" ? "r" : al === "center" ? "c" : "";
var th = h("th", { "class": cls, scope: "col", "aria-sort": "none" });
if (sortable && col.sortable !== false) {
var b = h("button", { type: "button" }, col.label, icon("arrow-up-down", 12));
b.addEventListener("click", function () { if (sortCol === ci) sortDir = -sortDir; else { sortCol = ci; sortDir = numeric[ci] ? -1 : 1; } draw(); });
th.appendChild(b);
} else th.appendChild(h("span", { "class": "st" }, col.label));
thead.appendChild(th); return th;
});
function view() {
var list = rows.filter(function (r) {
for (var k in filters) if (filters[k] && cellText(get(r, cols[k], +k)) !== filters[k]) return false;
if (!q) return true;
return cols.some(function (col, ci) { return cellText(get(r, col, ci)).toLowerCase().indexOf(q) >= 0; });
});
if (sortCol >= 0) {
var col = cols[sortCol], num = numeric[sortCol];
list = list.slice().sort(function (a, b) {
var x = get(a, col, sortCol), y = get(b, col, sortCol);
if (num) { x = cellNum(x); y = cellNum(y); if (x == null) return 1; if (y == null) return -1; return (x - y) * sortDir; }
return cellText(x).localeCompare(cellText(y), LANG, { numeric: true }) * sortDir;
});
}
return list;
}
function draw() {
var list = view(), pages = Math.max(1, Math.ceil(list.length / size)); page = Math.min(page, pages - 1);
ths.forEach(function (th, ci) { th.setAttribute("aria-sort", ci === sortCol ? (sortDir > 0 ? "ascending" : "descending") : "none"); });
tbody.textContent = "";
if (!list.length) tbody.appendChild(h("tr", null, h("td", { colspan: cols.length, "class": "g-empty" }, c.empty || (q ? "No rows match \u201c" + q + "\u201d" : "No data"))));
list.slice(page * size, page * size + size).forEach(function (r) {
tbody.appendChild(h("tr", null, cols.map(function (col, ci) { var al = col.align || (numeric[ci] ? "right" : "left"); return h("td", { "class": al === "right" ? "r" : al === "center" ? "c" : "" }, renderCell(get(r, col, ci), col)); })));
});
info.textContent = list.length === rows.length ? rows.length + " rows" : list.length + " of " + rows.length + " rows";
pager.textContent = "";
if (pages > 1) {
var prev = h("button", { type: "button", "class": "g-btn icon", "aria-label": "Previous page", disabled: page === 0 }, icon("chevron-left", 15));
var next = h("button", { type: "button", "class": "g-btn icon", "aria-label": "Next page", disabled: page >= pages - 1 }, icon("chevron-right", 15));
prev.addEventListener("click", function () { page--; draw(); }); next.addEventListener("click", function () { page++; draw(); });
pager.appendChild(prev); pager.appendChild(h("span", null, (page + 1) + " / " + pages)); pager.appendChild(next);
}
postHeight();
}
draw();
var dl = null;
if (c.download !== false && rows.length) { dl = h("button", { type: "button", "class": "g-btn sm ghost" }, icon("download", 13), "CSV"); var tid = c.id; dl.addEventListener("click", function () { var ex = EXPORTERS[tid](); download(ex.name, ex.text, ex.mime); }); }
return h("div", null,
c.title ? h("div", { "class": "g-chart-h" }, h("div", { "class": "g-chart-t" }, txt(c.title))) : null,
tools.childNodes.length ? tools : null,
h("div", { "class": "g-tbl-wrap" }, h("table", { "class": "g-tbl" }, h("thead", null, thead), tbody)),
h("div", { "class": "g-tbl-f" }, h("span", null, info, c.caption ? " \u00b7 " + c.caption : ""), h("div", { "class": "g-ctrl" }, pager, dl)));
};
/* ---------- sequences ---------- */
R.timeline = function (c) {
var out = h("div", { "class": "g-tl" }), lastG = null;
c.items.forEach(function (it) {
if (it.group && it.group !== lastG) { out.appendChild(h("div", { "class": "g-tl-g" }, it.group)); lastG = it.group; }
var tm = [it.date, it.time].filter(Boolean).join(" ");
out.appendChild(h("div", { "class": "g-tl-i" },
h("div", { "class": "g-tl-time" }, tm),
h("div", { "class": "g-tl-rail" }, h("div", { "class": "g-tl-dot" + (it.icon ? "" : " plain"), style: it.tone ? { "border-color": "var(--" + ({ success: "ok", warning: "warn", danger: "bad", info: "info", accent: "accent" }[it.tone] || "line") + ")" } : null }, it.icon ? icon(it.icon, 11) : null)),
h("div", { "class": "g-tl-b" }, h("div", { "class": "g-tl-t" }, h("span", { "class": "g-tl-tm" }, tm), txt(it.title || ""), it.tag ? h("span", { "class": "g-badge" + toneCls(it.tone) }, it.tag) : null), it.detail ? h("div", { "class": "g-tl-d" }, md(it.detail)) : null)));
});
return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "10px" } }, txt(c.title)) : null, out);
};
R.steps = function (c) {
var items = c.items, cur = 0, done = {};
var list = h("div", { "class": "g-steps" }), prog = h("div", { "class": "g-bar tone-neutral", role: "progressbar" }, h("i")), lbl = h("span", { "class": "txt-sm txt-muted" });
var back = h("button", { type: "button", "class": "g-btn" }, icon("chevron-left", 15), "Back"), next = h("button", { type: "button", "class": "g-btn primary" }, "Next", icon("chevron-right", 15));
function draw() {
list.textContent = "";
items.forEach(function (it, i) {
var st = c.interactive ? (done[i] ? " done" : i === cur ? " cur" : "") : "";
var b = h("div", { "class": "g-step-b" }, h("div", { "class": "g-step-t" }, txt(it.title || "")), (!c.interactive || i === cur) && it.detail ? h("div", { "class": "g-step-d" }, md(it.detail)) : null);
var row = h("div", { "class": "g-step" + st + (c.interactive ? " btn" : "") }, h("div", { "class": "g-step-rail" }, h("div", { "class": "g-step-n" }, done[i] && c.interactive ? icon("check", 13) : it.icon ? icon(it.icon, 13) : String(i + 1))), b);
if (c.interactive) { b.setAttribute("role", "button"); b.tabIndex = 0; var go = function () { cur = i; draw(); }; b.addEventListener("click", go); b.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } }); }
list.appendChild(row);
});
if (c.interactive) {
var n = Object.keys(done).length; prog.firstChild.style.width = (n / items.length * 100) + "%";
lbl.textContent = n === items.length ? "All steps complete" : "Step " + (cur + 1) + " of " + items.length + " \u00b7 " + n + " done";
back.disabled = cur === 0; next.textContent = ""; put(next, cur === items.length - 1 ? [icon("check", 15), done[cur] ? "Done" : "Finish"] : ["Mark done & next", icon("chevron-right", 15)]);
}
postHeight();
}
back.addEventListener("click", function () { if (cur > 0) { cur--; draw(); } });
next.addEventListener("click", function () { done[cur] = true; if (cur < items.length - 1) cur++; draw(); });
draw();
return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "10px" } }, txt(c.title)) : null,
c.interactive ? h("div", { style: { "margin-bottom": "12px" } }, lbl, prog) : null, list,
c.interactive ? h("div", { "class": "g-actions", style: { "margin-top": "4px" } }, back, next) : null);
};
R.flow = function (c) {
var hz = c.direction === "horizontal", out = h("div", { "class": "g-flow" + (hz ? " h" : "") });
c.items.forEach(function (it, i) {
if (i) out.appendChild(h("div", { "class": "g-flow-a", "aria-hidden": "true" }, icon(hz ? "arrow-right" : "arrow-down", 15)));
out.appendChild(h("div", { "class": "g-flow-i" + (it.tone ? " tone-" + it.tone : "") }, it.icon ? icon(it.icon, 18) : null, h("div", { "class": "g-flow-t" }, txt(it.title || "")), it.detail ? h("div", { "class": "g-flow-d" }, txt(it.detail)) : null));
});
return h("div", { "class": "g-card", style: { padding: "12px" } }, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "8px" } }, txt(c.title)) : null, out);
};
R.status = function (c) {
var ICO = { ok: "circle-check", warn: "triangle-alert", down: "circle-x", info: "info", unknown: "circle-help" };
var LBL = { ok: "operational", warn: "degraded", down: "down", info: "info", unknown: "unknown" };
var counts = {}; c.items.forEach(function (it) { var s = it.state || "unknown"; counts[s] = (counts[s] || 0) + 1; });
var head = h("div", { "class": "g-status-h" }, ["ok", "warn", "down", "info", "unknown"].filter(function (s) { return counts[s]; }).map(function (s) { return h("span", { "class": "g-badge" + toneCls({ ok: "success", warn: "warning", down: "danger", info: "info" }[s]) }, icon(ICO[s], 12), counts[s] + " " + LBL[s]); }));
return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "8px" } }, txt(c.title)) : null, head,
c.items.map(function (it) {
var s = it.state || "unknown", v = h("span", { "class": "g-status-v" });
if (it.value != null) watch(function () { var r; try { r = val(it.value); } catch (e) { r = null; } v.textContent = fmt(r); });
return h("div", { "class": "g-status-r" }, h("span", { "class": "st-" + s, title: LBL[s] }, icon(it.icon || ICO[s], 17)), h("span", { "class": "sr" }, LBL[s]), h("div", { style: { "min-width": "0" } }, h("div", { "class": "g-status-n" }, txt(it.name || "")), it.detail ? h("div", { "class": "g-status-d" }, txt(it.detail)) : null), v);
}));
};
/* ---------- selectable list (explorer) ---------- */
R.list = function (c) {
var items = c.items, selectable = c.selectable !== false, bind = c.bind, local = { v: items[0].id };
var hasDetail = items.some(function (it) { return it.detail; });
function cur() { return bind ? state[bind] : local.v; }
var detail = h("div", { "class": "g-detail", "aria-live": "polite" });
var btns = items.map(function (it, i) {
var sel = h("span", { "class": "sel" });
var b = h(selectable ? "button" : "div", { type: selectable ? "button" : null, "class": "g-li" + (selectable ? "" : " static"), role: selectable ? "option" : null },
it.icon ? h("span", { "class": "g-li-ic" }, icon(it.icon, 18)) : null,
h("span", { "class": "g-li-b" }, h("div", { "class": "g-li-t" }, txt(it.title)), it.subtitle ? h("div", { "class": "g-li-s" }, txt(it.subtitle)) : null),
h("span", { "class": "g-li-e" }, badge(it.badge), selectable ? sel : null));
if (selectable) {
b.addEventListener("click", function () { if (bind) setVar(bind, it.id); else { local.v = it.id; sync(); } });
b.addEventListener("keydown", function (e) { var d = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0; if (d) { e.preventDefault(); var n = btns[(i + d + btns.length) % btns.length]; n.focus(); n.click(); } });
}
b.__sel = sel; return b;
});
function sync() {
var v = cur(), it = null;
btns.forEach(function (b, i) {
var on = items[i].id === v; if (on) it = items[i];
if (!selectable) return;
b.setAttribute("aria-selected", on ? "true" : "false"); b.__sel.textContent = ""; b.__sel.appendChild(icon(on ? "circle-check" : "chevron-right", 16));
});
detail.textContent = "";
var d = it && it.detail;
detail.hidden = !d;
if (d) {
put(detail, [h("div", { "class": "g-detail-h" }, h("div", { "class": "g-detail-t" }, txt(d.title || it.title)), badge(d.badge)), d.md ? md(d.md) : null,
d.highlight ? h("div", { "class": "g-detail-hl" }, icon(d.highlight_icon || "award", 15), txt(d.highlight)) : null,
d.tags ? h("div", { "class": "g-tags" }, d.tags.map(function (t) { return h("span", { "class": "g-tag" }, t); })) : null,
d.button ? button(Object.assign({ full: true }, d.button)) : null]);
}
postHeight();
}
if (selectable) { if (bind) watch(sync); else sync(); }
return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "10px" } }, txt(c.title)) : null,
h("div", { "class": "g-list", role: selectable ? "listbox" : "list", "aria-label": c.title || "Options" }, btns), selectable && hasDetail ? detail : null);
};
/* ---------- checklist ---------- */
R.checklist = function (c) {
var items = c.items, on = {}, cnt = h("span", { "class": "g-check-c" }), totalL = h("span"), totalV = h("b");
items.forEach(function (it, i) { if (it.checked) on[i] = true; });
var fo = { format: c.format || "number", currency: c.currency, decimals: c.decimals };
var hasVal = items.some(function (it) { return it.value != null; });
function rng(list) { var lo = 0, hi = 0, any = false; list.forEach(function (it) { var v = it.value; if (v == null) return; any = true; if (Array.isArray(v)) { lo += v[0]; hi += v[1]; } else { lo += v; hi += v; } }); return any ? [lo, hi] : null; }
function fr(r) { return !r ? "\u2014" : r[0] === r[1] ? fmtU(r[0], Object.assign({ unit: c.unit }, fo)) : fmt(r[0], fo) + "\u2013" + fmtU(r[1], Object.assign({ unit: c.unit }, fo)).replace(/^[^\d\-]+/, function (p) { return /^[A-Z]{3}$/.test(c.currency || "") ? p : ""; }); }
var rowsEl = items.map(function (it, i) {
var b = h("button", { type: "button", "class": "g-ci", role: "checkbox" }, h("span", { "class": "g-ci-box" }), h("span", null, h("div", { "class": "g-ci-l" }, txt(it.label)), it.detail ? h("div", { "class": "g-ci-d" }, txt(it.detail)) : null));
b.addEventListener("click", function () { on[i] = !on[i]; sync(); });
return b;
});
function selected() { return items.filter(function (it, i) { return on[i]; }); }
function sync() {
rowsEl.forEach(function (b, i) { b.setAttribute("aria-checked", on[i] ? "true" : "false"); var box = b.firstChild; box.textContent = ""; if (on[i]) box.appendChild(icon("check", 13)); });
var sel = selected();
cnt.textContent = sel.length + " of " + items.length + " selected";
if (hasVal) { totalL.textContent = sel.length ? (c.total_label ? c.total_label.replace(/all items/i, "selected items") : "Total, selected items") : (c.total_label || "Total, all items"); totalV.textContent = fr(rng(sel.length ? sel : items)); }
if (c.bind) { setVar(c.bind, sel.length); var r = rng(sel); setVar(c.bind + "_total", r ? r[1] : 0); }
postHeight();
}
var copyBtn = null;
if (c.copy !== false) {
copyBtn = h("button", { type: "button", "class": "g-btn full" }, icon("copy", 15), c.copy_label || "Copy selected list");
copyBtn.addEventListener("click", function () {
var list = selected().length ? selected() : items;
copyText((c.title ? c.title + "\n" : "") + list.map(function (it) { return "- " + it.label + (it.detail ? " (" + it.detail + ")" : ""); }).join("\n"), copyBtn);
});
}
sync();
return h("div", { "class": "g-check" }, h("div", { "class": "g-check-h" }, h("div", { "class": "g-check-t" }, txt(c.title || "Checklist")), cnt), h("div", null, rowsEl),
hasVal ? h("div", { "class": "g-check-f" }, totalL, totalV) : null, copyBtn);
};
/* ---------- compare / products ---------- */
function stars(r, n) { return h("span", { "class": "g-stars", "aria-label": "Rated " + r + " out of 5" }, icon("star", 13), h("b", { style: { color: "var(--text)" } }, (Math.round(r * 10) / 10).toFixed(1)), n ? "(" + fmt(n, { format: "compact" }) + ")" : null); }
function img(src, alt, cls) {
var fig = h("div", { "class": "g-img loading " + (cls || "") });
if (!src) { fig.classList.remove("loading"); fig.appendChild(h("div", { "class": "g-ph" }, icon("image", 22), "No image")); return fig; }
var im = h("img", { src: src, alt: alt || "", loading: "lazy", decoding: "async", referrerpolicy: "no-referrer" });
im.addEventListener("load", function () { fig.classList.remove("loading"); postHeight(); });
im.addEventListener("error", function () { fig.classList.remove("loading"); im.remove(); fig.appendChild(h("div", { "class": "g-ph" }, icon("image", 22), "Image unavailable")); });
fig.appendChild(im); return fig;
}
R.compare = function (c) {
var anyImg = c.items.some(function (p) { return p.image; });
var cards = c.items.map(function (p) {
var price = h("div", { "class": "g-prod-p" });
if (p.price != null) watch(function () { var r; try { r = val(p.price); } catch (e) { r = p.price; } price.textContent = typeof r === "number" ? fmtU(r, Object.assign({ format: p.format || "money" }, p)) : String(r); });
return h("div", { "class": "g-prod" + (p.highlight ? " hl" : "") },
anyImg ? h("div", { "class": "g-prod-img" }, img(p.image, p.title, "")) : null,
h("div", { "class": "g-prod-b" },
p.badge ? h("div", null, badge(p.badge)) : null,
h("div", { style: { "min-width": "0" } }, h("div", { "class": "g-prod-t" }, p.icon ? icon(p.icon, 15) : null, " ", txt(p.title || "")), p.subtitle ? h("div", { "class": "g-prod-s" }, txt(p.subtitle)) : null),
p.price != null || p.rating != null ? h("div", { style: { display: "flex", "justify-content": "space-between", "align-items": "center", gap: "8px" } }, p.price != null ? price : h("span"), p.rating != null ? stars(p.rating, p.reviews) : null) : null,
p.md ? md(p.md, "txt-sm") : null,
p.pros || p.cons ? h("ul", { "class": "g-pc" }, (p.pros || []).map(function (x) { return h("li", { "class": "pro" }, icon("check", 13), h("span", null, x)); }), (p.cons || []).map(function (x) { return h("li", { "class": "con" }, icon("x", 13), h("span", null, x)); })) : null,
p.specs && !c.spec_table ? h("div", null, p.specs.map(function (kv) { return h("div", { "class": "g-kv-r" }, h("span", { "class": "g-kv-l" }, kv[0]), h("span", { "class": "g-kv-v" }, kv[1])); })) : null,
p.url ? h("div", { "class": "g-prod-f" }, button({ label: p.url_label || "View details", action: { type: "link", url: p.url }, full: true })) : null));
});
var grid = h("div", { "class": "g-cmp", style: { "--cols": c.columns || Math.min(3, c.items.length) } }, cards);
var table = null;
if (c.spec_table && c.items.some(function (p) { return p.specs; })) {
var keys = []; c.items.forEach(function (p) { (p.specs || []).forEach(function (kv) { if (keys.indexOf(kv[0]) < 0) keys.push(kv[0]); }); });
var rows = keys.map(function (k) { var r = { spec: k }; c.items.forEach(function (p, i) { var hit = (p.specs || []).filter(function (kv) { return kv[0] === k; })[0]; r["p" + i] = hit ? hit[1] : null; }); return r; });
table = R.table({ columns: [{ key: "spec", label: "Specification" }].concat(c.items.map(function (p, i) { return { key: "p" + i, label: p.title || "Option " + (i + 1) }; })), rows: rows, searchable: false, sortable: false, page_size: 50, download: true, title: "Specifications" });
}
return h("div", { "class": "g-stack" }, c.title ? h("div", { "class": "g-card-t" }, txt(c.title)) : null, grid, table);
};
/* ---------- media ---------- */
R.image = function (c) {
var f = img(c.src, c.alt || c.caption, c.ratio === "auto" ? "" : (c.ratio || "wide"));
var link = safeUrl(c.link);
return h("figure", { style: { margin: "0" } }, link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, f) : f,
c.caption || c.credit ? h("figcaption", { style: { "font-size": "12.5px", color: "var(--muted)", padding: "6px 2px 0" } }, c.caption || "", c.credit ? h("span", { style: { color: "var(--faint)", "font-size": "11px" } }, (c.caption ? " \u00b7 " : "") + c.credit) : null) : null);
};
R.gallery = function (c) {
var ims = c.images, car = ims.length > 1 && (c.layout === "carousel" || (c.layout !== "grid" && ims.length > 4));
var figs = ims.map(function (im) { return R.image({ src: im.src, caption: im.caption, credit: im.credit, link: im.link, alt: im.alt, ratio: "auto" }); });
var title2 = c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "8px" } }, txt(c.title)) : null;
if (!car) return h("div", null, title2, h("div", { "class": "g-gal" }, figs));
var track = h("div", { "class": "g-car-track", tabindex: "0", "aria-label": "Image carousel" }, figs), idx = 0;
var dots = h("div", { "class": "g-dots" }, ims.map(function (_, i) { var d = h("button", { type: "button", "aria-label": "Image " + (i + 1) }); d.addEventListener("click", function () { go(i); }); return d; }));
function mark() { Array.prototype.forEach.call(dots.children, function (d, i) { d.setAttribute("aria-current", i === idx ? "true" : "false"); }); }
function go(i) { idx = (i + ims.length) % ims.length; track.scrollTo({ left: track.clientWidth * idx + 10 * idx, behavior: "smooth" }); mark(); }
track.addEventListener("scroll", function () { var i = Math.round(track.scrollLeft / (track.clientWidth + 10)); if (i !== idx) { idx = i; mark(); } });
track.addEventListener("keydown", function (e) { if (e.key === "ArrowRight") { e.preventDefault(); go(idx + 1); } if (e.key === "ArrowLeft") { e.preventDefault(); go(idx - 1); } });
var l = h("button", { type: "button", "class": "g-car-nav l", "aria-label": "Previous image" }, icon("chevron-left", 18)), r = h("button", { type: "button", "class": "g-car-nav r", "aria-label": "Next image" }, icon("chevron-right", 18));
l.addEventListener("click", function () { go(idx - 1); }); r.addEventListener("click", function () { go(idx + 1); });
mark();
return h("div", null, title2, h("div", { "class": "g-car" }, track, l, r), dots);
};
R.map = function (c) {
var pts = c.places.filter(function (p) { return p.lat != null && p.lng != null; });
var el = h("div", { "class": "g-stack", style: { gap: "0" } });
if (c.title) el.appendChild(h("div", { "class": "g-card-t", style: { "margin-bottom": "8px" } }, icon("map", 16), txt(c.title)));
if (pts.length && CFG.maps !== false) {
var la = pts.map(function (p) { return p.lat; }), ln = pts.map(function (p) { return p.lng; });
var s = Math.min.apply(null, la), n = Math.max.apply(null, la), w = Math.min.apply(null, ln), e = Math.max.apply(null, ln);
var pad = Math.max(0.004, (n - s) * 0.2, (e - w) * 0.2);
var bbox = [w - pad, s - pad, e + pad, n + pad].map(function (x) { return x.toFixed(5); }).join(",");
var mk = pts.length === 1 ? "&marker=" + pts[0].lat.toFixed(5) + "," + pts[0].lng.toFixed(5) : "";
el.appendChild(h("iframe", { "class": "g-map-f", title: "Map of " + (c.title || "places"), src: "https://www.openstreetmap.org/export/embed.html?bbox=" + bbox + "&layer=mapnik" + mk, style: { height: (c.height || 300) + "px" }, loading: "lazy", referrerpolicy: "no-referrer", sandbox: "allow-scripts allow-same-origin allow-popups" }));
}
var stops = h("div", { "class": "g-stops" }, c.places.map(function (p, i) {
var q = p.lat != null ? p.lat + "," + p.lng : encodeURIComponent(p.name || "");
var href = safeUrl(p.url) || (p.lat != null ? "https://www.openstreetmap.org/?mlat=" + p.lat + "&mlon=" + p.lng + "#map=16/" + p.lat + "/" + p.lng : "https://www.openstreetmap.org/search?query=" + q);
return h("div", { "class": "g-stop" }, h("span", { "class": "g-stop-n" }, String(i + 1)), h("div", { "class": "g-stop-b" }, h("div", { "class": "g-stop-t" }, p.icon ? icon(p.icon, 14) : null, " ", txt(p.name || ""), p.time ? h("span", { "class": "g-badge", style: { "margin-left": "6px" } }, p.time) : null), p.note ? h("div", { "class": "g-stop-d" }, txt(p.note)) : null),
h("a", { "class": "g-btn icon", href: href, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open " + (p.name || "place") + " in OpenStreetMap" }, icon("external-link", 14)));
}));
el.appendChild(stops);
if (c.places.length >= 2) {
var wp = c.places.map(function (p) { return p.lat != null ? p.lat + "," + p.lng : encodeURIComponent(p.name || ""); });
var mode = c.mode || "walking";
var g = "https://www.google.com/maps/dir/?api=1&origin=" + wp[0] + "&destination=" + wp[wp.length - 1] + (wp.length > 2 ? "&waypoints=" + wp.slice(1, -1).slice(0, 8).join("%7C") : "") + "&travelmode=" + mode;
el.appendChild(h("div", { "class": "g-actions", style: { "margin-top": "10px" } }, button({ label: "Directions", icon: "route", action: { type: "link", url: g } })));
}
if (c.caption || c.places.some(function (p) { return p.approx; })) el.appendChild(h("div", { "class": "g-caption" }, c.caption || "Some locations are approximate (city-level geocoding)."));
return el;
};
R.weather = function (c) {
if (c.error || !c.data) return h("div", { "class": "g-callout tone-warning" }, icon("cloud", 17), h("div", null, h("div", { "class": "g-callout-t" }, "Weather unavailable"), c.error || "No data."));
var d = c.data, cur = d.current || {}, tu = d.temp_unit || "";
var hourly = d.hourly || {}, temps = hourly.temp || [];
var lo = Math.min.apply(null, d.daily.map(function (x) { return x.min; }).filter(function (x) { return x != null; })), hi = Math.max.apply(null, d.daily.map(function (x) { return x.max; }).filter(function (x) { return x != null; }));
var days = h("div", { "class": "g-wx-days" }, d.daily.map(function (x, i) {
var dt = new Date(x.date + "T12:00:00"), name = i === 0 ? "Today" : dt.toLocaleDateString(LANG, { weekday: "short" });
var a = ((x.min - lo) / ((hi - lo) || 1)) * 100, b = ((x.max - lo) / ((hi - lo) || 1)) * 100;
return h("div", { "class": "g-wx-d" }, h("span", null, name), h("span", { title: x.label }, icon(x.icon, 18)), h("span", { "class": "lab" }, x.label + (x.rain ? " \u00b7 " + Math.round(x.rain) + "%" : "")), h("span", { "class": "lo" }, Math.round(x.min) + "\u00b0"), h("div", { "class": "g-wx-rng" }, h("i", { style: { left: a + "%", right: (100 - b) + "%" } })), h("span", { "class": "hi" }, Math.round(x.max) + "\u00b0"));
}));
var chart = temps.length > 2 ? R.chart({ kind: "line", labels: hourly.time, series: [{ name: "Temperature", data: temps }], unit: tu, height: 150, table_view: true, title: "Next 24 hours" }) : null;
return h("div", { "class": "g-card" },
h("div", { "class": "g-card-h" }, h("div", null, h("div", { "class": "g-card-t" }, icon("map-pin", 15), d.place), h("div", { "class": "g-sub" }, cur.label)), d.sample ? h("span", { "class": "g-badge t-warning" }, icon("info", 12), "Sample data") : h("span", { "class": "g-badge t-success" }, icon("circle-check", 12), "Live")),
h("div", { "class": "g-wx-now" }, h("span", { "class": "g-wx-ic" }, icon(cur.icon || "cloud", 46)), h("span", { "class": "g-wx-temp" }, cur.temp != null ? Math.round(cur.temp) + tu : "\u2014"),
h("div", { "class": "g-wx-meta" }, cur.feels != null ? h("span", null, icon("thermometer", 14), "Feels " + Math.round(cur.feels) + "\u00b0") : null, cur.humidity != null ? h("span", null, icon("droplets", 14), Math.round(cur.humidity) + "%") : null, cur.wind != null ? h("span", null, icon("wind", 14), Math.round(cur.wind) + " " + d.wind_unit) : null)),
chart ? h("div", { style: { "margin-top": "12px" } }, chart) : null,
h("div", { style: { "margin-top": "10px" } }, days),
h("div", { "class": "g-caption" }, d.sample ? "Sample data shaped like " : "Live data from ", h("a", { href: d.source.url, target: "_blank", rel: "noopener noreferrer" }, d.source.title), d.sample ? "" : " \u00b7 fetched " + d.as_of));
};
R.app = function (c) {
var csp = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:\">";
var base = "<meta charset=\"utf-8\"><style>body{font-family:Inter,system-ui,sans-serif;margin:12px;color:#111}</style>";
var html = String(c.html);
html = /<head[^>]*>/i.test(html) ? html.replace(/<head[^>]*>/i, function (m) { return m + csp + base; }) : csp + base + html;
var f = h("iframe", { "class": "g-app", sandbox: "allow-scripts", referrerpolicy: "no-referrer", title: c.title || "Interactive app", style: { height: (c.height || 360) + "px" } });
f.srcdoc = html;
window.addEventListener("message", function (e) { if (e.source === f.contentWindow && e.data && e.data.type === "app:height" && typeof e.data.height === "number") { f.style.height = Math.max(80, Math.min(1400, e.data.height)) + "px"; postHeight(); } });
return h("div", null, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "8px" } }, txt(c.title)) : null, f, h("div", { "class": "g-caption" }, c.caption || "Custom app \u00b7 runs in an isolated sandbox without network access"));
};
/* ---------- inputs ---------- */
function field(label, valueEl, control, extra) { return h("div", { "class": "g-field" }, label || valueEl ? h("div", { "class": "g-field-h" }, h("label", { "class": "g-field-l" }, txt(label || "")), valueEl) : null, control, extra); }
R.slider = function (c) {
var id = nid("in"), v = h("span", { "class": "g-field-v" });
var r = h("input", { id: id, type: "range", "class": "g-range", min: c.min, max: c.max, step: c.step, "aria-label": c.label || c.bind });
r.addEventListener("input", function () { setVar(c.bind, +r.value); });
watch(function () { var x = +state[c.bind]; if (+r.value !== x) r.value = x; r.style.setProperty("--p", ((x - c.min) / ((c.max - c.min) || 1) * 100) + "%"); v.textContent = fmtU(x, c); r.setAttribute("aria-valuetext", fmtU(x, c)); });
var el = field(c.label, v, r, h("div", { "class": "g-range-m" }, h("span", null, fmtU(c.min, c)), h("span", null, fmtU(c.max, c))));
el.querySelector("label").setAttribute("for", id); return el;
};
R.number = function (c) {
var id = nid("in"), err = h("div", { "class": "g-field-e", role: "alert" });
var inp = h("input", { id: id, type: "number", "class": "g-in", min: c.min, max: c.max, step: c.step || "any", placeholder: c.placeholder || "", inputmode: "decimal" });
inp.addEventListener("input", function () {
var x = inp.value === "" ? NaN : +inp.value;
var bad = isNaN(x) ? "Enter a number" : (c.min != null && x < c.min) || (c.max != null && x > c.max) ? "Enter a value between " + fmt(c.min, c) + " and " + fmt(c.max, c) : "";
err.textContent = bad; inp.classList.toggle("bad", !!bad); inp.setAttribute("aria-invalid", bad ? "true" : "false");
if (!bad) setVar(c.bind, x);
});
watch(function () { if (D.activeElement !== inp && +inp.value !== +state[c.bind]) { inp.value = state[c.bind]; err.textContent = ""; inp.classList.remove("bad"); } });
var el = field(c.label, c.unit ? h("span", { "class": "g-field-l" }, c.unit) : null, inp, err); el.querySelector("label").setAttribute("for", id); return el;
};
R.select = function (c) {
var id = nid("in"), sel = h("select", { id: id, "class": "g-in" }, c.options.map(function (o, i) { return h("option", { value: String(i) }, o.label); }));
sel.addEventListener("change", function () { setVar(c.bind, c.options[+sel.value].value); });
watch(function () { var i = c.options.map(function (o) { return o.value; }).indexOf(state[c.bind]); if (i >= 0) sel.value = String(i); });
var el = field(c.label, null, sel); if (c.label) el.querySelector("label").setAttribute("for", id); return el;
};
R.segmented = function (c) {
var bar = h("div", { "class": "g-seg" + (c.full ? " full" : ""), role: "radiogroup", "aria-label": c.label || c.bind });
var btns = c.options.map(function (o, i) {
var b = h("button", { type: "button", role: "radio" }, o.icon ? icon(o.icon, 14) : null, o.label);
b.addEventListener("click", function () { setVar(c.bind, o.value); });
b.addEventListener("keydown", function (e) { var d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0; if (d) { e.preventDefault(); var n = btns[(i + d + btns.length) % btns.length]; n.focus(); n.click(); } });
bar.appendChild(b); return b;
});
watch(function () { btns.forEach(function (b, i) { var on = c.options[i].value === state[c.bind]; b.setAttribute("aria-checked", on ? "true" : "false"); b.setAttribute("aria-pressed", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; }); });
return c.label ? field(c.label, null, bar) : bar;
};
R.toggle = function (c) {
var sw = h("button", { type: "button", role: "switch", "class": "g-sw", "aria-label": c.label || c.bind });
sw.addEventListener("click", function () { setVar(c.bind, !state[c.bind]); });
watch(function () { sw.setAttribute("aria-checked", state[c.bind] ? "true" : "false"); });
return h("div", { "class": "g-tog" }, h("div", null, h("div", null, txt(c.label || "")), c.detail ? h("div", { "class": "g-tog-d" }, txt(c.detail)) : null), sw);
};
R.text_input = function (c) {
var id = nid("in"), inp = h(c.multiline ? "textarea" : "input", { id: id, "class": "g-in", placeholder: c.placeholder || "" });
inp.value = state[c.bind] == null ? "" : state[c.bind];
inp.addEventListener("input", function () { setVar(c.bind, inp.value); });
watch(function () { if (D.activeElement !== inp && inp.value !== String(state[c.bind] == null ? "" : state[c.bind])) inp.value = state[c.bind]; });
var el = field(c.label, null, inp); if (c.label) el.querySelector("label").setAttribute("for", id); return el;
};
R.date = function (c) {
var id = nid("in"), inp = h("input", { id: id, type: "date", "class": "g-in", min: c.min, max: c.max });
inp.addEventListener("change", function () { setVar(c.bind, inp.value); });
watch(function () { if (inp.value !== state[c.bind]) inp.value = state[c.bind] || ""; });
var el = field(c.label, null, inp); if (c.label) el.querySelector("label").setAttribute("for", id); return el;
};
R.button = function (c) { return button(c); };
R.actions = function (c) { return h("div", { "class": "g-actions " + (c.align || "") }, c.items.map(button)); };
/* ---------- diagram (layered auto layout) ---------- */
R.diagram = function (c) {
var holder = h("div", { "class": "g-diag" }), detail = h("div", { "class": "g-detail", hidden: true, "aria-live": "polite" }), lastW = 0, sel = null;
var TONE = { info: ["var(--info-bg)", "var(--info-line)", "var(--info)"], success: ["var(--ok-bg)", "var(--ok-line)", "var(--ok)"], warning: ["var(--warn-bg)", "var(--warn-line)", "var(--warn)"], danger: ["var(--bad-bg)", "var(--bad-line)", "var(--bad)"], accent: ["var(--acc-bg)", "var(--acc-line)", "var(--acc-ink)"] };
var ns = c.nodes, es = c.edges || [], byId = {};
ns.forEach(function (n, i) { byId[n.id] = { n: n, i: i, layer: 0, preds: [], succ: [] }; });
es.forEach(function (e) { if (e.from !== e.to) { byId[e.to].preds.push(e.from); byId[e.from].succ.push(e.to); } });
/* longest-path layering with cycle guard */
var indeg = {}, order = [], q = [];
ns.forEach(function (n) { indeg[n.id] = byId[n.id].preds.length; if (!indeg[n.id]) q.push(n.id); });
while (q.length) { var id = q.shift(); order.push(id); byId[id].succ.forEach(function (s) { byId[s].layer = Math.max(byId[s].layer, byId[id].layer + 1); if (--indeg[s] === 0) q.push(s); }); }
if (order.length < ns.length) { var maxL = 0; order.forEach(function (o) { maxL = Math.max(maxL, byId[o].layer); }); ns.forEach(function (n) { if (order.indexOf(n.id) < 0) { byId[n.id].layer = ++maxL; order.push(n.id); } }); }
var layers = []; ns.forEach(function (n) { var L = byId[n.id].layer; (layers[L] = layers[L] || []).push(n.id); });
layers = layers.filter(Boolean);
for (var pass = 0; pass < 2; pass++) layers.forEach(function (L, li) { if (!li) return; var pos = {}; layers[li - 1].forEach(function (id2, k) { pos[id2] = k; }); L.sort(function (a, b) { var f = function (x) { var p = byId[x].preds.filter(function (y) { return y in pos; }); return p.length ? p.reduce(function (s, y) { return s + pos[y]; }, 0) / p.length : byId[x].i; }; return f(a) - f(b); }); });
function draw() {
var W = holder.clientWidth || 600; if (W === lastW) return; lastW = W; holder.textContent = "";
var fitLR = (W - 44 * (layers.length - 1)) / layers.length;
var dir = c.direction === "TB" ? "TB" : fitLR >= 132 ? "LR" : c.direction === "LR" && fitLR >= 112 ? "LR" : "TB";
var nw = dir === "LR" ? Math.max(118, Math.min(170, (W - 40) / layers.length - 40)) : 168, nh = 54, gx = dir === "LR" ? 44 : 18, gy = dir === "LR" ? 16 : 40;
var maxPer = Math.max.apply(null, layers.map(function (l) { return l.length; }));
var totalW = dir === "LR" ? layers.length * nw + (layers.length - 1) * gx : maxPer * nw + (maxPer - 1) * gx;
var totalH = dir === "LR" ? maxPer * nh + (maxPer - 1) * gy : layers.length * nh + (layers.length - 1) * gy;
var P = {};
layers.forEach(function (L, li) {
var span = L.length * (dir === "LR" ? nh : nw) + (L.length - 1) * (dir === "LR" ? gy : gx), off = ((dir === "LR" ? totalH : totalW) - span) / 2;
L.forEach(function (id3, k) { P[id3] = dir === "LR" ? { x: li * (nw + gx), y: off + k * (nh + gy) } : { x: off + k * (nw + gx), y: li * (nh + gy) }; });
});
var mid = nid("ah");
var svg = sv("svg", { "class": "g-svg", viewBox: "-4 -4 " + (totalW + 8) + " " + (totalH + 8), style: "max-width:" + (totalW + 8) + "px;margin:0 auto", role: "img", "aria-label": (c.title || "Diagram") + ": " + es.map(function (e) { return byId[e.from].n.label + " to " + byId[e.to].n.label; }).join("; ") },
sv("defs", null, sv("marker", { id: mid, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, sv("path", { d: "M0 0L10 5L0 10z", fill: "var(--faint)" }))));
es.forEach(function (e) {
var a = P[e.from], b = P[e.to]; if (!a || !b) return;
var x1, y1, x2, y2, d;
if (dir === "LR") { x1 = a.x + nw; y1 = a.y + nh / 2; x2 = b.x - 2; y2 = b.y + nh / 2; if (x2 <= x1) { x1 = a.x + nw / 2; y1 = a.y + nh; x2 = b.x + nw / 2; y2 = b.y - 2; d = "M" + x1 + " " + y1 + "C" + x1 + " " + (y1 + 30) + " " + x2 + " " + (y2 - 30) + " " + x2 + " " + y2; } else { var mx = (x1 + x2) / 2; d = "M" + x1 + " " + y1 + "C" + mx + " " + y1 + " " + mx + " " + y2 + " " + x2 + " " + y2; } }
else { x1 = a.x + nw / 2; y1 = a.y + nh; x2 = b.x + nw / 2; y2 = b.y - 2; if (y2 <= y1) { x1 = a.x + nw; y1 = a.y + nh / 2; x2 = b.x + nw; y2 = b.y + nh / 2; d = "M" + x1 + " " + y1 + "C" + (x1 + 40) + " " + y1 + " " + (x2 + 40) + " " + y2 + " " + x2 + " " + y2; } else { var my = (y1 + y2) / 2; d = "M" + x1 + " " + y1 + "C" + x1 + " " + my + " " + x2 + " " + my + " " + x2 + " " + y2; } }
svg.appendChild(sv("path", { d: d, fill: "none", stroke: "var(--faint)", "stroke-width": 1.5, "stroke-dasharray": e.dashed ? "5 4" : null, "marker-end": "url(#" + mid + ")" }));
if (e.label) { var lx = (x1 + x2) / 2, ly = (y1 + y2) / 2, tw = e.label.length * 6 + 10; svg.appendChild(sv("rect", { x: lx - tw / 2, y: ly - 9, width: tw, height: 18, rx: 9, fill: "var(--card)", stroke: "var(--line)" })); svg.appendChild(sv("text", { x: lx, y: ly + 4, "text-anchor": "middle", style: "font-size:10.5px" }, e.label)); }
});
ns.forEach(function (n) {
var p = P[n.id], t = TONE[n.tone] || ["var(--card)", "var(--line)", "var(--text)"];
var g = sv("g", { "class": "nd", tabindex: n.detail ? "0" : null, role: n.detail ? "button" : null, "aria-label": n.label + (n.sublabel ? ", " + n.sublabel : "") });
g.appendChild(sv("rect", { x: p.x, y: p.y, width: nw, height: nh, rx: 12, fill: t[0], stroke: sel === n.id ? "var(--text)" : t[1], "stroke-width": sel === n.id ? 2 : 1 }));
var tx = p.x + 12;
if (n.icon && ICONS[n.icon]) { var ic = icon(n.icon, 18); attrs(ic, { x: p.x + 11, y: p.y + nh / 2 - 9, color: t[2] }); ic.style.color = t[2]; g.appendChild(ic); tx = p.x + 36; }
var maxc = Math.floor((nw - (tx - p.x) - 8) / 6.6);
var cut = function (s, m) { s = String(s || ""); return s.length > m ? s.slice(0, m - 1) + "\u2026" : s; };
g.appendChild(sv("text", { x: tx, y: p.y + (n.sublabel ? 23 : 31), style: "font-size:12.5px;font-weight:600;fill:var(--text)" }, cut(n.label, maxc)));
if (n.sublabel) g.appendChild(sv("text", { x: tx, y: p.y + 39, style: "font-size:11px;fill:var(--muted)" }, cut(n.sublabel, maxc + 2)));
g.appendChild(sv("title", null, n.label + (n.sublabel ? " \u2014 " + n.sublabel : "")));
if (n.detail) { var open = function () { sel = sel === n.id ? null : n.id; lastW = 0; draw(); showDetail(); }; g.addEventListener("click", open); g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } }); }
svg.appendChild(g);
});
holder.appendChild(svg); postHeight();
}
function showDetail() { detail.textContent = ""; var n = sel && byId[sel].n; detail.hidden = !n; if (n) put(detail, [h("div", { "class": "g-detail-t" }, n.icon ? icon(n.icon, 15) : null, " ", n.label), md(n.detail)]); postHeight(); }
afterMount(draw);
if (window.ResizeObserver) new ResizeObserver(function () { draw(); }).observe(holder);
var hasDetail = ns.some(function (n) { return n.detail; });
return h("div", { "class": "g-card" }, c.title ? h("div", { "class": "g-card-t", style: { "margin-bottom": "10px" } }, icon("workflow", 15), txt(c.title)) : null, holder, detail,
hasDetail || c.caption ? h("div", { "class": "g-caption" }, c.caption || "Select a box to see details.") : null);
};
/* ---------- page ---------- */
var mountQ = [];
function afterMount(fn) { mountQ.push(fn); }
var lastH = 0;
function postHeight() {
var hgt = Math.ceil(D.documentElement.getBoundingClientRect().height);
if (Math.abs(hgt - lastH) < 1) return; lastH = hgt;
try { window.parent.postMessage({ type: "iframe:height", height: hgt }, "*"); } catch (e) { /* not embedded */ }
}
function footer() {
var f = h("div", { "class": "g-foot" });
var ds = SPEC.data_source;
if (ds) {
var DS = { live: ["circle-check", "Live data"], cached: ["rotate-ccw", "Cached data"], user: ["info", "Based on your data"], demo: ["info", "Illustrative demo data, not live"], static: ["info", "Reference data"], estimate: ["info", "Estimates"] }[ds.kind] || ["info", ""];
f.appendChild(h("div", { "class": "g-ds" }, icon(DS[0], 13), [ds.label || DS[1], ds.as_of ? " \u00b7 " + ds.as_of : ""].join("")));
}
if (SPEC.footnote) f.appendChild(h("div", { "class": "g-caption", style: { "margin-top": "0" } }, txt(SPEC.footnote)));
if (SPEC.sources && SPEC.sources.length) f.appendChild(h("div", { "class": "g-src" }, SPEC.sources.map(function (s, i) {
var host = ""; try { host = new URL(s.url).hostname.replace(/^www\./, ""); } catch (e) { host = ""; }
return h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", title: s.title }, h("b", null, String(i + 1)), s.title !== host ? s.title : host);
})));
if (SPEC.followups && SPEC.followups.length) f.appendChild(h("div", { "class": "g-fu", role: "group", "aria-label": "Suggested follow-ups" }, SPEC.followups.map(function (t) {
var b = h("button", { type: "button" }, icon("message-circle", 14), t); b.addEventListener("click", function () { sendPrompt(t); }); return b;
})));
return f.childNodes.length ? f : null;
}
function render() {
var frame = SPEC.frame === "plain" ? "g-plain" : "g-frame";
var head = SPEC.title || SPEC.badge ? h("div", null,
h("div", { "class": "g-top" }, h("div", { "class": "g-top-t" }, SPEC.icon ? icon(SPEC.icon, 18) : null, h("span", null, txt(SPEC.title || ""))), badge(SPEC.badge)),
SPEC.subtitle ? h("div", { "class": "g-sub" }, txt(SPEC.subtitle)) : null) : null;
var body = h("div", { "class": frame + " g-stack g-fade" }, head, nodes(SPEC.components), footer());
ROOT.textContent = ""; ROOT.appendChild(body);
var fbEl = D.getElementById("gui-fallback"); if (fbEl) fbEl.remove();
mountQ.forEach(function (fn) { try { fn(); } catch (e) { console.warn("[gui]", e); } });
mountQ = [];
postHeight();
}
try { recompute(); render(); }
catch (e) {
console.error("[gui] render failed", e);
var fb = D.getElementById("gui-fallback");
ROOT.textContent = "";
ROOT.appendChild(h("div", { "class": "g-frame g-stack" }, errBox("view", e), fb ? h("pre", { style: { "white-space": "pre-wrap", margin: "0", "font-size": "13px" } }, fb.textContent) : null));
}
if (window.ResizeObserver) new ResizeObserver(postHeight).observe(D.body);
window.addEventListener("load", postHeight);
setTimeout(postHeight, 300); setTimeout(postHeight, 1200);
window.addEventListener("error", function (ev) { console.warn("[gui] runtime error", ev.message); });
})();"""
