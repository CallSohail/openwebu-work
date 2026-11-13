#!/usr/bin/env python3
"""owctl - a reliable local control tool for AI assistants on Windows.

An AI running through Open WebUI / Open Terminal / Tether calls ONE quoting-safe
command and gets clean JSON back, instead of composing fragile PowerShell that
cmd.exe breaks. Every destructive action is a two-phase, token-gated state
machine: `plan` captures a snapshot + token; `apply --token` only acts if the
world still matches that snapshot (compare-and-swap), so a stale plan can never
run. Deletions go to a reversible quarantine until you `purge`.

Design invariants ("the lift rule"):
  1. Single source of truth  - one command, deterministic JSON.
  2. Compare-and-swap         - apply is welded to the exact state it planned.
  3. Mutual exclusion         - a lockfile prevents two mutations at once.
  4. Reversibility            - delete = move to quarantine; purge frees space.

Stdlib only. Python 3.8+. Safe to run read-only commands without a lock.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

SCHEMA = 1
IS_WINDOWS = os.name == "nt"

# --- state location -------------------------------------------------------

def state_dir() -> str:
    base = os.environ.get("OWCTL_HOME")
    if not base:
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = os.path.join(root, "owctl")
    os.makedirs(base, exist_ok=True)
    for sub in ("plans", "quarantine"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base

# --- paths that must never be touched -------------------------------------

def _deny_roots() -> list:
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    drive = os.environ.get("SystemDrive", "C:") + os.sep
    roots = [
        sysroot,
        os.path.join(drive, "Program Files"),
        os.path.join(drive, "Program Files (x86)"),
        os.path.join(drive, "ProgramData"),
        os.path.join(drive, "$Recycle.Bin"),
        os.path.join(drive, "System Volume Information"),
    ]
    return [os.path.normcase(os.path.abspath(r)) for r in roots]

DENY = _deny_roots()

def is_protected(path: str) -> bool:
    """True if path is inside a protected system location (never modify)."""
    try:
        p = os.path.normcase(os.path.abspath(path))
    except Exception:
        return True
    for root in DENY:
        if p == root or p.startswith(root + os.sep):
            return True
    return False

# --- small helpers --------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def gb(n: int) -> float:
    return round(n / 1073741824, 2)

def emit(obj: dict) -> None:
    obj.setdefault("ok", True)
    obj.setdefault("schema", SCHEMA)
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def fail(msg: str, **extra) -> None:
    out = {"ok": False, "error": str(msg), "schema": SCHEMA}
    out.update(extra)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1)

def stat_sig(path: str):
    """Return (size, mtime_ns) or None if the path is gone/unreadable."""
    try:
        st = os.stat(path)
        return st.st_size, st.st_mtime_ns
    except OSError:
        return None

# --- mutual exclusion lock ------------------------------------------------

class Lock:
    """Cross-process lock via atomic O_EXCL create. Only one mutation at a time."""

    def __init__(self, stale_seconds: int = 300):
        self.path = os.path.join(state_dir(), "owctl.lock")
        self.stale = stale_seconds
        self.held = False

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()} {now_iso()}".encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > self.stale:
                        os.remove(self.path)   # crashed owner; reclaim
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    fail("owctl is busy: another operation holds the lock",
                         code="busy")
                time.sleep(0.2)

    def __exit__(self, *exc):
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass

# --- system info ----------------------------------------------------------

def _find_fastfetch() -> str:
    exe = shutil.which("fastfetch")
    if exe:
        return exe
    for cand in (
        os.path.expandvars(r"%USERPROFILE%\tools\fastfetch\fastfetch.exe"),
        os.path.expandvars(r"%USERPROFILE%\Desktop\Tether\fastfetch\fastfetch.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return ""

def _mem_windows():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    total, avail = m.ullTotalPhys, m.ullAvailPhys
    return {"total_gb": gb(total), "used_gb": gb(total - avail),
            "free_gb": gb(avail), "used_pct": m.dwMemoryLoad}

def _disks():
    out = []
    if IS_WINDOWS:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        letters = [f"{chr(65 + i)}:\\" for i in range(26) if bitmask & (1 << i)]
    else:
        letters = ["/"]
    for d in letters:
        try:
            u = shutil.disk_usage(d)
        except OSError:
            continue
        out.append({"drive": d.rstrip("\\") or "/", "total_gb": gb(u.total),
                    "free_gb": gb(u.free), "used_gb": gb(u.used),
                    "free_pct": round(u.free / u.total * 100) if u.total else 0})
    return out

def cmd_sysinfo(args):
    result = {"collected_at": now_iso(), "source": None}
    ff = _find_fastfetch()
    if ff and not args.builtin:
        try:
            raw = subprocess.run([ff, "--format", "json"], capture_output=True,
                                 text=True, timeout=20)
            if raw.returncode == 0 and raw.stdout.strip():
                data = json.loads(raw.stdout)
                result["source"] = "fastfetch"
                result["fastfetch"] = _summarize_fastfetch(data)
                result["fastfetch_path"] = ff
                emit(result)
                return
        except Exception:
            pass  # fall through to builtin
    # builtin fallback (no install needed)
    result["source"] = "builtin"
    result["os"] = {"system": _platform_name(), "release": os.sys.getwindowsversion().build
                    if IS_WINDOWS else os.uname().release}
    result["cpu"] = {"logical_cores": os.cpu_count()}
    if IS_WINDOWS:
        try:
            result["memory"] = _mem_windows()
        except Exception as e:
            result["memory_error"] = str(e)
    result["disks"] = _disks()
    emit(result)

def _platform_name():
    if IS_WINDOWS:
        v = sys.getwindowsversion()
        return f"Windows build {v.build}"
    return sys.platform

def _summarize_fastfetch(data):
    """Reduce fastfetch's verbose array to the fields that matter, in GB."""
    out = {}
    for item in data if isinstance(data, list) else []:
        t, r = item.get("type"), item.get("result")
        if not isinstance(r, dict) and t not in ("OS",):
            if not isinstance(r, (dict, list)):
                continue
        if t == "CPU" and isinstance(r, dict):
            out["cpu"] = {"name": r.get("cpu"), "cores": (r.get("cores") or {}).get("physical"),
                          "threads": (r.get("cores") or {}).get("logical")}
        elif t == "GPU" and isinstance(r, list) and r:
            out["gpu"] = [g.get("name") for g in r]
        elif t == "Memory" and isinstance(r, dict):
            tot, used = r.get("total", 0), r.get("used", 0)
            out["memory"] = {"total_gb": gb(tot), "used_gb": gb(used),
                             "free_gb": gb(max(tot - used, 0)),
                             "used_pct": round(used / tot * 100) if tot else 0}
        elif t == "Disk" and isinstance(r, list):
            out["disks"] = [{"mount": d.get("mountpoint"),
                             "total_gb": gb((d.get("bytes") or {}).get("total", 0)),
                             "free_gb": gb((d.get("bytes") or {}).get("free", 0)),
                             "free_pct": round((d.get("bytes") or {}).get("free", 0) /
                                               (d.get("bytes") or {}).get("total", 1) * 100)}
                            for d in r]
        elif t == "OS" and isinstance(r, dict):
            out["os"] = r.get("prettyName") or r.get("name")
        elif t == "Host" and isinstance(r, dict):
            out["host"] = r.get("name")
        elif t == "Battery" and isinstance(r, list) and r:
            out["battery"] = {"pct": r[0].get("capacity"), "status": r[0].get("status")}
        elif t == "Uptime" and isinstance(r, dict):
            out["uptime_since"] = r.get("bootTime")
    return out

# --- junk discovery (safe categories only) --------------------------------

def _candidate_roots(categories, days_old):
    """Yield (category, root, recurse, older_than_days) for requested categories."""
    home = os.path.expanduser("~")
    temp = os.environ.get("TEMP") or os.path.join(home, "AppData", "Local", "Temp")
    table = {
        "temp": (temp, True, 0),
        "downloads-old": (os.path.join(home, "Downloads"), False, days_old),
        "desktop-old": (os.path.join(home, "Desktop"), False, days_old),
    }
    for cat in categories:
        if cat in table:
            root, recurse, older = table[cat]
            if os.path.isdir(root):
                yield cat, root, recurse, older

def _scan_items(categories, days_old, max_items=5000):
    cutoff = time.time() - days_old * 86400 if days_old else None
    items, truncated = [], False
    for cat, root, recurse, older in _candidate_roots(categories, days_old):
        this_cut = time.time() - older * 86400 if older else None
        walker = os.walk(root) if recurse else [(root, [], os.listdir(root))]
        for dirpath, _dirs, files in walker:
            if is_protected(dirpath):
                continue
            for name in files:
                p = os.path.join(dirpath, name)
                if is_protected(p) or os.path.islink(p):
                    continue
                sig = stat_sig(p)
                if not sig:
                    continue
                size, mtime_ns = sig
                if this_cut and (mtime_ns / 1e9) > this_cut:
                    continue  # too new to be "old"
                items.append({"path": p, "category": cat, "size": size,
                              "mtime_ns": mtime_ns})
                if len(items) >= max_items:
                    truncated = True
                    break
            if truncated:
                break
        if truncated:
            break
    return items, truncated

# --- disk scan (read-only report) -----------------------------------------

def cmd_scan(args):
    base = os.path.abspath(args.path)
    if not os.path.isdir(base):
        fail(f"not a directory: {base}")
    big_files, folder_sizes = [], {}
    for dirpath, dirs, files in os.walk(base):
        if is_protected(dirpath):
            dirs[:] = []
            continue
        total = 0
        for name in files:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                continue
            sig = stat_sig(p)
            if not sig:
                continue
            total += sig[0]
            big_files.append((sig[0], p))
        folder_sizes[dirpath] = folder_sizes.get(dirpath, 0) + total
    big_files.sort(reverse=True)
    top_folders = sorted(folder_sizes.items(), key=lambda x: x[1], reverse=True)
    emit({
        "path": base,
        "collected_at": now_iso(),
        "disks": _disks(),
        "top_files": [{"gb": gb(s), "path": p} for s, p in big_files[:args.top]],
        "top_folders": [{"gb": gb(s), "path": p} for p, s in top_folders[:args.top]],
    })

# --- plan (snapshot + token) ----------------------------------------------

def _plan_token(items):
    h = hashlib.sha256()
    for it in sorted(items, key=lambda x: x["path"]):
        h.update(f"{it['path']}|{it['size']}|{it['mtime_ns']}\n".encode("utf-8", "replace"))
    return h.hexdigest()[:16]

def cmd_plan(args):
    cats = [c.strip() for c in args.categories.split(",") if c.strip()]
    known = {"temp", "downloads-old", "desktop-old"}
    bad = [c for c in cats if c not in known]
    if bad:
        fail(f"unknown categories: {bad}. known: {sorted(known)}")
    items, truncated = _scan_items(cats, args.days)
    token = _plan_token(items)
    plan = {"token": token, "created_at": now_iso(), "categories": cats,
            "days_old": args.days, "items": items, "truncated": truncated}
    with open(os.path.join(state_dir(), "plans", token + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(plan, f)
    by_cat = {}
    for it in items:
        c = by_cat.setdefault(it["category"], {"count": 0, "bytes": 0})
        c["count"] += 1
        c["bytes"] += it["size"]
    emit({
        "token": token,
        "total_items": len(items),
        "total_gb": gb(sum(i["size"] for i in items)),
        "by_category": {k: {"count": v["count"], "gb": gb(v["bytes"])}
                        for k, v in by_cat.items()},
        "truncated": truncated,
        "note": "Nothing was changed. Review, then run: owctl apply --token " + token,
    })

# --- apply (compare-and-swap, reversible) ---------------------------------

def cmd_apply(args):
    plan_path = os.path.join(state_dir(), "plans", args.token + ".json")
    if not os.path.isfile(plan_path):
        fail(f"no plan with token {args.token}. Run owctl plan first.")
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    with Lock():
        applied, skipped_changed, skipped_gone, failed = [], [], [], []
        qid = datetime.now().strftime("%Y%m%d-%H%M%S")
        qdir = os.path.join(state_dir(), "quarantine", qid)
        manifest = []
        for idx, it in enumerate(plan["items"]):
            p = it["path"]
            if is_protected(p):
                failed.append({"path": p, "why": "protected"})
                continue
            sig = stat_sig(p)
            if sig is None:
                skipped_gone.append(p)             # vanished since plan: fine
                continue
            if sig[0] != it["size"] or sig[1] != it["mtime_ns"]:
                skipped_changed.append(p)          # THE LIFT RULE: state moved
                continue
            try:
                os.makedirs(qdir, exist_ok=True)
                dest = os.path.join(qdir, f"{idx:06d}__{os.path.basename(p)}")
                shutil.move(p, dest)
                manifest.append({"q": dest, "original": p, "size": it["size"]})
                applied.append(p)
            except OSError as e:
                failed.append({"path": p, "why": str(e)})
        if manifest:
            with open(os.path.join(qdir, "_manifest.json"), "w", encoding="utf-8") as f:
                json.dump({"id": qid, "created_at": now_iso(), "items": manifest}, f)
    emit({
        "quarantine_id": qid if manifest else None,
        "moved_to_quarantine": len(applied),
        "freed_reversibly_gb": gb(sum(m["size"] for m in manifest)),
        "skipped_changed": len(skipped_changed),
        "skipped_already_gone": len(skipped_gone),
        "failed": failed,
        "note": ("Files are in quarantine and can be restored. To free the space "
                 "permanently: owctl purge --id " + qid) if manifest else
                "Nothing was moved.",
        "changed_detail": skipped_changed[:20],
    })

# --- quarantine management ------------------------------------------------

def _quarantines():
    root = os.path.join(state_dir(), "quarantine")
    out = []
    for qid in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        mpath = os.path.join(root, qid, "_manifest.json")
        if os.path.isfile(mpath):
            with open(mpath, encoding="utf-8") as f:
                out.append(json.load(f))
    return out

def cmd_quarantine(args):
    qs = _quarantines()
    emit({"quarantines": [{"id": q["id"], "created_at": q["created_at"],
                           "items": len(q["items"]),
                           "gb": gb(sum(i["size"] for i in q["items"]))} for q in qs]})

def cmd_restore(args):
    with Lock():
        qdir = os.path.join(state_dir(), "quarantine", args.id)
        mpath = os.path.join(qdir, "_manifest.json")
        if not os.path.isfile(mpath):
            fail(f"no quarantine {args.id}")
        with open(mpath, encoding="utf-8") as f:
            man = json.load(f)
        restored, conflict, failed = [], [], []
        for it in man["items"]:
            if os.path.exists(it["original"]):
                conflict.append(it["original"])   # something new took the name
                continue
            try:
                os.makedirs(os.path.dirname(it["original"]), exist_ok=True)
                shutil.move(it["q"], it["original"])
                restored.append(it["original"])
            except OSError as e:
                failed.append({"path": it["original"], "why": str(e)})
        if not conflict and not failed:
            shutil.rmtree(qdir, ignore_errors=True)
    emit({"restored": len(restored), "conflicts": conflict, "failed": failed})

def cmd_purge(args):
    with Lock():
        qdir = os.path.join(state_dir(), "quarantine", args.id)
        if not os.path.isdir(qdir):
            fail(f"no quarantine {args.id}")
        freed = 0
        for dirpath, _d, files in os.walk(qdir):
            for name in files:
                sig = stat_sig(os.path.join(dirpath, name))
                if sig:
                    freed += sig[0]
        shutil.rmtree(qdir, ignore_errors=True)
    emit({"purged": args.id, "freed_gb": gb(freed),
          "note": "Space is now permanently freed. This cannot be undone."})

# --- selftest (safe, uses a temp sandbox) ---------------------------------

def cmd_selftest(args):
    import tempfile
    sandbox = tempfile.mkdtemp(prefix="owctl-selftest-")
    os.environ["OWCTL_HOME"] = os.path.join(sandbox, "state")
    checks = []
    # protected-path guard
    checks.append(["denies C:\\Windows", is_protected(r"C:\Windows\system32") or not IS_WINDOWS])
    # token stability
    items = [{"path": "/a", "size": 1, "mtime_ns": 2}]
    checks.append(["token stable", _plan_token(items) == _plan_token(list(items))])
    # token changes when state moves
    moved = [{"path": "/a", "size": 1, "mtime_ns": 3}]
    checks.append(["token moves with state", _plan_token(items) != _plan_token(moved)])
    # lock excludes
    ok_lock = True
    try:
        with Lock():
            try:
                with Lock():
                    ok_lock = False  # second acquire should have failed/blocked
            except SystemExit:
                ok_lock = True
    except Exception:
        ok_lock = False
    checks.append(["lock is exclusive", ok_lock])
    shutil.rmtree(sandbox, ignore_errors=True)
    emit({"selftest": [{"name": n, "pass": bool(p)} for n, p in checks],
          "all_passed": all(p for _n, p in checks)})

# --- CLI ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(prog="owctl", description="Reliable local control tool for AI assistants.")
    ap.add_argument("--version", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("sysinfo", help="System info as JSON (fastfetch, else builtin).")
    s.add_argument("--builtin", action="store_true", help="Skip fastfetch; use builtin only.")
    s.set_defaults(func=cmd_sysinfo)

    s = sub.add_parser("scan", help="Report biggest files and folders (read-only).")
    s.add_argument("--path", default=os.path.expanduser("~"))
    s.add_argument("--top", type=int, default=20)
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("plan", help="Snapshot cleanable junk; returns a token. Changes nothing.")
    s.add_argument("--categories", default="temp",
                   help="Comma list: temp,downloads-old,desktop-old")
    s.add_argument("--days", type=int, default=30, help="'old' threshold for *-old categories.")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("apply", help="Move a plan's files to quarantine IF unchanged since plan.")
    s.add_argument("--token", required=True)
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("quarantine", help="List quarantined batches.")
    s.set_defaults(func=cmd_quarantine)

    s = sub.add_parser("restore", help="Restore a quarantined batch to its original location.")
    s.add_argument("--id", required=True)
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("purge", help="Permanently delete a quarantined batch (frees space).")
    s.add_argument("--id", required=True)
    s.set_defaults(func=cmd_purge)

    s = sub.add_parser("selftest", help="Run internal correctness checks.")
    s.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    if args.version:
        emit({"name": "owctl", "version": "0.1.0", "schema": SCHEMA, "windows": IS_WINDOWS})
        return
    if not getattr(args, "cmd", None):
        ap.print_help()
        return
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
