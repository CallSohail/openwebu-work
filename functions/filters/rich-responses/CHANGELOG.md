# Rich Responses Changelog

All notable changes to the Rich Responses Filter Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `rich-responses-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.0.0] - 2026-11-03

### Added

- Intent detection (chat, quick fact, explain, summarize, compare, how-to, troubleshoot, plan, code, recommend, creative, general) in English, French, Spanish and German, with brief and in-depth length hints.
- ChatGPT-style formatting guide and per-intent answer shapes injected into the system prompt, including document summaries that stay faithful to the source.
- Live icons for bare headings and bold labels while the answer streams, with code fences left untouched.
- Hint to use the Generative UI `render_ui` tool for interactive answers.
- Administrator and user Valves, Study Mode coexistence, 52 tests.

### Compatibility

- Open WebUI 0.12.0 or newer; `stream` hook behavior checked against the 0.12.5 source.
