# Artifacts Changelog

All notable changes to the Artifacts Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-artifacts-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

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
