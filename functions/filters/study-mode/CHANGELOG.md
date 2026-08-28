# Study Mode Changelog

## 1.1.0, 2026-08-28

Community-driven compatibility and quiz UX release.

### Model and prompt compatibility

- Added a `Compatible` quiz parser for common local/smaller-model structured-output variations while preserving validation and safety limits.
- Added optional `Strict` schema mode for models that reliably follow the documented JSON contract.
- Hardened the quiz-generation prompt to prioritize a complete valid schema on smaller models.
- Added explicit Study Mode scoping so quiz transport instructions apply only to interactive quiz-generation turns.
- Added administrator-selectable system-prompt integration: merge with an existing system message by default, or use a separate scoped system message.
- Preserved existing system/Modelfile instructions instead of replacing them.

### Multilingual routing

- Expanded automatic quiz-intent detection beyond English with common French, Spanish, German, Italian, Portuguese, Dutch, Urdu, and Arabic phrases.
- Quiz teaching style continues to force quiz behavior without relying on keyword detection.

### Quiz UI

- Added optional pinned MathJax 3.2.2 rendering for LaTeX expressions; disabled by default and gracefully degraded when CSP/network policy blocks it.
- Added keyboard shortcuts: A-E/1-5 answer, Left/Right navigation, Enter continue/finish, H hint, F fullscreen.
- Added fullscreen control when allowed by browser/iframe policy.
- Added client-side standalone HTML export using a browser Blob.
- Replaced dynamic node-clearing `innerHTML` calls with `replaceChildren()`.

### Reliability and security

- Added U+2028/U+2029 escaping to script-embedded quiz JSON in addition to `<`, `>`, and `&` escaping.
- Improved request/stream key persistence and fallback identifiers for quiz buffering.
- Ensured quiz progress status cleanup runs from the outlet `finally` path.
- Added regression tests covering multilingual routing, compatible/strict parsing, malformed LaTeX JSON, system prompt integration, script escaping, keyboard/fullscreen/export hooks, and status cleanup.
- Added dedicated GitHub Actions validation for Study Mode Python and generated browser JavaScript.

## 1.0.0, 2026-08-26

Initial public release.

### Tutoring

- Adaptive, Guided, Socratic, Explain-then-Practice, and Quiz teaching styles
- Beginner, Intermediate, Advanced, and automatic learner levels
- Adaptive pacing and configurable answer-reveal behavior
- Progressive hints, worked examples, misconception correction, and understanding checks
- Course-material grounding instructions
- Optional Memory-aware personalization without automatic memory writes

### Open WebUI integration

- Toggleable Filter architecture for use with existing models
- Native `ask_user` support when available
- Graceful text fallback when native tools are unavailable
- Per-user UserValves and administrator Valves
- Request-local metadata for observability

### Interactive quiz UI

- Persistent Rich UI quiz cards
- Hidden machine transport during generation
- Quiz JSON validation and fallback behavior
- Randomized answer positions with correct-answer remapping
- Per-question hints
- Previous and next navigation
- Immediate answer feedback and explanations
- Copy-quiz control
- Final score and mistake review
- Continue-studying and new-quiz actions
- Native progress status while a quiz is being prepared
- One-time accessible perfect-score celebration
- Dark-mode and reduced-motion support
