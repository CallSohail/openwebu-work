#!/usr/bin/env python3
"""Task scratch space with a cleanup ledger.

Every helper script, temp file or intermediate output that an AI agent
creates to get a task done goes into a per-task scratch folder. When the
task ends, `finish` keeps the deliverables the user asked for and removes
everything else the task created. Nothing outside the scratch folder is
ever deleted unless the agent registered it with `track` during this task
and it did not exist before.

Standard library only, so it runs on the Python that Tether ships.

Usage
  scratch.py start <task-name> [--root DIR]   create a scratch folder, print its path
  scratch.py track <path> [...]               register extra temp files created outside it
  scratch.py keep <path> [--to DIR]           mark a scratch file as a deliverable (moved out on finish)
  scratch.py status                           show what finish would keep and remove
  scratch.py finish [--dry-run]               move deliverables out, delete the rest
  scratch.py abandon                          stop tracking without deleting (for debugging)

State lives in <root>/.scratch/ACTIVE (one active task per root).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

LEDGER = "ledger.json"
DEFAULT_ROOT_ENV = "SCRATCH_ROOT"


def default_root() -> Path:
    env = os.environ.get(DEFAULT_ROOT_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()


def scratch_base(root: Path) -> Path:
    return root / ".scratch"


def active_file(root: Path) -> Path:
    return scratch_base(root) / "ACTIVE"


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip()).strip("-.")
    return (s or "task")[:48]


def load(root: Path) -> tuple[Path, dict]:
    marker = active_file(root)
    if not marker.is_file():
        sys.exit(f"No active task under {root}. Run: scratch.py start <task-name>")
    task_dir = Path(marker.read_text(encoding="utf-8").strip())
    ledger_path = task_dir / LEDGER
    if not ledger_path.is_file():
        sys.exit(f"Ledger missing in {task_dir}. Run abandon, then start again.")
    return task_dir, json.loads(ledger_path.read_text(encoding="utf-8"))


def save(task_dir: Path, ledger: dict) -> None:
    (task_dir / LEDGER).write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def created_at(p: Path) -> float:
    """Best available creation time: Windows st_ctime, macOS st_birthtime,
    Linux falls back to the older of mtime and ctime (conservative)."""
    st = p.stat()
    if os.name == "nt":
        return st.st_ctime
    birth = getattr(st, "st_birthtime", None)
    if birth:
        return birth
    return min(st.st_mtime, st.st_ctime)


def inside(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cmd_start(args) -> None:
    root = Path(args.root).expanduser().resolve() if args.root else default_root()
    if not root.is_dir():
        sys.exit(f"Root folder does not exist: {root}")
    if root == Path(root.anchor):
        sys.exit("Refusing to use a drive or filesystem root as the scratch root.")
    base = scratch_base(root)
    if active_file(root).is_file():
        prev = active_file(root).read_text(encoding="utf-8").strip()
        sys.exit(f"A task is already active: {prev}\nRun finish (or abandon) first.")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    task_dir = base / f"{stamp}-{slug(args.name)}"
    task_dir.mkdir(parents=True, exist_ok=False)
    gi = base / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n", encoding="utf-8")
    ledger = {
        "task": args.name,
        "root": str(root),
        "started": dt.datetime.now().isoformat(timespec="seconds"),
        "tracked": [],   # temp files outside the scratch dir, created by this task
        "keep": [],      # {"src": path in scratch, "dest": final path}
    }
    save(task_dir, ledger)
    active_file(root).write_text(str(task_dir), encoding="utf-8")
    print(task_dir)


def cmd_track(args) -> None:
    root = default_root() if not args.root else Path(args.root).resolve()
    task_dir, ledger = load(root)
    started = dt.datetime.fromisoformat(ledger["started"]).timestamp()
    for raw in args.paths:
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            print(f"skip (does not exist): {p}")
            continue
        if inside(task_dir, p):
            print(f"skip (already inside scratch): {p}")
            continue
        # Only files this task created may be tracked for deletion.
        if created_at(p) < started - 1:
            print(f"refused (existed before the task started): {p}")
            continue
        if str(p) not in ledger["tracked"]:
            ledger["tracked"].append(str(p))
            print(f"tracked: {p}")
    save(task_dir, ledger)


def cmd_keep(args) -> None:
    root = default_root() if not args.root else Path(args.root).resolve()
    task_dir, ledger = load(root)
    src = Path(args.path).expanduser().resolve()
    if not inside(task_dir, src) or not src.exists():
        sys.exit(f"keep only applies to files inside the scratch folder: {task_dir}")
    dest_dir = Path(args.to).expanduser().resolve() if args.to else Path(ledger["root"]) / "outputs"
    ledger["keep"] = [k for k in ledger["keep"] if k["src"] != str(src)]
    ledger["keep"].append({"src": str(src), "dest": str(dest_dir / src.name)})
    save(task_dir, ledger)
    print(f"will keep: {src.name} -> {dest_dir / src.name}")


def plan(task_dir: Path, ledger: dict) -> tuple[list[dict], list[Path]]:
    keep = ledger["keep"]
    keep_src = {k["src"] for k in keep}
    remove: list[Path] = []
    for p in sorted(task_dir.rglob("*")):
        if p.name == LEDGER or str(p.resolve()) in keep_src:
            continue
        if p.is_file() or p.is_symlink():
            remove.append(p)
    for t in ledger["tracked"]:
        tp = Path(t)
        if tp.exists():
            remove.append(tp)
    return keep, remove


def cmd_status(args) -> None:
    root = default_root() if not args.root else Path(args.root).resolve()
    task_dir, ledger = load(root)
    keep, remove = plan(task_dir, ledger)
    print(f"Task: {ledger['task']}  (started {ledger['started']})\nScratch: {task_dir}")
    print("Keep:")
    for k in keep:
        print(f"  {Path(k['src']).name} -> {k['dest']}")
    if not keep:
        print("  (nothing marked)")
    print("Remove:")
    for r in remove:
        print(f"  {r}")
    if not remove:
        print("  (nothing)")


def unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 2
    while True:
        cand = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


def cmd_finish(args) -> None:
    root = default_root() if not args.root else Path(args.root).resolve()
    task_dir, ledger = load(root)
    keep, remove = plan(task_dir, ledger)
    if args.dry_run:
        cmd_status(args)
        return
    moved = []
    for k in keep:
        src, dest = Path(k["src"]), unique(Path(k["dest"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved.append(dest)
    removed = 0
    for t in ledger["tracked"]:
        tp = Path(t)
        if tp.is_dir():
            shutil.rmtree(tp, ignore_errors=True); removed += 1
        elif tp.exists():
            tp.unlink(); removed += 1
    # The scratch dir itself goes as a whole; it only ever held task files.
    if inside(scratch_base(Path(ledger["root"])), task_dir):
        removed += sum(1 for p in task_dir.rglob("*") if p.is_file()) - 1
        shutil.rmtree(task_dir, ignore_errors=True)
    active_file(Path(ledger["root"])).unlink(missing_ok=True)
    base = scratch_base(Path(ledger["root"]))
    try:
        leftovers = [p for p in base.iterdir() if p.name != ".gitignore"]
        if not leftovers:
            shutil.rmtree(base, ignore_errors=True)
    except FileNotFoundError:
        pass
    print(f"Finished '{ledger['task']}': kept {len(moved)}, removed {max(removed, 0)} temp file(s).")
    for m in moved:
        print(f"  deliverable: {m}")


def cmd_abandon(args) -> None:
    root = default_root() if not args.root else Path(args.root).resolve()
    task_dir, _ = load(root)
    active_file(root).unlink(missing_ok=True)
    print(f"Stopped tracking. Scratch left in place for inspection: {task_dir}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="work folder (default: $SCRATCH_ROOT or current directory)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("name"); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("track"); s.add_argument("paths", nargs="+"); s.set_defaults(fn=cmd_track)
    s = sub.add_parser("keep"); s.add_argument("path"); s.add_argument("--to"); s.set_defaults(fn=cmd_keep)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("finish"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_finish)
    s = sub.add_parser("abandon"); s.set_defaults(fn=cmd_abandon)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
