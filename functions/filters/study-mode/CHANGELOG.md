# Study Mode Changelog

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
