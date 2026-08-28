# Action Functions

Actions add user-triggered controls to chat messages. They are useful when an operation should run only after an explicit click rather than automatically on every model request.

Typical uses include:

- export a response;
- verify an answer;
- send content to another service;
- generate a document from a message;
- trigger a workflow;
- apply a message-specific transformation.

## Available Actions

| Action | Version | Status | Purpose |
| --- | --- | --- | --- |
| [Quick Actions](quick-actions/) | 3.0.0 | Stable | Compact context-aware response transformations, Humanize actions, verification, creation workflows, English/French UI, and user/team custom actions |

## Directory convention

```text
actions/
└── action-name/
    ├── README.md
    ├── CHANGELOG.md
    └── action_name.py
```

Action implementations should keep user-triggered behavior explicit, document any browser-side `execute` usage, and avoid hidden external calls that are not clear from the source and README.
