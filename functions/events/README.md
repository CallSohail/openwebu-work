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
└── event-name/
    ├── README.md
    └── event_name.py
```

No Event implementations are included yet.
