"""Draws docs/star-history.svg: the repository's stars over time.

    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/name python .github/scripts/star_history.py

Run by .github/workflows/star-history.yml once a day. It reads when each
stargazer starred the repository (GitHub's star+json media type), and draws a
cumulative line in the style of the architecture diagrams, following light and
dark mode. Standard library only; nothing is sent anywhere but api.github.com.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "star-history.svg"
W, H = 820, 360
LEFT, RIGHT, TOP, BOTTOM = 64, 28, 64, 52


def fetch_star_dates(repo: str, token: str) -> list[dt.datetime]:
    dates, page = [], 1
    while True:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/stargazers?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github.star+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "star-history-chart",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            batch = json.load(response)
        dates += [dt.datetime.fromisoformat(item["starred_at"].replace("Z", "+00:00")) for item in batch if item.get("starred_at")]
        if len(batch) < 100 or page >= 400:  # 40,000 stars is GitHub's own limit for this list
            return sorted(dates)
        page += 1


def nice_step(top: int) -> int:
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000):
        if top / step <= 5:
            return step
    return 10 ** len(str(top))


def render(repo: str, dates: list[dt.datetime], created: dt.datetime, today: dt.datetime) -> str:
    total = len(dates)
    start = min([created] + dates[:1])
    span = max((today - start).total_seconds(), 86400)
    step = nice_step(max(total, 1))
    top = max(step, -(-max(total, 1) // step) * step)
    px = lambda t: LEFT + (W - LEFT - RIGHT) * (t - start).total_seconds() / span
    py = lambda n: H - BOTTOM - (H - TOP - BOTTOM) * n / top
    # A step line: flat until each star, then up by one
    points = [(px(start), py(0))]
    for i, when in enumerate(dates, 1):
        points += [(px(when), py(i - 1)), (px(when), py(i))]
    points.append((px(today), py(total)))
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)
    area = line + f" L{px(today):.1f} {py(0):.1f} L{px(start):.1f} {py(0):.1f} Z"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t d">',
        f'<title id="t">Star history of {html.escape(repo)}</title>',
        f'<desc id="d">{total} stars as of {today:%B %d, %Y}, counted from when each person starred the repository.</desc>',
        "<style>text{font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",\"Noto Sans\",Helvetica,Arial,sans-serif}"
        ".bg{fill:#ffffff;stroke:#d0d7de}.grid{stroke:#eaeef2}.axis{fill:#59636e}.title{fill:#1f2328;font-weight:600}.sub{fill:#59636e}"
        ".line{stroke:#0969da;fill:none;stroke-width:2.5}.area{fill:#0969da;opacity:.10}.dot{fill:#0969da}.count{fill:#1f2328;font-weight:700}"
        "@media (prefers-color-scheme:dark){.bg{fill:#0d1117;stroke:#3d444d}.grid{stroke:#21262d}.axis{fill:#9198a1}.title{fill:#f0f6fc}.sub{fill:#9198a1}"
        ".line{stroke:#4493f8}.area{fill:#4493f8;opacity:.16}.dot{fill:#4493f8}.count{fill:#f0f6fc}}</style>",
        f'<rect class="bg" x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12"/>',
        f'<text class="title" x="{LEFT}" y="34" font-size="16">Star history</text>',
        f'<text class="sub" x="{LEFT + 104}" y="34" font-size="13">{html.escape(repo)}</text>',
        f'<text class="sub" x="{W - RIGHT}" y="34" font-size="12" text-anchor="end">updated {today:%Y-%m-%d}</text>',
    ]
    for n in range(0, top + 1, step):
        y = py(n)
        out.append(f'<line class="grid" x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="axis" x="{LEFT - 10}" y="{y + 4:.1f}" font-size="12" text-anchor="end">{n:,}</text>')
    # Date ticks: about five, on month starts when the range is long enough
    ticks, months = [], max(1, round(span / 86400 / 30 / 5))
    tick = dt.datetime(start.year, start.month, 1, tzinfo=dt.timezone.utc)
    while tick <= today:
        if tick >= start:
            ticks.append(tick)
        month = tick.month - 1 + months
        tick = tick.replace(year=tick.year + month // 12, month=month % 12 + 1)
    if span < 86400 * 60 or len(ticks) < 2:
        ticks = [start + (today - start) * k / 4 for k in range(5)]
    for when in ticks:
        x = px(when)
        out.append(f'<text class="axis" x="{x:.1f}" y="{H - BOTTOM + 22}" font-size="12" text-anchor="middle">{when:%b %Y}</text>')
    out.append(f'<path class="area" d="{area}"/>')
    out.append(f'<path class="line" d="{line}" stroke-linejoin="round"/>')
    ex, ey = px(today), py(total)
    out.append(f'<circle class="dot" cx="{ex:.1f}" cy="{ey:.1f}" r="4.5"/>')
    label = f"{total:,} star" + ("" if total == 1 else "s")
    out.append(f'<text class="count" x="{ex - 10:.1f}" y="{max(ey - 12, TOP + 4):.1f}" font-size="14" text-anchor="end">{label}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        print("GITHUB_REPOSITORY and GITHUB_TOKEN are needed", file=sys.stderr)
        return 2
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers={"Authorization": f"Bearer {token}", "User-Agent": "star-history-chart"})
    with urllib.request.urlopen(request, timeout=30) as response:
        created = dt.datetime.fromisoformat(json.load(response)["created_at"].replace("Z", "+00:00"))
    dates = fetch_star_dates(repo, token)
    today = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(repo, dates, created, today), encoding="utf-8")
    print(f"{len(dates)} stars drawn to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
