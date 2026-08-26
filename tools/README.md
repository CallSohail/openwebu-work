# Tools

Tools give a model capabilities it can call during a conversation, such as querying an API, retrieving data, or performing a specialized operation.

## Structure

Each Tool should live in its own directory:

```text
tools/
└── tool-name/
    ├── README.md
    └── tool_name.py
```

## Requirements

A public Tool should:

- use a top-level `Tools` class expected by Open WebUI;
- keep credentials in Valves, not in source code;
- avoid deployment-specific hostnames and private infrastructure details;
- document required Python packages;
- provide clear error handling and timeouts for external requests;
- explain what data leaves the Open WebUI server;
- include simple test prompts in its README.

## Installation

Import the Python file through **Workspace > Tools**, review the code, configure its Valves, then enable the Tool for the intended model or chat.

## Security

Workspace Tools execute Python inside the Open WebUI server process. Only install reviewed code from trusted sources.
