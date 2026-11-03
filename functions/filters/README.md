# Filter Functions

Filters act as middleware around model requests and responses. They are a good fit for behavior enforcement, tutoring modes, redaction, moderation, context injection, observability, and response transformation.

A Filter can use:

- `inlet()` to modify a request before it reaches the model;
- `stream()` to inspect or transform streaming output;
- `outlet()` to process the completed response;
- `Valves` for administrator configuration;
- `UserValves` for user or chat-level preferences;
- Open WebUI events and Rich UI where appropriate.

## Directory convention

```text
filters/
└── filter-name/
    ├── README.md
    ├── CHANGELOG.md
    └── filter_name.py
```

## Included filters

| Filter | Version | Changelog | Purpose |
| --- | --- | --- | --- |
| [Rich Responses](rich-responses/) | 1.0.0 | [CHANGELOG](rich-responses/CHANGELOG.md) | ChatGPT-style streamed answers: intent-aware structure, live heading icons, callouts, tables and diagrams |
| [Study Mode](study-mode/) | 1.1.0 | [CHANGELOG](study-mode/CHANGELOG.md) | Adaptive tutoring and interactive learning for existing models |
