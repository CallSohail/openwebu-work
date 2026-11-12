# Artifacts Changelog

All notable changes to the Artifacts Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-artifacts-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.1.0] - 2026-11-12

### Added

- **Artifacts mode** (`artifacts_mode.py`), a toggle Filter: a switch by the message box that tells the model how and when to make artifacts, and never for small talk or ordinary answers. It can be on by default per model.
- `detect` valve: `rich` (default) makes artifacts only of marked blocks and kinds that need a viewer; `marked` and `all` are stricter and looser.
- Inside documents and slides: Mermaid diagrams, charts, SVG drawings, pictures from this site or `data:`, `==highlights==`, callouts (`> [!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`); on slides also columns (`|||`) and title, section and centred layouts. Downloads include them.
- Chart blocks (```` ```chart ````) with bar, line, area and pie charts and several series; the spreadsheet chart can show every column at once.
- A card that says an artifact is being written, with its code folded away, and a switch on each card to show the code in the chat again.

### Changed

- The panel no longer opens with half-written code: it opens once the answer is done, on the finished artifact.
- Ordinary answers, long answers, Markdown tables, JSON snippets and short code no longer become artifacts unless marked (see `detect`).
- `collapse_code` is on by default.
- The copy note is a small pill, and the offer to ask about a selection is a small chip that steps aside when you copy.
- Ready requests under *New* mark their block in its info line (```` ```slides artifact ````) and ask for diagrams, charts and highlights where they help.
- Excel-style errors inside a range now carry through SUM, AVERAGE, MIN, MAX, MEDIAN and PRODUCT.

### Removed

- Sharing from the panel (*Share*, *Make a copy to continue*) and the `allow_share` valve. Chats shared with Open WebUI's own Share still show their artifacts, read-only.

## [1.0.0] - 2026-11-12

First release. It replaces Chat Canvas 1.0.0; the function id is new (`chat_artifacts`), so Canvas should be disabled or deleted.

### Added

- A panel that docks beside the chat, as Claude's does, resizable, full screen on request and covering the screen on phones.
- Nine kinds: documents, slides, spreadsheets, Mermaid diagrams, web pages, React apps (React 18, Tailwind, npm packages, shadcn/ui pieces), SVG drawings, JSON data and code, found in ordinary code blocks, long answers and Markdown tables.
- Versions across answers, with a switcher and a Changes view, and following new versions as they arrive.
- Cards under answers, Open buttons on code blocks, a button beside ⋯ and a ⋯ menu entry.
- A live view while a model writes, opening by itself for pages, apps, drawings, diagrams, slides and sheets.
- Try fixing for errors inside pages and apps; asking about a selected part; editing as a private draft and sending it to the chat.
- Slides with thumbnails, speaker notes and presenting; a spreadsheet with formulas, sorting, filtering, column statistics, charts and editing; a JSON tree and table.
- Downloads per kind: Markdown, HTML, Word, PDF (print), self-contained slide decks, CSV, Excel with formulas, SVG, PNG, JSX and standalone app pages.
- Sharing through Open WebUI's own share links, pointed at the artifact, with *Make a copy to continue* for the people it is shared with.
- `window.claude.complete()` with the person's consent and a rate limit, and `window.storage` per person and artifact.
- *New*, with ready requests for ten kinds, and blank artifacts of one's own.
- A sandbox without the site's origin and a content policy limited to the configured libraries.
- Standing in for Open WebUI's built-in HTML/SVG Artifacts view (`replace_builtin`).
- English and French; light, dark, OLED black and *Her*.
