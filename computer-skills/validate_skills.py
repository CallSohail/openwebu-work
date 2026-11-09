#!/usr/bin/env python3
"""Check every skill folder the way Open Terminal will read it.

Open Terminal lists a skill only when <folder>/SKILL.md starts with a
'---' frontmatter block that has a non-empty `description`. It shows at
most 1024 characters of the description and at most 50 resource files,
four folders deep. This script enforces those limits plus a few house rules.

  python validate_skills.py [skills_dir]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip("\"'")
    return out


def validate(skills_dir: Path) -> list[str]:
    problems, names = [], set()
    for d in sorted(p for p in Path(skills_dir).iterdir() if p.is_dir() and not p.name.startswith(".")):
        md = d / "SKILL.md"
        if not md.is_file():
            problems.append(f"{d.name}: missing SKILL.md"); continue
        text = md.read_text(encoding="utf-8")
        fm = frontmatter(text)
        name, desc = fm.get("name", ""), fm.get("description", "")
        if not desc:
            problems.append(f"{d.name}: empty description (Open Terminal would hide it)")
        if len(desc) > 1024:
            problems.append(f"{d.name}: description is {len(desc)} chars, limit 1024")
        if name != d.name:
            problems.append(f"{d.name}: name '{name}' should match the folder name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", name or ""):
            problems.append(f"{d.name}: name must be lowercase letters, digits and hyphens")
        if name in names:
            problems.append(f"{d.name}: duplicate name")
        names.add(name)
        resources = [p for p in d.rglob("*") if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts]
        if len(resources) > 50:
            problems.append(f"{d.name}: {len(resources)} resource files, Open Terminal lists only 50")
        if len(text) > 20000:
            problems.append(f"{d.name}: SKILL.md over 20000 chars, Open WebUI reads 20000 at a time by default")
        for ref in re.findall(r"scripts/[A-Za-z0-9_.-]+\.py", text):
            if not (d / ref).is_file():
                problems.append(f"{d.name}: references missing {ref}")
    return problems


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "skills"
    issues = validate(target)
    for i in issues:
        print("FAIL", i)
    count = sum(1 for p in target.iterdir() if p.is_dir())
    print(f"{count} skills checked, {len(issues)} problem(s).")
    sys.exit(1 if issues else 0)
