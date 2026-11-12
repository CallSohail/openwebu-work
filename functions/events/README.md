# Event Functions

Event Functions run in response to Open WebUI system events. They are intended for lifecycle automation and background reactions rather than in-band chat transformation.

Typical uses include:

- user onboarding and provisioning;
- audit and observability hooks;
- reacting to configuration changes;
- startup and shutdown setup;
- chat lifecycle automation;
- external notifications and integrations.

Event Functions were introduced in Open WebUI 0.10.0 and are auto-detected from a top-level `Event` class.

## Directory convention

```text
events/
├── README.md
├── chat-canvas/
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── chat_canvas.py
│   ├── screenshots/
│   └── tests/
├── chat-files/
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── chat_files.py
│   ├── screenshots/
│   └── tests/
├── chat-inspector/
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── chat_inspector.py
│   ├── screenshots/
│   └── tests/
├── chat-gallery/
│   ├── README.md
│   ├── ARCHITECTURE.md
│   ├── CHANGELOG.md
│   ├── chat_gallery.py
│   ├── screenshots/
│   └── tests/
└── secure-onboarding/
    ├── README.md
    ├── CHANGELOG.md
    ├── locales/
    │   ├── README.md
    │   ├── ca.json
    │   ├── en.json
    │   ├── es.json
    │   └── fr.json
    ├── secure_onboarding.py
    ├── smoke_secure_onboarding.mjs
    ├── sync_locales.py
    └── test_secure_onboarding.py
```

## Included Event Functions

### Secure Dynamic Onboarding 9.3.0

Creates one persistent, multilingual onboarding chat per eligible user. The Rich UI guide follows the Open WebUI interface language when a matching locale is embedded, while keeping an optional language dropdown and an administrator-configured fallback locale. Its content is filtered by effective permissions, accessible Tools and models, global feature flags, role, and administrator Valves. Saving Valves can synchronize every existing guide in the background, and the ASCII-safe Rich UI transport prevents mojibake. It supports contributor-friendly JSON locale catalogs, controlled testing, signup and first-login delivery, in-place content updates, idempotent batch deployment, Redis locking, and user-respected deletion behavior.

See [secure-onboarding/README.md](secure-onboarding/README.md) for installation, rollout, privacy, security, Valves, testing, and troubleshooting, and [secure-onboarding/CHANGELOG.md](secure-onboarding/CHANGELOG.md) for the release history.

### Chat Inspector 1.1.0

Adds Context to the chat's ⋯ menu, opening a pop-up that shows how much of the model's context window a chat uses, the way Claude does: a grid of 100 squares coloured by what fills them, and a breakdown into system prompt, the tools and documents Open WebUI added, messages and free space. Where the model reported its own prompt size, every part is fitted to that count; elsewhere, estimates are tuned against the real tokenizers of five model families. It also shows the last answer's token counts and warns before the chat overflows. It reads only, doesn't poll, and is in English and French. It requires Open WebUI 0.13.0 or newer.

See [chat-inspector/README.md](chat-inspector/README.md) for installation, Valves, how the numbers are made and limits, and [chat-inspector/ARCHITECTURE.md](chat-inspector/ARCHITECTURE.md) for how it works.

### Chat Gallery 1.1.0

Adds a Gallery tab to the chat: every image in the current chat as a grid, and a second view with every image the person uploaded or generated in any chat. It has search, a kind filter, a viewer with zoom and generation details read from PNG files, copy, download, zip, attach to message, delete, and a jump back to the source message, in English and French. It runs in the browser with the person's own access and adds no server routes. A maintained fork of @G30's Chat Gallery; it requires Open WebUI 0.13.0 or newer.

See [chat-gallery/README.md](chat-gallery/README.md) for installation, Valves, security and limits, [chat-gallery/ARCHITECTURE.md](chat-gallery/ARCHITECTURE.md) for how it works, and [chat-gallery/CHANGELOG.md](chat-gallery/CHANGELOG.md) for the release history.

### Chat Files 1.0.0

Adds a Files tab to the chat: every document, spreadsheet and other file in the current chat, including references to knowledge bases, notes and web pages, and a second view with every file the person owns. It previews PDFs, tables, JSON, text, code and audio, shows the text Open WebUI extracted from office files, and offers search, filters, rename, delete, zip, attach and a jump back to the message, in English and French. It is a companion to Chat Gallery and takes turns with it. It requires Open WebUI 0.13.0 or newer.

See [chat-files/README.md](chat-files/README.md) for installation, Valves, security and limits, [chat-files/ARCHITECTURE.md](chat-files/ARCHITECTURE.md) for how it works, and [chat-files/CHANGELOG.md](chat-files/CHANGELOG.md) for the release history.

### Chat Canvas 1.0.0

Adds a Canvas tab to the chat: each code block and longer answer becomes a page to edit, preview, compare with the original and hand back to the model with a request. HTML previews run in a sandbox, edits are kept in the person's browser and never change the chat, and nothing is sent to the model without the person pressing send. It runs beside Open WebUI's own Artifacts, Chat Gallery and Chat Files, in English and French. It requires Open WebUI 0.13.0 or newer.

See [chat-canvas/README.md](chat-canvas/README.md) for installation, Valves, security and limits, [chat-canvas/ARCHITECTURE.md](chat-canvas/ARCHITECTURE.md) for how it works, and [chat-canvas/CHANGELOG.md](chat-canvas/CHANGELOG.md) for the release history.
