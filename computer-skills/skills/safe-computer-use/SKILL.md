---
name: safe-computer-use
description: Ground rules for acting on the user's real computer through Open Terminal or Tether. Load whenever a task deletes, moves, renames, installs, changes settings, sends, submits, or touches many files at once. Covers dry runs, confirmations, backups, Recycle Bin instead of permanent delete, secrets, and how to tell if you are sandboxed.
---

# Safe computer use

Open Terminal runs as the user's own account. Unless Tether's sandbox is on, you can reach everything they can: their documents, SSH keys, browser profile, mail client data. The folder the file browser opens in does not limit what commands can touch. Act like a careful colleague at their keyboard.

## Know where you are first

Run once per conversation, before anything that writes:
- `python -c "import os,getpass,platform;print(platform.platform(),getpass.getuser(),os.getcwd(),os.environ.get('OPEN_TERMINAL_ISOLATED'))"`
- If `OPEN_TERMINAL_ISOLATED` is `true`, Tether's sandbox is on: only the chosen workspace folder is readable and writable, home and temp are private throwaway folders, network is blocked and pip cannot reach an index. Do not try to work around it. Tell the user when a task needs something outside it.
- Read `AGENTS.md` in the home folder if it exists; its rules override defaults here.

## Ask, then act

Show the exact plan and wait for a clear yes before:
- deleting, moving or renaming more than a handful of files, or anything outside the work folder
- installing or uninstalling software, changing PATH, registry, services, scheduled tasks, firewall or startup items
- sending anything (email, chat, form submission, git push, uploading to a website)
- anything in the browser that is irreversible: submit, pay, publish, accept terms, delete

For bulk changes, produce a dry-run table first (old name, new name / action) and save it as CSV in the scratch folder so the user can review it.

## Make changes reversible

- Prefer copies: write `name.cleaned.ext` next to the original instead of overwriting.
- Before modifying an existing file in place, copy it to `.scratch/<task>/backup/`.
- Delete to the Recycle Bin, not permanently. On Windows:
  ```powershell
  Add-Type -AssemblyName Microsoft.VisualBasic
  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($path,'OnlyErrorDialogs','SendToRecycleBin')
  ```
  On macOS use `osascript -e 'tell app "Finder" to delete POSIX file "<path>"'`. On Linux use `gio trash <path>`.
- Never use `-Force`, `/f`, `--force`, `--hard` or `--no-verify` unless the user asked for that exact flag.

## Secrets and personal data

- Never print the content of `.env`, key files, browser profiles, password stores or `credentials*`. Report the path only.
- Never type passwords, card numbers or one-time codes into terminals, forms or websites. Ask the user to do that step.
- Do not copy personal data (other people's CVs, grades, health or ID documents) into new files beyond what the task needs, and say where any such file was written.
- Do not paste personal data into web searches or third-party sites.

## Long-running and risky commands

- Give long commands a timeout and run them in the background when supported, then poll.
- Do not start servers that listen on `0.0.0.0` or open firewall ports unless asked; bind to `127.0.0.1`.
- Stop processes you started when the task ends. List them in the final answer if they must keep running.

## Report honestly

End every task with: what changed (paths), what was created, what was removed, anything that failed or was skipped. If you could not verify something, say so.
