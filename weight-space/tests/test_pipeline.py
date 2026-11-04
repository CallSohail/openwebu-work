"""End-to-end check of diagnose -> edit -> probe -> inspect on a tiny random Qwen3.5-architecture model.

    python -m pytest tests -q        (from weight-space/)

Verifies the mechanics, not that any edit helps: that needs the real model and wsp.gate.
"""
import json
import os
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wsp import diagnose, edit, gate, inspect_weights, st  # noqa: E402
from wsp import model as M  # noqa: E402


class ByteTok:
    """Stand-in tokenizer: one token per byte."""
    def __call__(self, text, add_special_tokens=False):
        return {"input_ids": list(text.encode())}


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    from transformers import AutoConfig, AutoModelForCausalLM
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "data", "qwen35_text_config.json")))
    cfg.update(hidden_size=64, intermediate_size=128, num_hidden_layers=4, num_attention_heads=2, num_key_value_heads=1,
               head_dim=32, linear_num_key_heads=2, linear_num_value_heads=2, linear_key_head_dim=16,
               linear_value_head_dim=16, vocab_size=256, layer_types=["linear_attention"] * 3 + ["full_attention"],
               eos_token_id=0, tie_word_embeddings=True)
    torch.manual_seed(0)
    m = AutoModelForCausalLM.from_config(AutoConfig.for_model(**cfg)).eval()
    d = tmp_path_factory.mktemp("tiny")
    m.save_pretrained(d)
    return m, str(d)


PAIRS = [{"productive": "Q: 2+3? A: 2+3=5. Check: 5-3=2. Done, the answer is 5.",
          "repetitive": "Q: 2+3? A: 2+3=5. But is it 5? Yes. But is it 5? Yes. But is it 5? Yes."},
         {"productive": "Q: 4*6? A: 4*6=24. Check: 24/6=4. Done, the answer is 24.",
          "repetitive": "Q: 4*6? A: 4*6=24. Now 4*6=24. Now 4*6=24. Now 4*6=24. Now 4*6=24."}]


def test_full_pipeline(tiny, tmp_path):
    m, path = tiny
    table, out = diagnose.diagnose(m, ByteTok(), PAIRS, layers=[1, 2], top_k=8, tail=12, writer_tail=8)
    assert len(table) == 4 and set(out) == {1, 2}
    for v in out.values():
        assert abs(float(v["u"].norm()) - 1) < 1e-5 and len(v["neurons"]) == 8

    dirs = {l: {"u": v["u"].numpy(), "neurons": v["neurons"].numpy()} for l, v in out.items()}
    dst = str(tmp_path / "edited")
    man = edit.edit(path, dirs, alpha=1.0, out=dst, max_share=0.2)
    assert man["weights_touched"] == 2 * 64 * 8

    a, b = st.index(path), st.index(dst)
    for t in man["tensors"]:
        dt = a[t["name"]]["dtype"]
        W0, W1 = st.to_f32(st.read(a[t["name"]]), dt), st.to_f32(st.read(b[t["name"]]), dt)
        changed_cols = np.flatnonzero((W0 != W1).any(0))
        assert set(changed_cols) <= set(t["columns"])           # only the selected columns move
        u = dirs[t["layer"]]["u"] / np.linalg.norm(dirs[t["layer"]]["u"])
        before, after = np.abs(u @ W0[:, t["columns"]]), np.abs(u @ W1[:, t["columns"]])
        assert after.max() < 0.05 * before.max()                  # alpha=1 projects u out (up to bf16 rounding)
        others = [n for n in a if n != t["name"] and n in b and "down_proj" not in n]
        assert all(np.array_equal(st.read(a[n]), st.read(b[n])) for n in others[:20])

    from transformers import AutoModelForCausalLM
    e = AutoModelForCausalLM.from_pretrained(dst).eval()
    r = gate.probe(m, e, ByteTok(), PAIRS)
    assert set(r) == {"productive", "repetitive"}

    rep = inspect_weights.inspect(path, dst)
    assert rep["changed_total"] > 0 and rep["changed_pct"] < 20


def test_too_broad_is_refused(tiny, tmp_path):
    m, path = tiny
    _, out = diagnose.diagnose(m, ByteTok(), PAIRS, layers=[1], top_k=128, tail=12, writer_tail=8)
    dirs = {l: {"u": v["u"].numpy(), "neurons": v["neurons"].numpy()} for l, v in out.items()}
    with pytest.raises(SystemExit, match="Too broad"):
        edit.edit(path, dirs, 1.0, str(tmp_path / "x"))


def test_never_overwrites_original(tiny):
    _, path = tiny
    with pytest.raises(SystemExit, match="original"):
        edit.edit(path, {}, 1.0, path)


def test_grading():
    assert gate.last_boxed(r"so \boxed{\frac{1}{2}} and \boxed{760}") == "760"
    assert gate.same("1,000", "1000") and gate.same(r"\frac{1}{2}", "0.5") and not gate.same("3", "4")
    assert gate.tail_repetition([1, 2, 3, 4] * 50) > 0.9 and gate.tail_repetition(list(range(200))) == 0


def test_recover_u_from_rank_one_edit():
    from wsp.reproduce_mit import recover_u
    rng = np.random.default_rng(0)
    W0 = rng.normal(size=(32, 100)).astype(np.float32)
    u = rng.normal(size=32); u /= np.linalg.norm(u)
    cols = [3, 7, 50]
    W1 = edit.apply(W0, u, np.array(cols), 6.0)
    assert abs(abs(float(recover_u(W0, W1, cols) @ u)) - 1) < 1e-4
