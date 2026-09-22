# Secure Dynamic Onboarding Changelog

All notable changes to the Secure Dynamic Onboarding Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `secure-onboarding-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [9.2.1] - 2026-09-22

### Added

- Complete Spanish and Catalan onboarding catalogs contributed by [delfireinoso](https://github.com/delfireinoso) through issues [#11](https://github.com/CallSohail/openwebu-work/issues/11) and [#12](https://github.com/CallSohail/openwebu-work/issues/12).
- Automatic `es` and `ca` selection from the matching Open WebUI interface locale, plus ES and CA buttons in the in-guide language selector.

### Fixed

- Repaired the submitted JSON delimiter, catalog IDs, Catalan locale code, and a small set of obvious translation typos before embedding the catalogs.

### Compatibility

- Open WebUI 0.11.3 or newer.
- Existing English and French behavior is unchanged.

## [9.2.0] - 2026-09-21

### Added

- Contributor-ready English and French JSON catalogs with stable message IDs.
- A deterministic `sync_locales.py` workflow that validates catalogs and embeds additional languages into the self-contained Open WebUI Function.
- Dynamic language buttons and automatic exact/base locale matching from `user.settings.ui.language`.
- CI checks for stale, incomplete, malformed, or incorrectly named locale catalogs.
- Tests that compare regular-user and administrator rendering boundaries.

### Security

- Administrator-only tutorial definitions are removed from regular-user HTML before delivery instead of being hidden only by client-side conditions.
- Administrator-only translated strings are excluded from regular-user localization payloads.

### Changed

- The guide is now prepared for community languages without requiring translators to edit application logic.
- Template revision increased to 10 so existing guides refresh in place.

## [9.1.0] - 2026-09-18

First version published in this repository.

### Fixed

- Redis commands reconnect and retry once when an idle socket is dropped, instead of falling back to process-local locking on the first `BrokenPipeError`.
- A dropped connection is logged as a single INFO line instead of a full traceback.

### Changed

- Connections are opened with `health_check_interval=30` when the running Open WebUI build supports it.

### Compatibility

- Open WebUI 0.11.3 or newer.
- Function type: Event (`Event` class).

## [9.0.0]

### Added

- Delivery controls: `create_on_signup`, `create_on_first_login`, `skip_pending_users`, `include_admins`, `deploy_to_all_users`, `deployment_revision`, `recreate_deleted_guides`.
- Background deployment in batches, resumable and idempotent per revision.
- Content versioning with `guide_revision`, in-place updates, and a one-time "guide updated" banner with optional release notes.
- One on/off valve per section (33 switches), plus the Features tab, language switch and example buttons.
- Welcome chat options: emoji, title templates, pinning, per-user interface language.

### Changed

- All valves and their descriptions are in English.
- Features are resolved server-side from the instance switch AND the user permission; an unreadable permission source hides the feature instead of allowing it.

## [8.0.0]

### Added

- Cover page with key facts and a clickable table of contents.
- Chapters for the user menu, Calendar, Automations, built-in tools and administration.

### Changed

- Font set to Arial with a system fallback.

## [7.0.0]

### Added

- Interactive mocks of the real chat screens: + menu with sub-lists, Integrations, model selector, response actions, feedback forms, sidebar, Notes, folders and channels.
- "Features" tab listing every capability available to the account, each with a pop-up tutorial.

---

Releases 7.0.0 through 9.0.0 predate this repository, so they carry no publication date.
