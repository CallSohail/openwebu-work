# Functions

Functions extend Open WebUI itself. Open WebUI currently supports four main Function primitives: Filter, Pipe, Action, and Event.

```text
functions/
├── filters/
├── pipes/
├── actions/
└── events/
```

Each plugin belongs in the directory matching the top-level class Open WebUI detects.

| Directory | Class | Purpose |
| --- | --- | --- |
| `filters/` | `Filter` | Intercept or transform requests, streams, and completed responses |
| `pipes/` | `Pipe` | Add custom models, providers, routers, or agent workflows |
| `actions/` | `Action` | Add user-triggered message actions |
| `events/` | `Event` | React to system-level Open WebUI events |

Import Functions from **Admin Panel > Functions** and review them before enabling.
