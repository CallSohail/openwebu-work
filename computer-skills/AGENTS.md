# Standing instructions for this computer

<!--
Copy this file to your home folder (C:\Users\<you>\AGENTS.md on Windows, ~/AGENTS.md elsewhere).
Open WebUI adds it to every message when this terminal is selected. Keep it under 32 KiB.
Edit the lines in [brackets].
-->

## Who I am
- [Name], [role] at [organisation]. Preferred language: [French / English]. Formal French (vous) for anything addressed to other people.

## Where to work
- Work folder: `~/EveGPT-work` with `inputs/`, `outputs/`, `notes/`. Create it if missing.
- Read anywhere I point you to. Write only inside the work folder unless I name another folder.
- Never open mail stores, browser profiles, password managers, `.ssh`, or `.env` files unless I ask for that exact file.

## Skills
- Before any task that creates files, load the `workspace-hygiene` skill and use its scratch folder.
- Before deleting, moving, installing, sending or changing settings, load `safe-computer-use`.
- If one of the `profile-*` skills matches my role, follow it.

## Cleanup (always)
- Helper scripts, temp files, test copies and intermediate outputs go in `.scratch/<task>/` and are deleted when the task is done.
- Keep only what I asked for, in `outputs/` or the folder I named. If you think a script is worth keeping, ask me.
- End every task with: files created, files changed, files removed.

## Ask me first
- Deleting or moving more than 10 files, or any file outside the work folder. Use the Recycle Bin, never permanent delete.
- Installing or uninstalling anything, changing PATH, startup items, services or system settings.
- Sending, submitting, publishing, paying, or accepting anything, in any app or website.
- Typing passwords, codes or card numbers: never. I do that myself.

## Style
- Lead with the result, then the paths, then what needs my decision.
- Keep answers short. Tables for lists of files.
