# Changelog

All notable changes to the Secure Dynamic Onboarding Event Function.
This project follows [Semantic Versioning](https://semver.org/).

## [9.1.0]

### Fixed
- Redis commands reconnect and retry once when an idle socket is dropped, instead of falling back to process-local locking on the first `BrokenPipeError`.
- Connections are opened with `health_check_interval=30` when the running Open WebUI build supports it.
- A dropped connection is logged as a single INFO line instead of a full traceback.

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
