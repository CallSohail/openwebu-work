---
name: files-and-data
description: Organize files and work with data on the user's Windows PC, including CSV and Excel analysis, charts, PDF creation, merging and conversion, image processing and Python scripts. Use when the user asks to create, sort, rename, search, zip, convert or analyze files, build charts or reports, or write and run a Python script.
---

# Files and Data

Follow the `windows-safe-terminal` skill as well. Everything stays in `C:\Users\Pret\Desktop\Tether`.

## Python setup

- Check first: `python --version` and `python -m pip --version`. If `python` isn't found, try `py --version`.
- Install missing packages only when needed: `python -m pip install <package>`.
- For a bigger project, create a virtual environment once: `python -m venv .venv`, then use `.venv\Scripts\python.exe` for every later command.
- Write scripts to `.py` files and run them. Print clear output so the result is visible in the chat.

## Package guide

| Task | Package |
| --- | --- |
| CSV, Excel analysis, pivots | `pandas`, `openpyxl` |
| Excel formatting, colors, formulas | `openpyxl` |
| Charts | `matplotlib` (PNG), `plotly` (interactive HTML) |
| Create PDFs | `reportlab` or `fpdf2` |
| Merge, split, rotate, read PDFs | `pypdf` |
| Word, PowerPoint files without opening Office | `python-docx`, `python-pptx` |
| Images | `Pillow` |
| Web requests | `requests` |

## File operations

- Before a bulk change (rename, move, delete, replace text), show a preview list of exactly what will change and ask for confirmation. Use a dry run where possible.
- Never overwrite: write a new file (`report-v2.xlsx`) or ask first.
- For moves and renames, write a log (`organize-log.md` or `.csv`) listing old and new names, so the change can be undone.
- Keep originals when converting or editing images and PDFs. Write results to a new folder such as `output\`.
- Use UTF-8 everywhere. In pandas, read CSVs from French Excel with `sep=";"` and `encoding="utf-8-sig"` when columns look merged.

## Data work

1. Look at the data first: row count, columns, a few sample rows, missing values.
2. Say which assumptions you make (date format, decimal comma, currency).
3. Show results as a small table in the chat, and save the full result to a file.
4. Charts: label both axes, add a title, and save as PNG at 150 dpi or more. Show the image with `display_file`.
5. Double-check totals, for example that the sum of per-region revenue equals the overall revenue.

## Zip and backup

- Create: `powershell -NoProfile -Command "Compress-Archive -Path 'C:\Users\Pret\Desktop\Tether\playground' -DestinationPath 'C:\Users\Pret\Desktop\Tether\backup.zip'"`.
- Verify by listing the contents of the archive after creating it.

## Finish

List the created files with full paths and sizes, and display the main ones.
