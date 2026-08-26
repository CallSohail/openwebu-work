# Study Mode

**Type:** Filter Function  
**Version:** 1.0.0  
**Minimum Open WebUI:** 0.11.1  
**Tags:** `filter`, `study-mode`, `education`, `tutoring`, `ask-user`, `rich-ui`, `quiz`

Study Mode turns an existing Open WebUI model into a more interactive tutor without replacing the underlying model. It is designed to work with ordinary text models and to take advantage of native Open WebUI capabilities when the selected model supports them.

## What it provides

- adaptive teaching based on the learner's request and apparent level;
- Guided, Socratic, Explain-then-Practice, Quiz, and Adaptive teaching styles;
- Beginner, Intermediate, Advanced, and automatic learner levels;
- adaptive pacing and answer-reveal policies;
- progressive hints and misconception correction;
- understanding checks and practice prompts;
- grounding instructions for attached course materials;
- optional use of Open WebUI's native `ask_user` built-in;
- interactive multiple-choice quiz cards rendered with Rich UI;
- hidden quiz transport so machine JSON is not shown in chat;
- randomized answer positions while keeping scoring correct;
- per-question hints, navigation, answer feedback, and quiz copy controls;
- progress feedback while the quiz is being prepared;
- final score, mistake review, follow-up study, and new-quiz actions;
- a brief accessible perfect-score celebration;
- model-independent fallback behavior when native tools are unavailable.

## Why it is a Filter

Study Mode is meant to work with models you already use. A Filter can be attached to those models and toggled per chat, rather than creating a separate model entry for every provider.

```text
Existing model + Study Mode Filter = tutoring behavior
```

## Installation

1. Open Open WebUI as an administrator.
2. Go to **Admin Panel > Functions**.
3. Create or import a Function.
4. Paste the contents of `study_mode.py`.
5. Save and enable the Function.
6. Attach the Filter to the models where you want Study Mode available.
7. Optionally make it a default Filter for selected models.

## Recommended model configuration

Study Mode itself does not require tool calling. For the richer clarification flow, configure the model with:

```text
Function Calling: Native
Builtin Tools: On
Ask User: On
```

If `ask_user` is unavailable or the model cannot call tools reliably, Study Mode falls back to concise normal-text clarification instead of failing the session.

## Main user settings

The Filter exposes UserValves for settings such as:

- teaching style;
- learner level;
- pace;
- answer policy;
- understanding checks;
- analogy use;
- course-material grounding;
- one-question-at-a-time behavior;
- quiz setup, count, and difficulty.

Administrators also have Valves for quiz UI behavior, status messages, maximum quiz size, option randomization, hint and copy controls, and the perfect-score celebration.

## Interactive quiz flow

A typical quiz session looks like this:

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
Model generates a validated quiz specification
        |
        v
Filter suppresses machine transport
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

The quiz renderer independently randomizes option order on load and remaps the correct answer, so the model does not need to manage answer positions.

## Suggested tests

### Adaptive tutoring

```text
Teach me MQTT.
```

### Socratic problem solving

Set the style to Socratic, then ask:

```text
Help me solve x^2 - 5x + 6 = 0.
```

### Explain then practice

```text
Teach me Python inheritance.
```

### Interactive quiz

```text
Quiz me on Python OOP.
```

### Course material

Attach a study document, then ask:

```text
Study this material with me.
```

## Compatibility notes

The core tutoring behavior is prompt-driven and works across text-capable models. Exact instruction-following quality still depends on the selected model.

Native `ask_user` requires a model that can perform OpenAI-style native function calls correctly. Study Mode treats that feature as an enhancement, not a hard dependency.

The interactive quiz renderer depends on the current Open WebUI Rich UI and Filter event APIs and therefore declares Open WebUI 0.11.1 as its minimum supported version.

## Privacy and deployment neutrality

This public version contains no deployment-specific institutional names, private endpoints, internal email addresses, API keys, or private infrastructure details. All environment-specific configuration should remain in Open WebUI Valves or deployment configuration.

## Security

Functions execute Python inside the Open WebUI server process. Review the code before importing it and restrict Function management to trusted administrators.

The quiz renderer validates model-produced quiz data and renders it through a controlled template rather than treating arbitrary model output as trusted HTML.

## Versioning

This public repository starts Study Mode at **1.0.0**. Future releases should follow semantic versioning.

Recommended Git tag:

```text
study-mode-v1.0.0
```
