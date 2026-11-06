---
name: office-automation
description: Open and control Microsoft Word, Excel, PowerPoint and Outlook on the user's Windows PC, visibly on screen or in the background. Use when the user asks to open an Office app, type or write into it, create or edit .docx, .xlsx or .pptx files, convert Office files to PDF, or make an Outlook draft.
---

# Office Automation on Windows

Follow the `windows-safe-terminal` skill as well. Save every file in `C:\Users\Pret\Desktop\Tether`.

## Choose the method

| User wants | Method |
| --- | --- |
| To **see** Word, Excel or PowerPoint open and working | PowerShell COM automation with `Visible = $true` |
| Only a finished file, no window | Python: `python-docx`, `openpyxl`, `python-pptx` (install with pip if missing) |
| Convert Office to PDF with exact layout | COM: open the file and save as PDF |

Commands run through cmd.exe, so write COM code into a `.ps1` file and run it with:
`powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\Pret\Desktop\Tether\office.ps1"`

## Word (COM)

```powershell
$word = New-Object -ComObject Word.Application
$word.Visible = $true
$doc = $word.Documents.Add()
$sel = $word.Selection

$sel.Style = "Heading 1"
$sel.TypeText("Weekly Meeting")
$sel.TypeParagraph()
$sel.Style = "Normal"

# Type slowly so the user can watch
foreach ($c in "This text is typed live.".ToCharArray()) { $sel.TypeText([string]$c); Start-Sleep -Milliseconds 30 }
$sel.TypeParagraph()

# Table: 3 columns, 4 rows
$table = $doc.Tables.Add($sel.Range, 4, 3)
$table.Borders.Enable = $true
$table.Cell(1,1).Range.Text = "Name"

$doc.SaveAs2("C:\Users\Pret\Desktop\Tether\meeting.docx")
# PDF export: 17 = wdFormatPDF
$doc.SaveAs2("C:\Users\Pret\Desktop\Tether\meeting.pdf", 17)
```

- Bold, centered title: `$sel.Font.Bold = 1; $sel.ParagraphFormat.Alignment = 1` (0 left, 1 center, 2 right).
- Font: `$sel.Font.Name = "Calibri"; $sel.Font.Size = 14`.
- Bullets: `$sel.Range.ListFormat.ApplyBulletDefault()`.
- Open an existing file: `$doc = $word.Documents.Open("C:\Users\Pret\Desktop\Tether\file.docx")`.

## Excel (COM)

```powershell
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $true
$wb = $xl.Workbooks.Add()
$ws = $wb.Worksheets.Item(1)
$ws.Cells.Item(1,1) = "Month"; $ws.Cells.Item(1,2) = "Sales"
$ws.Range("B14").Formula = "=SUM(B2:B13)"
$ws.Range("A1:B1").Font.Bold = $true
$ws.Columns.AutoFit() | Out-Null
$chart = $ws.Shapes.AddChart2(201, 51).Chart   # 51 = clustered column
$chart.SetSourceData($ws.Range("A1:B13"))
$wb.SaveAs("C:\Users\Pret\Desktop\Tether\sales.xlsx", 51)  # 51 = .xlsx
```

## PowerPoint (COM)

```powershell
$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1        # PowerPoint needs -1 (msoTrue), not $true
$pres = $pp.Presentations.Add()
$slide = $pres.Slides.Add(1, 2)   # 2 = title and content layout
$slide.Shapes.Item(1).TextFrame.TextRange.Text = "AI in Education"
$slide.Shapes.Item(2).TextFrame.TextRange.Text = "Point one`rPoint two`rPoint three"
$slide.Shapes.AddPicture("C:\Users\Pret\Desktop\Tether\chart.png", 0, -1, 400, 150, 400, 300) | Out-Null
$pres.SaveAs("C:\Users\Pret\Desktop\Tether\deck.pptx")
```

## Outlook

- Only create **drafts**: `$ol = New-Object -ComObject Outlook.Application; $m = $ol.CreateItem(0); $m.Subject = "..."; $m.Body = "..."; $m.Save()`.
- Never call `.Send()` unless the user explicitly says "send it" in this chat after seeing the recipient, subject and body.

## Rules

- Leave the app open when the user wanted to watch. When working in the background, close it at the end: `$doc.Close(); $word.Quit()` or `$wb.Close(); $xl.Quit()`.
- Before saving over an existing file, ask the user, or save under a new name such as `-v2`.
- `Cannot create ActiveX component` or a class-not-registered error means that Office app isn't installed or activated. Say so and offer the Python method instead.
- If a file is open in Word or Excel, writing to it fails with a sharing error. Ask the user to close it, or save a copy.
- After saving, verify the file exists with its size, then show it with `display_file`.
