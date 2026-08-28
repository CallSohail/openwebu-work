# Changelog

This repository contains independently versioned Open WebUI extensions. Plugin-specific release notes live next to each implementation when they need more detail.

## 2026-08-28

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
