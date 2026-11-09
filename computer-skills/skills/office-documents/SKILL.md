---
name: office-documents
description: Read, create and edit Word, Excel, PowerPoint and PDF files on the user's computer - summarise, clean, merge, split, convert, fill templates, compare versions, extract tables, OCR scans. Use for any .docx, .xlsx, .xlsm, .csv, .pptx or .pdf task. Writes results as new files and never overwrites the original.
---

# Office documents

## Look before you touch

Run `python -B scripts/office_inspect.py <file>` first. It reads the file's internal XML with no dependencies and reports headings, tables, comments and tracked changes for Word; sheets, used ranges, formula counts and a preview for Excel; slide titles and notes for PowerPoint. Use that to plan, and mention tracked changes or formulas the user may not know about.

## Libraries

Install only inside the task scratch folder so nothing lingers (see workspace-hygiene):
```
uv venv .scratch/<task>/venv && uv pip install --python .scratch/<task>/venv python-docx openpyxl python-pptx pypdf
```
If uv is missing: `python -m venv .scratch/<task>/venv` then that venv's pip. Under Tether's sandbox there is no package index; tell the user which feature needs a package and offer to work with the standard library.

Optional system tools, use if present: LibreOffice (`soffice --headless --convert-to pdf`), `pandoc`, `pdftotext`, `qpdf`, `tesseract` (OCR, use `-l fra+eng`).

## Rules for each format

Word
- Keep the user's styles. Change text through runs, not by rebuilding paragraphs, or formatting is lost.
- Templates: replace `{{placeholders}}` across runs (a placeholder can be split over several runs; join run text before matching).
- For "compare versions", report added, removed and changed paragraphs with their heading.

Excel
- Never round-trip a workbook with formulas through pandas; use openpyxl so formulas, formats and other sheets survive.
- Excel with `data_only=True` gives cached values only if the file was last saved by Excel. Say so when values come back empty.
- Dates in French files: `DD/MM/YYYY`, decimal comma, `;` as CSV separator. Detect before parsing; write CSV back in the same convention.
- Freeze header rows, set column widths and add filters on outputs; people judge the file by how it opens.

PowerPoint
- Reuse the deck's own layouts and master. Check text fits: flag any shape whose text exceeds about 90% of its box.
- Speaker notes go in the notes slide, not in a text box.

PDF
- Merge, split, rotate with pypdf. Extract text first; if pages return no text they are scans, so OCR them.
- Do not "edit" PDF text in place. Rebuild from the source document or add an annotation layer, and explain.
- Redaction must remove the text, not draw a box over it. Verify by extracting text from the result.

## Output

- Name outputs clearly: `<original>.cleaned.xlsx`, `<original>.fr.docx`, `annexes-merged.pdf`.
- Put them in `outputs/` or the folder the user named, then show them with the display tool so they open in the file browser preview.
- Report page, sheet or slide counts so the user can sanity-check.
