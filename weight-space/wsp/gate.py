"""Step 3 - Eval gate. No gate, no ship.

Cheap check first (seconds on CPU) - does the edit hit the loop and spare normal reasoning?

    python -m wsp.gate probe ORIGINAL EDITED --pairs data/contrast_pairs_qwen_mit.json

  Teacher-forces each pair through both models and reports the change in mean log-prob per token on the part
  after the shared prefix. A good edit makes the repetitive branch clearly less likely and leaves the productive
  branch almost unchanged (Astra's final Qwen edit: -0.103 vs -0.009 nats/token).

The real gate - paired generation on your own problems, same seeds and sampling for both models:

    python -m wsp.gate generate ORIGINAL EDITED --problems problems.jsonl [--guard guard.jsonl] --out gate.json

  problems.jsonl: one {"id", "prompt", "answer"} per line (answer graded from the last \\boxed{}).
  Ships only if primary accuracy improves by >= --min-gain points and no guard set drops by more than --max-drop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter

import torch

from . import model as M

SUFFIX = "Please reason step by step, and put your final answer within \\boxed{}."


# ---------- grading ----------
def last_boxed(text: str) -> str | None:
    i = text.rfind("\\boxed{")
    if i < 0:
        return None
    j, depth = i + 7, 1
    while j < len(text) and depth:
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        j += 1
    return text[i + 7:j - 1] if depth == 0 else None


def norm(s: str) -> str:
    s = s.strip().strip("$").replace("\\!", "").replace(" ", "").replace("\\left", "").replace("\\right", "")
    s = re.sub(r"\\text\{(.*?)\}", r"\1", s)
    s = re.sub(r"\\d?frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = s.replace("^\\circ", "").replace("^{\\circ}", "").replace("\\%", "").replace("%", "").rstrip(".")
    if re.fullmatch(r"-?[\d,]+(\.\d+)?", s):
        s = s.replace(",", "")
    return s


def same(pred: str | None, gold: str) -> bool:
    if pred is None:
        return False
    try:
        from math_verify import parse, verify  # optional, what MIT used
        return bool(verify(parse("$" + gold + "$"), parse("$" + pred + "$")))
    except Exception:
        pass
    a, b = norm(pred), norm(str(gold))
    if a == b:
        return True
    try:
        return abs(float(eval(a, {"__builtins__": {}})) - float(eval(b, {"__builtins__": {}}))) < 1e-6
    except Exception:
        return False


def tail_repetition(ids: list[int], n: int = 8, window: int = 400) -> float:
    """Share of n-grams in the last `window` tokens that occur more than once (1.0 = pure loop)."""
    t = ids[-window:]
    grams = [tuple(t[i:i + n]) for i in range(len(t) - n + 1)]
    if not grams:
        return 0.0
    c = Counter(grams)
    return sum(v for v in c.values() if v > 1) / len(grams)


# ---------- probe ----------
def common_prefix(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


def probe(orig, edited, tok, pairs):
    res = {"productive": [], "repetitive": []}
    for p in pairs:
        a = tok(p["productive"], add_special_tokens=False)["input_ids"]
        b = tok(p["repetitive"], add_special_tokens=False)["input_ids"]
        k = common_prefix(a, b)
        for key, ids in (("productive", a), ("repetitive", b)):
            d = M.token_logprobs(edited, ids) - M.token_logprobs(orig, ids)
            res[key].append(float(d[k - 1:].mean()))
    return {k: {"mean_delta": sum(v) / len(v), "per_pair": v} for k, v in res.items()}


# ---------- generation ----------
def prompt_ids(tok, text: str, think: bool):
    msgs = [{"role": "user", "content": text}]
    try:
        s = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=think)
    except Exception:
        s = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    return tok(s, add_special_tokens=False, return_tensors="pt")["input_ids"]


@torch.no_grad()
def run(model, tok, problems, a, tag):
    rows = []
    dev = next(model.parameters()).device
    for p in problems:
        x = prompt_ids(tok, p["prompt"] + ("\n" + SUFFIX if a.suffix else ""), a.think).to(dev)
        samples = []
        for s in range(a.samples):
            seed = int(hashlib.sha256(f"{p['id']}:{s}:{a.seed}".encode()).hexdigest()[:8], 16)
            torch.manual_seed(seed)  # same seed for original and edited -> paired comparison
            y = model.generate(x, do_sample=True, temperature=a.temperature, top_p=a.top_p, top_k=a.top_k,
                               repetition_penalty=a.repetition_penalty, max_new_tokens=a.max_new_tokens,
                               pad_token_id=tok.pad_token_id or tok.eos_token_id)[0, x.shape[1]:].tolist()
            eos = {tok.eos_token_id} | set(getattr(model.generation_config, "eos_token_id", None) or [])
            cut = not any(t in eos for t in y)
            text = tok.decode(y, skip_special_tokens=False)
            answer_part = text.split("</think>")[-1] if "</think>" in text else text
            pred = last_boxed(answer_part) or last_boxed(text)
            samples.append({"correct": same(pred, p["answer"]), "pred": pred, "tokens": len(y), "cut": cut,
                            "tail_rep": round(tail_repetition(y), 3), "closed_think": "</think>" in text})
        rows.append({"id": p["id"], "samples": samples})
        ok = sum(s["correct"] for s in samples)
        print(f"[{tag}] {p['id']}: {ok}/{len(samples)} correct, cut {sum(s['cut'] for s in samples)}", flush=True)
    return rows


def summary(rows):
    s = [x for r in rows for x in r["samples"]]
    n = len(s) or 1
    return {"n": len(s), "accuracy": 100 * sum(x["correct"] for x in s) / n, "cut_pct": 100 * sum(x["cut"] for x in s) / n,
            "mean_tokens": sum(x["tokens"] for x in s) / n, "mean_tail_rep": sum(x["tail_rep"] for x in s) / n}


def paired(ro, re_):
    d = [sum(x["correct"] for x in b["samples"]) / len(b["samples"]) - sum(x["correct"] for x in a["samples"]) / len(a["samples"])
         for a, b in zip(ro, re_)]
    n = len(d)
    m = sum(d) / n
    se = (sum((x - m) ** 2 for x in d) / (n - 1)) ** 0.5 / n ** 0.5 if n > 1 else float("nan")
    return {"gain_pts": 100 * m, "se_pts": 100 * se, "rescued": sum(x > 0 for x in d), "lost": sum(x < 0 for x in d)}


def load_jsonl(p):
    return [json.loads(l) for l in open(p) if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("probe")
    pg = sub.add_parser("generate")
    for p in (pp, pg):
        p.add_argument("original")
        p.add_argument("edited")
        p.add_argument("--device", default="cpu")
        p.add_argument("--dtype", default="float32", help="float32 on CPU; bfloat16 on GPU")
    pp.add_argument("--pairs", required=True)
    pg.add_argument("--problems", required=True)
    pg.add_argument("--guard", nargs="*", default=[], help="guard sets that must not regress")
    pg.add_argument("--samples", type=int, default=1)
    pg.add_argument("--max-new-tokens", type=int, default=4096, help="MIT used 32768; loops only show up when this is large")
    pg.add_argument("--temperature", type=float, default=0.6)
    pg.add_argument("--top-p", type=float, default=0.95)
    pg.add_argument("--top-k", type=int, default=20)
    pg.add_argument("--repetition-penalty", type=float, default=1.0)
    pg.add_argument("--think", action=argparse.BooleanOptionalAction, default=True)
    pg.add_argument("--suffix", action=argparse.BooleanOptionalAction, default=True, help="append the \\boxed{} instruction")
    pg.add_argument("--seed", type=int, default=0)
    pg.add_argument("--min-gain", type=float, default=5.0)
    pg.add_argument("--max-drop", type=float, default=2.0)
    pg.add_argument("--out", default="gate.json")
    a = ap.parse_args(argv)

    orig, tok = M.load(a.original, a.device, a.dtype)
    edited, _ = M.load(a.edited, a.device, a.dtype)
    if a.cmd == "probe":
        r = probe(orig, edited, tok, json.load(open(a.pairs))["pairs"])
        print(json.dumps(r, indent=1))
        ok = r["repetitive"]["mean_delta"] < 0 and abs(r["productive"]["mean_delta"]) < abs(r["repetitive"]["mean_delta"]) / 3
        print("probe:", "SELECTIVE (worth a generation test)" if ok else "NOT selective - rethink the edit")
        return

    sets = {"primary": load_jsonl(a.problems), **{f"guard:{g}": load_jsonl(g) for g in a.guard}}
    result = {"settings": {k: v for k, v in vars(a).items() if k not in ("cmd",)}, "sets": {}}
    for name, probs in sets.items():
        ro, re_ = run(orig, tok, probs, a, f"{name} original"), run(edited, tok, probs, a, f"{name} edited")
        result["sets"][name] = {"original": summary(ro), "edited": summary(re_), "paired": paired(ro, re_),
                                "rows": {"original": ro, "edited": re_}}
    prim = result["sets"]["primary"]
    gain = prim["edited"]["accuracy"] - prim["original"]["accuracy"]
    drops = {k: v["original"]["accuracy"] - v["edited"]["accuracy"] for k, v in result["sets"].items() if k != "primary"}
    ship = gain >= a.min_gain and all(d <= a.max_drop for d in drops.values())
    result["gate"] = {"primary_gain_pts": gain, "guard_drops_pts": drops, "min_gain": a.min_gain, "max_drop": a.max_drop,
                      "decision": "SHIP" if ship else "DISCARD"}
    json.dump(result, open(a.out, "w"), indent=1)

    print(f"\n| set | model | accuracy | cut at limit | mean tokens | tail repetition |\n|---|---|---|---|---|---|")
    for k, v in result["sets"].items():
        for m in ("original", "edited"):
            s = v[m]
            print(f"| {k} | {m} | {s['accuracy']:.1f}% | {s['cut_pct']:.1f}% | {s['mean_tokens']:.0f} | {s['mean_tail_rep']:.2f} |")
    pr = prim["paired"]
    print(f"\nprimary gain {gain:+.1f} pts (paired SE {pr['se_pts']:.1f}, rescued {pr['rescued']}, lost {pr['lost']}); "
          f"guard drops {drops or 'n/a'}\nGATE: {result['gate']['decision']}  -> {a.out}")


if __name__ == "__main__":
    main()
