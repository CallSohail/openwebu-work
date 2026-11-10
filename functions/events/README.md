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

### Chat Inspector 0.1.1

Shows reported token usage for the current branch's last assistant response and the response model's explicit capability settings. It reads data only on request and distinguishes configuration from effective availability. See [chat-inspector/README.md](chat-inspector/README.md) for installation, security, limitations and testing.

### Chat Gallery 1.1.0

Adds a Gallery tab to the chat: every image in the current chat as a grid, and a second view with every image the person uploaded or generated in any chat. It has search, a kind filter, a viewer with zoom and generation details read from PNG files, copy, download, zip, attach to message, delete, and a jump back to the source message, in English and French. It runs in the browser with the person's own access and adds no server routes. A maintained fork of @G30's Chat Gallery; it requires Open WebUI 0.13.0 or newer.

See [chat-gallery/README.md](chat-gallery/README.md) for installation, Valves, security and limits, [chat-gallery/ARCHITECTURE.md](chat-gallery/ARCHITECTURE.md) for how it works, and [chat-gallery/CHANGELOG.md](chat-gallery/CHANGELOG.md) for the release history.
