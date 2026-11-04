"""Rebuild MIT's edited Qwen3.5-0.8B (50-pass run, MATH-500 7.9% -> 48.1%) bit for bit from the public weights.

    python -m wsp.reproduce_mit QWEN_DIR --out qwen35-08b-mit-edit
    python -m wsp.reproduce_mit --show                 # what the edit touches, no weights needed

QWEN_DIR: a download of Qwen/Qwen3.5-0.8B at revision 2fc06364715b967f1860aea9cf38778875588b17, e.g.
    huggingface-cli download Qwen/Qwen3.5-0.8B --revision 2fc06364715b967f1860aea9cf38778875588b17 --local-dir QWEN_DIR

The edit (mit_edit/p50c, CC BY 4.0, from github.com/RikiyaT/lm_understand-logs) stores the changed bfloat16
entries of two tensors. Every rebuilt tensor is checked against the sha256 of the checkpoint MIT evaluated.
The output folder also gets the unit loop direction u recovered from the diff, for each layer.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import numpy as np

from . import st

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mit_edit", "p50c")
ALPHA = 6.0


def show():
    man = json.load(open(os.path.join(HERE, "manifest.json")))
    sel = json.load(open(os.path.join(HERE, "selected_neurons.json")))
    total = 0
    for t in man["tensors"]:
        layer = t["name"].split(".layers.")[1].split(".")[0]
        print(f"{t['name']}  shape {t['shape']}  columns {t['changed_columns']}  entries changed {t['changed_entries']:,}  "
              f"rel. change {100 * t['relative_change']:.2f}%")
        print(f"   neurons: {sel[layer]['selected_neurons']}")
        total += t["shape"][0] * t["changed_columns"]
    print(f"Formula: W = W0 - {ALPHA:g} * outer(u, mask64 * (u @ W0));  {total:,} weights touched (0.016% of the model)")


def recover_u(W0: np.ndarray, W1: np.ndarray, cols: list[int]) -> np.ndarray:
    """dW[:, cols] = -alpha * outer(u, u @ W0[:, cols]) is rank one, so its top left singular vector is u.
    (The sign of u does not matter: u and -u give the same edit.)"""
    d = (W1 - W0)[:, cols].astype(np.float64)
    return np.linalg.svd(d, full_matrices=False)[0][:, 0].astype(np.float32)


def rebuild(base: str, out: str):
    man = json.load(open(os.path.join(HERE, "manifest.json")))
    sel = json.load(open(os.path.join(HERE, "selected_neurons.json")))
    z = np.load(os.path.join(HERE, "edit.npz"))
    idx = st.index(base)
    want = {t["name"]: (i, t) for i, t in enumerate(man["tensors"])}
    missing = [n for n in want if n not in idx]
    if missing:
        raise SystemExit(f"Base checkpoint lacks {missing}. Use Qwen/Qwen3.5-0.8B (tensor names model.language_model.*).")
    os.makedirs(out, exist_ok=True)
    src = base if os.path.isdir(base) else os.path.dirname(base)
    for f in os.listdir(src):
        if os.path.isfile(os.path.join(src, f)) and not f.endswith(".safetensors"):
            shutil.copy2(os.path.join(src, f), os.path.join(out, f))
    dirs = {}
    for file in st.shards(base):
        dst = os.path.join(out, os.path.basename(file))
        if not any(idx[n]["file"] == file for n in want):
            shutil.copy2(file, dst)
            continue
        tensors, meta = st.load_file(file)
        for name, (i, t) in want.items():
            if name not in tensors:
                continue
            dt, b = tensors[name]
            assert dt == "BF16" and list(b.shape) == t["shape"], (name, dt, b.shape)
            if st.sha256(b) != t["base_sha256"]:
                raise SystemExit(f"{name}: base tensor differs from the one MIT edited (wrong revision?)")
            e = b.copy().reshape(-1)
            e[z[f"t{i}_idx"]] = z[f"t{i}_val"]
            e = e.reshape(b.shape)
            if st.sha256(e) != t["edited_sha256"]:
                raise SystemExit(f"{name}: rebuilt tensor does not match MIT's evaluated checkpoint")
            tensors[name] = (dt, e)
            layer = name.split(".layers.")[1].split(".")[0]
            dirs[int(layer)] = {"u": recover_u(st.to_f32(b, dt), st.to_f32(e, dt), sel[layer]["selected_neurons"]),
                                "neurons": np.array(sel[layer]["selected_neurons"])}
            print(f"{name}: rebuilt, {t['changed_entries']:,} entries changed, sha256 OK")
        st.save_file(dst, tensors, meta)
    import torch
    torch.save({l: {"u": torch.as_tensor(v["u"]), "neurons": torch.as_tensor(v["neurons"])} for l, v in dirs.items()},
               os.path.join(out, "mit_directions.pt"))
    print(f"-> {out}  (also mit_directions.pt: the recovered loop direction per layer, usable with wsp.edit --alpha)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args(argv)
    if a.show or not a.base:
        return show()
    rebuild(a.base, a.out or "qwen35-08b-mit-edit")


if __name__ == "__main__":
    main()
