# Tools

Tools give a model capabilities it can call during a conversation, such as querying an API, retrieving data, or performing a specialized operation.

## Available Tools

| Tool | Version | Changelog | Purpose |
| --- | --- | --- | --- |
| [Rich Email Composer](email-composer/) | 1.0.0 | [CHANGELOG](email-composer/CHANGELOG.md) | Interactive Rich UI email drafting with direct editing, AI-assisted revisions, contextual formatting, recipient validation, copy, `.eml` export, and mail-client handoff |
| [RAGFlow Advanced Connector](ragflow/) | 3.0.0 | [CHANGELOG](ragflow/CHANGELOG.md) | RAGFlow retrieval, dataset discovery, document search, and configurable retrieval controls |

## Structure

Each Tool should live in its own directory:

```text
tools/
├── README.md
├── email-composer/
│   ├── README.md
│   ├── CHANGELOG.md
│   ├── SOURCE_SHA256
│   ├── email_composer.py
│   ├── test_email_composer.py
│   └── unpack_source.py
└── ragflow/
    ├── README.md
    ├── CHANGELOG.md
    └── ragflow.py
```

## Requirements

A public Tool should:

- use a top-level `Tools` class expected by Open WebUI;
- keep credentials in Valves, not in source code;
- avoid deployment-specific hostnames, branding, and private infrastructure details;
- document required Python packages;
- provide clear error handling and timeouts for external requests;
- explain what data leaves the Open WebUI server;
- include simple test prompts in its README;
- keep a `CHANGELOG.md` beside the implementation.

## Installation

Import the Python file through **Workspace > Tools**, review the code, configure its Valves, then enable the Tool for the intended model or chat.

Each Tool directory contains its own setup and configuration guide.

## Security

Workspace Tools execute Python inside the Open WebUI server process. Only install reviewed code from trusted sources.
