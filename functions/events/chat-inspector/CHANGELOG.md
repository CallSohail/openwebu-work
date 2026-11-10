# Chat Inspector Changelog

All notable changes to the Chat Inspector Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-inspector-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.0.0] - 2026-11-10

A rewrite. The function id, `chat_inspector`, is unchanged, so pasting the new file over 0.2.0 upgrades it in place.

### Added

- A **Context** tab in the Tabs panel's + menu, beside Canvas, Files and Gallery, in place of the floating pop-up.
- A Claude-style grid of 100 squares, one per percent of the context window, coloured by what fills it, with free space and the share Open WebUI reserves before it summarises older messages.
- A breakdown: system prompt, the tools, documents and instructions Open WebUI added, the summary of earlier messages, messages, the latest answer, and what came after the last count. Each part says whether it was counted by the model, estimated, or worked out.
- The context window, from an administrator's rule, the model's or chat's settings, your own settings for Ollama models, or the provider; unknown windows are said to be unknown.
- A warning as the chat fills up and once it is past the window, how many more exchanges like the recent ones fit, and how far it is from Open WebUI's summary threshold.
- The last answer's read, written, reasoning and cached tokens, speed and time; totals for the conversation.
- A model picker for chats that ask several models at once.
- French. Five Valves: `context_windows`, `warn_percent`, `show_capabilities`, `header_button`, `language`.

### Changed

- Token estimates are tuned per model family (GPT-4o, GPT-4, Llama, Qwen, Mistral) against the real tokenizers: 7 to 12 % average error on held-out text in 20 languages and code, against 19 to 42 % for the old characters ÷ 4.
- When the model reported its own prompt size, every part is fitted to that count, so totals match it.
- No polling: the tab reads the chat when it opens, when you return to it, and once an answer has finished.
- The optional button outside the + menu is off by default.
- Replaced the simulated browser test with tests against a real Open WebUI and a streaming fake model; the CI workflow no longer installs a browser, which had stopped working on current GitHub runners.

### Removed

- `build.py` and `inspector.js`: `chat_inspector.py` is now edited directly, like the other functions in this repository.
