# Computer Skills for Open Terminal and Tether

Agent Skills that teach an Open WebUI model how to work properly on a real computer through [Open Terminal](https://github.com/open-webui/open-terminal), including PCs connected with [Tether](https://github.com/open-webui/tether). Nine core skills cover safe and tidy work, and eight role profiles adapt the model to how different people at a university work, the way Hermes Agent profiles do.

The rule that runs through all of them: helper scripts and temp files go into a per-task scratch folder and are deleted when the task ends. Only the files the user asked for stay.

## What is in the pack

Core skills

| Skill | Use it for | Ships a tool |
| --- | --- | --- |
| [workspace-hygiene](skills/workspace-hygiene/) | Every task that creates files. Scratch folder, deliverables to `outputs/`, cleanup ledger | `scratch.py` |
| [safe-computer-use](skills/safe-computer-use/) | Deleting, moving, installing, sending, settings. Dry runs, Recycle Bin, secrets, sandbox detection | |
| [file-finder](skills/file-finder/) | Hidden files, large files, duplicates, recent changes, temp and conflict copies, folder sizes. Read-only | `find_files.py` |
| [office-documents](skills/office-documents/) | Word, Excel, PowerPoint, PDF: inspect, clean, merge, convert, fill templates, OCR | `office_inspect.py` |
| [data-analysis](skills/data-analysis/) | CSV, Excel, logs: profile, clean, analyse, chart, report | |
| [browser-research](skills/browser-research/) | Websites through Tether's Chrome, signed-in or clean profile, read-only, cited | |
| [system-diagnostics](skills/system-diagnostics/) | Slow PC, network, proxy, PATH, updates, event logs. Read-only | `sysinfo.py` |
| [desktop-control](skills/desktop-control/) | Native apps with no other way in, screenshot tutorials | |
| [coding-agent-handoff](skills/coding-agent-handoff/) | Claude Code, Codex, Gemini CLI and others from the model picker | |

Role profiles

| Profile | For |
| --- | --- |
| [profile-personal-assistant](skills/profile-personal-assistant/) | Briefings, finding files, tidying, meeting and expense packs, draft replies |
| [profile-lecturer](skills/profile-lecturer/) | Course material, eCampus GIFT quizzes, tested exercises, gradebooks, feedback |
| [profile-phd-researcher](skills/profile-phd-researcher/) | Literature on HAL, arXiv, theses.fr, BibTeX, experiments, LaTeX, supervisor reports |
| [profile-hr-officer](skills/profile-hr-officer/) | Trackers, mail merge, onboarding, CV screening grids with human review, GDPR care |
| [profile-it-support](skills/profile-it-support/) | Diagnostics, config reviews, tickets, runbooks, production-safe changes |
| [profile-student](skills/profile-student/) | Revision plans, applications, official student aid sources, interview practice |
| [profile-communications](skills/profile-communications/) | FR/EN news, banners, social posts, link and accessibility checks |
| [profile-admin-assistant](skills/profile-admin-assistant/) | Minutes, letters from templates, request trackers, procedures, filing |

Also here:
- [AGENTS.md](AGENTS.md): a template for standing rules in the home folder, which Open WebUI adds to every message.
- [ARCHITECTURE.md](ARCHITECTURE.md): how Tether, the relay, Open Terminal, the sandbox and skill discovery fit together.
- [validate_skills.py](validate_skills.py) and [tests/](tests/): checks every skill the way Open Terminal reads it, and tests the tools.

## Install on a Windows PC running Tether

```powershell
git clone https://github.com/CallSohail/openwebu-work
cd openwebu-work\computer-skills
.\install.ps1 -WithAgentsMd
```
Then edit the `[bracketed]` lines in `%USERPROFILE%\AGENTS.md`.

To install a subset, for example for a lecturer:
```powershell
.\install.ps1 -Only workspace-hygiene,safe-computer-use,office-documents,data-analysis,browser-research,profile-lecturer
```

macOS or Linux: `./install.sh --with-agents-md`.

The installer copies skills to `~/.agents/skills`, which Open Terminal scans.

Open Terminal's home is not always your Windows profile: Tether can set a custom home folder (Settings, Open Terminal, Advanced). Check by asking the chat `Run: python -c "import pathlib;print(pathlib.Path.home())"`, then install there with `.\install.ps1 -HomeDir <that path>`. The file browser's "Home / .agents / skills" view shows what the terminal actually sees. Existing skills with the same name are moved to a dated backup folder first. `-Uninstall` removes only this pack's skills.

If Tether's sandbox is on, Open Terminal gets a private, throwaway home folder, so skills installed in your real home are not visible, and copying them into the workspace folder does not help because only home-relative paths are scanned. Use the sandbox for simple document work, and turn it off when you want these skills.

## Use in a chat

1. In Open WebUI, select the Tether device or terminal from the cloud icon.
2. Type `$` to pick a skill, or just ask; the model sees every skill's description and loads the right one.
3. A good opening message for a role: `Use profile-lecturer. Course folder is courses/BD-L2.`

Check it works:
- `List the skills you can use from this terminal.`
- `Start a scratch task called test, write a hello.py there, run it, then finish and tell me what you removed.`

## Why the cleanup rule matters

Without it, after a week of use the work folder fills with `merge_temp.py`, `test2.csv`, `debug.log` and half-converted copies, and nobody knows which file is the real output. The `workspace-hygiene` skill gives the model one place to put throwaway files and one command (`scratch.py finish`) that keeps the deliverables and deletes the rest. It only ever deletes inside the scratch folder or files it registered during the task, and it refuses to register a file that existed before the task started.

## Adding your own skill

1. Create `skills/<name>/SKILL.md` with frontmatter `name` (same as the folder) and a `description` that says when to use it, under 1024 characters.
2. Put scripts in `skills/<name>/scripts/`. Standard library only, so they run on Tether's bundled Python.
3. Run `python validate_skills.py` and `python -m pytest tests -q`.

## Requirements

Open Terminal 0.17 or newer, Open WebUI 0.13 or newer with native function calling and built-in tools enabled for the model. Tether 0.1.8 or newer for the private local connection.

## Licence

This pack is MIT, like the rest of the repository. Tether is a separate product with its own licence (free for personal non-commercial use; organisational production use needs an Open WebUI Enterprise licence). Open Terminal is MIT.
