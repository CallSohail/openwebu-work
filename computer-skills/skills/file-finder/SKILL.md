---
name: file-finder
description: Find things on the user's computer without changing anything - hidden files and folders, the biggest files, duplicates, recent changes, temp and lock files, OneDrive or sync conflict copies, files by name, and folder sizes. Use for "where is my file", "what is taking space", "show hidden files", "find duplicates" and clean-up planning.
---

# File finder

Everything here is read-only. Finding is one step, cleaning is a separate step that needs the user's yes (see safe-computer-use).

## Tool

`scripts/find_files.py` (standard library, works on Tether's bundled Python). Default search path is the home folder. Caches, `.git`, `node_modules`, venvs and `.scratch` are skipped unless `--all`.

```
python -B scripts/find_files.py hidden  [PATH...]                 # Windows hidden/system attribute or dot-names
python -B scripts/find_files.py large   [PATH...] --top 30 --min-mb 100
python -B scripts/find_files.py dupes   [PATH...] --min-kb 100    # size, then SHA-256
python -B scripts/find_files.py recent  [PATH...] --days 7
python -B scripts/find_files.py temp    [PATH...]                 # ~$*, *.tmp, *.bak, lock files, "- Copy", PC-name conflict copies
python -B scripts/find_files.py name    [PATH...] --glob "*.pdf" | --regex "contrat|contract"
python -B scripts/find_files.py sizes   [PATH...] --depth 2
```
Add `--csv` or `--json` to save results; write them to the scratch folder, and keep the CSV as a deliverable only if the user wants it.

## Searching inside documents

- Text and code: `rg -i "<term>" <path>` if ripgrep exists, otherwise `Select-String -Path <path>\* -Pattern "<term>" -Recurse` on Windows or `grep -ril` elsewhere.
- Word, Excel, PowerPoint: use office-documents (`office_inspect.py`) or open with python-docx/openpyxl in a scratch venv. PDF: `pdftotext` or pypdf.
- Report the file, and the matching line trimmed to one sentence.

## Windows specifics

- Show hidden items with attributes: `Get-ChildItem -Force -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Attributes -match 'Hidden|System' }`.
- Where a downloaded file came from: `Get-Content <file> -Stream Zone.Identifier` (shows HostUrl/ReferrerUrl when recorded).
- Large hidden folders worth explaining: `AppData\Local\Temp`, browser profiles under `AppData\Local\Google\Chrome\User Data`, `AppData\Local\Packages`, `.cache`, `.conda`, `.npm`, `.vscode`, `OneDrive` placeholders. Explain what each belongs to; never suggest deleting a profile or `AppData\Roaming` folder wholesale.
- OneDrive "files on demand" may report a size that is not on disk. Say so when sizes look odd.

## Presenting results

- Lead with the answer (the file, the total size), then the table.
- For clean-up, group by what is safe (temp, installers already run, exact duplicates) versus needs a human decision (old projects, large videos).
- Offer the next step, for example "Send these 14 temp files (2.1 GB) to the Recycle Bin?", and wait.
