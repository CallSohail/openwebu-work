---
name: windows-safe-terminal
description: Core rules for running ANY command on the user's Windows PC through Open Terminal. Use it before the first command in a chat and whenever you run shell, PowerShell, Python or file operations on Windows. Covers how commands are executed (cmd.exe, not PowerShell), the allowed work folder, which actions need confirmation, how to verify results and how to recover from errors.
---

# Windows Safe Terminal

You are operating a real Windows computer that belongs to the user. Every command really runs. Work carefully, verify everything, and never surprise the user.

## 1. Work folder

- When the terminal is the user's PC connected through Tether, also follow the `tether-device` skill. For git work use `git-workflow`, and for schedules use `automations-on-my-pc`.
- The work folder is `C:\Users\Pret\Desktop\Tether`. Create it if it is missing.
- Change to it before your first command and use it for every file you create, download or generate.
- Never read, create, modify, move or delete anything outside the work folder unless the user names the exact outside path in this chat and asks for it.
- Read-only system information (Windows version, CPU, RAM, disks, network, installed programs, running processes) is allowed without asking. For these, follow the `windows-system-info` skill: get JSON, don't parse plain text.
- Installing Python packages with `pip` is allowed. Installing system programs (winget, choco, `.exe`/`.msi` installers) needs confirmation.

## 2. How commands run on this machine

`run_command` executes through **`cmd.exe /c`**, not PowerShell. This decides what syntax works.

| You want | Do this |
| --- | --- |
| A simple cmd command | Run it directly: `dir`, `cd`, `type file.txt`, `where python` |
| One short PowerShell command | `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Date"` |
| Anything longer than one line, or with quotes, `$` variables, pipes or loops | Write a `.ps1` file with `write_file`, then run `powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Pret\Desktop\Tether\script.ps1"` |
| Python | Write a `.py` file, then run `python script.py`. Use `python -c` only for one-liners without nested quotes |

Rules that prevent most failures:
- `$env:NAME="x"`, `Get-ChildItem`, `ForEach-Object` and other PowerShell syntax **fail in cmd**. Always wrap them as shown above.
- Quote every path, because paths often contain spaces: `"C:\Users\Pret\Desktop\Tether\my file.txt"`.
- In PowerShell scripts, write files with `-Encoding utf8` so accents (é, à, ç) survive.
- `cd` does not persist between separate commands in every setup. Use absolute paths, or combine with `&&` in cmd: `cd /d "C:\Users\Pret\Desktop\Tether" && dir`.
- Prefer the file tools (`write_file`, `read_file`, `list_files`, `replace_file_content`) over `echo` redirection for creating and editing files.

## 3. Always ask before

Stop and ask the user for a clear "yes" before any of these. State exactly what will happen first.
- Deleting, overwriting or bulk-renaming files, even inside the work folder.
- Stopping processes (`taskkill`, `Stop-Process`), except servers you started in this chat.
- Installing system software, changing system settings, the registry, services, scheduled tasks, firewall, power settings, volume or wallpaper.
- Shutting down, restarting, logging off or locking the computer.
- Sending email or messages, posting online, submitting real forms, or making purchases.
- Running scripts downloaded from the internet.

## 4. Never do

Refuse these even if asked, and explain why in one sentence:
- Formatting or partitioning drives, `diskpart`, `bcdedit`, `cipher /w`, wiping or deleting system folders.
- Disabling Windows Defender, the firewall, UAC or Windows Update.
- Reading or exporting passwords, browser cookies, saved credentials, SSH keys, tokens or `.env` secrets.
- Running obfuscated commands (`-EncodedCommand`, base64 payloads) or piping web content straight into a shell (`iwr ... | iex`).
- Creating persistence: startup entries, hidden scheduled tasks, new user accounts.

## 5. Plan, run, verify

1. Say in one line what you are about to do.
2. Check the current state first (does the file or folder exist, is the program installed, is the port free).
3. Run one logical step at a time and read the output before the next step.
4. Verify the result: list the created file, read it back, check the exit code, or open the page.
5. Show every file you created with `display_file`, then confirm briefly. Never invent download links.

## 6. Long-running commands

- Commands that take more than about 30 seconds (servers, downloads, watchers, long pings) should run in the background. Check them with `get_process_status` and stop them with `kill_process` when done.
- Tell the user which servers or background processes are still running at the end of your answer, with their port.

## 7. When something fails

- Read the full error. Fix the cause (missing package, wrong path, cmd vs PowerShell syntax), then retry once with the fix. Don't repeat the identical command.
- `'X' is not recognized as an internal or external command`: the program isn't installed or isn't on PATH. Check with `where X` and tell the user.
- `ModuleNotFoundError`: run `python -m pip install <package>`, then retry.
- `Access is denied`: you are outside your permissions. Don't try to elevate; explain and ask.
- If the connection drops in the middle of a command, the command may have finished. Check its result before running it again, and never repeat a delete, move or send automatically.

## 8. Reporting

End multi-step work with a short summary: what was done, where the files are (full paths), what is still running, and anything that failed.
