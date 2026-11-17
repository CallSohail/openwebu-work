"""Draws the architecture diagrams in this repository as SVG files.

    python docs/diagrams/build.py

Each diagram is a few lanes (where code runs: the browser, the Open WebUI
server, services outside it), boxes placed on a grid inside the lanes, and
arrows between boxes. The specs live in specs.py next to this file; edit a
spec and run this again to redraw. The SVGs follow the reader's light or dark
mode on GitHub. Standard library only.
"""

from __future__ import annotations

import html
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

LANE_W = 300
LANE_GAP = 36
PAD = 28
HEAD = 64
ROW_H = 104
BOX_W = 252
TITLE_SIZE = 14
LINE_SIZE = 12
LINE_H = 16

# Box kinds: what the box is. Each has a light and a dark fill and stroke.
KINDS = {
    "plugin": ("#ddf4ff", "#0969da", "#0c2d4f", "#4493f8"),   # code from this repository
    "ui": ("#ffffff", "#8c959f", "#161b22", "#6e7681"),       # what the person sees in Open WebUI
    "server": ("#dafbe1", "#1a7f37", "#0f2d1a", "#3fb950"),   # Open WebUI's own server and API
    "ext": ("#fbefff", "#8250df", "#2a1a3f", "#a371f7"),      # services outside Open WebUI
    "store": ("#fff1e5", "#bc4c00", "#3a1f0b", "#f0883e"),    # where something is kept
    "sandbox": ("#ffebe9", "#cf222e", "#3b1215", "#f85149"),  # code that runs walled off
}
LEGEND = {
    "plugin": "This repository",
    "ui": "Open WebUI page",
    "server": "Open WebUI server",
    "ext": "Outside service",
    "store": "Stored data",
    "sandbox": "Sandboxed code",
}


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def box_height(box: dict) -> int:
    return 34 + LINE_H * len(box.get("lines", [])) + (6 if box.get("lines") else 0)


def layout(spec: dict) -> tuple[dict, int, int]:
    lanes = spec["lanes"]
    rows = max(b["row"] + b.get("rows", 1) for b in spec["boxes"])
    places = {}
    for box in spec["boxes"]:
        lane = box["lane"]
        x = PAD + lane * (LANE_W + LANE_GAP) + (LANE_W - BOX_W) / 2
        h = box_height(box)
        slot = ROW_H * box.get("rows", 1)
        y = PAD + HEAD + box["row"] * ROW_H + (slot - h) / 2
        places[box["id"]] = (x, y, BOX_W, h)
    width = PAD * 2 + len(lanes) * LANE_W + (len(lanes) - 1) * LANE_GAP
    height = PAD + HEAD + rows * ROW_H + 18 + 40 + PAD
    return places, int(width), int(height)


def anchor(place, side):
    x, y, w, h = place
    return {"l": (x, y + h / 2), "r": (x + w, y + h / 2), "t": (x + w / 2, y), "b": (x + w / 2, y + h)}[side]


def gutter(x_box, toward_right):
    """The x of the gap between lanes next to a box, on the side it faces."""
    lane_left = x_box - (LANE_W - BOX_W) / 2
    return lane_left + LANE_W + LANE_GAP / 2 if toward_right else lane_left - LANE_GAP / 2


def route(a, b, hint=None, gap=None):
    """Points for an arrow from box a to box b, with right angles only. With gap=k it travels along the free space
    between rows k-1 and k, so it never runs behind a box in a lane it crosses."""
    if gap is not None:
        right = b[0] > a[0]
        p, q = anchor(a, "r" if right else "l"), anchor(b, "l" if right else "r")
        y = PAD + HEAD + gap * ROW_H
        g1, g2 = gutter(a[0], right), gutter(b[0], not right)
        return [p, (g1, p[1]), (g1, y), (g2, y), (g2, q[1]), q]
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if hint:
        s, e = hint
        p, q = anchor(a, s), anchor(b, e)
        if s in "lr" and e in "lr":
            mx = (p[0] + q[0]) / 2
            return [p, (mx, p[1]), (mx, q[1]), q] if abs(p[1] - q[1]) > 1 else [p, q]
        if s in "tb" and e in "tb":
            my = (p[1] + q[1]) / 2
            return [p, (p[0], my), (q[0], my), q] if abs(p[0] - q[0]) > 1 else [p, q]
        if s in "lr":
            return [p, (q[0], p[1]), q]
        return [p, (p[0], q[1]), q]
    if abs(ax - bx) < 1:  # same lane: top to bottom
        if by > ay:
            return [anchor(a, "b"), anchor(b, "t")]
        return [anchor(a, "t"), anchor(b, "b")]
    p = anchor(a, "r" if bx > ax else "l")
    q = anchor(b, "l" if bx > ax else "r")
    if abs(p[1] - q[1]) < 1:
        return [p, q]
    mx = (p[0] + q[0]) / 2
    return [p, (mx, p[1]), (mx, q[1]), q]


def label_point(points):
    # The middle of the longest segment
    best, at = -1, points[0]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = abs(x2 - x1) + abs(y2 - y1)
        if length > best:
            best, at = length, ((x1 + x2) / 2, (y1 + y2) / 2)
    return at


def render(spec: dict) -> str:
    places, width, height = layout(spec)
    out = []
    add = out.append
    title = spec["title"]
    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-labelledby="t d">')
    add(f'<title id="t">{esc(title)}</title><desc id="d">{esc(spec.get("desc", title))}</desc>')
    css = [
        ".bg{fill:#ffffff}.lane{fill:#f6f8fa;stroke:#d0d7de}.lanet{fill:#1f2328;font-weight:600}.lanes{fill:#59636e}",
        ".t{fill:#1f2328;font-weight:600}.s{fill:#3d444d}.edge{stroke:#59636e;fill:none;stroke-width:1.5}.edge.dash{stroke-dasharray:5 4}",
        ".el{fill:#1f2328}.elb{fill:#ffffff;stroke:#d0d7de}.head{fill:#59636e}.cap{fill:#1f2328;font-weight:700}.capsub{fill:#59636e}",
        ".num{fill:#1f2328}.numt{fill:#ffffff;font-weight:700}",
    ]
    dark = [
        ".bg{fill:#0d1117}.lane{fill:#151b23;stroke:#3d444d}.lanet{fill:#f0f6fc}.lanes{fill:#9198a1}",
        ".t{fill:#f0f6fc}.s{fill:#c9d1d9}.edge{stroke:#9198a1}.el{fill:#f0f6fc}.elb{fill:#0d1117;stroke:#3d444d}.head{fill:#9198a1}",
        ".cap{fill:#f0f6fc}.capsub{fill:#9198a1}.num{fill:#f0f6fc}.numt{fill:#0d1117}",
    ]
    for kind, (lf, ls, df, ds) in KINDS.items():
        css.append(f".k-{kind}{{fill:{lf};stroke:{ls}}}.k-{kind}-bar{{fill:{ls}}}")
        dark.append(f".k-{kind}{{fill:{df};stroke:{ds}}}.k-{kind}-bar{{fill:{ds}}}")
    add('<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif}' + "".join(css)
        + "@media (prefers-color-scheme:dark){" + "".join(dark) + "}</style>")
    add('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0 0L10 5L0 10z"/></marker></defs>')
    add(f'<rect class="bg" width="{width}" height="{height}" rx="12"/>')
    lane_h = height - PAD * 2 - 58
    for i, (name, sub) in enumerate(spec["lanes"]):
        x = PAD + i * (LANE_W + LANE_GAP)
        add(f'<rect class="lane" x="{x}" y="{PAD}" width="{LANE_W}" height="{lane_h}" rx="10"/>')
        add(f'<text class="lanet" x="{x + 16}" y="{PAD + 26}" font-size="15">{esc(name)}</text>')
        if sub:
            add(f'<text class="lanes" x="{x + 16}" y="{PAD + 45}" font-size="12">{esc(sub)}</text>')
    # Arrows under boxes
    labels = []
    for edge in spec["edges"]:
        src, dst, text = edge[0], edge[1], edge[2] if len(edge) > 2 else ""
        opts = edge[3] if len(edge) > 3 else {}
        points = route(places[src], places[dst], opts.get("via"), opts.get("gap"))
        d = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
        both = ' marker-start="url(#arrow)"' if opts.get("both") else ""
        cls = "edge dash" if opts.get("dash") else "edge"
        add(f'<path class="{cls}" d="{d}" marker-end="url(#arrow)"{both}/>')
        if text:
            lx, ly = opts.get("at") or label_point(points)
            labels.append((lx + opts.get("dx", 0), ly + opts.get("dy", 0), text))
    for box in spec["boxes"]:
        x, y, w, h = places[box["id"]]
        kind = box.get("kind", "plugin")
        dash = ' stroke-dasharray="6 4"' if kind == "sandbox" else ""
        add(f'<rect class="k-{kind}" x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="8" stroke-width="1.5"{dash}/>')
        add(f'<rect class="k-{kind}-bar" x="{x:.1f}" y="{y:.1f}" width="5" height="{h}" rx="2"/>')
        tx = x + 16
        if box.get("step"):
            add(f'<circle class="num" cx="{x + w - 16:.1f}" cy="{y + 16:.1f}" r="10"/><text class="numt" x="{x + w - 16:.1f}" y="{y + 20:.1f}" font-size="11" text-anchor="middle">{esc(box["step"])}</text>')
        add(f'<text class="t" x="{tx:.1f}" y="{y + 22:.1f}" font-size="{TITLE_SIZE}">{esc(box["title"])}</text>')
        for i, line in enumerate(box.get("lines", [])):
            add(f'<text class="s" x="{tx:.1f}" y="{y + 42 + i * LINE_H:.1f}" font-size="{LINE_SIZE}">{esc(line)}</text>')
    for lx, ly, text in labels:
        lines = text.split("\n")
        tw = max(len(line) for line in lines) * 6.3 + 14
        th = 16 * len(lines) + 4
        add(f'<rect class="elb" x="{lx - tw / 2:.1f}" y="{ly - th / 2:.1f}" width="{tw:.1f}" height="{th}" rx="6"/>')
        for i, line in enumerate(lines):
            add(f'<text class="el" x="{lx:.1f}" y="{ly - th / 2 + 15 + i * 16:.1f}" font-size="11.5" text-anchor="middle">{esc(line)}</text>')
    # Legend: only the kinds this diagram uses
    used = [k for k in KINDS if any(b.get("kind", "plugin") == k for b in spec["boxes"])]
    lx = PAD
    ly = height - PAD - 14
    for kind in used:
        add(f'<rect class="k-{kind}" x="{lx}" y="{ly - 10}" width="14" height="14" rx="3" stroke-width="1.5"/>')
        add(f'<text class="capsub" x="{lx + 20}" y="{ly + 1}" font-size="12">{esc(LEGEND[kind])}</text>')
        lx += 34 + len(LEGEND[kind]) * 6.6
    add("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    spec_path = pathlib.Path(__file__).with_name("specs.py")
    loader = importlib.util.spec_from_file_location("diagram_specs", spec_path)
    specs = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(specs)
    only = set(sys.argv[1:])
    for spec in specs.DIAGRAMS:
        if only and spec["name"] not in only:
            continue
        target = ROOT / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(spec), encoding="utf-8")
        print("wrote", target.relative_to(ROOT))


if __name__ == "__main__":
    main()
