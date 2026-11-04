# Qwen3.5-0.8B: loop fix (weight-space edit)

The full edited model is `Qwen/Qwen3.5-0.8B` with **130,188 bf16 entries changed** in two tensors:
`model.language_model.layers.{15,19}.mlp.down_proj.weight`, 64 output columns each.

```
W' = W - 6 * outer(u, mask64 * (u @ W))      # u = the loop direction at that layer's output
```

There was no training, data or gradient step. These are the exact weights MIT CSAIL evaluated (run p50c). My
pipeline (`wsp.diagnose` → `wsp.edit --alpha 6`) independently finds the same two layers, the same 64 neurons per layer,
and the same direction (cosine 1.0000).

## Get the weights (about 1 minute)

```bash
huggingface-cli download Qwen/Qwen3.5-0.8B --revision 2fc06364715b967f1860aea9cf38778875588b17 --local-dir qwen35
```

```bash
cd weight-space && python -m wsp.pack apply ../qwen35 release/qwen35-0.8b-loopfix --out ../qwen35-loopfix
```

Every tensor is sha256-checked during the rebuild. To check the full file:

```
model.safetensors-00001-of-00001.safetensors
  base    04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696
  edited  ef4c8a34dfa883259ca29a4552358212e86fc40367789e2523ee24c7373abd40
```

The output folder is a normal Hugging Face model directory, so you can use it with `transformers`, vLLM or anything else that loads
Qwen3.5-0.8B. Recommended sampling with thinking on: temperature 0.6, top_p 0.95, top_k 20, presence_penalty 1.5, and a large
`max_tokens` (the edited model's correct answers often run 5–15k tokens).

## Results

| Test | Base | Edited |
|---|---|---|
| **Loop-rescue, this session** (48 held-out GSM8K/MATH-500 test loops, 1,024 tokens): fell back into a loop | 72.9% | **2.1%** |
| Loop-rescue: repeated-text share in the last 400 words | 0.80 | **0.36** |
| Guard, this session (80 held-out problems, thinking off): accuracy | 37.5% | **37.5%** |
| MIT, 32k tokens, vLLM: MATH-500 | 7.9% | **48.1%** |
| MIT: GSM8K | 13.8% | **67.1%** |
| MIT: IFEval | 21.0% | **62.3%** |
| MIT: MMLU-Pro (1k) | 40.3% | **45.5%** |
| MIT: MATH-500 answers that run to the 32k limit | 93.0% | **33.1%** |

In the loop-rescue test, 34 items looped only with the base model and 0 only with the edit (exact McNemar p = 1.2e-10). See
`results/report/index.html` for charts and examples.

## Limits

- The edit fixes one failure, the thinking loops. It does not add knowledge; Gemma 3 270M, which doesn't loop, gained nothing.
- CPU-only here, so the local test measures loop escape rather than final accuracy. The accuracy numbers above are MIT's.
- Weight-space editing was demonstrated with GPT-6 Astra and is unproven for other models. This edit passed the eval gate
  above; verify it on your own workload before you rely on it.

Credits: method and the original edit come from Takehi, Teoh, Zhang and Khattab (MIT CSAIL), via their logs
(github.com/RikiyaT/lm_understand-logs, CC BY 4.0).
