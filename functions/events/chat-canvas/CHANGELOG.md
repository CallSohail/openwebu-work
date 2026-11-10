# Chat Canvas Changelog

All notable changes to the Chat Canvas Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-canvas-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.0.0] - 2026-11-10

### Added

- A **Canvas** tab beside the chat that lists the code blocks and longer documents in the chat, newest first, each as an editable page. It reads fenced blocks the way Markdown does: longer fences holding shorter ones, `~~~` fences, blocks inside lists, and a block still being written. Code inside the model's reasoning is left out.
- An editor with line numbers, Tab and Shift+Tab indentation that keeps undo, a line and column readout, optional line wrapping, and right-to-left text.
- **Preview** for HTML (sandboxed), SVG, Markdown, JSON, and CSV or TSV.
- **Changes**: what you changed against the original, line by line, or against another canvas such as the model's earlier version.
- **Ask for a change**: describe a change to the whole canvas or to the selected part; the request and your current version are put in the message box for you to check and send.
- **Put in the message box**, Copy, Download, Download all as a zip, Save to my files, Save as a note, Show in chat.
- **New**: a blank canvas of your own, with a title and a language.
- Edits are kept in the browser, per person and per chat, and survive a reload. The editor says so, and says when it can't.
- A canvas follows an answer while it is still being written, and Canvas picks up new answers on its own while it is in front.
- A Canvas button in the chat's tab row or top bar, and a Canvas item in the Tabs panel's + menu. Works beside Chat Gallery and Chat Files with a fixed tab and button order.
- English and French. Seven administrator Valves.

### Security

- An HTML preview runs in a sandboxed frame that is never given same-origin access, so the page can't reach Open WebUI, its storage or the person's session. The `run_scripts` valve also stops the page's own scripts.
- SVG is shown as an image, so scripts in it can't run. The Markdown preview creates text and a fixed set of elements only; raw HTML stays text and only `http`, `https` and `mailto` links are links.
- Nothing is sent to the model for the person: Canvas fills the message box and stops.
- Code containing `{{ }}` templates, which Open WebUI would rewrite in the message box, travels as an attached file instead.
- The sign-in token goes only to addresses the browser resolves to the Open WebUI site.

### Compatibility

- Open WebUI 0.13.0 or newer. Tested in a browser on 0.13.0 and 0.13.3, with and without Chat Gallery 1.1.0 and Chat Files 1.0.0.
