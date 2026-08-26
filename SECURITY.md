# Security Policy

Open WebUI Tools and Functions execute Python on the host running Open WebUI. Treat every extension in this repository as server-side code that must be reviewed before installation.

## Repository rules

Do not commit:

- API keys, passwords, tokens, or cookies;
- private hostnames, internal IP addresses, or internal-only URLs unless they are clearly generic examples;
- personal or institutional email addresses;
- production credentials or exported configuration containing secrets;
- user data or private documents.

Use Valves or deployment-level secret management for environment-specific configuration.

## Reporting a security issue

Do not publish credentials or exploit details in a public issue. Contact the repository owner privately through an appropriate GitHub contact channel when sensitive disclosure is required.
