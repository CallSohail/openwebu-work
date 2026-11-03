# Generative UI Changelog

All notable changes to the Generative UI Tool are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `generative-ui-v<version>`. See [RELEASING.md](../../RELEASING.md) for the release process.

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
