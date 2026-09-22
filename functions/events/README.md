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

## Included Event Functions

### Secure Dynamic Onboarding 9.2.1

Creates one persistent, multilingual onboarding chat per eligible user. The Rich UI guide follows the Open WebUI interface language when a matching locale is embedded, while keeping an in-guide language selector and an administrator-configured fallback locale. Its content is filtered by effective permissions, accessible Tools and models, global feature flags, role, and administrator Valves. It supports contributor-friendly JSON locale catalogs, controlled testing, signup and first-login delivery, in-place content updates, idempotent batch deployment, Redis locking, and user-respected deletion behavior.

See [secure-onboarding/README.md](secure-onboarding/README.md) for installation, rollout, privacy, security, Valves, testing, and troubleshooting, and [secure-onboarding/CHANGELOG.md](secure-onboarding/CHANGELOG.md) for the release history.
