"""Extract the readable source embedded in email_composer.py without executing it."""

from __future__ import annotations

import ast
import base64
import gzip
from pathlib import Path


HERE = Path(__file__).resolve().parent
DIST = HERE / "email_composer.py"
OUTPUT = HERE / "email_composer_readable.py"


def extract_payload(path: Path) -> bytes:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_PAYLOAD" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("_PAYLOAD is not a bytes literal")
        return bytes(value)
    raise RuntimeError("Could not find _PAYLOAD in email_composer.py")


def main() -> None:
    payload = extract_payload(DIST)
    source = gzip.decompress(base64.b64decode(payload))
    OUTPUT.write_bytes(source)
    print(f"Wrote {OUTPUT} ({len(source)} bytes)")


if __name__ == "__main__":
    main()
