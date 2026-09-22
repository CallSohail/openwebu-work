# Open WebUI Extensions

A structured collection of reusable Open WebUI Tools and Functions.

The goal is simple: keep each extension portable, documented, and safe to review before it is installed on an Open WebUI server.

## Extensions

| Extension | Type | Version | Status | Purpose |
| --- | --- | --- | --- | --- |
| [Rich Email Composer](tools/email-composer/) | Tool | 1.0.0 | Stable | Interactive Rich UI email drafting with direct editing, AI-assisted revisions, contextual formatting, recipient validation, copy, `.eml` export, and mail-client handoff |
| [Study Mode](functions/filters/study-mode/) | Filter | 1.1.0 | Stable | Guided learning, Socratic tutoring, adaptive pacing, native `ask_user`, multilingual interactive quizzes, LaTeX/keyboard/fullscreen/export options, and local-model compatibility hardening |
| [Quick Actions](functions/actions/quick-actions/) | Action | 3.0.1 | Stable | Compact context-aware response transformations, Humanize actions, verification, creation workflows, English/French UI, and user/team custom actions |
| [Secure Dynamic Onboarding](functions/events/secure-onboarding/) | Event | 9.2.1 | Stable | English, French, Spanish and Catalan permission-aware onboarding with contributor locale catalogs, guided tutorials, a feature library, safe rollout, and batch deployment |
| [RAGFlow Advanced Connector](tools/ragflow/) | Tool | 3.0.0 | Stable | RAGFlow retrieval, dataset discovery, document search, and configurable retrieval controls |

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
├── tools/
│   ├── README.md
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
email-composer-v1.0.0
study-mode-v1.1.0
quick-actions-v3.0.1
ragflow-v3.0.0
secure-onboarding-v9.2.1
```

See [RELEASING.md](RELEASING.md) for the release checklist, tag convention, and suggested GitHub repository topics.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the extension structure, documentation checklist, security expectations, and versioning rules.

## Changelog

Repository-level changes are tracked in [CHANGELOG.md](CHANGELOG.md).

Every extension also keeps its own changelog beside the implementation:

| Extension | Changelog | Latest |
| --- | --- | --- |
| Rich Email Composer | [tools/email-composer/CHANGELOG.md](tools/email-composer/CHANGELOG.md) | `email-composer-v1.0.0` |
| RAGFlow Advanced Connector | [tools/ragflow/CHANGELOG.md](tools/ragflow/CHANGELOG.md) | `ragflow-v3.0.0` |
| Study Mode | [functions/filters/study-mode/CHANGELOG.md](functions/filters/study-mode/CHANGELOG.md) | `study-mode-v1.1.0` |
| Quick Actions | [functions/actions/quick-actions/CHANGELOG.md](functions/actions/quick-actions/CHANGELOG.md) | `quick-actions-v3.0.1` |
| Secure Dynamic Onboarding | [functions/events/secure-onboarding/CHANGELOG.md](functions/events/secure-onboarding/CHANGELOG.md) | `secure-onboarding-v9.2.1` |

## License

Released under the [MIT License](LICENSE).
