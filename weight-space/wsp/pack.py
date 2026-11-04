"""Ship an edit as a small exact diff, and rebuild the full model from it.

    python -m wsp.pack create BASE EDITED --out release/          # edit.npz + manifest.json (a few hundred KB)
    python -m wsp.pack apply BASE release/ --out MODEL_DIR         # full edited model, every tensor sha256-checked

Only entries whose stored bits differ are kept (indices + new raw values), so the rebuild is bit-exact.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import numpy as np

from . import st


def create(base, edited, out):
    a, b = st.index(base), st.index(edited)
    os.makedirs(out, exist_ok=True)
    arrays, tensors = {}, []
    for name in sorted(a):
        if name not in b:
            continue
        x, y = st.read(a[name]), st.read(b[name])
        if x.dtype != y.dtype or x.shape != y.shape:
            raise SystemExit(f"{name}: dtype/shape changed")
        diff = np.flatnonzero(x.reshape(-1) != y.reshape(-1))
        if not len(diff):
            continue
        i = len(tensors)
        arrays[f"t{i}_idx"] = diff.astype(np.int64)
        arrays[f"t{i}_val"] = y.reshape(-1)[diff]
        tensors.append({"name": name, "dtype": a[name]["dtype"], "shape": a[name]["shape"], "changed_entries": int(len(diff)),
                        "changed_columns": int((x != y).any(0).sum()) if x.ndim == 2 else None,
                        "base_sha256": st.sha256(x), "edited_sha256": st.sha256(y)})
    np.savez_compressed(os.path.join(out, "edit.npz"), **arrays)
    man = {"tensors": tensors}
    src = os.path.join(edited, "edit_manifest.json")
    if os.path.exists(src):
        man["edit"] = {k: v for k, v in json.load(open(src)).items() if k != "tensors"}
    json.dump(man, open(os.path.join(out, "manifest.json"), "w"), indent=1)
    print(f"{len(tensors)} tensors, {sum(t['changed_entries'] for t in tensors):,} entries -> {out}/edit.npz "
          f"({os.path.getsize(os.path.join(out, 'edit.npz')) / 1e3:.0f} KB)")


def apply(base, pkg, out):
    man = json.load(open(os.path.join(pkg, "manifest.json")))
    z = np.load(os.path.join(pkg, "edit.npz"))
    idx = st.index(base)
    want = {t["name"]: (i, t) for i, t in enumerate(man["tensors"])}
    os.makedirs(out, exist_ok=True)
    src = base if os.path.isdir(base) else os.path.dirname(base)
    for f in os.listdir(src):
        if os.path.isfile(os.path.join(src, f)) and not f.endswith(".safetensors"):
            shutil.copy2(os.path.join(src, f), os.path.join(out, f))
    for file in st.shards(base):
        dst = os.path.join(out, os.path.basename(file))
        if not any(idx[n]["file"] == file for n in want if n in idx):
            shutil.copy2(file, dst)
            continue
        tensors, meta = st.load_file(file)
        for name, (i, t) in want.items():
            if name not in tensors:
                continue
            dt, x = tensors[name]
            if st.sha256(x) != t["base_sha256"]:
                raise SystemExit(f"{name}: base tensor differs from the one this edit was made on")
            y = x.copy().reshape(-1)
            y[z[f"t{i}_idx"]] = z[f"t{i}_val"]
            y = y.reshape(x.shape)
            if st.sha256(y) != t["edited_sha256"]:
                raise SystemExit(f"{name}: rebuilt tensor hash mismatch")
            tensors[name] = (dt, y)
            print(f"{name}: {t['changed_entries']:,} entries applied, sha256 OK")
        st.save_file(dst, tensors, meta)
    print("->", out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("base")
    c.add_argument("edited")
    c.add_argument("--out", required=True)
    p = sub.add_parser("apply")
    p.add_argument("base")
    p.add_argument("package")
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    create(a.base, a.edited, a.out) if a.cmd == "create" else apply(a.base, a.package, a.out)


if __name__ == "__main__":
    main()
