---
name: workspace-hygiene
description: Use on EVERY task that creates files on the user's computer. Puts helper scripts, temp files and intermediate outputs in a per-task scratch folder, keeps only the deliverables the user asked for, and deletes the rest when the task ends. Load it before writing any script, CSV, log or converted file, even a one-line helper.
---

# Workspace hygiene

You are working on a real person's computer. Every file you leave behind is clutter they have to understand and clean up. The rule is simple: helpers live in a scratch folder, deliverables go where the user can find them, and the scratch folder is gone when you finish.

## The routine

1. Start the task. From the user's work folder:
   ```
   python <this skill>/scripts/scratch.py start "<short task name>"
   ```
   It prints the scratch path, e.g. `EveGPT-work/.scratch/20261109-1105-merge-exports/`. Write every helper script, temp CSV, debug log and test file there.

2. Work. If a tool forces a temp file somewhere else (a converter writing next to its input, a log in the work folder), register it right away:
   ```
   python <this skill>/scripts/scratch.py track <path>
   ```
   `track` refuses files that existed before the task started, so you can never schedule a user's own file for deletion.

3. Mark deliverables. For each file the user actually asked for:
   ```
   python <this skill>/scripts/scratch.py keep <scratch-path/file> [--to <folder>]
   ```
   Default destination is `outputs/` in the work folder. Use the folder the user named if they named one.

4. Check, then finish:
   ```
   python <this skill>/scripts/scratch.py status
   python <this skill>/scripts/scratch.py finish
   ```
   `finish` moves deliverables out, deletes the scratch folder and tracked temp files, and prints what it kept and removed. Include that summary in your final answer.

## Decide what is a deliverable

Keep: what the user asked for (the report, the cleaned spreadsheet, the converted PDFs), plus a script only when the user asked for something reusable ("make me a script I can run every Monday").

Remove: one-off helper scripts, probes, intermediate CSVs, screenshots taken only to check your work, logs from successful runs, test copies of user files.

When unsure whether the user wants a script, keep it in the scratch folder, ask in your final message, and finish only after they answer. Never delete to be tidy if the user might want it.

## Hard rules

- Never delete, overwrite or move a file that existed before the task unless the user asked for that exact change. For edits, write a new file next to the original (`report.cleaned.xlsx`) or make a copy first.
- Never run recursive deletes (`rm -rf`, `Remove-Item -Recurse`, `rd /s`) outside the scratch folder.
- If a command fails halfway, run `scratch.py status` before retrying so you know what is already on disk.
- If the session ends before you finish, the next session may find `.scratch/ACTIVE`. Run `status`, tell the user what is there, and finish or abandon with their agreement.
- Python caches: run helpers with `python -B` (no `__pycache__`), and do not `pip install` into the user's global Python for a one-off task. Use `uv run --with <pkg>` or a venv inside the scratch folder so it disappears on finish.

## Without the script

If Python is not available, follow the same pattern by hand: create `.scratch/<date>-<task>/`, keep a `ledger.txt` listing every file you create outside it, move deliverables to `outputs/`, then delete the scratch folder and the ledger entries, and list what you removed.
