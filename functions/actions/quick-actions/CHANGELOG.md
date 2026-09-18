# Quick Actions Changelog

All notable changes to Quick Actions are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `quick-actions-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [3.0.1] - 2026-08-28

### Fixed

- The toolbar icon becoming difficult to see in Open WebUI dark and OLED themes.

### Changed

- Replaced the monochrome external icon with a theme-independent hosted Quick Actions icon that stays visible in light and dark mode.

## [3.0.0] - 2026-08-28

First public Action Function in this repository.

### Added

- Humanize section with five writing transformations.
- Complete English and French built-in UI and prompt catalogs.
- Admin language default and per-user language override.
- Action-specific inline SVG icons.
- Named custom inputs using `{input:Label}`.

### Changed

- Replaced many admin section toggles with one native multiselect.
- Replaced the user code, data, and study toggles with a native hidden-sections multiselect.
- Reduced menu density and moved Explore into More.
- Improved light, dark, system, high-contrast, and reduced-motion behavior.
- Changed the compact-menu failure fallback to look like an intentional custom instruction.

### Fixed

- Intentional menu cancel no longer opens the fallback input dialog.

### Compatibility

- Open WebUI 0.11.1 or newer.
- Function type: Action (`Action` class).
- No extra Python packages, and no direct LLM or API call from the Action itself.

### Upgrade notes

- Safe preview mode, draft protection, concurrency cancellation, timeout cleanup, and older-message targeting are preserved from earlier releases.
