#!/usr/bin/env python3
"""Read-only snapshot of the computer the agent is running on.

Prints OS, Python, user, disks with free space, network basics, PATH
entries that point nowhere, and which common tools are installed.
Changes nothing. Standard library only. Add --json for machine output.
"""
from __future__ import annotations

import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

TOOLS = ["python", "py", "uv", "pip", "git", "node", "npm", "docker", "ffmpeg",
         "pandoc", "soffice", "libreoffice", "tesseract", "code", "claude", "codex",
         "gemini", "opencode", "winget", "powershell", "pwsh"]


def disks():
    out = []
    if os.name == "nt":
        import string
        roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    else:
        roots = ["/"] + [str(p) for p in Path("/mnt").glob("*")] if Path("/mnt").exists() else ["/"]
    seen = set()
    for r in roots:
        try:
            u = shutil.disk_usage(r)
        except OSError:
            continue
        key = (u.total, u.used)
        if key in seen:
            continue
        seen.add(key)
        out.append({"root": r, "total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1),
                    "used_pct": round(100 * u.used / u.total, 1) if u.total else 0})
    return out


def tool_version(name):
    path = shutil.which(name)
    if not path:
        return None
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8)
        line = (r.stdout or r.stderr).strip().splitlines()
        return {"path": path, "version": line[0][:120] if line else ""}
    except Exception:
        return {"path": path, "version": "?"}


def main():
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "home": str(Path.home()),
        "cwd": os.getcwd(),
        "python": f"{sys.version.split()[0]} at {sys.executable}",
        "sandboxed": os.environ.get("OPEN_TERMINAL_ISOLATED") == "true",
        "proxy": {k.lower(): v for k, v in os.environ.items() if k.lower() in ("http_proxy", "https_proxy")},
        "disks": disks(),
        "missing_path_entries": [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and not os.path.isdir(p)],
        "tools": {t: tool_version(t) for t in TOOLS},
    }
    if "--json" in sys.argv:
        print(json.dumps(info, indent=2)); return
    print(f"OS        {info['os']}  [{info['machine']}]")
    print(f"Host/user {info['host']} / {info['user']}   home {info['home']}")
    print(f"Python    {info['python']}")
    print(f"Sandboxed {'yes (Tether sandbox: only the workspace folder is writable, no network)' if info['sandboxed'] else 'no (runs as the full user account)'}")
    if info["proxy"]:
        print("Proxy     " + ", ".join(f"{k}={v}" for k, v in info["proxy"].items()))
    print("\nDisks")
    for d in info["disks"]:
        warn = "  <-- above 85% full" if d["used_pct"] > 85 else ""
        print(f"  {d['root']:<6} {d['free_gb']:>7} GB free of {d['total_gb']:>7} GB ({d['used_pct']}% used){warn}")
    print("\nTools")
    for t, v in info["tools"].items():
        print(f"  {t:<12} {v['version'] if v else '-'}")
    if info["missing_path_entries"]:
        print("\nPATH entries that do not exist")
        for p in info["missing_path_entries"]:
            print(f"  {p}")
    print("\nRead-only snapshot, nothing was changed.")


if __name__ == "__main__":
    main()
