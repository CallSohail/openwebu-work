# Weight-Space Editing Toolkit

Look at every weight of a small open model, find the direction a failure writes into it, remove that direction from a few columns, and keep the edit only if a paired evaluation shows it helped. There's no training, no gradients and no dataset.

This is a working implementation of the method from *Sparks of Weight-Space Programming* (Takehi, Teoh, Zhang, Khattab, MIT CSAIL, 2 Nov 2026). It was rebuilt from the authors' **full experiment logs** ([RikiyaT/lm_understand-logs](https://github.com/RikiyaT/lm_understand-logs), CC BY 4.0), not from the blog summary. The [AuremHQ/weight-space-programming](https://github.com/AuremHQ/weight-space-programming) repo describes the same workflow as a spec only; it has no code yet.

## What the research actually did (Experiment 8)

Qwen3.5-0.8B with thinking on gets stuck in loops: 85 of its first 96 practice answers ran to the 32,768-token limit. The editor model (GPT-6 Astra) worked through a Python kernel over the weights. It had a budget of 50 forward passes and used only practice problems, never the test sets. It found the following:

1. **Direction.** It built 4 matched text pairs. Each pair has the same prefix, then one text reasons productively and the other loops (`data/contrast_pairs_qwen_mit.json`). The loop direction is `u = unit(mean[resid_loop − resid_productive])`, taken at the output of layers 15 and 19 over the last 36 tokens.
2. **Writers.** For each MLP neuron `j`, it scored `(u · W_down[:, j]) × (act_loop_j − act_prod_j)`. It kept the top 64 per layer.
3. **Edit.** `W' = W − 6 · outer(u, mask64 · (u @ W))` on `layers.{15,19}.mlp.down_proj`. That is 131,072 weights, or 0.016% of the model.
4. **Controls.** A random direction with the same norm gained only +1.6 points. Teacher-forced log-prob fell 0.103 nats/token on the looping branch and 0.009 on the productive one.

| Qwen3.5-0.8B | MATH-500 | GSM8K | IFEval | MMLU-Pro | Answers cut at limit |
|---|---|---|---|---|---|
| Base | 7.9 | 13.8 | 21.0 | 40.3 | 93.0% |
| Edited (50 passes) | **48.1** | **67.1** | **62.3** | 45.5 | 33.1% |

The control is just as important. Gemma 3 270M rarely loops (8.5% of answers cut off), and nothing improved it. Editing **removes an obstacle that hides an ability; it does not add abilities.** Use this on one specific, repeatable failure such as loops, a stuck behaviour or a narrow quirk.

## Result in this repo: Qwen3.5-0.8B, improved

**Final weights:** [`release/qwen35-0.8b-loopfix/`](release/qwen35-0.8b-loopfix/). It's a 404 KB exact diff that rebuilds the full model with sha256 checks. **Report with charts:** `results/report/index.html`.

| | Base | Edited |
|---|---|---|
| Fell back into a loop (48 held-out test loops, this session) | 72.9% | **2.1%** |
| Guard: quick accuracy on 80 held-out problems | 37.5% | 37.5% |
| MATH-500 / GSM8K at 32k tokens (MIT's evaluation of these exact weights) | 7.9% / 13.8% | **48.1% / 67.1%** |

What was done:
1. Reproduced MIT's edit bit for bit.
2. Ran this pipeline blind. It picked the same layers (15, 19), the same 64 neurons per layer, and the same direction (cos 1.0000).
3. Tried four stronger or wider variants (α8, α10, top-128, 4 layers) on 16 dev loops plus a guard set. α10 finished some answers sooner but lost 3.75 guard points, so it was discarded. None beat α6.
4. Confirmed the α6 edit on 48 untouched test loops: 34 loops fixed, 0 introduced, McNemar p = 1.2e-10.

Everything is in `results/`.

## What's here

| Command | Step | What it does |
|---|---|---|
| `python -m wsp.inspect_weights MODEL --out report.html` | See | Builds a self-contained HTML page with every tensor's shape, mean/std/max, kurtosis, >6σ outliers, histogram, per-layer norms and a \|W\| heatmap. Add `--diff EDITED` to show exactly which entries and columns an edit changed. Needs numpy only. |
| `python -m wsp.reproduce_mit --show` | Study | Shows the MIT edit: layers, the 64 neuron IDs per layer and the formula. Needs no weights. |
| `python -m wsp.reproduce_mit QWEN_DIR --out qwen-mit` | Reproduce | Rebuilds MIT's edited model **bit for bit** from public Qwen weights (sha256-checked). It also recovers the loop direction `u` for each layer. |
| `python -m wsp.diagnose MODEL --pairs pairs.json --out diag` | 1. Diagnose | Runs the contrast-pair recipe above. It ranks layers by how much their MLP writes along the failure direction and picks the top-k writer neurons. |
| `python -m wsp.edit MODEL --directions diag/directions.pt --alpha 6 --out MODEL-edited` | 2. Fix | Applies the rank-one column edit to a copy. It refuses edits that touch more than 1% of weights and never overwrites the original. Writes a manifest with hashes and the command that reproduces the edit. |
| `python -m wsp.gate probe ORIG EDITED --pairs pairs.json` | 3a. Quick check | Takes seconds. Checks whether the edit makes the looping branch less likely while leaving the productive one alone. |
| `python -m wsp.loopprobe --data data/rescue_dev.jsonl --base M --model a=E` | Select | Scores candidates by teacher-forcing them on real base-model loops. |
| `python -m wsp.rescue --data data/rescue_test.jsonl --model base=M --model edited=E --out runs/x` | Test (CPU) | Continues real base-model loops from where they began, using MIT's sampling, and measures whether each model loops again or escapes. |
| `python -m wsp.guard --problems data/heldout_*.jsonl --model base=M --model edited=E --out g.json` | Guard | Measures reference-solution likelihood and quick-answer accuracy, so an edit can't silently damage the model. |
| `python -m wsp.pack create BASE EDITED --out pkg` / `apply BASE pkg --out DIR` | Ship | Packs an edit as an exact small diff, and rebuilds the full model from it with hash checks. |
| `python -m wsp.report --rescue runs/x --base M --edited final=E --out report` | Report | Builds the charts and a self-contained HTML page. |
| `python -m wsp.gate generate ORIG EDITED --problems p.jsonl --guard g.jsonl` | 3b. Gate | Paired generation with the same seeds and sampling for both models. Reports accuracy, cut rate and tail repetition. Prints **SHIP** only if the primary score gains ≥5 points and no guard set drops by more than 2. |

## Quickstart: Qwen3.5-0.8B

```bash
pip install -r requirements.txt
huggingface-cli download Qwen/Qwen3.5-0.8B --revision 2fc06364715b967f1860aea9cf38778875588b17 --local-dir qwen35
```

```bash
python -m wsp.reproduce_mit qwen35 --out qwen35-mit                # MIT's exact edited model
python -m wsp.inspect_weights qwen35 --diff qwen35-mit --out diff.html
python -m wsp.diagnose qwen35 --pairs data/contrast_pairs_qwen_mit.json --layers 15 19 --out diag
python -m wsp.edit qwen35 --directions diag/directions.pt --alpha 6 --out qwen35-mine
python -m wsp.gate probe qwen35 qwen35-mine --pairs data/contrast_pairs_qwen_mit.json
python -m wsp.gate generate qwen35 qwen35-mine --problems my_math.jsonl --max-new-tokens 32768 --device cuda --dtype bfloat16
```

Your own `diagnose` + `edit` run won't match MIT's to the bit. Their direction came from Astra's kernel at bf16 precision, so expect neuron overlap rather than identity. The gate decides whether your version is as good.

## Your own model

1. **Collect failures.** Run the model on your prompts and save 4–8 real failing outputs.
2. **Write pairs.** For each failure, keep the shared prefix and write one continuation that goes right and one that shows the failure (the model's own loop text). Use the raw chat template, exactly as the model sees it. The Qwen file is an example.
3. **Run the steps.** Run `diagnose` without `--layers` and let it pick the layers whose MLPs write the direction most. Then `edit` with alpha 1 → 2 → 4 → 6, running `probe` after each. Keep the smallest alpha that is selective.
4. **Run the gate.** Use problems the pairs were *not* built from, plus a guard set for something that must not get worse.

**Limits.** Models above 3B parameters are refused (a starting value). The tool won't touch embeddings, the output head or norms, and never removes safety or refusal behaviour. Log every attempt, including discarded ones.

## Hardware

Edits and probes run on CPU in seconds to minutes. The **real gate does not**: loops only show up with long generations (MIT used 32k tokens), so a fair evaluation needs a GPU. CPU runs with `--max-new-tokens 2048` only confirm that the mechanics work.

## Tests

`python -m pytest tests -q` builds a tiny random Qwen3.5-architecture model and runs diagnose → edit → probe → inspect. It checks the following:
- Only the selected columns change.
- u is projected out.
- Edits that are too broad are refused.
- The original is never overwritten.
- Grading works.
- The direction can be recovered from a rank-one diff.

## Honest limits

- The method is a blog post with public logs, not peer-reviewed or independently replicated. It was shown with one editor model.
- The headline gain depends on budget. MIT's 10- and 25-pass runs barely moved the loop rate.
- This toolkit automates the *recipe* Astra converged on. Finding a *different* kind of failure still needs reasoning about the diagnosis table, which is the part a frontier model or a person does.

Credits: research and logs by Takehi, Teoh, Zhang and Khattab (MIT CSAIL). `mit_edit/` and `data/contrast_pairs_qwen_mit.json` come from their logs under CC BY 4.0, and `data/qwen35_text_config.json` comes from Qwen's public release.
