"""Loop-rescue benchmark: can a model get out of a loop the base model fell into, and still answer correctly?

Each item is a real base-model answer (MIT's published unedited Qwen3.5-0.8B samples on GSM8K / MATH-500 test),
cut where its repetition loop starts. Every model continues from exactly that text with the same sampling
(MIT's: temperature 0.6, top_p 0.95, top_k 20, presence penalty 1.5) for up to --max-new-tokens tokens.

Per continuation we record: did it fall back into a loop (8-gram duplicate share >= 0.9 over 512 tokens; the run is
stopped there), close its thinking (</think>), give a \\boxed{} answer, and was the answer right.

    python -m wsp.rescue --data data/rescue_test.jsonl --model base=MODEL --model edited=EDITED --out runs/rescue

This tests the failure the edit targets on CPU-sized budgets. It is not a replacement for a full 32k-token benchmark.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter

import torch
from transformers import LogitsProcessor, LogitsProcessorList, StoppingCriteria, StoppingCriteriaList

from . import model as M
from .gate import last_boxed, same


def dup_share(ids, n=8):
    g = [tuple(ids[i:i + n]) for i in range(len(ids) - n + 1)]
    if not g:
        return 0.0
    c = Counter(g)
    return sum(v for v in c.values() if v > 1) / len(g)


class Presence(LogitsProcessor):
    """vLLM-style presence penalty: subtract `p` from every token already present in the answer."""
    def __init__(self, seen: torch.Tensor, p: float):
        self.seen, self.p = seen, p

    def __call__(self, input_ids, scores):
        self.seen[torch.arange(len(input_ids)), input_ids[:, -1]] = True
        return scores - self.p * self.seen.to(scores.dtype)


class LoopStop(StoppingCriteria):
    def __init__(self, start: int, window=512, thr=0.9, every=32, min_new=256):
        self.start, self.window, self.thr, self.every, self.min_new = start, window, thr, every, min_new
        self.looped: set[int] = set()

    def __call__(self, input_ids, scores, **kw):
        n = input_ids.shape[1] - self.start
        done = torch.zeros(len(input_ids), dtype=torch.bool, device=input_ids.device)
        if n >= self.min_new and n % self.every == 0:
            for i, row in enumerate(input_ids[:, -self.window:].tolist()):
                if i in self.looped or dup_share(row) >= self.thr:
                    self.looped.add(i)
                    done[i] = True
        elif self.looped:
            done[list(self.looped)] = True
        return done


def grade(item, text, looped, eos):
    ans_part = text.split("</think>")[-1] if "</think>" in text else ""
    pred = last_boxed(ans_part) if ans_part else None
    return {"looped": looped, "finished": eos, "closed_think": "</think>" in text, "boxed": pred is not None,
            "pred": pred, "correct": same(pred, item["answer"]), "tail_dup": round(dup_share(text.split()[-400:], 4), 3)}


@torch.no_grad()
def run_model(path, items, a, out_file):
    done = {json.loads(l)["id"] for l in open(out_file)} if os.path.exists(out_file) else set()
    todo = [x for x in items if x["id"] not in done]
    if not todo:
        return
    model, tok = M.load(path, a.device, a.dtype)
    tok.padding_side = "left"
    eos_ids = set(model.generation_config.eos_token_id if isinstance(model.generation_config.eos_token_id, list)
                  else [model.generation_config.eos_token_id])
    think_start = tok.convert_tokens_to_ids("<think>")
    todo.sort(key=lambda x: len(x["prefix"]))
    for b in range(0, len(todo), a.batch_size):
        batch = todo[b:b + a.batch_size]
        enc = tok([x["prefix"] for x in batch], return_tensors="pt", padding=True, add_special_tokens=False).to(a.device)
        V = model.get_output_embeddings().weight.shape[0]
        seen = torch.zeros(len(batch), V, dtype=torch.bool, device=a.device)
        for i, row in enumerate(enc.input_ids.tolist()):  # presence covers the answer so far (after <think>)
            k = len(row) - 1 - row[::-1].index(think_start) if think_start in row else 0
            seen[i, torch.tensor(row[k + 1:], dtype=torch.long)] = True
        stop = LoopStop(enc.input_ids.shape[1])
        torch.manual_seed(a.seed + b)
        t = time.time()
        y = model.generate(**enc, do_sample=True, temperature=a.temperature, top_p=a.top_p, top_k=a.top_k,
                           max_new_tokens=a.max_new_tokens, logits_processor=LogitsProcessorList([Presence(seen, a.presence)]),
                           stopping_criteria=StoppingCriteriaList([stop]), pad_token_id=tok.pad_token_id or 0)
        gen = y[:, enc.input_ids.shape[1]:].tolist()
        with open(out_file, "a") as f:
            for i, (x, g) in enumerate(zip(batch, gen)):
                eos_pos = next((j for j, t_ in enumerate(g) if t_ in eos_ids), None)
                g = g[:eos_pos + 1] if eos_pos is not None else g
                text = tok.decode(g, skip_special_tokens=False)
                r = {"id": x["id"], "set": x["set"], "tokens": len(g), **grade(x, text, i in stop.looped, eos_pos is not None),
                     "text": text}
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[{os.path.basename(out_file)}] batch {b // a.batch_size + 1}/{-(-len(todo) // a.batch_size)} "
              f"{time.time() - t:.0f}s", flush=True)
    del model


def summarize(rows):
    n = len(rows) or 1
    pct = lambda k: 100 * sum(bool(r[k]) for r in rows) / n
    out = {"n": len(rows), "correct": pct("correct"), "looped_again": pct("looped"), "closed_think": pct("closed_think"),
           "boxed": pct("boxed"), "cut_at_limit": 100 * sum((not r["looped"]) and (not r["finished"]) for r in rows) / n,
           "mean_tokens": sum(r["tokens"] for r in rows) / n}
    for s in sorted({r["set"] for r in rows}):
        sub = [r for r in rows if r["set"] == s]
        out[f"correct_{s}"] = 100 * sum(r["correct"] for r in sub) / len(sub)
        out[f"looped_{s}"] = 100 * sum(r["looped"] for r in sub) / len(sub)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", action="append", required=True, help="name=path (repeatable)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-new-tokens", type=int, default=2048)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument("--presence", type=float, default=1.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--dtype", default="bfloat16")
    a = ap.parse_args(argv)
    torch.set_num_threads(os.cpu_count())
    items = [json.loads(l) for l in open(a.data) if l.strip()]
    os.makedirs(a.out, exist_ok=True)
    summary = {}
    for spec in a.model:
        name, path = spec.split("=", 1)
        f = os.path.join(a.out, f"{name}.jsonl")
        run_model(path, items, a, f)
        summary[name] = summarize([json.loads(l) for l in open(f)])
    json.dump({"settings": vars(a), "summary": summary}, open(os.path.join(a.out, "summary.json"), "w"), indent=1)
    keys = ["correct", "looped_again", "closed_think", "boxed", "cut_at_limit", "mean_tokens"]
    print("| model | " + " | ".join(keys) + " |\n|" + "---|" * (len(keys) + 1))
    for k, v in summary.items():
        print(f"| {k} | " + " | ".join(f"{v[x]:.1f}" for x in keys) + " |")


if __name__ == "__main__":
    main()
