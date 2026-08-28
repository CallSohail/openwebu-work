# Open WebUI Extensions

A structured collection of reusable Open WebUI Tools and Functions.

The goal is simple: keep each extension portable, documented, and safe to review before it is installed on an Open WebUI server.

## Extensions

| Extension | Type | Version | Status | Purpose |
| --- | --- | --- | --- | --- |
| [Study Mode](functions/filters/study-mode/) | Filter | 1.0.0 | Stable | Guided learning, Socratic tutoring, adaptive pacing, native `ask_user` support, and interactive quizzes |
| [Quick Actions](functions/actions/quick-actions/) | Action | 3.0.0 | Stable | Compact context-aware response transformations, Humanize actions, verification, creation workflows, English/French UI, and user/team custom actions |
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
│   └── ragflow/
│       ├── README.md
│       └── ragflow.py
│
└── functions/
    ├── README.md
    ├── filters/
    │   ├── README.md
    │   └── study-mode/
    │       ├── README.md
    │       ├── CHANGELOG.md
    │       └── study_mode.py
    ├── pipes/
    │   └── README.md
    ├── actions/
    │   ├── README.md
    │   └── quick-actions/
    │       ├── README.md
    │       ├── CHANGELOG.md
    │       └── quick_actions.py
    └── events/
        └── README.md
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

The repository follows current Open WebUI plugin APIs. Individual extensions declare their own compatibility requirements.

Study Mode and Quick Actions currently declare **Open WebUI 0.11.1 or newer** because they use the current Function event APIs and interactive browser-side UI capabilities.

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
study-mode-v1.0.0
quick-actions-v3.0.0
ragflow-v3.0.0
```

See [RELEASING.md](RELEASING.md) for the release checklist, tag convention, and suggested GitHub repository topics.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the extension structure, documentation checklist, security expectations, and versioning rules.

## Changelog

Repository-level changes are tracked in [CHANGELOG.md](CHANGELOG.md). Extensions may also keep their own changelog beside the implementation.

## License

Released under the [MIT License](LICENSE).
