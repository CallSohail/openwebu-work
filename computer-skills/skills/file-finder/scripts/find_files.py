#!/usr/bin/env python3
"""Read-only file finder for an AI agent working on a real computer.

Never modifies, moves or deletes anything. Prints a table (default),
CSV (--csv) or JSON (--json). Standard library only.

Subcommands
  hidden   files/folders with the Windows hidden or system attribute, or a leading dot
  large    biggest files (--top N, --min-mb M)
  dupes    duplicate files by size then SHA-256
  recent   files modified in the last --days N
  temp     leftovers: ~$*, *.tmp, *.bak, .~lock*, Thumbs.db, desktop.ini, conflict copies
  name     names matching a glob or regex (--glob "*.pdf" | --regex "contrat|contract")
  sizes    folder sizes N levels deep (--depth 2)

Common options
  PATH ...           where to look (default: home folder)
  --exclude NAME     skip folders by name (repeatable). AppData caches, .git,
                     node_modules, __pycache__ and .scratch are skipped unless --all
  --all              do not apply default excludes
  --limit N          stop listing after N rows (default 200)
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections import defaultdict
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".scratch",
    "Cache", "Code Cache", "GPUCache", "CachedData", "Temp", "INetCache",
}
TEMP_PATTERNS = ["~$*", "*.tmp", "*.bak", ".~lock.*", "Thumbs.db", "desktop.ini", "*.crdownload", "*.part"]
CONFLICT_RE = re.compile(r"(-[A-Z0-9-]{3,}(\s?\(\d+\))?|\s\(conflicted copy.*\)|-copy|\s-\sCopie|\s-\sCopy)(?=\.[^.]+$)", re.I)

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def is_hidden(entry: os.DirEntry) -> tuple[bool, str]:
    kinds = []
    if entry.name.startswith("."):
        kinds.append("dot")
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes  # Windows only
        if attrs & FILE_ATTRIBUTE_HIDDEN:
            kinds.append("hidden")
        if attrs & FILE_ATTRIBUTE_SYSTEM:
            kinds.append("system")
    except (AttributeError, OSError):
        pass
    return bool(kinds), "+".join(kinds)


def walk(roots, excludes, include_dirs=False):
    """Yield (DirEntry, depth). Skips symlinked dirs and unreadable folders."""
    stack = [(Path(r).expanduser(), 0) for r in roots]
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name in excludes:
                                continue
                            if include_dirs:
                                yield e, depth
                            stack.append((Path(e.path), depth + 1))
                        elif e.is_file(follow_symlinks=False):
                            yield e, depth
                    except OSError:
                        continue
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue


def row(e: os.DirEntry, **extra):
    try:
        st = e.stat(follow_symlinks=False)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0
    r = {"path": e.path, "size": size, "size_h": human(size),
         "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else ""}
    r.update(extra)
    return r


def sha256(path: str, chunk=1 << 20) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while b := f.read(chunk):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def cmd_hidden(a):
    out = []
    for e, _ in walk(a.paths, a.excludes, include_dirs=True):
        hid, kind = is_hidden(e)
        if hid:
            out.append(row(e, kind=kind, type="dir" if e.is_dir(follow_symlinks=False) else "file"))
    return out


def cmd_large(a):
    rows = [row(e) for e, _ in walk(a.paths, a.excludes)]
    rows = [r for r in rows if r["size"] >= a.min_mb * 1024 * 1024]
    return sorted(rows, key=lambda r: -r["size"])[: a.top]


def cmd_dupes(a):
    by_size = defaultdict(list)
    for e, _ in walk(a.paths, a.excludes):
        try:
            s = e.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if s >= a.min_kb * 1024:
            by_size[s].append(e)
    out, group = [], 0
    for size, entries in sorted(by_size.items(), key=lambda kv: -kv[0]):
        if len(entries) < 2:
            continue
        by_hash = defaultdict(list)
        for e in entries:
            h = sha256(e.path)
            if h:
                by_hash[h].append(e)
        for h, same in by_hash.items():
            if len(same) > 1:
                group += 1
                for e in same:
                    out.append(row(e, group=group, sha256=h[:12]))
    return out


def cmd_recent(a):
    cutoff = time.time() - a.days * 86400
    rows = []
    for e, _ in walk(a.paths, a.excludes):
        try:
            if e.stat(follow_symlinks=False).st_mtime >= cutoff:
                rows.append(row(e))
        except OSError:
            pass
    return sorted(rows, key=lambda r: r["modified"], reverse=True)


def cmd_temp(a):
    out = []
    for e, _ in walk(a.paths, a.excludes):
        if any(fnmatch.fnmatch(e.name, p) for p in TEMP_PATTERNS):
            out.append(row(e, reason="temp/lock"))
        elif CONFLICT_RE.search(e.name):
            out.append(row(e, reason="possible conflict copy"))
    return out


def cmd_name(a):
    rx = re.compile(a.regex, re.I) if a.regex else None
    out = []
    for e, _ in walk(a.paths, a.excludes, include_dirs=True):
        if (a.glob and fnmatch.fnmatch(e.name.lower(), a.glob.lower())) or (rx and rx.search(e.name)):
            out.append(row(e))
    return out


def cmd_sizes(a):
    totals = defaultdict(int)
    for root in a.paths:
        root = Path(root).expanduser().resolve()
        for e, _ in walk([root], a.excludes):
            try:
                size = e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            rel = Path(e.path).resolve().relative_to(root).parts[:-1]
            for d in range(0, min(a.depth, len(rel)) + 1):
                totals[str(root.joinpath(*rel[:d]))] += size
    rows = [{"path": p, "size": s, "size_h": human(s), "modified": ""} for p, s in totals.items()]
    return sorted(rows, key=lambda r: -r["size"])


def emit(rows, a):
    rows = rows[: a.limit]
    if a.json:
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False); print(); return
    if not rows:
        print("No matches."); return
    cols = [c for c in rows[0].keys() if c != "size"]
    if a.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows); return
    widths = {c: min(max(len(c), *(len(str(r.get(c, ""))) for r in rows)), 90) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[: widths[c]].ljust(widths[c]) for c in cols))
    print(f"\n{len(rows)} row(s). Read-only scan, nothing was changed.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        p = sub.add_parser(name)
        p.add_argument("paths", nargs="*")
        p.add_argument("--exclude", action="append", default=[])
        p.add_argument("--all", action="store_true")
        p.add_argument("--limit", type=int, default=200)
        g = p.add_mutually_exclusive_group(); g.add_argument("--json", action="store_true"); g.add_argument("--csv", action="store_true")
        p.set_defaults(fn=fn)
        return p

    add("hidden", cmd_hidden)
    p = add("large", cmd_large); p.add_argument("--top", type=int, default=30); p.add_argument("--min-mb", type=float, default=0)
    p = add("dupes", cmd_dupes); p.add_argument("--min-kb", type=float, default=1)
    p = add("recent", cmd_recent); p.add_argument("--days", type=float, default=1)
    add("temp", cmd_temp)
    p = add("name", cmd_name); p.add_argument("--glob"); p.add_argument("--regex")
    p = add("sizes", cmd_sizes); p.add_argument("--depth", type=int, default=2)

    a = ap.parse_args(argv)
    a.paths = a.paths or [str(Path.home())]
    a.excludes = set(a.exclude) | (set() if a.all else DEFAULT_EXCLUDES)
    if a.cmd == "name" and not (a.glob or a.regex):
        ap.error("name needs --glob or --regex")
    emit(a.fn(a), a)


if __name__ == "__main__":
    main()
