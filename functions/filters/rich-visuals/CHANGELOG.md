# Rich Visuals Changelog

All notable changes to the Rich Visuals Filter Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `rich-visuals-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.0.0] - 2026-11-17

First release, for Open WebUI 0.13.4's built-in Visualization.

### Added

- Classification of each message: greetings, thanks and small talk in seven languages, requests for plain text, and background tasks are left alone; questions that benefit from a visual (weather, comparisons, numbers, money, plans, processes, health, travel, science) get a request for one; other questions get clean Markdown.
- Icon tokens (`[[icon:name]]`, with an optional accessible label) that become inline Lucide SVG icons while the Visualization tool call streams, with 225 icons and friendly aliases; the model is offered the icons that fit the topic.
- Repair of what models write instead: emojis in visuals become icons; loose tokens, icon names in elements, icon-library classes and images of made-up icon files become real icons.
- Emojis left out of answer text unless asked for, with spacing tidied and code blocks untouched.
- Support for escaped arguments, pieces cut between chunks at any size, and the Responses API.
- No events at all: no status lines or messages.
- Valves: `enabled`, `visuals`, `emojis`, `icons_per_answer`, `skip_phrases`, `priority`; per-person `enabled` and `visuals`.
