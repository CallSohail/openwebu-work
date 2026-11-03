# Generative UI Changelog

All notable changes to the Generative UI Tool are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `generative-ui-v<version>`. See [RELEASING.md](../../RELEASING.md) for the release process.

## [1.2.0] - 2026-11-03

### Added

- Live cards in streaming mode: the card appears the moment a ```genui block opens (title and a "Rendering..." skeleton) and fills in component by component while the JSON streams; only newly completed components fade in. Valves `live_preview` and `live_preview_interval`.
- Stop-safe: if the answer is stopped mid-card, `outlet` finishes the card from what arrived or removes the shell.
- "Download as HTML" button on every final card (standalone, still interactive).
- Interactive state (sliders, tabs, inputs) survives page reloads when "Allow iframe same origin" is enabled; silently disabled otherwise.
- Loading states for live weather and photos while a card is streaming.

### Fixed

- Embeds published by the filter no longer duplicate older live versions of the same card; cards from other tools in the message are kept.

## [1.1.0] - 2026-11-03

### Added

- Streaming mode: the same file installed under Admin Panel > Functions acts as a Filter. Models write ```genui JSON blocks inside normal answers; the filter hides the JSON while it streams, shows build progress, renders the card the moment the block closes and keeps the text streaming. No tool calling required.
- Intent-aware prompt injection (`prompt_mode` auto/always/off), placeholder captions, per-message card limit, repair of blocks cut off at the end of a stream, outlet rendering for non-streaming responses, Responses-API support.
- 40 streaming tests.

### Changed

- Tool description and README document both modes; CI runs the whole test folder.

## [1.0.0] - 2026-11-03

### Added

- `render_ui`: renders a validated JSON spec as an interactive embed with 43 component types: layout (grid, card, section, tabs, accordion), text (Markdown subset, callouts, code with copy, key-value, tags), data (metrics with progress and sparklines, gauges, eight chart kinds, heatmap, sortable and filterable tables with CSV export, status boards), sequences (timeline, interactive steps, flow, auto-layout diagrams), choices (selectable explorer, checklist with totals, product comparison), media and places (images, gallery and carousel, OpenStreetMap, live weather), inputs and actions.
- Reactive `state`, `computed` values, `{{expr}}` templates and `visible_if`, powered by a safe expression evaluator with financial functions (`pmt`, `fv`, `pv`).
- `show_weather`: live forecasts from Open-Meteo with clear error states.
- `ui_examples`: complete example specs that models can copy.
- Lenient parsing and repair of malformed model output, type and field aliases, and migration of Frontier UI 1.x `blocks` specs, with every repair reported to the model.
- Sandboxed `app` component for custom HTML/JS mini apps without network access.
- Light and dark themes that follow Open WebUI, container-query layouts for narrow chats and phones, keyboard and screen-reader support.
- Python test suite, browser test suite running in an Open WebUI 0.12.5 iframe harness, demo builder and screenshots.

### Security

- Allow-listed components and fields, URL scheme filtering, no `innerHTML` for model data, single-pass HTML templating, ASCII-escaped JSON, renderer CSP with `connect-src 'none'`, size and depth limits.

### Compatibility

- Open WebUI 0.12.0 or newer; embed and message APIs checked against the 0.12.5 source. Not yet run in a live Open WebUI instance.

### Upgrade notes

- Replaces the unpublished Frontier UI 1.3.1 (`frontier_ui.py`). Its `blocks` specs keep rendering. Stock quotes, Craft/Office file tools, Gmail drafts and notes were removed.
