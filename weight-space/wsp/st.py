"""Numpy-only safetensors I/O, so weights can be read, diffed and patched without torch.

bfloat16 tensors are kept as their raw uint16 bit patterns; `to_f32` converts any tensor to float32.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import struct

import numpy as np

DT = {"BF16": np.uint16, "F16": np.float16, "F32": np.float32, "F64": np.float64, "I64": np.int64,
      "I32": np.int32, "I16": np.int16, "I8": np.int8, "U8": np.uint8, "BOOL": np.bool_}


def shards(path: str) -> list[str]:
    """A .safetensors file, or every .safetensors file in a model folder."""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.safetensors")))
        if not files:
            raise FileNotFoundError(f"No .safetensors files in {path}")
        return files
    return [path]


def header(path: str) -> tuple[dict, int]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return json.loads(f.read(n)), 8 + n


def index(path: str) -> dict[str, dict]:
    """{tensor name: {"file", "dtype", "shape", "offsets", "base"}} across all shards."""
    out = {}
    for file in shards(path):
        head, base = header(file)
        for name, info in head.items():
            if name != "__metadata__":
                out[name] = {"file": file, "dtype": info["dtype"], "shape": info["shape"],
                             "offsets": info["data_offsets"], "base": base}
    return out


def read(entry: dict) -> np.ndarray:
    a, b = entry["offsets"]
    with open(entry["file"], "rb") as f:
        f.seek(entry["base"] + a)
        buf = f.read(b - a)
    return np.frombuffer(buf, dtype=DT[entry["dtype"]]).reshape(entry["shape"])


def to_f32(arr: np.ndarray, dtype: str) -> np.ndarray:
    if dtype == "BF16":
        return (arr.astype(np.uint32) << 16).view(np.float32)
    return arr.astype(np.float32)


def from_f32(x: np.ndarray, dtype: str) -> np.ndarray:
    """float32 -> storage dtype; bfloat16 rounds to nearest even, like torch."""
    if dtype == "BF16":
        u = np.ascontiguousarray(x, dtype=np.float32).view(np.uint32).astype(np.uint64)
        u = u + 0x7FFF + ((u >> 16) & 1)
        return (u >> 16).astype(np.uint16)
    return x.astype(DT[dtype])


def load_file(path: str) -> tuple[dict, dict]:
    """({name: (dtype, array)}, metadata) for one shard."""
    head, base = header(path)
    meta = head.pop("__metadata__", None) or {}
    out = {}
    for name, info in head.items():
        out[name] = (info["dtype"], read({"file": path, "dtype": info["dtype"], "shape": info["shape"],
                                          "offsets": info["data_offsets"], "base": base}))
    return out, meta


def save_file(path: str, tensors: dict, metadata: dict | None = None) -> None:
    head, off, blobs = {}, 0, []
    for name in sorted(tensors):
        dt, arr = tensors[name]
        b = np.ascontiguousarray(arr).tobytes()
        head[name] = {"dtype": dt, "shape": list(arr.shape), "data_offsets": [off, off + len(b)]}
        off += len(b)
        blobs.append(b)
    if metadata:
        head["__metadata__"] = {k: str(v) for k, v in metadata.items()}
    h = json.dumps(head, separators=(",", ":")).encode()
    h += b" " * (-len(h) % 8)
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(h)))
        f.write(h)
        for b in blobs:
            f.write(b)


def sha256(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()
