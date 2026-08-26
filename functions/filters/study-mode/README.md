# Study Mode

**Type:** Filter Function  
**Version:** 1.0.0  
**Minimum Open WebUI:** 0.11.1  
**Tags:** `filter`, `study-mode`, `education`, `tutoring`, `ask-user`, `rich-ui`, `quiz`

Study Mode turns an existing Open WebUI model into an interactive tutor without replacing the underlying model. The core tutoring behavior works with ordinary text-capable models, while models with reliable Native tool calling can also use Open WebUI's built-in `ask_user` experience.

## Features

- Adaptive, Guided, Socratic, Explain-then-Practice, and Quiz teaching styles
- Beginner, Intermediate, Advanced, and automatic learner levels
- Adaptive, slow, normal, and fast pacing
- Adaptive, guide-first, hints-first, and direct-answer policies
- Progressive hints and misconception correction
- Understanding checks and practice prompts
- Grounding instructions for attached course materials
- Optional native `ask_user` clarification
- Interactive multiple-choice quiz cards using Rich UI
- Hidden quiz transport so machine JSON is not exposed in the chat
- Randomized answer positions with correct-answer remapping
- Per-question hints and immediate answer feedback
- Previous and next navigation
- Copy-quiz control
- Native progress feedback while a quiz is prepared
- Final scoring, mistake review, follow-up study, and new-quiz actions
- Brief, accessible perfect-score celebration
- Graceful fallback when native tools or Rich UI generation fail

## Why it is a Filter

Study Mode is intended to work with models already configured in Open WebUI.

```text
Existing model + Study Mode Filter = tutoring behavior
```

That means the same Filter can be attached to different local or remote models without creating a separate model entry for each one.

## Installation

1. Open Open WebUI as an administrator.
2. Go to **Admin Panel > Functions**.
3. Create or import a Function.
4. Paste the contents of `study_mode.py`.
5. Save and enable the Function.
6. Attach the Filter to the models where Study Mode should be available.
7. Optionally configure it as a default Filter for selected models.

## Recommended model configuration

Study Mode itself does not require tool calling. For the richer clarification flow, configure compatible models with:

```text
Function Calling: Native
Builtin Tools: On
Ask User: On
```

If `ask_user` is unavailable or the model cannot call tools reliably, the Filter instructs the model to use a concise normal-text clarification instead of failing the learning session.

## User configuration

Study Mode exposes `UserValves` so the learning experience can be adjusted without editing the Filter.

| Setting | Purpose |
| --- | --- |
| Teaching style | Adaptive, Guided, Socratic, Explain then Practice, or Quiz |
| Learner level | Auto, Beginner, Intermediate, or Advanced |
| Pace | Adaptive, Slow, Normal, or Fast |
| Answer policy | Adaptive, Guide me first, Hints first, or Direct answers allowed |
| Prefer Ask User | Prefer Open WebUI's native `ask_user` when an important clarification is needed |
| Check understanding | Add short comprehension checks at useful points |
| Use analogies | Use analogies when they genuinely clarify a concept |
| Use course materials | Prioritize attached or retrieved learning material when relevant |
| Personalize with Memory | Allow relevant existing learning memories to inform tutoring when Memory tools are available |
| One question at a time | Keep interactive learning focused on a single learner-facing question |
| Quiz setup | Ask for missing quiz settings or use configured defaults |
| Default quiz count | Number of questions used when the learner does not provide one |
| Default quiz difficulty | Adaptive, Easy, Medium, or Hard |

## Administrator configuration

Administrator `Valves` control behavior that should remain consistent across users.

Important controls include:

- Rich UI quiz rendering
- maximum accepted quiz size
- hidden quiz transport
- quiz-card-only rendering
- per-question Hint control
- Copy quiz control
- answer option randomization
- quiz preparation status
- preparation and completion status text
- perfect-score celebration
- course-material grounding
- general Study Mode status updates

## Interactive quiz flow

A typical multiple-choice quiz session looks like this:

```text
User requests a quiz
        |
        v
Collect missing preferences with ask_user when available
        |
        v
Show "Preparing your quiz..."
        |
        v
Model produces a structured quiz specification
        |
        v
Filter suppresses machine transport
        |
        v
Validate quiz data
        |
        +---- invalid ----> Restore a readable model response
        |
      valid
        |
        v
Persistent Rich UI quiz card
        |
        v
Hints, answer feedback, navigation, scoring
        |
        v
Review mistakes / continue studying / new quiz
```

The Rich UI renderer randomizes option order independently for every question and remaps the correct answer after shuffling. This avoids predictable answer positions without relying on the model to randomize them correctly.

## Example tests

### Adaptive tutoring

```text
Teach me MQTT.
```

Expected behavior: explain at an appropriate level, use examples where useful, then check understanding when that adds value.

### Socratic problem solving

Set the teaching style to Socratic, then ask:

```text
Help me solve x^2 - 5x + 6 = 0.
```

Expected behavior: guide the learner toward the factorization rather than immediately dumping the final roots.

### Explain then Practice

```text
Teach me Python inheritance.
```

Expected behavior: concept, small example, practice, then feedback.

### Interactive quiz

```text
Quiz me on Python OOP.
```

Expected behavior: collect missing quiz preferences when useful, show the preparation status, then render the interactive quiz card.

### Course material

Attach a study document, then ask:

```text
Study this material with me.
```

Expected behavior: treat the supplied material as the primary source and organize it into a learning path instead of automatically producing a long generic summary.

## Compatibility

The core tutoring behavior is prompt-driven and works across text-capable models. Exact instruction-following quality still depends on the selected model.

Native `ask_user` requires a model that can produce correct native function calls. Study Mode treats `ask_user` as an enhancement, not a hard dependency.

The interactive quiz renderer depends on the current Open WebUI Filter event and Rich UI APIs, so this release declares Open WebUI **0.11.1** as its minimum supported version.

## Privacy

This public implementation contains no deployment-specific institutional names, private endpoints, internal email addresses, API keys, or private infrastructure details.

The Filter does not automatically create or modify user memories. When Memory-based personalization is enabled, the prompt allows relevant existing learning context to be used, while memory writes remain an explicit user-controlled action.

## Security

Open WebUI Functions execute Python inside the Open WebUI server process. Review the code before importing it and restrict Function management to trusted administrators.

The quiz renderer validates model-produced quiz data and renders it through a controlled template rather than treating arbitrary model output as trusted HTML.

## Known limitations

- Tutoring quality depends on the instruction-following ability of the selected model.
- `ask_user` cannot be guaranteed on models that do not support reliable Native tool calling.
- Rich UI quiz generation relies on the model producing a valid structured quiz payload. Invalid payloads fall back to a readable response rather than rendering a broken card.
- Quiz state lives in the rendered interaction. It is not intended to be a permanent learning analytics database.
- Automatic long-term mastery tracking is not included in version 1.0.0.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for Study Mode release notes.

## Versioning

This public release line starts at **1.0.0** and follows semantic versioning.

Recommended Git tag:

```text
study-mode-v1.0.0
```

## License

Released under the repository's [MIT License](../../../LICENSE).
