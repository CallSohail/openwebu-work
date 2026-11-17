# Rich Visuals Changelog

All notable changes to the Rich Visuals Filter Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `rich-visuals-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.1.0] - 2026-11-17

### Fixed

- Visuals with a chart or any script failed with "Uncaught SyntaxError: Failed to execute 'replaceWith' on 'Element'". An icon token or emoji inside a JavaScript string was replaced with SVG markup, which broke the string. Icons now go only into the visual's visible text. In scripts and styles, tokens and emojis are removed; in attributes they are removed; comments are left as they are.
- The filter also changed the arguments of other tools (web search, code and so on). Only `render_visualization` is touched now.

### Changed

- The `render_visualization` arguments are held until they are complete, then changed once as parsed JSON and sent in one piece. The visual appears when the tool call ends, as before, and the JSON is always valid. Arguments that are not valid JSON, or are far larger than Visualization accepts (400 KB), go out exactly as they came.
- Icons take the colour of the text around them instead of the theme's accent colour. The model is told to use colour only for meaning (danger, warning, good) and icons only where they help scanning.
- The instructions added to the system prompt are about 60% shorter (about 180 tokens with a visual, a few words without). `icons_per_answer` now defaults to 18.
- The title of a visual loses emojis and tokens too.

### Removed

- Handling of `\uXXXX`-escaped emojis while streaming: the arguments are now parsed as a whole, so escapes need no special case.
- Changes to Responses API function-call events, which Open WebUI 0.13.4 turns into chat chunks before filters see them.

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
