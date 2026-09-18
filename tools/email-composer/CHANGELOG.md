# Rich Email Composer Changelog

All notable changes to Rich Email Composer are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `email-composer-v<version>`. See [RELEASING.md](../../RELEASING.md) for the release process.

## [1.0.0] - 2026-09-11

Initial public release.

### Added

- Interactive Rich UI email drafting inside Open WebUI chats.
- Direct editing for recipients, subject, and body.
- AI-assisted revision requests using the current edited draft.
- Contextual body formatting with headings, lists, checklists, inline formatting, code, and emoji.
- Recipient validation, duplicate protection, field limits, and optional domain allow/block policies.
- Light and dark themes with responsive mobile behavior.
- Rich and plain clipboard copy, mail-client handoff, and multipart `.eml` export.
- Long-mailto fallback behavior.
- Message-level embed rendering with an inline `HTMLResponse` fallback.
- Regression tests and a dedicated GitHub Actions validation workflow.
- `unpack_source.py` and `SOURCE_SHA256` for inspecting and verifying the packaged source.

### Changed

- Removed deployment-specific branding, domains, and institutional styling from the public version.

### Security

- Input sanitization, Base64 payload transport, header cleaning, and a no-external-assets policy.

### Compatibility

- Function type: Tool (`Tools` class).
- No hard-coded Open WebUI `0.11.x` requirement in the public Tool metadata; the Tool relies on the Rich UI embed and event capabilities described in the [README](README.md).
