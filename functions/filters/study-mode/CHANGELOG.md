# Study Mode Changelog

All notable changes to Study Mode are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `study-mode-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.1.0] - 2026-08-28

Community-driven compatibility and quiz UX release.

### Added

- A `Compatible` quiz parser for common local and smaller-model structured-output variations, preserving validation and safety limits.
- An optional `Strict` schema mode for models that reliably follow the documented JSON contract.
- Administrator-selectable system-prompt integration: merge with an existing system message by default, or use a separate scoped system message.
- Automatic quiz-intent detection beyond English, with common French, Spanish, German, Italian, Portuguese, Dutch, Urdu, and Arabic phrases.
- Optional pinned MathJax 3.2.2 rendering for LaTeX expressions, disabled by default and degrading gracefully when CSP or network policy blocks it.
- Keyboard shortcuts: A-E and 1-5 to answer, Left and Right to navigate, Enter to continue or finish, H for a hint, F for fullscreen.
- A fullscreen control, when browser and iframe policy allow it.
- Client-side standalone HTML export using a browser Blob.
- Regression tests covering multilingual routing, compatible and strict parsing, malformed LaTeX JSON, system-prompt integration, script escaping, the keyboard, fullscreen and export hooks, and status cleanup.
- A dedicated GitHub Actions workflow validating the Study Mode Python and its generated browser JavaScript.

### Changed

- Hardened the quiz-generation prompt to prioritize a complete valid schema on smaller models.
- Scoped Study Mode so quiz transport instructions apply only to interactive quiz-generation turns.
- Preserved existing system and Modelfile instructions instead of replacing them.
- Quiz teaching style continues to force quiz behavior without relying on keyword detection.
- Replaced dynamic node-clearing `innerHTML` calls with `replaceChildren()`.

### Fixed

- Request and stream key persistence, with fallback identifiers for quiz buffering.
- Quiz progress status cleanup now runs from the outlet `finally` path.

### Security

- Added U+2028 and U+2029 escaping to script-embedded quiz JSON, in addition to `<`, `>`, and `&` escaping.

## [1.0.0] - 2026-08-26

Initial public release.

### Added

**Tutoring**

- Adaptive, Guided, Socratic, Explain-then-Practice, and Quiz teaching styles.
- Beginner, Intermediate, Advanced, and automatic learner levels.
- Adaptive pacing and configurable answer-reveal behavior.
- Progressive hints, worked examples, misconception correction, and understanding checks.
- Course-material grounding instructions.
- Optional memory-aware personalization, without automatic memory writes.

**Open WebUI integration**

- Toggleable Filter architecture for use with existing models.
- Native `ask_user` support when available, with a graceful text fallback when native tools are not.
- Per-user UserValves and administrator Valves.
- Request-local metadata for observability.

**Interactive quiz UI**

- Persistent Rich UI quiz cards, with hidden machine transport during generation.
- Quiz JSON validation and fallback behavior.
- Randomized answer positions with correct-answer remapping.
- Per-question hints, previous and next navigation, immediate feedback and explanations.
- Copy-quiz control, final score, and mistake review.
- Continue-studying and new-quiz actions.
- Native progress status while a quiz is being prepared.
- A one-time accessible perfect-score celebration.
- Dark-mode and reduced-motion support.

### Compatibility

- Open WebUI 0.11.1 or newer.
- Function type: Filter (`Filter` class).
