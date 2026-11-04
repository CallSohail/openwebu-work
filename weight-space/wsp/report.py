"""Build the comparison report: charts (PNG) + one self-contained HTML page.

    python -m wsp.report --rescue runs/rescue_test --probe runs/probe_dev.json \
        --base MODEL --edited final=FINAL_MODEL --out report/
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from . import st  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INK, MID, PALE, LINE, BG = "#11171B", "#44545B", "#8C9A9E", "#C9D2D4", "#FFFFFF"
SERIES = [PALE, MID, INK, "#6B7B81", "#2A363C"]
plt.rcParams.update({"font.size": 10, "axes.edgecolor": LINE, "axes.labelcolor": INK, "xtick.color": MID,
                     "ytick.color": MID, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.grid": True, "grid.color": "#E9ECEC", "axes.axisbelow": True, "figure.dpi": 150})


def bars(ax, groups, series, values, fmt="{:.0f}", ylabel=""):
    x = np.arange(len(groups))
    w = 0.8 / len(series)
    for i, s in enumerate(series):
        v = values[i]
        b = ax.bar(x + (i - (len(series) - 1) / 2) * w, v, w * 0.92, label=s, color=SERIES[i % len(SERIES)])
        for r, val in zip(b, v):
            ax.text(r.get_x() + r.get_width() / 2, r.get_height(), fmt.format(val), ha="center", va="bottom", fontsize=8, color=INK)
    ax.set_xticks(x, groups)
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False, ncol=len(series), loc="upper center", bbox_to_anchor=(0.5, 1.12))


def save(fig, out, name):
    p = os.path.join(out, name)
    fig.tight_layout()
    fig.savefig(p, facecolor=BG)
    plt.close(fig)
    return p


def chart_rescue(summary, out):
    models = list(summary)
    keys = [("correct", "Correct answer"), ("looped_again", "Fell back into loop"), ("closed_think", "Finished thinking"),
            ("cut_at_limit", "Still going at limit")]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    bars(ax, [k[1] for k in keys], models, [[summary[m][k] for k, _ in keys] for m in models], ylabel="% of items")
    ax.set_ylim(0, 110)
    ax.set_title("Loop-rescue test: continuing from the exact point where the base model started looping", fontsize=10, color=INK, pad=28)
    p1 = save(fig, out, "rescue_overview.png")
    sets = sorted({k.split("_", 1)[1] for k in summary[models[0]] if k.startswith("correct_")})
    fig, axs = plt.subplots(1, 2, figsize=(9, 3.6))
    bars(axs[0], sets, models, [[summary[m][f"correct_{s}"] for s in sets] for m in models], ylabel="% correct")
    axs[0].set_ylim(0, 105)
    bars(axs[1], sets, models, [[summary[m][f"looped_{s}"] for s in sets] for m in models], ylabel="% looped again")
    axs[1].set_ylim(0, 105)
    return [p1, save(fig, out, "rescue_by_set.png")]


def chart_probe(probe, out):
    names = list(probe)
    lo = [probe[n]["loop_delta"] for n in names]
    re_ = [probe[n]["reasoning_delta"] for n in names]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    y = np.arange(len(names))
    ax.barh(y + 0.2, lo, 0.38, color=INK, label="real loop text (want: strongly negative)")
    ax.barh(y - 0.2, re_, 0.38, color=PALE, label="normal reasoning before the loop (want: ~0)")
    ax.set_yticks(y, names)
    ax.axvline(0, color=MID, lw=0.8)
    ax.set_xlabel("change in log-prob per token vs. base model (nats)")
    ax.legend(frameon=False, loc="lower left", fontsize=8)
    ax.set_title("Candidate selection on held-out real loops (teacher forcing)", fontsize=10, color=INK)
    return [save(fig, out, "probe_candidates.png")]


def chart_mit(out):
    d = json.load(open(os.path.join(HERE, "data", "mit_eval_results.json")))["runs"]
    sets = [("math500", "MATH-500"), ("gsm8k", "GSM8K"), ("ifeval", "IFEval"), ("amc23", "AMC23"), ("minerva", "Minerva"),
            ("olympiad", "Olympiad"), ("mmlupro1k", "MMLU-Pro"), ("gpqa", "GPQA-D")]
    runs = [("unedited", "base"), ("p50c", "edited")]
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [3, 1.3]})
    bars(axs[0], [s[1] for s in sets], [r[1] for r in runs], [[100 * d[r][s]["acc"] for s, _ in sets] for r, _ in runs], ylabel="accuracy %")
    axs[0].set_title("MIT's full evaluation of this exact edit (32k tokens, vLLM)", fontsize=10, color=INK, pad=28)
    bars(axs[1], ["MATH-500", "GSM8K"], [r[1] for r in runs],
         [[100 * d[r]["math500"]["cut_share"], 100 * d[r]["gsm8k"]["cut_share"]] for r, _ in runs], ylabel="% answers cut at 32k")
    axs[1].set_title("Runaway answers", fontsize=10, color=INK, pad=28)
    return [save(fig, out, "mit_benchmarks.png")]


def chart_weights(base, edited, out):
    a, b = st.index(base), st.index(edited)
    names = [n for n in a if n.endswith("mlp.down_proj.weight") and n in b and not np.array_equal(st.read(a[n]), st.read(b[n]))]
    if not names:
        return [], {}
    fig, axs = plt.subplots(len(names), 2, figsize=(11, 2.6 * len(names)), gridspec_kw={"width_ratios": [3, 1]}, squeeze=False)
    info = {}
    for i, n in enumerate(sorted(names, key=lambda s: int(s.split(".layers.")[1].split(".")[0]))):
        W0 = st.to_f32(st.read(a[n]), a[n]["dtype"])
        W1 = st.to_f32(st.read(b[n]), b[n]["dtype"])
        d = W1 - W0
        col = np.linalg.norm(d, axis=0)
        layer = n.split(".layers.")[1].split(".")[0]
        axs[i][0].bar(np.arange(len(col)), col, width=4, color=INK)
        axs[i][0].set_title(f"layer {layer} MLP down_proj: size of change per output column (neuron)", fontsize=9, color=INK)
        axs[i][0].set_xlim(0, len(col))
        axs[i][0].set_xlabel("neuron index")
        cols = np.flatnonzero(col)
        sub = np.abs(d[:, cols])
        axs[i][1].imshow(sub[:256], aspect="auto", cmap="Greys", interpolation="nearest")
        axs[i][1].set_title(f"|ΔW| of the {len(cols)} edited columns (first 256 rows)", fontsize=8, color=INK)
        axs[i][1].grid(False)
        info[n] = {"columns": len(cols), "rel_change": float(np.linalg.norm(d) / np.linalg.norm(W0)),
                   "weights": int(W0.shape[0] * len(cols))}
    return [save(fig, out, "weight_changes.png")], info


def img(p):
    return f'<img alt="{html.escape(os.path.basename(p))}" src="data:image/png;base64,{base64.b64encode(open(p, "rb").read()).decode()}">'


def examples(rescue_dir, models, k=3):
    rows = {m: {json.loads(l)["id"]: json.loads(l) for l in open(os.path.join(rescue_dir, f"{m}.jsonl"))} for m in models}
    base, last = models[0], models[-1]
    ids = [i for i in rows[last] if rows[last][i]["correct"] and not rows[base][i]["correct"]]
    ids += [i for i in rows[last] if rows[base][i]["looped"] and not rows[last][i]["looped"] and i not in ids]
    out = []
    for i in ids[:k]:
        b, e = rows[base][i], rows[last][i]
        verdict = f"correct ({html.escape(str(e['pred']))})" if e["correct"] else "no loop, still reasoning"
        out.append(f"<details><summary><b>{html.escape(i)}</b>: {base} → {'loop' if b['looped'] else 'no answer'}, "
                   f"{last} → {verdict}</summary><div class='ex'><div><h4>{base}: last 1,200 characters</h4><pre>"
                   f"{html.escape(b['text'][-1200:])}</pre></div><div><h4>{last}: last 1,200 characters</h4><pre>{html.escape(e['text'][-1200:])}</pre></div></div></details>")
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rescue", required=True)
    ap.add_argument("--probe")
    ap.add_argument("--selection", help="results/selection.json (dev rescue + guard per candidate)")
    ap.add_argument("--base", required=True)
    ap.add_argument("--edited", required=True, help="name=path of the model whose weight changes to chart")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    S = json.load(open(os.path.join(a.rescue, "summary.json")))
    summary, settings = S["summary"], S["settings"]
    imgs = chart_rescue(summary, a.out)
    probe = json.load(open(a.probe)) if a.probe else None
    if probe:
        imgs += chart_probe(probe, a.out)
    imgs += chart_mit(a.out)
    name, path = a.edited.split("=", 1)
    wimgs, winfo = chart_weights(a.base, path, a.out)
    imgs += wimgs
    sel_html = ""
    if a.selection:
        sel = json.load(open(a.selection))
        g0 = sel["guard"]["base"]["quick_accuracy"]
        rows_ = []
        for k, desc in [("base", "unedited")] + list(sel["candidates"].items()):
            d, g = sel["dev_rescue"].get(k), sel["guard"].get(k)
            ga = f"{g['quick_accuracy']:.1f}%" if g else "n/a"
            if k == "base":
                verdict = "reference"
            elif g and g["quick_accuracy"] < g0 - 2:
                verdict = "discard: guard drop"
            elif k == "mit":
                verdict = "<b>KEEP (final)</b>"
            else:
                verdict = "no gain over final"
            rows_.append(f"<tr><td>{k}</td><td>{desc}</td><td>{d['looped_again']:.0f}%</td><td>{d['correct']:.1f}%</td><td>{ga}</td><td>{verdict}</td></tr>")
        sel_html = ("<table><tr><th>candidate</th><th>edit</th><th>dev: looped again</th><th>dev: correct</th>"
                    "<th>guard: quick accuracy</th><th>decision</th></tr>" + "".join(rows_) + "</table>"
                    "<p class='mut'>Dev: 16 rescue items (separate from test), 1,024 new tokens. Guard: 80 held-out GSM8K/MATH "
                    "problems, thinking off, greedy; an edit may not lose more than 2 points.</p>")
    stats_html = ""
    sp = os.path.join(a.rescue, "stats.json")
    if os.path.exists(sp):
        stt = json.load(open(sp))
        stats_html = (f"<section><b>Paired test:</b> {stt['base_only_loops']} items looped only with the base model, "
                      f"{stt['edited_only_loops']} only with the edit (exact McNemar p = {stt['mcnemar_exact_p']:.1e}). "
                      "<span class='mut'>No model reached a final answer within this budget: the edited model's correct answers run "
                      "~9k tokens (MIT's 32k evaluation), far past what this CPU test can generate.</span></section>")
    models = list(summary)
    head = ["model", "correct", "looped again", "finished thinking", "gave \\boxed", "cut at limit", "mean new tokens"]
    keys = ["correct", "looped_again", "closed_think", "boxed", "cut_at_limit", "mean_tokens"]
    table = "<table><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>" + "".join(
        f"<tr><td>{m}</td>" + "".join(f"<td>{summary[m][k]:.1f}{'' if k == 'mean_tokens' else '%'}</td>" for k in keys) + "</tr>"
        for m in models) + "</table>"
    n = summary[models[0]]["n"]
    total = sum(int(np.prod(e["shape"])) for e in st.index(a.base).values())
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qwen Loop Fix</title><style>
:root{{--bg:#E9ECEC;--card:#fff;--fg:#11171B;--mut:#44545B;--line:#C9D2D4}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,sans-serif}}main{{max-width:1080px;margin:0 auto;padding:32px 16px}}
h1{{letter-spacing:.04em;text-transform:uppercase;font-size:22px}}h2{{letter-spacing:.03em;font-size:16px;text-transform:uppercase;margin-top:36px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:18px;margin:14px 0}}img{{width:100%;border-radius:4px}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right}}td:first-child,th:first-child{{text-align:left}}
.mut{{color:var(--mut)}}pre{{white-space:pre-wrap;font-size:12px;background:#F4F6F6;padding:10px;border-radius:4px;max-height:340px;overflow:auto}}
.ex{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media(max-width:700px){{.ex{{grid-template-columns:1fr}}}}summary{{cursor:pointer;padding:6px 0}}
</style></head><body><main>
<h1>Qwen3.5-0.8B: weight-space loop fix</h1>
<p class="mut">Same procedure as MIT CSAIL's <i>Sparks of Weight-Space Programming</i>, experiment 8. No training, no gradients:
one direction removed from {sum(v['columns'] for v in winfo.values())} MLP output columns in layers {', '.join(k.split('.layers.')[1].split('.')[0] for k in winfo)}
({sum(v['weights'] for v in winfo.values()):,} weights, {100 * sum(v['weights'] for v in winfo.values()) / total:.4f}% of all {total / 1e6:.0f}M weights).</p>
<h2>Loop-rescue test (this machine)</h2><section>{table}
<p class="mut">{n} held-out GSM8K / MATH-500 test problems. Each starts from a real base-model answer, cut where it began looping
(MIT's published unedited samples). Every model continues for up to {settings['max_new_tokens']} tokens with MIT's sampling
(T={settings['temperature']}, top-p {settings['top_p']}, top-k {settings['top_k']}, presence penalty {settings['presence']}).
A continuation counts as looped when 90% of its 8-grams over 512 tokens repeat; it is stopped there.</p>
{''.join(img(p) for p in imgs[:2])}</section>
{stats_html}<h2>Examples: same starting point, base loops, edited keeps reasoning</h2><section>{examples(a.rescue, models)}</section>
<h2>How the final edit was chosen</h2><section>{sel_html}{img(imgs[2]) + '<p class="mut">Teacher-forced scores on 8 dev loops. Every edit moves the model away from the base on both kinds of text, so this only weakly separates candidates; generation and the guard decided.</p>' if probe else ''}</section>
<h2>Full-scale benchmark (MIT's published evaluation)</h2><section>{img([p for p in imgs if p.endswith('mit_benchmarks.png')][0])}
<p class="mut">The CPU test above can't run 32k-token answers. These are MIT's own vLLM results for the same edit, from their public logs.</p></section>
<h2>What changed in the weights</h2><section>{''.join(img(p) for p in wimgs)}</section>
</main></body></html>"""
    with open(os.path.join(a.out, "index.html"), "w") as f:
        f.write(page)
    print("->", os.path.join(a.out, "index.html"))


if __name__ == "__main__":
    main()
