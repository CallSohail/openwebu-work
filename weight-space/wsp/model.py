"""Torch helpers: load a model, find its decoder layers, record residual streams and MLP activations."""
from __future__ import annotations

import re

import torch


def load(path: str, device: str = "cpu", dtype: str = "float32"):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForCausalLM.from_pretrained(path, dtype=getattr(torch, dtype)).to(device).eval()
    return model, tok


def n_params(model) -> int:
    return sum(p.numel() for p in model.parameters())


def layers(model) -> dict[int, torch.nn.Module]:
    """{index: decoder layer} for any HF decoder whose layers have .mlp.down_proj."""
    out = {}
    for name, mod in model.named_modules():
        m = re.search(r"layers\.(\d+)$", name)
        if m and hasattr(mod, "mlp") and hasattr(mod.mlp, "down_proj"):
            out[int(m.group(1))] = mod
    if not out:
        raise RuntimeError("No decoder layers with mlp.down_proj found")
    return dict(sorted(out.items()))


def down_proj(model, layer: int) -> torch.Tensor:
    """W of layer's MLP output matrix, shape [d_model, d_mlp] (column j = what neuron j writes)."""
    return layers(model)[layer].mlp.down_proj.weight


@torch.no_grad()
def record(model, ids: list[int], tail: int) -> tuple[torch.Tensor, torch.Tensor]:
    """One forward pass over token ids.

    Returns (resid [n_layers+1, d_model], mlp [n_layers, d_mlp]): the mean over the last `tail` tokens of
    every residual stream (index 0 = embeddings, index l+1 = output of layer l) and of every MLP's
    hidden activation (the input of down_proj, i.e. how strongly each neuron fires).
    """
    L = layers(model)
    acts: dict[int, torch.Tensor] = {}
    hooks = [L[i].mlp.down_proj.register_forward_pre_hook(
        lambda m, inp, i=i: acts.__setitem__(i, inp[0][0, -tail:].float().mean(0).cpu())) for i in L]
    try:
        dev = next(model.parameters()).device
        out = model(input_ids=torch.tensor([ids], device=dev), output_hidden_states=True, use_cache=False)
    finally:
        for h in hooks:
            h.remove()
    resid = torch.stack([h[0, -tail:].float().mean(0).cpu() for h in out.hidden_states])
    return resid, torch.stack([acts[i] for i in L])


@torch.no_grad()
def token_logprobs(model, ids: list[int]) -> torch.Tensor:
    """log p(token_t | tokens_<t) for t = 1..n-1."""
    dev = next(model.parameters()).device
    x = torch.tensor([ids], device=dev)
    logits = model(input_ids=x, use_cache=False).logits[0, :-1].float()
    return torch.log_softmax(logits, -1).gather(1, x[0, 1:, None])[:, 0].cpu()
