---
name: owctl
description: Use the owctl tool on the user's Windows PC for reliable system info and safe disk cleanup, instead of hand-writing PowerShell. Use whenever the user asks how their PC is doing, about CPU/RAM/disk/storage, to find what is filling the disk, or to free up space, clean temp files or old downloads.
---

# owctl — reliable system info and safe cleanup

`owctl` is a local Python tool on this PC that returns clean JSON from one quoting-safe command. Prefer it over hand-written PowerShell: no `$_`, no nested quotes, no cmd.exe breakage, and the same answer every time.

Run it as (it is installed in the work folder):
```
python C:\Users\Pret\Desktop\Tether\owctl\owctl.py <command>
```
If `python` is not found, try `py` instead. Every command prints JSON with an `ok` field; on failure `ok` is false and `error` explains why. Read the JSON; show the user a short table, not raw JSON.

## System info

```
python ...\owctl.py sysinfo
```
Returns CPU, GPU, memory (total/used/free GB and %), disks (free GB and %), OS, host, battery and uptime. It uses fastfetch when available and falls back to built-in calls, so it always works. This is the single command for "how is my PC doing" — do not build `Get-CimInstance` or `Get-Counter` one-liners.

## Disk cleanup — a safe three-step state machine

Never delete directly. Cleanup is always **plan → apply → purge**, and each step is bound to the exact files it saw. This is deliberate: a plan cannot act on files that changed after it was made.

**Step 1 — report (read-only).** Show the user what is using space:
```
python ...\owctl.py scan --path C:\Users\Pret --top 20
```
Returns the biggest files and folders and the free space on each drive. Changes nothing.

**Step 2 — plan.** Snapshot the junk you intend to remove and get a token:
```
python ...\owctl.py plan --categories temp,downloads-old --days 30
```
Categories: `temp` (the Temp folder), `downloads-old`, `desktop-old` (files older than `--days`). It returns a `token`, the item count and total GB per category. **It deletes nothing.** Show the user the totals and the token, and ask them to confirm.

**Step 3 — apply (only after the user agrees).** Move the planned files to a reversible quarantine:
```
python ...\owctl.py apply --token <token>
```
It re-checks every file against the plan. Any file that **changed since the plan is refused** (reported under `skipped_changed`); only unchanged files move. Nothing is lost yet — files sit in quarantine and can be restored. Tell the user how much was moved and that space is freed only after purge.

**Step 4 — free the space (after the user confirms they do not need the files).**
```
python ...\owctl.py purge --id <quarantine_id>
```
This permanently deletes that quarantine batch and frees the space. It cannot be undone, so confirm first.

**Undo at any time before purge:**
```
python ...\owctl.py quarantine            # list batches
python ...\owctl.py restore --id <id>     # put files back
```

## Rules

- Always run `scan` or `plan` and show the user the result **before** `apply`. Never apply a token the user has not seen the plan for.
- Always get an explicit "yes" before `apply`, and again before `purge` (purge is permanent).
- owctl already refuses system folders (`C:\Windows`, `Program Files`, `ProgramData`, the Recycle Bin), symlinks and junctions. Do not try to work around that.
- If `apply` reports `skipped_changed`, that is correct behaviour, not an error: those files moved since the plan. Re-run `plan` to get a fresh token if the user still wants them gone.
- On a nearly-full disk, moving to quarantine frees nothing until `purge`; tell the user that purge is the step that reclaims space.
- For anything owctl does not cover, fall back to the `windows-system-info` and `files-and-data` skills.
