"""Guard metrics: an edit must not damage what already worked.

  1. Reference likelihood - mean log-prob per token of human-written correct solutions (GSM8K / MATH train, held out
     from every MIT run), placed after the question with thinking off. A damaged model gets worse here.
  2. Quick answers - greedy, thinking off, up to --max-new-tokens; accuracy of the \\boxed{} answer.

    python -m wsp.guard --problems data/heldout_gsm8k.jsonl data/heldout_math.jsonl --model base=M --model edit=E --out guard.json
"""
from __future__ import annotations

import argparse
import json
import os

import torch

from . import model as M
from .gate import SUFFIX, last_boxed, same

PRACTICE = "/home/user/rikiyat/lm_understand-logs/exp8_math/practice"


def solutions():
    out = {}
    for fn in ("gsm8k_train.jsonl", "math_train.jsonl"):
        p = os.path.join(PRACTICE, fn)
        if os.path.exists(p):
            for l in open(p):
                r = json.loads(l)
                out[r["id"]] = r.get("solution")
    return out


def chat(tok, q):
    return tok.apply_chat_template([{"role": "user", "content": q + "\n" + SUFFIX}], tokenize=False,
                                   add_generation_prompt=True, enable_thinking=False)


@torch.no_grad()
def evaluate(path, probs, sols, a):
    model, tok = M.load(path, dtype=a.dtype)
    tok.padding_side = "left"
    nll = []
    for p in probs:
        s = sols.get(p["id"])
        if not s:
            continue
        pre = tok(chat(tok, p["prompt"]), add_special_tokens=False)["input_ids"]
        ids = pre + tok(s, add_special_tokens=False)["input_ids"]
        lp = M.token_logprobs(model, ids)
        nll.append(float(lp[len(pre) - 1:].mean()))
    rows = []
    for b in range(0, len(probs), a.batch_size):
        batch = probs[b:b + a.batch_size]
        enc = tok([chat(tok, p["prompt"]) for p in batch], return_tensors="pt", padding=True, add_special_tokens=False)
        y = model.generate(**enc, do_sample=False, max_new_tokens=a.max_new_tokens, pad_token_id=tok.pad_token_id or 0)
        for p, g in zip(batch, y[:, enc.input_ids.shape[1]:]):
            t = tok.decode(g, skip_special_tokens=True)
            pred = last_boxed(t)
            rows.append({"id": p["id"], "pred": pred, "correct": same(pred, p["answer"])})
        print(f"[{os.path.basename(path)}] quick answers {len(rows)}/{len(probs)}", flush=True)
    return {"ref_logprob": sum(nll) / len(nll) if nll else None, "n_ref": len(nll),
            "quick_accuracy": 100 * sum(r["correct"] for r in rows) / len(rows), "n": len(rows), "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--problems", nargs="+", required=True)
    ap.add_argument("--model", action="append", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=768)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--dtype", default="bfloat16")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    torch.set_num_threads(os.cpu_count())
    probs = [json.loads(l) for f in a.problems for l in open(f) if l.strip()]
    sols = solutions()
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    for spec in a.model:
        name, path = spec.split("=", 1)
        if name not in res:
            res[name] = evaluate(path, probs, sols, a)
            json.dump(res, open(a.out, "w"), indent=1)
        print(f"{name:10s} reference log-prob {res[name]['ref_logprob']:.4f}  quick accuracy {res[name]['quick_accuracy']:.1f}%", flush=True)


if __name__ == "__main__":
    main()
