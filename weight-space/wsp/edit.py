"""Step 2 - Fix: subtract the diagnosed direction from the selected MLP output columns only.

    W' = W - alpha * outer(u, mask * (u @ W))

For each selected neuron (column j of down_proj) this removes alpha times its component along u; with
alpha = 1 it is an exact projection, alpha > 1 pushes against the direction (Astra's final edit: alpha 6,
64 columns in layers 15 and 19 of Qwen3.5-0.8B). No gradients, no data, no training.

    python -m wsp.edit MODEL_DIR --directions diag/directions.pt --alpha 6 --out MODEL_DIR-edited

The original files are never modified. The output folder is a complete model (config + tokenizer copied,
untouched shards hard-linked) plus edit_manifest.json and edit_directions.pt, which reproduce the edit
exactly from the original weights.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil

import numpy as np

from . import st

MAX_SHARE = 0.01  # reject edits touching more than 1% of all weights


def find_down_proj(names, layer: int) -> str:
    pat = re.compile(rf"(^|\.)layers\.{layer}\.mlp\.down_proj\.weight$")
    hits = [n for n in names if pat.search(n) and not re.search(r"(^|\.)(mtp|visual|vision|audio)", n)]
    if len(hits) != 1:
        raise SystemExit(f"Layer {layer}: expected one down_proj tensor, found {hits}")
    return hits[0]


def apply(W: np.ndarray, u: np.ndarray, cols: np.ndarray, alpha: float) -> np.ndarray:
    W = W.astype(np.float64)
    u = u.astype(np.float64)
    u = u / np.linalg.norm(u)
    coef = np.zeros(W.shape[1])
    coef[cols] = (u @ W)[cols]
    return (W - alpha * np.outer(u, coef)).astype(np.float32)


def edit(model_dir: str, directions: dict, alpha: float, out: str, top_k: int | None = None,
         max_share: float = MAX_SHARE) -> dict:
    if os.path.abspath(model_dir) == os.path.abspath(out):
        raise SystemExit("Refusing to write over the original model.")
    idx = st.index(model_dir)
    total = sum(int(np.prod(e["shape"])) for e in idx.values())
    plan = {}
    for layer, d in directions.items():
        name = find_down_proj(idx, int(layer))
        cols = np.asarray(d["neurons"])[:top_k] if top_k else np.asarray(d["neurons"])
        plan[name] = (int(layer), np.asarray(d["u"], dtype=np.float32), np.sort(cols))
    touched = sum(idx[n]["shape"][0] * len(c) for n, (_, _, c) in plan.items())
    share = touched / total
    if share > max_share:
        raise SystemExit(f"Too broad: edit touches {touched:,} weights = {100 * share:.3f}% (> {100 * max_share}%).")

    os.makedirs(out, exist_ok=True)
    src_dir = model_dir if os.path.isdir(model_dir) else os.path.dirname(model_dir)
    for f in os.listdir(src_dir):
        p = os.path.join(src_dir, f)
        if os.path.isfile(p) and not f.endswith(".safetensors"):
            shutil.copy2(p, os.path.join(out, f))
    files_to_edit = {idx[n]["file"] for n in plan}
    report = []
    for file in st.shards(model_dir):
        dst = os.path.join(out, os.path.basename(file))
        if os.path.exists(dst):
            os.remove(dst)
        if file not in files_to_edit:
            try:
                os.link(file, dst)
            except OSError:
                shutil.copy2(file, dst)
            continue
        tensors, meta = st.load_file(file)
        for name, (layer, u, cols) in plan.items():
            if name not in tensors:
                continue
            dt, raw = tensors[name]
            W0 = st.to_f32(raw, dt)
            W1 = apply(W0, u, cols, alpha)
            new = st.from_f32(W1, dt)
            tensors[name] = (dt, new)
            changed = new != raw
            un = u / np.linalg.norm(u)
            report.append({
                "name": name, "layer": layer, "shape": list(raw.shape), "dtype": dt, "columns": cols.tolist(),
                "weights_touched": int(raw.shape[0] * len(cols)), "entries_changed_after_rounding": int(changed.sum()),
                "changed_columns": int(changed.any(0).sum()),
                "relative_change": float(np.linalg.norm(W1 - W0) / np.linalg.norm(W0)),
                "u_write_before": float(np.abs(un @ W0[:, cols]).sum()),
                "u_write_after": float(np.abs(un @ st.to_f32(new, dt)[:, cols]).sum()),
                "base_sha256": st.sha256(raw), "edited_sha256": st.sha256(new)})
        st.save_file(dst, tensors, meta)

    import torch
    torch.save({int(l): {"u": torch.as_tensor(np.asarray(d["u"], dtype=np.float32)),
                         "neurons": torch.as_tensor(plan[find_down_proj(idx, int(l))][2])}
                for l, d in directions.items()}, os.path.join(out, "edit_directions.pt"))
    manifest = {"base_model": os.path.abspath(model_dir), "formula": "W' = W - alpha * outer(u, mask * (u @ W))",
                "alpha": alpha, "total_params": total, "weights_touched": touched, "share_touched_pct": 100 * share,
                "tensors": report,
                "reproduce": f"python -m wsp.edit {model_dir} --directions {out}/edit_directions.pt --alpha {alpha} --out <dir>",
                "note": "Weight-space editing was demonstrated with GPT-6 Astra and is unproven for other models. "
                        "Verify with wsp.gate before use."}
    json.dump(manifest, open(os.path.join(out, "edit_manifest.json"), "w"), indent=1)
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="model folder with .safetensors")
    ap.add_argument("--directions", required=True, help="directions.pt from wsp.diagnose")
    ap.add_argument("--alpha", type=float, default=1.0)
    ap.add_argument("--top-k", type=int, help="use only the first k neurons per layer")
    ap.add_argument("--layers", type=int, nargs="*", help="subset of the diagnosed layers")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import torch
    d = torch.load(a.directions, weights_only=True)
    d = {int(l): {"u": v["u"].numpy(), "neurons": v["neurons"].numpy()} for l, v in d.items()
         if not a.layers or int(l) in a.layers}
    m = edit(a.model, d, a.alpha, a.out, a.top_k)
    for t in m["tensors"]:
        print(f"{t['name']}: {len(t['columns'])} columns, {t['weights_touched']:,} weights, rel. change "
              f"{100 * t['relative_change']:.2f}%, |u-write| {t['u_write_before']:.3f} -> {t['u_write_after']:.3f}")
    print(f"total {m['weights_touched']:,} weights = {m['share_touched_pct']:.4f}% of the model -> {a.out}")


if __name__ == "__main__":
    main()
