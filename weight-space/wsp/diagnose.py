"""Step 1 - Diagnose: find the direction a failure writes into the residual stream, and who writes it.

Same procedure Astra used on Qwen3.5-0.8B (MIT exp8, run p50c):

  1. Teacher-force matched pairs of texts that share a prefix and then either continue productively or loop.
  2. For every layer l, the loop direction is u_l = unit(mean over pairs of [resid_rep - resid_prod]) at the
     output of layer l, averaged over the last `--tail` tokens (Astra: 36).
  3. Score how much each MLP *adds* along u_l:  mlp_write_l = u_l . W_down_l (mean act_rep - mean act_prod).
     Layers with the largest score are where the loop is written (Qwen: layers 15 and 19).
  4. Per neuron j of a chosen layer:  score_j = (u_l . W_down[:, j]) * (act_rep_j - act_prod_j)
     over the last `--writer-tail` tokens (Astra: 24). The top-k (Astra: 64) are the loop writers.

    python -m wsp.diagnose MODEL --pairs data/contrast_pairs_qwen_mit.json --out diag/
    python -m wsp.diagnose MODEL --pairs my_pairs.json --layers 15 19 --top-k 64 --out diag/

Pairs file: {"pairs": [{"productive": "...", "repetitive": "..."}, ...]} - full raw texts (chat template included).
Write pairs from your model's own failures: copy a real looping output and a correct continuation of the same prefix.
"""
from __future__ import annotations

import argparse
import json
import os

import torch

from . import model as M


def encode(tok, text: str) -> list[int]:
    return tok(text, add_special_tokens=False)["input_ids"]


def contrast(model, tok, pairs, tail: int, writer_tail: int):
    """Mean (repetitive - productive) residuals and MLP activations over all pairs, for both tails."""
    dr = dmw = None
    for p in pairs:
        a, b = encode(tok, p["productive"]), encode(tok, p["repetitive"])
        rp, _ = M.record(model, a, tail)
        rr, _ = M.record(model, b, tail)
        _, mp = M.record(model, a, writer_tail)
        _, mr = M.record(model, b, writer_tail)
        r, m = rr - rp, mr - mp
        dr = r if dr is None else dr + r
        dmw = m if dmw is None else dmw + m
    n = len(pairs)
    return dr / n, dmw / n


def diagnose(model, tok, pairs, layers=None, top_k=64, tail=36, writer_tail=24, n_layers=2):
    d_resid, d_mlp = contrast(model, tok, pairs, tail, writer_tail)
    L = M.layers(model)
    table = []
    for l in L:
        u = d_resid[l + 1] / d_resid[l + 1].norm()
        W = M.down_proj(model, l).detach().float().cpu()
        write = float(u @ (W @ d_mlp[l]))  # how much this MLP adds along the loop direction
        table.append({"layer": l, "contrast_norm": float(d_resid[l + 1].norm()),
                      "growth": float((d_resid[l + 1] - d_resid[l]) @ u), "mlp_write": write})
    chosen = layers or [r["layer"] for r in sorted(table, key=lambda r: -r["mlp_write"])[:n_layers]]
    out = {}
    for l in chosen:
        u = d_resid[l + 1] / d_resid[l + 1].norm()
        W = M.down_proj(model, l).detach().float().cpu()
        proj = u @ W                          # what each neuron writes along u, per unit activation
        score = proj * d_mlp[l]               # ... times how much more it fires when looping
        top = torch.argsort(score, descending=True)[:top_k]
        pos = score.clamp_min(0).sum()
        out[l] = {"u": u, "neurons": top, "score": score,
                  "top_mass": float(score[top].clamp_min(0).sum() / pos) if pos > 0 else 0.0}
    return table, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--out", default="diag")
    ap.add_argument("--layers", type=int, nargs="*", help="layers to target (default: top --n-layers by mlp_write)")
    ap.add_argument("--n-layers", type=int, default=2)
    ap.add_argument("--top-k", type=int, default=64)
    ap.add_argument("--tail", type=int, default=36)
    ap.add_argument("--writer-tail", type=int, default=24)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args(argv)

    pairs = json.load(open(a.pairs))["pairs"]
    if len(pairs) < 2:
        raise SystemExit("Need at least 2 productive/repetitive pairs.")
    model, tok = M.load(a.model, a.device)
    if M.n_params(model) > 3e9:
        raise SystemExit("Too large for weight-space editing with current tooling (>3B parameters).")
    table, out = diagnose(model, tok, pairs, a.layers, a.top_k, a.tail, a.writer_tail, a.n_layers)

    os.makedirs(a.out, exist_ok=True)
    torch.save({l: {"u": v["u"], "neurons": v["neurons"]} for l, v in out.items()}, os.path.join(a.out, "directions.pt"))
    report = {"model": a.model, "pairs": len(pairs), "tail": a.tail, "writer_tail": a.writer_tail, "per_layer": table,
              "targets": {str(l): {"neurons": v["neurons"].tolist(), "top_k_share_of_positive_score": v["top_mass"]}
                          for l, v in out.items()}}
    json.dump(report, open(os.path.join(a.out, "diagnosis.json"), "w"), indent=1)

    print(f"{'layer':>5} {'contrast':>9} {'growth':>8} {'mlp_write':>10}")
    for r in table:
        mark = "  <- target" if r["layer"] in out else ""
        print(f"{r['layer']:>5} {r['contrast_norm']:9.3f} {r['growth']:8.4f} {r['mlp_write']:10.4f}{mark}")
    for l, v in out.items():
        print(f"layer {l}: top-{a.top_k} writers carry {100 * v['top_mass']:.1f}% of positive loop-writing score; "
              f"first 12: {v['neurons'][:12].tolist()}")
    print(f"-> {a.out}/directions.pt, {a.out}/diagnosis.json")


if __name__ == "__main__":
    main()
