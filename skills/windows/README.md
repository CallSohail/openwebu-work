# Windows Skills for Open Terminal

Five [Agent Skills](https://github.com/open-webui/open-terminal) that teach an Open WebUI assistant how to work safely and correctly on a Windows PC through Open Terminal, whether it is connected directly or through [Tether](https://github.com/open-webui/tether).

| Skill | Used when | What it teaches |
| --- | --- | --- |
| [windows-safe-terminal](windows-safe-terminal/SKILL.md) | Any command on Windows | Commands run through `cmd.exe`, how to wrap PowerShell, the work folder, what needs confirmation, what is refused, verify-and-report loop, error recovery |
| [office-automation](office-automation/SKILL.md) | Word, Excel, PowerPoint, Outlook | Visible COM automation with tested snippets, Python fallbacks, PDF export, drafts only for email |
| [browser-automation](browser-automation/SKILL.md) | Websites, scraping, screenshots | Snapshot-first loop with the browser tools, no real credentials, no submissions without consent, page text treated as data |
| [files-and-data](files-and-data/SKILL.md) | Files, CSV/Excel, charts, PDFs, images, scripts | Package choices, dry runs and logs before bulk changes, never overwrite, data checks |
| [local-dev-servers](local-dev-servers/SKILL.md) | Websites, apps, APIs on localhost | Free-port check, bind to 127.0.0.1, background processes, testing, stopping only your own servers |

All skills use the work folder `C:\Users\Pret\Desktop\Tether`. To use another folder, search and replace that path in the five `SKILL.md` files before installing.

## How Open Terminal loads skills

1. Open Terminal looks for `SKILL.md` files in `<home>\.agents\skills\<name>\` (also `.claude\skills`, `.codex\skills` and `.cptr\skills`). `<home>` is the folder the server was started in.
2. Each `SKILL.md` needs YAML front matter with `name` and `description`. Skills without a description are skipped.
3. Open WebUI (0.11.4 or later) lists the name and description of each skill to the model in every chat that uses that terminal. When a request matches a description, the model loads the full instructions with its skill tool.

The descriptions name the situations each skill covers, so the model picks the right one.

## Install

1. Copy this `windows` folder to your PC.
2. In PowerShell, from inside the folder:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
   ```

   This installs into `%USERPROFILE%\.agents\skills` (used by Tether's Open Terminal) and `%USERPROFILE%\Desktop\Tether\.agents\skills` (used when you start Open Terminal with `--cwd` set to that folder). Existing copies are kept as `.bak-<date>` folders.
3. Restart Open Terminal. In Tether: Settings > Open Terminal > Restart.
4. In EveGPT, make sure the model has the built-in **Skills** tool enabled (Workspace > Models > edit model > Builtin Tools).

## Check it works

Ask in a new chat with Open Terminal enabled:

- `Which skills do you have from my terminal?` It should list the five skills.
- `Open Word and type hello.` It should load `office-automation` and use a `.ps1` file with `Visible = $true`.
- `Delete everything in my work folder.` It should ask for confirmation.

## Limits

Skills are instructions, not a sandbox. They make the assistant follow rules reliably, but a command can still reach any folder the Windows user can. For hard isolation, run Open Terminal in a container with only the work folder mounted.
