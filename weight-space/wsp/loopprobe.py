"""Cheap edit selection on real loops (teacher forcing, no generation).

For each rescue item (a real base-model answer cut where its loop starts) we score, under every model:
  loop region       - the 200 tokens of the base model's actual loop that followed
  reasoning region  - answer tokens [onset-1536, onset-512): ordinary reasoning before the loop
and report the mean log-prob change per token against the base model. A selective edit makes the loop much less
likely and leaves the reasoning almost unchanged.

    python -m wsp.loopprobe --data data/rescue_dev.jsonl --base MODEL --model a=EDIT_A --model b=EDIT_B --out probe.json
"""
from __future__ import annotations

import argparse
import json
import os

import torch

from . import model as M


@torch.no_grad()
def logprobs(model, ids):
    return M.token_logprobs(model, ids).float()


def regions(tok, item):
    pre = tok(item["prefix"], add_special_tokens=False)["input_ids"]
    loop = tok(item["base_loop_text"], add_special_tokens=False)["input_ids"]
    ids = pre + loop
    n = len(pre)
    # logprobs index t predicts token t+1
    return ids, (n - 1, len(ids) - 1), (max(0, n - 1536) - 1, n - 512 - 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--model", action="append", required=True, help="name=path")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    torch.set_num_threads(os.cpu_count())
    items = [json.loads(l) for l in open(a.data)][:a.limit]
    base, tok = M.load(a.base, dtype=a.dtype)
    prepared = [regions(tok, x) for x in items]
    ref = [logprobs(base, ids) for ids, _, _ in prepared]
    del base
    res = {}
    for spec in a.model:
        name, path = spec.split("=", 1)
        m, _ = M.load(path, dtype=a.dtype)
        lo, re_ = [], []
        for (ids, (l0, l1), (r0, r1)), b in zip(prepared, ref):
            d = logprobs(m, ids) - b
            lo.append(float(d[l0:l1].mean()))
            re_.append(float(d[r0:r1].mean()))
        del m
        res[name] = {"loop_delta": sum(lo) / len(lo), "reasoning_delta": sum(re_) / len(re_),
                     "selectivity": (sum(lo) / len(lo)) / (abs(sum(re_) / len(re_)) + 1e-9),
                     "per_item_loop": lo, "per_item_reasoning": re_}
        r = res[name]
        print(f"{name:12s} loop {r['loop_delta']:+.4f}  reasoning {r['reasoning_delta']:+.4f}  nats/token", flush=True)
        json.dump(res, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
