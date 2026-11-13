# owctl

A small, dependency-free control tool that an AI assistant (through Open WebUI / Open Terminal / Tether) runs on a Windows PC to get **reliable system info** and perform **safe, reversible disk cleanup** — instead of composing fragile PowerShell that cmd.exe breaks.

One quoting-safe command in, clean JSON out, the same answer every time.

## Why it exists

Running shell commands through Open Terminal, the model keeps hitting cmd.exe quoting failures (`$_` eaten, backslashes doubled, nested quotes mangled), and it rebuilds the command differently each time, so results are inconsistent and sometimes wrong. `owctl` replaces that with a single tool whose output is deterministic JSON.

## Design invariants (the "lift rule")

A lift on the 3rd floor is not also on the ground floor. An action must be welded to the state it was planned against. owctl enforces this:

1. **Single source of truth.** One command → one JSON answer. No ad-hoc pipelines.
2. **Compare-and-swap.** Destructive work is two-phase: `plan` records each file's size and modification time and returns a **token**; `apply --token` re-checks every file and **refuses any that changed since the plan**. A stale plan can never act.
3. **Mutual exclusion.** A lockfile (atomic `O_EXCL`) means only one mutation runs at a time; two chats cannot corrupt each other. A crashed owner's lock expires automatically.
4. **Reversibility.** "Delete" moves files to a quarantine with a manifest. `restore` puts them back; `purge` frees the space permanently, only on explicit confirmation.

## Commands

| Command | Effect |
| --- | --- |
| `owctl sysinfo` | CPU, GPU, RAM, disks, OS, battery, uptime as JSON (fastfetch if present, else built-in). Read-only. |
| `owctl scan --path <dir> --top 20` | Biggest files and folders under a path, plus free space. Read-only. |
| `owctl plan --categories temp,downloads-old --days 30` | Snapshot cleanable junk; returns a token and totals. Changes nothing. |
| `owctl apply --token <token>` | Move the plan's **unchanged** files to quarantine. Refuses changed files. |
| `owctl quarantine` | List quarantined batches. |
| `owctl restore --id <id>` | Put a batch back where it came from. |
| `owctl purge --id <id>` | Permanently delete a batch and free the space. |
| `owctl selftest` | Internal correctness checks. |
| `owctl --version` | Version and schema. |

Categories for `plan`: `temp`, `downloads-old`, `desktop-old` (the `-old` ones honour `--days`).

## Safety

- Hard denylist: never touches `C:\Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, `$Recycle.Bin`, or `System Volume Information`.
- Never follows symlinks or junctions.
- `apply` never deletes; it moves to quarantine. Space is reclaimed only by `purge`, which is explicit and irreversible.
- Read-only commands take no lock; every mutation does.

## Requirements

Python 3.8+ (standard library only — no `pip install`). Works best with [fastfetch](https://github.com/fastfetch-cli/fastfetch) on PATH for richer hardware detail, but falls back to built-in calls without it.

## Install

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

This copies `owctl.py` into the work folder and prints the exact command the assistant should call. Pair it with the `owctl` skill (in `skills/windows/owctl/`) so the model knows when and how to use it.

## State

owctl keeps its plans and quarantine under `%LOCALAPPDATA%\owctl` (override with the `OWCTL_HOME` environment variable). Quarantined files live there until `purge` or `restore`.
