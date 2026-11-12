# Changelog

This repository contains independently versioned Open WebUI extensions. Plugin-specific release notes live next to each implementation when they need more detail.

## 2026-11-12

### Chat Files 2.0.0

Redesigned in Open WebUI's greys. Files is chosen from the tab row's + menu and no longer adds its own button beside it; the button stays where there is no such menu. Files show as a list with Name, Kind, Size and Date columns or as a grid of cards, with pictures of images, kind chips with counts, a menu on every file (⋯, right click, Shift+F10), date groups in My files, range picking, keyboard shortcuts and fuller details: media type, exact size, the file ID, and lines, rows, picture size or length found by the preview. The tab, lists, selection, jobs, layout and preview are explicit state machines; Files follows Open WebUI's language live; names lose direction-control characters that could disguise an extension; zips stay within their 32-bit limits. The browser suite passed 133 of 133 checks on Open WebUI 0.13.3 with Chat Gallery, 126 of 126 without it, and 129 of 129 on 0.13.0.

### Artifacts 1.2.0

Spreadsheets can be corrected as in Excel: click a cell and type, move with Enter, Tab and the arrows, select ranges and see their sum, copy and paste with Excel, undo and redo, insert and delete rows and columns; new formulas (IFERROR, COUNTIF, SUMIF, AVERAGEIF, ROUNDUP, ROUNDDOWN) recalculate as you type, and the Excel download keeps them. Slides can be edited, added and deleted one at a time and download as PowerPoint, with diagrams and charts as pictures. The panel now uses Open WebUI's greys and centred dialogs, follows the platform's language (French included) the moment it changes, and runs its panel, loading, streaming and editing states as explicit state machines. Artifacts mode tells the model to write presentations as artifacts rather than scripts that make files. The browser suite passed 153 of 153 checks on Open WebUI 0.13.0 and 0.13.3, alone and with the other event functions.

### Artifacts 1.1.0

Artifacts now stays out of ordinary conversation: a companion toggle Filter, Artifacts mode, tells the model when and how to make artifacts, and by default only marked blocks and kinds that need a viewer (whole pages, apps, drawings, diagrams, slides, CSV) become artifacts. While one is written, its code is folded behind a card that says so, and the panel opens only on the finished artifact. Documents and slides can hold Mermaid diagrams, multi-series bar, line, area and pie charts, drawings, pictures, highlights and callouts, and slides columns and title or section layouts, all kept in downloads. Sharing was removed from the panel; the copy note and the ask-about-a-selection chip are smaller. The browser suite passed 125 of 125 checks on Open WebUI 0.13.0 and 0.13.3, alone and with the other event functions.

### Artifacts 1.0.0

Replaced Chat Canvas with Artifacts, modelled on Claude's: documents, slides, spreadsheets, Mermaid diagrams, web pages, React apps, SVG drawings, JSON data and code open in a resizable panel beside the chat, with versions across answers and a Changes view, a live view that opens while a model writes, Try fixing, asking about a selected part, private edits that can be sent back to the chat, downloads in the usual formats (Excel keeps formulas), and sharing through Open WebUI's own share links pointed at the artifact, with a copy to continue for the people it is shared with. Generated code runs in a sandbox without the site's origin, held by a content policy to the configured libraries, and may use the chat's model or browser storage only with the person's consent. It stands in for Open WebUI's built-in HTML/SVG Artifacts. The browser suite passed 109 of 109 checks on Open WebUI 0.13.0 and 0.13.3, alone and with the other event functions; Chat Inspector's suite was updated to run beside it.

### Chat Inspector 1.2.0

Context now lists every tool a conversation called, counted and told apart as MCP (by server), workspace tool, tool server, each group of built-in tools, provider-run or automatic, with web searches, pages, sources, code runs, images, failed and declined calls, timings and token cost; the last answer's calls open to show their input and result. Tool results are their own part of the context, answers with tool calls are fitted to the last round's prompt count rather than the total over all rounds, and the model's filters, actions and pipe are shown. The extra explanatory text is gone. The browser suite, now with a real native tool call, passed 103 of 103 checks on Open WebUI 0.13.0 and 0.13.3.

### Chat Inspector 1.1.0

Context moved to the chat's ⋯ menu, under Overview, which every account has, and opens as a pop-up beside the chat instead of a full tab: clear of the message box, so people can keep chatting while it updates, and a sheet from the bottom on phones. Its wording now uses the platform's own name from `WEBUI_NAME`, such as EveGPT. New valves choose the name, the pop-up or the full view, and each way in. The browser suite passed 76 of 76 checks on Open WebUI 0.13.0 and 0.13.3.

## 2026-11-10

### Chat Inspector 1.0.0

Rewrote Chat Inspector as a Context tab in the Tabs panel's + menu, beside Canvas, Files and Gallery, in place of its floating pop-up. It shows a Claude-style grid of the context window and a breakdown of what fills it, fitted to the token count the model reported where there is one, with estimates tuned against the GPT-4o, GPT-4, Llama 3.1, Qwen 2.5 and Mistral tokenizers elsewhere (7 to 12 % average error on held-out text, against 19 to 42 % for characters ÷ 4). It finds the context window from an administrator's rule, model settings or the provider, warns as the chat fills, stops polling, and adds French. Its CI no longer needs a browser install, which had stopped working on current GitHub runners. Includes server tests, a fake streaming model and a browser suite that passed on Open WebUI 0.13.0 and 0.13.3.

### Chat Canvas 1.0.0

Added a Canvas tab Event Function that turns a chat's code blocks and longer answers into editable pages. It has an editor with line numbers and indentation, previews for HTML (sandboxed), SVG, Markdown, JSON and CSV, a line-by-line view of what changed, an *Ask for a change* flow that puts a request and the current version in the message box without sending it, blank canvases, and saving to files or notes. Edits are kept in the browser per person and per chat. It works beside Chat Gallery and Chat Files, and Open WebUI's own Artifacts. Includes server tests, a browser suite that passed on Open WebUI 0.13.0 and 0.13.3 with and without the other two, and a CI workflow.

### Chat Files 1.0.0

Added a Files tab Event Function, a companion to Chat Gallery for every other kind of file. *This chat* lists every file attached on every branch, files the model links to, and the knowledge bases, notes and web pages the chat refers to; *My files* lists every file the person owns. It previews PDFs, CSV and TSV tables, JSON, text, code, audio and video, and shows the text Open WebUI extracted from Word and PowerPoint files. It adds search, a kind filter, ordering, rename, delete, zip, copy, attach and show in chat, takes turns with Chat Gallery, and was measured at 4.5:1 text contrast in light, dark, OLED and Her themes. Includes server tests, a browser suite that passed on Open WebUI 0.13.0 and 0.13.3 with and without Chat Gallery, and a CI workflow.

### Chat Gallery 1.1.0

Added a maintained fork of @G30's Chat Gallery Event Function. The second view now lists every image a person uploaded or generated, so it is useful on a server without image generation. A Gallery button lets accounts without the Tabs panel's + menu open it, and on a screen without a tab row the gallery opens over the chat. Adds a kind filter, sort, refresh, French, small pictures for the grid, request timeouts and seven administrator Valves. Fixes a flaw that could send the sign-in token to another site through a crafted image address in a chat. Includes server tests, a browser suite that passed on Open WebUI 0.13.0 and 0.13.3, and a CI workflow.

## 2026-11-03

### Generative UI 1.2.0

Streaming-mode cards now appear as soon as the model starts a visual and fill in section by section while the answer streams, without requiring the same-origin iframe setting. Cards gain a Download as HTML button and keep their state across reloads when same-origin iframes are allowed.

### Generative UI 1.1.0 and Rich Responses 1.0.1

Generative UI gains a streaming mode: install the same file as a Filter and models can write ```genui blocks inside normal answers. The cards render while the answer is still streaming, with no tool calling needed. Rich Responses now points models to these blocks.

### Rich Responses 1.0.0

Added a Filter that makes normal answers ChatGPT-style while they stream: it detects the request type (explain, summarize a document, compare, how-to, troubleshoot, plan, code, quick fact, chat, creative writing), steers the model to the matching Markdown structure, and adds icons to bare headings and bold labels live during streaming. It pairs with Generative UI for interactive cards.

### Generative UI 1.0.0

Added a new Tool that renders ChatGPT-style interactive answers inline in the chat for any tool-calling model. The model writes one JSON spec; the Tool repairs and validates it, fetches live weather when asked, and renders dashboards, charts, sortable tables, calculators with sliders, checklists, comparisons, option explorers, timelines, auto-layout diagrams, maps and more with a self-contained renderer. Includes a Python test suite, browser tests in an Open WebUI 0.12.5 iframe harness, screenshots and a CI workflow. It replaces the unpublished `frontier_ui.py` prototype and keeps rendering its `blocks` specs.

## 2026-09-25

### Secure Dynamic Onboarding 9.3.0

Added automatic synchronization of existing onboarding guides whenever an administrator saves Valves, so section visibility, branding, language controls, and guide content reach users after they refresh or reopen the chat. Replaced the language buttons with a compact dropdown that lists every embedded locale by name. Added mojibake repair and an ASCII-safe Rich UI transport to preserve French, Spanish, Catalan, punctuation, and symbols across Open WebUI message storage and iframe rendering.

## 2026-09-22

### Secure Dynamic Onboarding 9.2.1

Added complete Spanish and Catalan onboarding catalogs contributed by [delfireinoso](https://github.com/delfireinoso) through issues [#11](https://github.com/CallSohail/openwebu-work/issues/11) and [#12](https://github.com/CallSohail/openwebu-work/issues/12). The guide now selects `es` and `ca` automatically from the matching Open WebUI interface locale and exposes both languages in its selector. The submitted catalogs were validated and received small mechanical repairs before embedding.

## 2026-09-21

### Secure Dynamic Onboarding 9.2.0

Added contributor-ready JSON locale catalogs, a documented translation workflow, strict catalog validation, automatic exact/base matching with the Open WebUI interface language, and dynamic language-selector buttons. English and French remain built in, unsupported interface locales use the administrator's configured fallback language, and additional catalogs are compiled into the portable single-file Event Function. Regular-user output now also removes administrator-only tutorial source and administrator-only translations on the server before the Rich UI payload is returned.

## 2026-09-18

### Documentation and release metadata

Brought every extension up to the same documentation and release standard.

- added the missing changelogs for the RAGFlow Advanced Connector and Secure Dynamic Onboarding, so all five extensions now keep one beside the implementation;
- rewrote the existing changelogs onto the section names from the release-notes template (`Added`, `Changed`, `Fixed`, `Security`, `Compatibility`, `Upgrade notes`) under dated `## [version]` headings;
- expanded the RAGFlow README with its Tool methods, a complete Valve reference with defaults and ranges, the data flow, and known limitations;
- added a Changelog section to each extension README, and a changelog column or link to the Tool, Filter, Action and Event category indexes;
- corrected the Quick Actions version in the Action index from 3.0.0 to 3.0.1;
- added a per-extension changelog table to the root README;
- required a `CHANGELOG.md` beside every implementation in CONTRIBUTING.md, with the section names it should use;
- replaced the stale tag list in RELEASING.md with a published-release table, annotated-tag instructions, and the rule that a tag points at the commit where that version's source last changed.

### Secure Dynamic Onboarding 9.1.0

Added a bilingual, role-aware Event Function that creates and maintains one persistent onboarding chat per eligible user. The release includes an interactive guided tour, a permission-filtered feature library, exact composer and navigation tutorials, Notes, Folders, Channels, Calendar and Automations guidance, safe test-user rollout, signup and first-login delivery, idempotent batch deployment, in-place guide revisions, deleted-guide handling, Redis locking, restrictive iframe CSP, disclosure controls, Python tests, and a full DOM smoke test.

## 2026-09-11

### Rich Email Composer 1.0.0

Added a portable Rich UI email drafting Tool with direct recipient, subject, and body editing, AI-assisted revision prompts, contextual formatting controls, recipient and domain validation, light/dark/mobile support, rich/plain copy, mail-client handoff, multipart `.eml` export, large-mailto fallback, message-level embed rendering, tests, CI, and deployment-neutral styling. The public Tool contains no university-specific names, domains, logos, or color theme and does not hard-code an Open WebUI `0.11.x` version requirement.

## 2026-08-28

### Study Mode 1.1.0

Community-driven Study Mode update focused on compatibility and quiz usability. Added multilingual quiz-intent detection, a bounded Compatible parser for common local-model structured-output variations, optional Strict mode, scoped system-prompt integration, optional MathJax LaTeX rendering, keyboard controls, fullscreen, client-side HTML export, stronger stream/status cleanup, U+2028/U+2029 script escaping, and dedicated regression/JavaScript CI coverage.

### Quick Actions 3.0.1

Fixed the toolbar icon becoming difficult to see in dark/OLED themes by replacing the monochrome external icon with a theme-independent hosted Quick Actions icon.

### Quick Actions 3.0.0

Added the first public Action Function to the repository: a compact context-aware Quick Actions menu for assistant responses. The release includes response rewriting and creation workflows, verification and follow-up actions, code/data/study helpers, a dedicated Humanize section, English/French built-in UI and prompts, admin Team actions, per-user My actions, custom `{input}` actions, safe composer preview/send modes, draft protection, older-message targeting, mobile/desktop layouts, and light/dark accessibility handling.

## 2026-08-26

### Repository structure

- organized extensions into `tools/` and `functions/`;
- added dedicated Function directories for Filters, Pipes, Actions, and Events;
- added category documentation and installation guidance;
- added repository security, contribution, and licensing files;
- moved the RAGFlow connector into `tools/ragflow/`;
- published Study Mode as the public `1.0.0` release line.

### Study Mode 1.0.0

Initial public release with adaptive tutoring, guided and Socratic learning, explain-then-practice mode, native `ask_user` integration when available, interactive quiz Rich UI, quiz transport suppression, randomized answer positions, progress status, hints, copy controls, scoring, and accessible perfect-score feedback.
