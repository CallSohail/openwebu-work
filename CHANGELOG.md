# Changelog

This repository contains independently versioned Open WebUI extensions. Plugin-specific release notes live next to each implementation when they need more detail.

## 2026-09-25

### Secure Dynamic Onboarding 9.3.0

Added automatic synchronization of existing onboarding guides whenever an administrator saves Valves, so section visibility, branding, language controls, and guide content reach users after they refresh or reopen the chat. Replaced the language buttons with a compact dropdown that lists every embedded locale by name. Added mojibake repair and an ASCII-safe Rich UI transport to preserve French, Spanish, Catalan, punctuation, and symbols across Open WebUI message storage and iframe rendering.

## 2026-09-22

### Secure Dynamic Onboarding 9.2.1

Added complete Spanish and Catalan onboarding catalogs contributed by [delfireinoso](https://github.com/delfireinoso) through issues [#11](https://github.com/CallSohail/openwebu-work/issues/11) and [#12](https://github.com/CallSohail/openwebu-work/issues/12). The guide now selects `es` and `ca` automatically from the matching Open WebUI interface locale and exposes both languages in its selector. The submitted catalogs were validated and received small mechanical repairs before embedding.

## 2026-09-21

### Secure Dynamic Onboarding 9.2.0

Added contributor-ready JSON locale catalogs, a documented translation workflow, strict catalog validation, automatic exact/base matching with the Open WebUI interface language, and dynamic language-selector buttons. English and French remain built in, unsupported interface locales use the administrator's configured fallback language, and additional catalogs are compiled into the portable single-file Event Function. Regular-user output now also removes administrator-only tutorial source and administrator-only translations on the server before the Rich UI payload is returned.

## 2026-09-18

### Documentation and release metadata

Brought every extension up to the same documentation and release standard.

- added the missing changelogs for the RAGFlow Advanced Connector and Secure Dynamic Onboarding, so all five extensions now keep one beside the implementation;
- rewrote the existing changelogs onto the section names from the release-notes template (`Added`, `Changed`, `Fixed`, `Security`, `Compatibility`, `Upgrade notes`) under dated `## [version]` headings;
- expanded the RAGFlow README with its Tool methods, a complete Valve reference with defaults and ranges, the data flow, and known limitations;
- added a Changelog section to each extension README, and a changelog column or link to the Tool, Filter, Action and Event category indexes;
- corrected the Quick Actions version in the Action index from 3.0.0 to 3.0.1;
- added a per-extension changelog table to the root README;
- required a `CHANGELOG.md` beside every implementation in CONTRIBUTING.md, with the section names it should use;
- replaced the stale tag list in RELEASING.md with a published-release table, annotated-tag instructions, and the rule that a tag points at the commit where that version's source last changed.

### Secure Dynamic Onboarding 9.1.0

Added a bilingual, role-aware Event Function that creates and maintains one persistent onboarding chat per eligible user. The release includes an interactive guided tour, a permission-filtered feature library, exact composer and navigation tutorials, Notes, Folders, Channels, Calendar and Automations guidance, safe test-user rollout, signup and first-login delivery, idempotent batch deployment, in-place guide revisions, deleted-guide handling, Redis locking, restrictive iframe CSP, disclosure controls, Python tests, and a full DOM smoke test.

## 2026-09-11

### Rich Email Composer 1.0.0

Added a portable Rich UI email drafting Tool with direct recipient, subject, and body editing, AI-assisted revision prompts, contextual formatting controls, recipient and domain validation, light/dark/mobile support, rich/plain copy, mail-client handoff, multipart `.eml` export, large-mailto fallback, message-level embed rendering, tests, CI, and deployment-neutral styling. The public Tool contains no university-specific names, domains, logos, or color theme and does not hard-code an Open WebUI `0.11.x` version requirement.

## 2026-08-28

### Study Mode 1.1.0

Community-driven Study Mode update focused on compatibility and quiz usability. Added multilingual quiz-intent detection, a bounded Compatible parser for common local-model structured-output variations, optional Strict mode, scoped system-prompt integration, optional MathJax LaTeX rendering, keyboard controls, fullscreen, client-side HTML export, stronger stream/status cleanup, U+2028/U+2029 script escaping, and dedicated regression/JavaScript CI coverage.

### Quick Actions 3.0.1

Fixed the toolbar icon becoming difficult to see in dark/OLED themes by replacing the monochrome external icon with a theme-independent hosted Quick Actions icon.

### Quick Actions 3.0.0

Added the first public Action Function to the repository: a compact context-aware Quick Actions menu for assistant responses. The release includes response rewriting and creation workflows, verification and follow-up actions, code/data/study helpers, a dedicated Humanize section, English/French built-in UI and prompts, admin Team actions, per-user My actions, custom `{input}` actions, safe composer preview/send modes, draft protection, older-message targeting, mobile/desktop layouts, and light/dark accessibility handling.

## 2026-08-26

### Repository structure

- organized extensions into `tools/` and `functions/`;
- added dedicated Function directories for Filters, Pipes, Actions, and Events;
- added category documentation and installation guidance;
- added repository security, contribution, and licensing files;
- moved the RAGFlow connector into `tools/ragflow/`;
- published Study Mode as the public `1.0.0` release line.

### Study Mode 1.0.0

Initial public release with adaptive tutoring, guided and Socratic learning, explain-then-practice mode, native `ask_user` integration when available, interactive quiz Rich UI, quiz transport suppression, randomized answer positions, progress status, hints, copy controls, scoring, and accessible perfect-score feedback.
