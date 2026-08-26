# Pipe Functions

Pipes expose custom logic as a selectable model in Open WebUI. Use a Pipe when the extension needs to own the complete request flow rather than modify an existing model.

Typical uses include:

- custom model providers;
- model routers and fallbacks;
- multi-step research or agent workflows;
- non-LLM interfaces that still appear as a model;
- custom inference gateways.

## Directory convention

```text
pipes/
└── pipe-name/
    ├── README.md
    └── pipe_name.py
```

No Pipe implementations are included yet.
