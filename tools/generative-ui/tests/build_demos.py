"""Render every demo through the Tool exactly as Open WebUI would and write the embeds plus a harness page.

Usage: python tests/build_demos.py [out_dir] [--offline] [--theme light|dark]
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from demos import all_demos, load_module  # noqa: E402

HARNESS = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Generative UI harness</title>
<style>body{margin:0;padding:24px;background:BG;font-family:system-ui}iframe{width:100%;border:0;display:block}
.m{max-width:WIDTHpx;margin:0 auto 32px}</style></head><body><div class="m"><iframe id="f"
 sandbox="allow-scripts allow-forms allow-popups allow-downloads" title="embed"></iframe></div>
<script>
/* Mirrors Open WebUI 0.12.5 FullHeightIframe.svelte + Chat.svelte message handling */
window.__prompts = []; window.__heights = [];
const f = document.getElementById('f');
window.addEventListener('message', (e) => {
  if (e.source !== f.contentWindow) return;
  const d = e.data || {};
  if (d.type === 'iframe:height' && typeof d.height === 'number') { f.style.height = Math.max(0, d.height) + 'px'; window.__heights.push(d.height); }
  if (['input:prompt', 'input:prompt:submit', 'action:submit'].includes(d.type)) window.__prompts.push(d);
});
fetch('EMBED').then(r => r.text()).then(html => { f.srcdoc = html; });
</script></body></html>"""


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else Path(__file__).parent / "out")
    out.mkdir(parents=True, exist_ok=True)
    offline = "--offline" in sys.argv
    theme = "dark" if "--theme" in sys.argv and sys.argv[sys.argv.index("--theme") + 1] == "dark" else "light"
    mod = load_module()
    tool = mod.Tools()
    tool.valves.theme = theme
    if offline:
        tool.valves.live_weather = tool.valves.resolve_images = tool.valves.geocode_places = False
    index = {}
    for name, spec in all_demos(mod).items():
        res = await tool.render_ui(json.loads(json.dumps(spec)))
        if isinstance(res, str):
            print(f"{name}: ERROR {res}")
            continue
        resp, ctx = res
        (out / f"{name}.html").write_bytes(resp.body)
        bg = "#212121" if theme == "dark" else "#ffffff"
        for width in (760, 390):
            (out / f"harness_{name}_{width}.html").write_text(
                HARNESS.replace("EMBED", f"{name}.html").replace("WIDTH", str(width)).replace("BG", bg))
        index[name] = {"bytes": len(resp.body), "context": ctx}
        print(f"{name}: {len(resp.body) // 1024} KB")
    (out / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
