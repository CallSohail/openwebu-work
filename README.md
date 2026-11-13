# Open WebUI Extensions

A structured collection of reusable Open WebUI Tools and Functions.

The goal is simple: keep each extension portable, documented, and safe to review before it is installed on an Open WebUI server.

## Extensions

| Extension | Type | Version | Status | Purpose |
| --- | --- | --- | --- | --- |
| [Generative UI](tools/generative-ui/) | Tool + Filter | 1.2.0 | Stable | ChatGPT-style interactive cards that stream inside answers (Filter, any model) or come from a `render_ui` tool call: dashboards, charts, tables, calculators, checklists, comparisons, explorers, timelines, diagrams, maps and live weather, rendered inline from a validated JSON spec |
| [Rich Email Composer](tools/email-composer/) | Tool | 1.0.0 | Stable | Interactive Rich UI email drafting with direct editing, AI-assisted revisions, contextual formatting, recipient validation, copy, `.eml` export, and mail-client handoff |
| [Rich Responses](functions/filters/rich-responses/) | Filter | 1.0.1 | Stable | ChatGPT-style streamed answers for any model: detects explain, summarize, compare, how-to and more, shapes the Markdown, and adds icons to headings live |
| [Study Mode](functions/filters/study-mode/) | Filter | 1.1.0 | Stable | Guided learning, Socratic tutoring, adaptive pacing, native `ask_user`, multilingual interactive quizzes, LaTeX/keyboard/fullscreen/export options, and local-model compatibility hardening |
| [Quick Actions](functions/actions/quick-actions/) | Action | 3.0.1 | Stable | Compact context-aware response transformations, Humanize actions, verification, creation workflows, English/French UI, and user/team custom actions |
| [Secure Dynamic Onboarding](functions/events/secure-onboarding/) | Event | 9.3.0 | Stable | English, French, Spanish and Catalan permission-aware onboarding with a language dropdown, immediate Valve synchronization, UTF-8-safe Rich UI delivery, guided tutorials, and safe rollout |
| [Chat Inspector](functions/events/chat-inspector/) | Event | 1.2.0 | Stable | Context, in the chat's ⋯ menu: a pop-up showing how full the model's context window is, Claude-style, worded with the platform's own name: a grid of what fills it, fitted to the model's own token count, every tool call by kind (MCP, workspace, tool server, built-in), web searches and sources, and a warning before the chat overflows. Token estimates tuned against five real tokenizers |
| [Chat Gallery](functions/events/chat-gallery/) | Event | 1.2.0 | Stable | A Gallery tab with every image in the current chat and every image the person uploaded or generated: filters, zoom, generation details, copy, download, zip, delete and a jump back to the message. Maintained fork of @G30's Chat Gallery, in English and French |
| [Chat Files](functions/events/chat-files/) | Event | 2.0.0 | Stable | A Files tab, chosen from the Tabs panel's + menu, with every document, spreadsheet and other file in the chat and in the person's account, as a list or a grid: previews (PDF, tables, JSON, text, audio, extracted text) with detailed facts, search, kind chips, a menu on every file, rename, delete, zip and a jump back to the message. Companion to Chat Gallery, in English and French, checked for contrast in light, dark, OLED and Her themes |
| [Artifacts](functions/events/chat-artifacts/) | Event | 1.2.0 | Stable | Claude-style Artifacts, with an Artifacts mode switch so ordinary answers stay plain: documents, slides, spreadsheets with formulas, Mermaid diagrams, web pages, React apps, drawings and data open in a panel beside the chat, with versions, live preview, Try fixing, spreadsheets corrected cell by cell as in Excel, slides edited one at a time, exports (PowerPoint, PDF, Word, Excel, PNG), diagrams and charts inside slides and documents, and a sandbox that keeps generated code away from the page and the network. Replaces Chat Canvas |
| [RAGFlow Advanced Connector](tools/ragflow/) | Tool | 3.0.0 | Stable | RAGFlow retrieval, dataset discovery, document search, and configurable retrieval controls |
| [Pulse](apps/pulse/) | Channel app | 0.1.1 | Experimental | A first channel app, run beside Open WebUI: polls with live vote buttons, private replies, downloads and a slow-job example, built with oix |

## Repository structure

```text
.
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── CHANGELOG.md
├── RELEASING.md
│
├── apps/
│   ├── README.md
│   └── pulse/
│       ├── README.md
│       ├── CHANGELOG.md
│       ├── pulse.py
│       ├── test_pulse.py
│       └── screenshots/
│
├── tools/
│   ├── README.md
│   ├── generative-ui/
│   │   ├── README.md
│   │   ├── ARCHITECTURE.md
│   │   ├── CHANGELOG.md
│   │   ├── generative_ui.py
│   │   ├── screenshots/
│   │   └── tests/
│   │       ├── browser_test.js
│   │       ├── build_demos.py
│   │       ├── demos.py
│   │       └── test_generative_ui.py
│   ├── email-composer/
│   │   ├── README.md
│   │   ├── CHANGELOG.md
│   │   ├── SOURCE_SHA256
│   │   ├── email_composer.py
│   │   ├── test_email_composer.py
│   │   └── unpack_source.py
│   └── ragflow/
│       ├── README.md
│       ├── CHANGELOG.md
│       └── ragflow.py
│
└── functions/
    ├── README.md
    ├── filters/
    │   ├── README.md
    │   ├── rich-responses/
    │   │   ├── README.md
    │   │   ├── CHANGELOG.md
    │   │   ├── rich_responses.py
    │   │   └── test_rich_responses.py
    │   └── study-mode/
    │       ├── README.md
    │       ├── CHANGELOG.md
    │       ├── test_study_mode.py
    │       └── study_mode.py
    ├── pipes/
    │   └── README.md
    ├── actions/
    │   ├── README.md
    │   └── quick-actions/
    │       ├── README.md
    │       ├── CHANGELOG.md
    │       ├── quick-actions-icon.svg
    │       └── quick_actions.py
    └── events/
        ├── README.md
        ├── chat-artifacts/
        │   ├── README.md
        │   ├── ARCHITECTURE.md
        │   ├── CHANGELOG.md
        │   ├── artifacts_mode.py
        │   ├── chat_artifacts.py
        │   ├── screenshots/
        │   └── tests/
        │       ├── e2e.mjs
        │       ├── fake_model.py
        │       ├── seed.py
        │       └── test_chat_artifacts.py
        ├── chat-files/
        │   ├── README.md
        │   ├── ARCHITECTURE.md
        │   ├── CHANGELOG.md
        │   ├── chat_files.py
        │   ├── screenshots/
        │   └── tests/
        │       ├── e2e.mjs
        │       ├── seed.py
        │       └── test_chat_files.py
        ├── chat-inspector/
        │   ├── README.md
        │   ├── ARCHITECTURE.md
        │   ├── CHANGELOG.md
        │   ├── chat_inspector.py
        │   ├── screenshots/
        │   └── tests/
        │       ├── e2e.mjs
        │       ├── fake_model.py
        │       ├── fit_tokens.py
        │       ├── seed.py
        │       ├── test_chat_inspector.py
        │       └── token_samples.json
        ├── chat-gallery/
        │   ├── README.md
        │   ├── ARCHITECTURE.md
        │   ├── CHANGELOG.md
        │   ├── chat_gallery.py
        │   ├── screenshots/
        │   └── tests/
        │       ├── e2e.mjs
        │       ├── seed.py
        │       └── test_chat_gallery.py
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

## Extension types

- **Tool**: gives a model a callable capability, such as an API or data source.
- **Filter**: intercepts or transforms requests, streams, or completed responses.
- **Pipe**: exposes a custom model, provider, router, or full workflow.
- **Action**: adds a user-triggered operation to a chat message.
- **Event**: reacts to Open WebUI lifecycle or system events.

Each category has its own README with the intended use and directory convention.

## Installation

Each extension has a dedicated README with its setup steps. In general:

- Tools are imported from **Workspace > Tools**.
- Functions are imported from **Admin Panel > Functions**.
- Review source code before enabling an extension.
- Put credentials and deployment-specific settings in Valves or environment-backed configuration, never directly in source files.

## Compatibility

The repository follows current Open WebUI plugin APIs. Individual extensions declare their own compatibility requirements when a hard minimum is necessary.

Rich Email Composer intentionally does not hard-code an Open WebUI `0.11.x` version requirement in its public Tool metadata. It relies on Rich UI embed/event capabilities described in its own README.

Study Mode and Quick Actions currently declare **Open WebUI 0.11.1 or newer** because they use current Function event APIs and interactive browser-side UI capabilities.

Generative UI declares **Open WebUI 0.12.0 or newer** because it relies on Tool embeds returned as `(HTMLResponse, context)` and the embed prompt messages checked against the 0.12.5 source.

Artifacts and Chat Files declare **Open WebUI 0.13.0 or newer** for the same reason. Artifacts was tested in a browser on 0.13.0 and 0.13.3, alone and with Chat Inspector, Files and Gallery.

Chat Files: It was tested in a browser on 0.13.0 and 0.13.3, with and without Chat Gallery.

Chat Gallery declares **Open WebUI 0.13.0 or newer**, the release that added the Tabs panel it lives in. It was tested in a browser on 0.13.0 and 0.13.3.

Secure Dynamic Onboarding declares **Open WebUI 0.11.3 or newer** because it uses current Event Function hooks, Rich UI embeds, effective permission catalogs, Notes, Channels, Calendar, Automations, and current chat storage APIs.

## Security

Open WebUI Tools and Functions execute Python on the Open WebUI server. Treat extensions as trusted server-side code.

This repository should not contain:

- API keys or passwords;
- private endpoints or internal IP addresses;
- personal or institutional email addresses;
- user data or private documents;
- deployment-specific secrets.

See [SECURITY.md](SECURITY.md) for the repository policy.

## Versioning and releases

Extensions are versioned independently using semantic versioning.

Recommended Git tag format:

```text
<plugin-name>-v<version>
```

Examples:

```text
generative-ui-v1.2.0
email-composer-v1.0.0
study-mode-v1.1.0
quick-actions-v3.0.1
ragflow-v3.0.0
secure-onboarding-v9.3.0
chat-gallery-v1.1.0
chat-files-v2.0.0
chat-artifacts-v1.2.0
```

See [RELEASING.md](RELEASING.md) for the release checklist, tag convention, and suggested GitHub repository topics.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the extension structure, documentation checklist, security expectations, and versioning rules.

## Changelog

Repository-level changes are tracked in [CHANGELOG.md](CHANGELOG.md).

Every extension also keeps its own changelog beside the implementation:

| Extension | Changelog | Latest |
| --- | --- | --- |
| Generative UI | [tools/generative-ui/CHANGELOG.md](tools/generative-ui/CHANGELOG.md) | `generative-ui-v1.2.0` |
| Rich Email Composer | [tools/email-composer/CHANGELOG.md](tools/email-composer/CHANGELOG.md) | `email-composer-v1.0.0` |
| RAGFlow Advanced Connector | [tools/ragflow/CHANGELOG.md](tools/ragflow/CHANGELOG.md) | `ragflow-v3.0.0` |
| Rich Responses | [functions/filters/rich-responses/CHANGELOG.md](functions/filters/rich-responses/CHANGELOG.md) | `rich-responses-v1.0.1` |
| Study Mode | [functions/filters/study-mode/CHANGELOG.md](functions/filters/study-mode/CHANGELOG.md) | `study-mode-v1.1.0` |
| Quick Actions | [functions/actions/quick-actions/CHANGELOG.md](functions/actions/quick-actions/CHANGELOG.md) | `quick-actions-v3.0.1` |
| Secure Dynamic Onboarding | [functions/events/secure-onboarding/CHANGELOG.md](functions/events/secure-onboarding/CHANGELOG.md) | `secure-onboarding-v9.3.0` |
| Chat Gallery | [functions/events/chat-gallery/CHANGELOG.md](functions/events/chat-gallery/CHANGELOG.md) | `chat-gallery-v1.1.0` |
| Chat Files | [functions/events/chat-files/CHANGELOG.md](functions/events/chat-files/CHANGELOG.md) | `chat-files-v2.0.0` |
| Artifacts | [functions/events/chat-artifacts/CHANGELOG.md](functions/events/chat-artifacts/CHANGELOG.md) | `chat-artifacts-v1.2.0` |

## License

Released under the [MIT License](LICENSE).
