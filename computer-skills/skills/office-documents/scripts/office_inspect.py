#!/usr/bin/env python3
"""Describe .docx, .xlsx and .pptx files without installing anything.

Office files are zip archives of XML. This reads them directly so the agent
can see structure before deciding whether it needs python-docx, openpyxl or
python-pptx. Read-only. Standard library only.

  office_inspect.py report.docx           headings, paragraph/table/image/comment counts, word count
  office_inspect.py data.xlsx             sheets, used ranges, formula count, first rows
  office_inspect.py talk.pptx             slide titles, notes presence, image count
  office_inspect.py FILE --json
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def text_of(el, tag):
    return "".join(t.text or "" for t in el.iter(tag))


def docx(z: zipfile.ZipFile):
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", NS)
    headings, words, paras = [], 0, 0
    for p in body.iter(f"{{{NS['w']}}}p"):
        paras += 1
        txt = text_of(p, f"{{{NS['w']}}}t")
        words += len(txt.split())
        style = p.find("w:pPr/w:pStyle", NS)
        style_val = style.get(f"{{{NS['w']}}}val", "") if style is not None else ""
        if re.match(r"(Heading|Titre|Title)", style_val, re.I) and txt.strip():
            headings.append(f"{style_val}: {txt.strip()[:100]}")
    names = z.namelist()
    return {
        "type": "docx", "paragraphs": paras, "words": words,
        "tables": len(list(body.iter(f"{{{NS['w']}}}tbl"))),
        "images": sum(1 for n in names if n.startswith("word/media/")),
        "comments": len(ET.fromstring(z.read("word/comments.xml")).findall("w:comment", NS)) if "word/comments.xml" in names else 0,
        "tracked_changes": len(list(root.iter(f"{{{NS['w']}}}ins"))) + len(list(root.iter(f"{{{NS['w']}}}del"))),
        "headings": headings[:60],
    }


def xlsx(z: zipfile.ZipFile):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = [text_of(si, f"{{{NS['s']}}}t") for si in ss.findall("s:si", NS)]
    sheets = []
    for sh in wb.find("s:sheets", NS):
        rid = sh.get(f"{{{NS['r']}}}id")
        path = "xl/" + target[rid].lstrip("/").removeprefix("xl/")
        x = ET.fromstring(z.read(path))
        dim = x.find("s:dimension", NS)
        rows = x.findall("s:sheetData/s:row", NS)
        preview = []
        for r in rows[:5]:
            vals = []
            for c in r.findall("s:c", NS):
                v = c.find("s:v", NS)
                val = v.text if v is not None else text_of(c, f"{{{NS['s']}}}t")
                if c.get("t") == "s" and val is not None and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                vals.append((val or "")[:30])
            preview.append(vals[:12])
        sheets.append({
            "name": sh.get("name"), "state": sh.get("state", "visible"),
            "range": dim.get("ref") if dim is not None else "?", "rows": len(rows),
            "formulas": len(list(x.iter(f"{{{NS['s']}}}f"))), "preview": preview,
        })
    return {"type": "xlsx", "sheets": sheets}


def pptx(z: zipfile.ZipFile):
    names = z.namelist()
    slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out = []
    for i, n in enumerate(slides, 1):
        x = ET.fromstring(z.read(n))
        title = ""
        for sp in x.iter(f"{{{NS['p']}}}sp"):
            ph = sp.find(".//p:nvPr/p:ph", NS)
            if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                title = text_of(sp, f"{{{NS['a']}}}t").strip()
                break
        if not title:
            texts = [t.text for t in x.iter(f"{{{NS['a']}}}t") if t.text and t.text.strip()]
            title = (texts[0] if texts else "(no text)")
        out.append({"slide": i, "title": title[:100], "has_notes": f"ppt/notesSlides/notesSlide{i}.xml" in names})
    return {"type": "pptx", "slides": len(slides), "images": sum(1 for n in names if n.startswith("ppt/media/")), "outline": out}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    f = Path(args[0])
    kind = f.suffix.lower()
    with zipfile.ZipFile(f) as z:
        info = {".docx": docx, ".xlsx": xlsx, ".xlsm": xlsx, ".pptx": pptx}.get(kind, lambda _: sys.exit(f"Unsupported: {kind}"))(z)
    info["file"] = str(f)
    print(json.dumps(info, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
