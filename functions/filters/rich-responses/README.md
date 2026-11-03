# Rich Responses

ChatGPT-style answers for every model in Open WebUI, streamed live with icons.

With this filter, normal questions get structured, scannable Markdown that streams in real time: "explain this", "summarize this PDF", "compare X and Y", "how do I...". The model starts with the answer, uses emoji icons in headings, bolds key terms, and adds tables, callouts with icons (`> [!TIP]`), Mermaid diagrams and collapsible details where they help. Simple questions stay short and casual chat stays plain. Interactive cards (dashboards, calculators, sliders) remain the job of the [Generative UI](../../../tools/generative-ui/) tool, and this filter tells the model when to call it.

| | |
| --- | --- |
| Type | Filter Function (`inlet`, `stream`, `outlet`) |
| Version | 1.0.1 |
| Open WebUI | 0.12.0 or newer |
| Dependencies | none |

## How it works

1. **`inlet`, before the model runs.** The filter reads the last user message (and whether files are attached) and detects the request type in English, French, Spanish or German: chat, quick fact, explain, summarize, compare, how-to, troubleshoot, plan, code, recommend, creative writing or general. It appends a short style guide and the matching *answer shape* to the system prompt. "Briefly" / "sans détails" makes it compact; "in detail" makes it thorough.
2. **`stream`, while the answer streams.** If the model writes a heading or a bold label without an icon (`## Overview`, `**Pros:**`), the filter inserts one live (`## 🧭 Overview`, `✅ **Pros:**`). Only line starts that look like headings, fences or bold labels are held, for a few characters (headings until their end of line). Everything else passes through immediately, so streaming stays real time. Code blocks are never touched.
3. **`outlet`.** Cleans up per-message stream state.

Everything renders with Open WebUI's own Markdown renderer: headings, emoji, tables, GitHub-style alerts with icons, Mermaid, LaTeX and `<details>`. Raw HTML and SVG icons are not rendered in chat messages (Open WebUI sanitizes them), which is why emoji are the icon set for streamed text.

| Request | Shape the model is asked to stream |
| --- | --- |
| "hi", "thanks" | Natural reply, no formatting |
| "Capital of Peru?" | One bold answer sentence |
| "Explain how DNS works" | Direct answer, 🧭 Overview, 🧩 How it works, optional Mermaid diagram, 🧪 Example, tip callout, 🎯 In short |
| "Summarize this PDF" (or a file attached) | TL;DR note callout, 💡 Key points, 📑 Section by section, data table, ✅ Takeaways, ⚠️ Caveats; faithful to the source, citations kept |
| "Compare A vs B" | Verdict, comparison table with ✅/❌, ⚖️ trade-offs, 🎯 which to choose |
| "How do I install X" | 📋 Prerequisites, 🛠️ numbered steps with code, warning callouts, ✅ Verify, 🩺 Troubleshooting |
| Error messages | Likely cause, 🛠️ Fix, 🔍 other causes, prevention tip |
| "Plan 3 days in Rome" | Day headings with icons, bold times, summary table, 💡 Tips |
| Code requests | Approach, runnable code block, 🔍 How it works, notes |
| "Write a poem/email" | Just the piece, no decoration |

## Installation

1. **Admin Panel > Functions > + (Import)**, paste `rich_responses.py`, save and enable it.
2. Either toggle **Global** on the function (all models) or attach it per model in **Workspace > Models > edit > Filters**.
3. Optional: also install the [Generative UI](../../../tools/generative-ui/) tool for interactive cards.

## Configuration

Valves (administrator):

| Valve | Default | Meaning |
| --- | --- | --- |
| `style` | `chatgpt` | `chatgpt`, `compact` (shorter answers) or `off` |
| `intent_detection` | on | Tailor the answer shape to the request |
| `live_heading_icons` | on | Add icons to bare headings while streaming |
| `live_label_icons` | on | Add icons before bold labels like `**Pros:**` |
| `visual_tool_hint` | on | Tell the model to use `render_ui` for interactive needs |
| `system_prompt_integration` | `append` | Append to the system prompt or add a separate system message |
| `skip_when_study_mode` | on | Leave requests already shaped by Study Mode alone |
| `priority` | 10 | Filter order |

User Valves: `style` (`default`, `chatgpt`, `compact`, `off`) and `live_icons`.

## Test prompts

- Explain how HTTPS works.
- Explain quantum computing briefly.
- Explain Kubernetes in detail.
- Summarize this PDF. (attach a file)
- Compare PostgreSQL and MongoDB.
- How do I set up SSH keys on Ubuntu?
- My Python script fails with ModuleNotFoundError: No module named 'numpy'.
- Plan a 3-day trip to Rome.
- Give me a Python function that validates an email address.
- Recommend five sci-fi books.
- Write a short poem about autumn. (should stay undecorated)
- Capital of Peru? (one line)
- Merci ! (plain reply)

## Permissions and data

Runs inside Open WebUI only. It reads the last user message to choose a shape, edits the system prompt, and edits streamed text in memory. Nothing is stored or sent anywhere.

## Testing

```bash
python -m pytest -q functions/filters/rich-responses/test_rich_responses.py
```

The 52 tests cover intent detection in four languages, prompt injection (idempotent, `append` and `separate`, `off`, Study Mode), and the live icon transformer. That includes 25 random chunkings of the same stream giving identical output, code fences left untouched, `#hashtag` and mid-line `#`, bold text that is not a label, and the flush of a held last line on `finish_reason`. They also check per-message isolation and user opt-out.

## Known limitations

- The filter steers the model; a weak model may still ignore parts of the shape. The live icon pass makes headings consistent either way.
- Icons are emoji. Open WebUI does not render inline SVG in chat Markdown.
- Live icons apply to OpenAI-style streaming chunks (vLLM, Ollama, OpenAI-compatible APIs) and Responses-API deltas. For Responses-API streams, a heading on the very last line that has no newline may lose its icon.
- Not yet run inside a live Open WebUI instance; the `stream` hook contract was checked against the 0.12.5 source.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
