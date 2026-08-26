# Open WebUI Extensions

A structured collection of reusable Open WebUI extensions, organized by the extension type that Open WebUI actually loads.

This repository is intended for plugins that are useful beyond one deployment. Code committed here should use generic configuration, avoid environment-specific secrets or URLs, and include enough documentation for another administrator to install and review it safely.

## Repository layout

```text
.
├── tools/
│   ├── README.md
│   └── ragflow/
│       ├── README.md
│       └── ragflow.py
└── functions/
    ├── README.md
    ├── filters/
    │   ├── README.md
    │   └── study-mode/
    │       ├── README.md
    │       └── study_mode.py
    ├── pipes/
    │   └── README.md
    ├── actions/
    │   └── README.md
    └── events/
        └── README.md
```

## Extension catalog

| Extension | Type | Version | What it does | Tags |
| --- | --- | --- | --- | --- |
| [Study Mode](functions/filters/study-mode/) | Filter | 1.0.0 | Adds guided learning, Socratic tutoring, adaptive pacing, native `ask_user` support, and an interactive quiz experience | `filter`, `study-mode`, `education`, `ask-user`, `rich-ui` |
| [RAGFlow Advanced Connector](tools/ragflow/) | Tool | 3.0.0 | Connects Open WebUI models to RAGFlow retrieval APIs with configurable search and retrieval behavior | `tool`, `rag`, `ragflow`, `retrieval` |

## Choosing the right extension type

- **Tool**: give a model a capability it can call, such as an API or data source.
- **Filter**: inspect or modify messages before, during, or after model generation.
- **Pipe**: expose a custom model, provider, router, or full workflow.
- **Action**: add a user-triggered button to a chat message.
- **Event**: react to Open WebUI system events such as signups, startup, configuration changes, or chat lifecycle events.

## Installation

Each extension has its own README with installation and configuration instructions. In general:

- Tools are imported from **Workspace > Tools**.
- Functions are imported from **Admin Panel > Functions**.
- Review every plugin before importing it.
- Configure secrets through Valves or environment-backed settings, never by hard-coding credentials in source files.

## Compatibility

The repository targets current Open WebUI plugin APIs. Individual plugins declare their minimum supported Open WebUI version in their source metadata and README.

## Security

Open WebUI Tools and Functions execute Python on the Open WebUI server. Treat every plugin as trusted server-side code. Review source before installation and restrict Tool/Function management to trusted administrators.

No deployment-specific credentials, private endpoints, institutional email addresses, or private infrastructure details should be committed to this repository.

See [SECURITY.md](SECURITY.md) for repository security guidance.

## Versioning

Plugins use semantic versioning independently. A change to one plugin does not require every other plugin to change version.

Recommended Git tag pattern:

```text
<plugin-name>-v<version>
```

For example:

```text
study-mode-v1.0.0
ragflow-v3.0.0
```

## Contributing

Keep each extension self-contained in its own directory with:

```text
plugin-name/
├── README.md
└── plugin_name.py
```

The README should explain purpose, compatibility, installation, configuration, permissions, testing, and known limitations.

## License

Released under the [MIT License](LICENSE).
