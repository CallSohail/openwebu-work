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
├── README.md
└── secure-onboarding/
    ├── README.md
    ├── CHANGELOG.md
    ├── secure_onboarding.py
    ├── smoke_secure_onboarding.mjs
    └── test_secure_onboarding.py
```

## Included Event Functions

### Secure Dynamic Onboarding 9.1.0

Creates one persistent, bilingual onboarding chat per eligible user. The Rich UI guide is filtered by effective permissions, accessible Tools and models, global feature flags, role, and administrator Valves. It supports controlled testing, signup and first-login delivery, in-place content updates, idempotent batch deployment, Redis locking, and user-respected deletion behavior.

See [secure-onboarding/README.md](secure-onboarding/README.md) for installation, rollout, privacy, security, Valves, testing, and troubleshooting.
