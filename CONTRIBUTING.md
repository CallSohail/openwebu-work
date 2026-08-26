# Contributing

Contributions are welcome when they keep each Open WebUI extension focused, portable, and easy to review.

## Repository convention

Use the directory that matches the extension type:

```text
tools/<tool-name>/
functions/filters/<filter-name>/
functions/pipes/<pipe-name>/
functions/actions/<action-name>/
functions/events/<event-name>/
```

Each implementation should include at least:

```text
README.md
<plugin_name>.py
```

## Before opening a pull request

Check that the extension:

- has clear Open WebUI metadata at the top of the Python file;
- declares a semantic version;
- documents the minimum Open WebUI version when relevant;
- avoids hard-coded API keys, passwords, tokens, private endpoints, and personal data;
- uses Valves or environment-backed configuration for deployment-specific values;
- handles network errors and timeouts when calling external services;
- has safe defaults;
- explains permissions and data flow in its README;
- includes a few realistic test prompts or test steps;
- does not depend on one specific model unless that limitation is documented;
- keeps user-visible error messages clear and actionable.

## Documentation style

A plugin README should cover:

1. What the extension does
2. Extension type
3. Version and compatibility
4. Features
5. Installation
6. Configuration
7. Example usage
8. Permissions and security
9. Known limitations
10. Versioning or release notes

## Versioning

Use semantic versioning:

```text
MAJOR.MINOR.PATCH
```

Examples:

- `1.0.1` for a compatible bug fix
- `1.1.0` for a compatible feature
- `2.0.0` for a breaking change

Recommended Git tag format:

```text
<plugin-name>-v<version>
```

## Security

Never include credentials or private deployment information in a pull request. See [SECURITY.md](SECURITY.md) for the repository policy.
