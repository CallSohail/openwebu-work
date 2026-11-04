"""See every weight: per-tensor statistics, histograms, per-layer norms and |W| heatmaps, as one HTML file.

    python -m wsp.inspect_weights MODEL_DIR_OR_FILE --out report.html
    python -m wsp.inspect_weights BASE --diff EDITED --out diff.html     # what an edit changed

Needs only numpy. Works on any safetensors checkpoint (Qwen, Llama, Gemma, ...).
"""
from __future__ import annotations

import argparse
import html
import json
import re

import numpy as np

from . import st

GRID = 48  # heatmap resolution


def layer_of(name: str) -> int | None:
    m = re.search(r"layers\.(\d+)\.", name)
    return int(m.group(1)) if m else None


def heatmap(x: np.ndarray) -> list[list[float]]:
    """Downsample |W| of a 2-D tensor to at most GRID x GRID block means."""
    r, c = x.shape
    rs = np.linspace(0, r, min(GRID, r) + 1).astype(int)
    cs = np.linspace(0, c, min(GRID, c) + 1).astype(int)
    a = np.abs(x)
    rows = np.add.reduceat(a, rs[:-1], axis=0) / np.diff(rs)[:, None]
    grid = np.add.reduceat(rows, cs[:-1], axis=1) / np.diff(cs)[None, :]
    return np.round(grid, 6).tolist()


def stats(x: np.ndarray) -> dict:
    flat = x.reshape(-1).astype(np.float64)
    std = float(flat.std())
    lo, hi = np.percentile(flat, [0.1, 99.9]) if flat.size > 1 else (flat.min(), flat.max())
    hist, edges = np.histogram(flat, bins=60, range=(lo, hi) if hi > lo else None)
    kurt = float(((flat - flat.mean()) ** 4).mean() / std ** 4 - 3) if std > 0 else 0.0
    return {
        "params": int(flat.size), "mean": float(flat.mean()), "std": std,
        "min": float(flat.min()), "max": float(flat.max()), "absmax": float(np.abs(flat).max()),
        "fro": float(np.sqrt((flat ** 2).sum())), "kurtosis": kurt,
        "near_zero": float((np.abs(flat) < 1e-6).mean()),
        "outliers_6sd": int((np.abs(flat - flat.mean()) > 6 * std).sum()) if std > 0 else 0,
        "hist": hist.tolist(), "edges": [float(edges[0]), float(edges[-1])],
    }


def inspect(path: str, diff: str | None = None) -> dict:
    idx = st.index(path)
    other = st.index(diff) if diff else None
    tensors = []
    for name in sorted(idx, key=lambda n: (layer_of(n) if layer_of(n) is not None else -1, n)):
        e = idx[name]
        x = st.to_f32(st.read(e), e["dtype"])
        row = {"name": name, "layer": layer_of(name), "dtype": e["dtype"], "shape": e["shape"], **stats(x)}
        if x.ndim == 2:
            row["heat"] = heatmap(x)
        if other is not None and name in other:
            o = other[name]
            y = st.to_f32(st.read(o), o["dtype"])
            if y.shape == x.shape:
                d = y - x
                changed = d != 0
                row["diff"] = {
                    "changed": int(changed.sum()),
                    "rel_change": float(np.linalg.norm(d) / (np.linalg.norm(x) or 1)),
                    "changed_cols": int(changed.any(0).sum()) if d.ndim == 2 else None,
                    "changed_rows": int(changed.any(1).sum()) if d.ndim == 2 else None,
                    "cols": np.flatnonzero(changed.any(0)).tolist()[:4096] if d.ndim == 2 else None,
                }
                if d.ndim == 2 and changed.any():
                    row["diff_heat"] = heatmap(d)
                    s = np.linalg.svd(d[:, changed.any(0)], compute_uv=False)
                    row["diff"]["rank1_share"] = float(s[0] ** 2 / (s ** 2).sum())
        tensors.append(row)
    total = sum(t["params"] for t in tensors)
    out = {"source": path, "diff_against": diff, "total_params": total, "tensors": tensors}
    if diff:
        changed = sum(t.get("diff", {}).get("changed", 0) for t in tensors)
        out["changed_total"] = changed
        out["changed_pct"] = 100 * changed / total
    return out


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weight Inspector</title><style>
:root{--bg:#f6f7f7;--fg:#11171b;--mut:#5b6a70;--line:#d5dcde;--card:#fff;--hi:#44545b;--hot:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#11171b;--fg:#e9ecec;--mut:#8c9a9e;--line:#2a363c;--card:#182126;--hi:#c9d2d4;--hot:#ff8a80}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut)}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin:16px 0}.kpi{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:10px 14px}
.kpi b{display:block;font-size:18px}input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg);margin:8px 0}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:4px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th{position:sticky;top:0;background:var(--card);cursor:pointer}
td:first-child,th:first-child{text-align:left;white-space:normal;word-break:break-all}tr.sel{outline:2px solid var(--hi)}tr:hover{background:color-mix(in srgb,var(--hi) 8%,transparent)}
.chg{color:var(--hot);font-weight:600}.wrap{overflow:auto;max-height:60vh;border-radius:4px}
#detail{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin:16px 0}.panel{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:12px}
canvas{width:100%;image-rendering:pixelated;border-radius:4px}svg{width:100%;height:auto}
</style></head><body><main>
<h1>Weight Inspector</h1><div class="mut" id="src"></div><div class="kpis" id="kpis"></div>
<div class="panel"><b>Per-layer Frobenius norm</b> <span class="mut">(sum over the layer's tensors; red = layer touched by the diff)</span><div id="layers"></div></div>
<div id="detail"></div>
<input id="q" placeholder="Filter tensors (regex), e.g. layers\\.15\\..*down_proj">
<div class="wrap"><table id="t"></table></div>
</main><script>
const D=__DATA__;
const $=s=>document.querySelector(s),f=(x,d=4)=>Math.abs(x)>=1e4||(Math.abs(x)<1e-3&&x!==0)?x.toExponential(2):x.toFixed(d);
$('#src').textContent=D.source+(D.diff_against?'  vs  '+D.diff_against:'');
const k=[['Tensors',D.tensors.length],['Parameters',D.total_params.toLocaleString()]];
if(D.diff_against)k.push(['Weights changed',(D.changed_total||0).toLocaleString()],['Share changed',f(D.changed_pct,5)+'%']);
$('#kpis').innerHTML=k.map(([a,b])=>`<div class="kpi"><span class="mut">${a}</span><b>${b}</b></div>`).join('');
// layer chart
const L={};D.tensors.forEach(t=>{if(t.layer==null)return;L[t.layer]=L[t.layer]||{n:0,c:0};L[t.layer].n+=t.fro**2;if(t.diff&&t.diff.changed)L[t.layer].c=1});
const ks=Object.keys(L).map(Number).sort((a,b)=>a-b),mx=Math.max(...ks.map(i=>Math.sqrt(L[i].n)),1e-9);
const W=900,H=140,bw=W/Math.max(ks.length,1);
$('#layers').innerHTML=`<svg viewBox="0 0 ${W} ${H+18}">`+ks.map((i,j)=>{const h=Math.sqrt(L[i].n)/mx*H;return`<rect x="${j*bw+1}" y="${H-h}" width="${bw-2}" height="${h}" rx="2" fill="${L[i].c?'var(--hot)':'var(--hi)'}"><title>layer ${i}: ${f(Math.sqrt(L[i].n),2)}</title></rect><text x="${j*bw+bw/2}" y="${H+14}" font-size="10" text-anchor="middle" fill="currentColor">${i}</text>`}).join('')+'</svg>';
// table
const cols=[['name','Tensor'],['shape','Shape'],['params','Params'],['mean','Mean'],['std','Std'],['absmax','|max|'],['kurtosis','Kurtosis'],['outliers_6sd','>6σ']];
if(D.diff_against)cols.push(['changed','Changed'],['rel_change','Rel. Δ'],['changed_cols','Cols']);
let rows=D.tensors,sortK=null,asc=1;
const val=(t,c)=>c in t?t[c]:(t.diff?t.diff[c]:null);
function draw(){const re=(()=>{try{return new RegExp($('#q').value,'i')}catch(e){return /.*/}})();
 let r=rows.filter(t=>re.test(t.name));if(sortK)r=[...r].sort((a,b)=>((val(a,sortK)??-1e99)>(val(b,sortK)??-1e99)?1:-1)*asc);
 $('#t').innerHTML='<tr>'+cols.map(([c,l])=>`<th data-k="${c}">${l}</th>`).join('')+'</tr>'+r.map(t=>'<tr data-n="'+t.name+'">'+cols.map(([c])=>{let v=val(t,c);
 if(c==='shape')v=v.join('×');else if(c==='params'||c==='changed'||c==='outliers_6sd'||c==='changed_cols')v=v==null?'':v.toLocaleString();else if(typeof v==='number')v=f(v);
 return`<td class="${(c==='changed'&&val(t,c))?'chg':''}">${v??''}</td>`}).join('')+'</tr>').join('');}
$('#t').onclick=e=>{const th=e.target.closest('th');if(th){const c=th.dataset.k;asc=sortK===c?-asc:-1;sortK=c;return draw()}const tr=e.target.closest('tr[data-n]');if(tr)show(tr.dataset.n)};
$('#q').oninput=draw;
function heat(g,title){const c=document.createElement('canvas'),R=g.length,C=g[0].length;c.width=C;c.height=R;const x=c.getContext('2d'),im=x.createImageData(C,R);
 const m=Math.max(...g.flat())||1;g.forEach((row,i)=>row.forEach((v,j)=>{const p=(i*C+j)*4,s=Math.sqrt(v/m);im.data[p]=233-s*216;im.data[p+1]=236-s*213;im.data[p+2]=236-s*209;im.data[p+3]=255}));
 x.putImageData(im,0,0);const d=document.createElement('div');d.className='panel';d.innerHTML=`<b>${title}</b> <span class="mut">rows×cols, block mean, darker = larger</span>`;d.append(c);return d}
function show(n){const t=D.tensors.find(x=>x.name===n);document.querySelectorAll('tr.sel').forEach(r=>r.classList.remove('sel'));const tr=document.querySelector(`tr[data-n="${n}"]`);tr&&tr.classList.add('sel');
 const d=$('#detail');d.innerHTML='';const p=document.createElement('div');p.className='panel';const hm=Math.max(...t.hist,1);
 p.innerHTML=`<b>${n}</b><div class="mut">${t.dtype} · ${t.shape.join('×')} · std ${f(t.std)} · kurtosis ${f(t.kurtosis,2)}</div><svg viewBox="0 0 600 160">${t.hist.map((h,i)=>`<rect x="${i*10}" y="${150-h/hm*140}" width="9" height="${h/hm*140}" fill="var(--hi)"/>`).join('')}<text x="0" y="160" font-size="10" fill="currentColor">${f(t.edges[0])}</text><text x="600" y="160" font-size="10" text-anchor="end" fill="currentColor">${f(t.edges[1])}</text></svg>`+
 (t.diff&&t.diff.changed?`<div>Changed <b class="chg">${t.diff.changed.toLocaleString()}</b> entries in <b>${t.diff.changed_cols}</b> columns · rel. Δ ${f(t.diff.rel_change)} · rank-1 share of Δ ${f(t.diff.rank1_share??0,3)}</div><div class="mut" style="word-break:break-all">cols: ${(t.diff.cols||[]).join(', ')}</div>`:'');
 d.append(p);if(t.heat)d.append(heat(t.heat,'|W| heatmap'));if(t.diff_heat)d.append(heat(t.diff_heat,'|ΔW| heatmap (edit)'))}
draw();const first=D.tensors.find(t=>t.diff&&t.diff.changed)||D.tensors.find(t=>t.heat);first&&show(first.name);
</script></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model folder or .safetensors file")
    ap.add_argument("--diff", help="second checkpoint (e.g. edited) to compare against")
    ap.add_argument("--out", default="weights_report.html")
    ap.add_argument("--json", help="also write the raw statistics as JSON")
    a = ap.parse_args(argv)
    data = inspect(a.model, a.diff)
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(a.out, "w") as f:
        f.write(PAGE.replace("__DATA__", blob))
    if a.json:
        slim = {**data, "tensors": [{k: v for k, v in t.items() if k not in ("heat", "diff_heat")} for t in data["tensors"]]}
        json.dump(slim, open(a.json, "w"), indent=1)
    print(f"{len(data['tensors'])} tensors, {data['total_params']:,} params -> {a.out}")
    if a.diff:
        print(f"changed {data['changed_total']:,} weights ({data['changed_pct']:.4f}%)")
    print("Report: " + html.escape(a.out))


if __name__ == "__main__":
    main()
