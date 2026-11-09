---
name: data-analysis
description: Analyse CSV, Excel, JSON or log files on the user's computer and produce tables, charts and short written findings. Use for statistics, trends, pivots, cleaning, comparisons, surveys, forecasting and HTML or Excel reports. Covers checking the data first, choosing the method, and stating limits honestly.
---

# Data analysis

## Workflow

1. Profile first. Rows, columns, types, missing values, duplicates, value ranges, encoding and separator. Show this in a short table before analysing. For files over ~200 MB read in chunks or use DuckDB/polars if available.
2. Confirm the question. Restate what will be measured and how in one sentence. If a column's meaning is unclear, ask.
3. Clean in a script in the scratch folder, never by hand-editing the source. Log every rule applied with a count ("removed 14 duplicate rows by email").
4. Analyse with the simplest method that answers the question. For tests, check assumptions and report effect size with the p-value.
5. Chart. One idea per chart, labelled axes with units, title that states the finding. Save PNG (and SVG if a report needs it).
6. Write findings: 3 to 6 sentences, numbers with units, plus caveats (sample size, missing data, outliers removed).

## Tools

- pandas + matplotlib in a scratch venv (`uv run --with pandas --with matplotlib --with openpyxl python -B script.py`), or the standard library `csv` and `statistics` modules for small jobs.
- Excel outputs: openpyxl, one sheet per table, frozen header, number formats set, chart on a summary sheet.
- HTML reports: a single self-contained file with inline CSS and charts as embedded PNG/SVG, saved to `outputs/`.

## Honesty rules

- Correlation is not cause. Say "is associated with".
- Do not extrapolate a forecast past twice the history length without saying how uncertain it is.
- When you drop or impute data, say how much and why.
- If the data cannot answer the question, say so and say what data would.

## Personal data

Grades, HR records and survey answers are personal data. Aggregate (counts, averages) in outputs, avoid names unless the user needs them, and never upload the data to a web service.

## Cleanup

Analysis scripts stay in the scratch folder and are removed at the end, unless the user wants a rerunnable report. Then keep one tidy script with a header comment saying what it reads and writes.
