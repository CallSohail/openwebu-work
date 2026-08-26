# Action Functions

Actions add user-triggered controls to chat messages. They are useful when an operation should run only after an explicit click rather than automatically on every model request.

Typical uses include:

- export a response;
- verify an answer;
- send content to another service;
- generate a document from a message;
- trigger a workflow;
- apply a message-specific transformation.

## Directory convention

```text
actions/
└── action-name/
    ├── README.md
    └── action_name.py
```

No Action implementations are included yet.
