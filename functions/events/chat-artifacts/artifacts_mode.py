"""
title: Artifacts mode
description: A switch in the message box. When it is on, the model makes artifacts (documents, slides, spreadsheets, diagrams, web pages, apps, drawings) for the Artifacts panel when you ask for something to create, and answers normally otherwise.
author: Muhammad Sohail
version: 1.2.0
license: MIT
required_open_webui_version: 0.13.0
"""

# Companion to the Artifacts Event Function (chat_artifacts.py). It is a toggle Filter, so Open WebUI shows it as a switch
# by the message box: people turn it on for a chat, or an administrator turns it on by default for a model.

from typing import Optional

from pydantic import BaseModel, Field

MARK = "<!-- artifacts-mode -->"

INSTRUCTIONS = """You can create artifacts: substantial, self-contained content that opens in a panel beside the chat, where the user can view, run, edit and download it.

When to make one
- Only when the user asks you to create, draft, build or change something they will keep or reuse: a document, report, letter, plan, slide deck, spreadsheet or table of data, chart, diagram, web page, interface design, interactive app or game, drawing, data set, or a program longer than about 15 lines.
- Never for greetings, small talk, questions, explanations, advice, summaries in a few sentences, or short code snippets. Answer those normally, without any artifact.
- If unsure, answer normally.
- When the user asks for a presentation, a document, a spreadsheet, a chart or a diagram, write the artifact itself in your answer. Do not write a script, run code, use the code interpreter, a terminal or any other tool to produce .pptx, .docx, .xlsx, .pdf or image files, and do not describe the slides in prose: the panel shows the artifact and downloads it as PowerPoint, Word, Excel, PDF or PNG.
- Use tools only to gather what the artifact needs (a web search for facts, for example), then write the artifact.

How to write one
- Put the artifact in one fenced code block. Its first line names it: <!-- artifact: Title --> for Markdown, slides, HTML and SVG; // artifact: Title for JSX and JavaScript; %% artifact: Title for Mermaid; # artifact: Title for CSV, JSON, Python and other code.
- One artifact per answer unless the user asks for several.
- To change an artifact, send the whole new version in a new block with exactly the same title. Never send only the changed part.
- Keep the text around the block to a sentence or two. Don't repeat its content in the text.

Formats
- Document: ```markdown. Use headings, lists, tables, ==highlighted text==, and callouts (a line "> [!NOTE]", "> [!TIP]", "> [!IMPORTANT]" or "> [!WARNING]" followed by "> text"). Put diagrams in ```mermaid blocks, charts in ```chart blocks and drawings in ```svg blocks inside the document.
- Slides: ```slides. Separate slides with a line containing only ---. Start each slide with a # heading; keep to about six short bullets. Speaker notes go after a line starting with "Note:". A slide may hold a ```mermaid diagram, a ```chart, an ```svg drawing, a table, an image ![description](url), ==highlights==, callouts, and two columns separated by a line containing only |||. A slide whose first line is <!-- layout: title --> is a title slide; <!-- layout: section --> a section divider.
- Chart (inside documents or slides, or on its own): ```chart, then optional lines "type: bar", "type: line", "type: area" or "type: pie" and "title: ...", then CSV with a header row; the first column holds labels and the others numbers.
- Spreadsheet: ```csv with a header row. Formulas such as =SUM(B2:B10), =AVERAGE(C2:C9), =B2*C2 and =IF(B2>100,"high","low") are calculated.
- Diagram: ```mermaid (flowchart, sequenceDiagram, gantt, timeline, mindmap, pie, classDiagram, erDiagram, stateDiagram).
- Web page or interface design: ```html, one complete file with its CSS and JavaScript and a <title>.
- Interactive app: ```jsx, one React component with a default export and no required props. You may use Tailwind classes and import from react, lucide-react, recharts and shadcn/ui components from @/components/ui/* (card, button, input, label, textarea, badge, alert, tabs, switch, slider, progress, separator). No other local files.
- Drawing: ```svg with a viewBox.
- Data: ```json.
- Program: the right language, complete and runnable.

Pages and apps can only load the approved libraries; outside images and APIs may be blocked, so draw with SVG and CSS and keep data inside the artifact. An app may call `await window.claude.complete(prompt)` to ask the model, and use window.storage to save data.

Answer in the user's language."""


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Order among filters; lower runs first.")
        instructions: str = Field(
            default="",
            description="Replaces the instructions given to the model when the switch is on. Empty uses the built-in ones, which match what the Artifacts panel can show.",
        )

    def __init__(self):
        self.valves = self.Valves()
        # A switch by the message box, off until someone turns it on (or a model has it on by default)
        self.toggle = True
        self.icon = (
            "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
            "stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09"
            "L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z'/>"
            "<path d='M18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25"
            "l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z'/></svg>"
        )

    def _text(self) -> str:
        custom = str(self.valves.instructions or "").strip()
        return MARK + "\n" + (custom[:20000] if custom else INSTRUCTIONS)

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body
        text = self._text()
        first = messages[0] if messages and isinstance(messages[0], dict) else None
        if first and first.get("role") == "system":
            content = first.get("content")
            if isinstance(content, str):
                if MARK not in content:
                    first["content"] = (content.rstrip() + "\n\n" + text) if content.strip() else text
            elif isinstance(content, list):
                if not any(isinstance(part, dict) and MARK in str(part.get("text", "")) for part in content):
                    content.append({"type": "text", "text": text})
            else:
                first["content"] = text
        else:
            messages.insert(0, {"role": "system", "content": text})
        return body
