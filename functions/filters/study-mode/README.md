# Study Mode

**Type:** Filter Function  
**Version:** 1.1.0  
**Minimum Open WebUI:** 0.11.1  
**Author:** Muhammad Sohail  
**Tags:** `filter`, `study-mode`, `education`, `tutoring`, `ask-user`, `rich-ui`, `quiz`

Study Mode turns an existing Open WebUI model into an interactive tutor without replacing the underlying model. Core tutoring is prompt-driven and works with ordinary text-capable models. Models with reliable Native tool calling can additionally use Open WebUI's built-in `ask_user` experience.

## Highlights

- Adaptive, Guided, Socratic, Explain-then-Practice, and Quiz teaching styles
- Beginner, Intermediate, Advanced, and automatic learner levels
- Configurable pacing and answer-reveal behavior
- Progressive hints, misconception correction, examples, and understanding checks
- Grounding instructions for attached course material
- Optional native `ask_user` clarification with normal-text fallback
- Interactive multiple-choice Rich UI with hidden machine transport
- Randomized answer positions with correct-answer remapping
- Per-question hints, corrections, explanations, scoring, and mistake review
- Multilingual quiz-intent detection for common English, French, Spanish, German, Italian, Portuguese, Dutch, Urdu, and Arabic requests
- More tolerant quiz parsing for smaller/local models, with optional Strict mode
- Optional MathJax 3.2.2 rendering for LaTeX expressions
- Keyboard shortcuts for answering and navigation
- Fullscreen quiz control
- Standalone HTML export from the quiz iframe
- Scoped system-prompt integration designed to coexist with existing model prompts
- Stream/status cleanup hardening and script-safe JSON escaping

## Installation

1. Open **Admin Panel > Functions** in Open WebUI.
2. Create or import a Function.
3. Paste the contents of `study_mode.py`.
4. Save and enable the Function.
5. Attach the Filter to the models where Study Mode should be available.
6. Open the administrator Valves and choose the quiz/UI behavior you want.
7. Users can adjust their learning preferences through User Valves.

## Recommended model configuration

Study Mode does **not** require tool calling for its core tutoring behavior.

For compatible models, the richer clarification flow can use:

```text
Function Calling: Native
Builtin Tools: On
Ask User: On
```

If `ask_user` is unavailable or the selected model cannot call tools reliably, Study Mode falls back to ordinary conversational clarification.

Quiz Rich UI is also independent of native function calling. The model only needs to follow the quiz-transport instruction well enough to produce the structured quiz payload.

## Administrator Valves

Important v1.1 settings include:

| Setting | Recommended default | Purpose |
| --- | --- | --- |
| System prompt integration | Merge | Append the scoped Study Mode overlay to an existing system message for broad provider compatibility. Separate mode is available for setups that prefer multiple system messages. |
| Interactive quiz UI | On | Render validated quizzes as Rich UI cards. |
| Maximum quiz questions | 20 | Hard cap accepted by the renderer. |
| Quiz schema tolerance | Compatible | Repairs a limited set of common structured-output variations from local/smaller models while still validating the quiz. |
| Multilingual quiz detection | On | Detect common quiz requests in several languages. |
| MathJax | Off | Render LaTeX through pinned MathJax 3.2.2. Opt-in because strict iframe CSP or offline deployments may block external scripts. |
| Keyboard shortcuts | On | Enable keyboard-first quiz operation. |
| Fullscreen button | On | Request browser fullscreen where the Rich UI iframe policy allows it. |
| Export HTML | On | Download a standalone HTML snapshot of the quiz client-side. |
| Hidden quiz transport | On | Keep machine JSON out of the visible response/reasoning UI. |
| Quiz card only | On | Show the interactive quiz rather than the transport text when rendering succeeds. |
| Randomize options | On | Shuffle each question independently and remap the correct answer. |
| Quiz progress status | On | Show quiz preparation progress and always finalize the status. |

### Compatible vs Strict quiz parsing

`Compatible` is intended for local and smaller instruction-tuned models that sometimes produce almost-correct structured output. It can normalize a bounded set of variations, including string-only options, alternative answer fields, missing optional quiz metadata, trailing commas, and some malformed LaTeX backslashes.

It still validates question structure, answer count, unique option identifiers, the correct answer, maximum quiz length, and text length limits before rendering.

`Strict` requires the documented quiz schema and is useful when the selected model follows JSON instructions reliably.

This improves compatibility but does not guarantee that every model will generate a valid quiz. Model instruction-following quality still matters.

## User Valves

Users can configure:

| Setting | Purpose |
| --- | --- |
| Teaching style | Adaptive, Guided, Socratic, Explain then Practice, or Quiz |
| Learner level | Auto, Beginner, Intermediate, or Advanced |
| Pace | Adaptive, Slow, Normal, or Fast |
| Answer policy | Adaptive, Guide me first, Hints first, or Direct answers allowed |
| Prefer Ask User | Use native `ask_user` when available for material clarifications |
| Check understanding | Add short comprehension checks where useful |
| Use analogies | Use analogies only when they clarify the concept |
| Use course materials | Prioritize attached/retrieved learning material |
| Personalize with Memory | Allow relevant existing learning context when Memory tools are available |
| One question at a time | Keep interactive tutoring focused |
| Quiz setup | Ask for missing count/difficulty or use defaults |
| Default quiz count | Default number of quiz questions |
| Default quiz difficulty | Adaptive, Easy, Medium, or Hard |

## Quiz keyboard shortcuts

When keyboard shortcuts are enabled:

```text
A-E or 1-5   Select an answer
Left Arrow   Previous question
Right Arrow  Next question
Enter        Continue / finish after answering
H            Toggle hint
F            Toggle fullscreen when available
```

Shortcuts are ignored while the user is typing in an input-like control.

## Math and LaTeX

MathJax is intentionally **off by default**. When enabled, the quiz iframe loads the pinned MathJax 3.2.2 `tex-chtml` build and renders common inline/display LaTeX delimiters.

If the Open WebUI iframe Content Security Policy blocks the CDN, the network is unavailable, or the script fails to load, the quiz remains usable and shows the original LaTeX text rather than breaking the card.

For privacy-sensitive or offline deployments, leave MathJax disabled or self-host/allow the required resource through your deployment policy before enabling it.

## HTML export

The Export HTML control creates a browser `Blob` from the rendered quiz and downloads it from inside the Rich UI iframe. It does not write a file on the Open WebUI server.

The exported HTML is a snapshot of the quiz UI. Browser or iframe download policy may disable the feature in hardened deployments.

## Existing system prompts

Study Mode is a scoped overlay, not a replacement system prompt. Version 1.1 adds explicit integration behavior because users may already have complex Modelfiles, model system prompts, or other active Filters.

Default **Merge** mode preserves the existing system text and appends the Study Mode instructions. **Separate** mode adds another system message after existing leading system messages.

The prompt also scopes interactive quiz JSON rules to actual quiz-generation turns so an unrelated base instruction is less likely to conflict with quiz transport requirements. No prompt-composition strategy can resolve every contradictory system prompt, so heavily customized deployments should test their model/Filter order.

## Interactive quiz flow

```text
User requests a quiz
        |
        v
Detect quiz intent / use Quiz style
        |
        v
Collect missing preferences with ask_user when available
        |
        v
Show "Preparing your quiz..."
        |
        v
Model produces structured quiz data
        |
        v
Suppress machine transport while streaming
        |
        v
Strict or Compatible validation
        |
        +---- invalid ----> Restore a readable model response
        |
      valid
        |
        v
Persistent Rich UI quiz card
        |
        v
Hints / keyboard / navigation / feedback / scoring
        |
        v
Review mistakes / continue studying / new quiz / export
```

## Example tests

### Adaptive tutoring

```text
Teach me MQTT.
```

### Socratic problem solving

Set the teaching style to Socratic:

```text
Help me solve x^2 - 5x + 6 = 0.
```

### Interactive quiz

```text
Quiz me on Python OOP.
```

French detection example:

```text
Fais-moi un quiz sur les réseaux informatiques.
```

### LaTeX quiz

Enable **MathJax**, then try:

```text
Give me a 5-question medium algebra quiz. Use LaTeX for the equations.
```

### Course material

Attach a study document:

```text
Study this material with me, then quiz me on the important concepts.
```

## Compatibility and limitations

- Core tutoring works with text-capable models, but quality depends on instruction following.
- Native `ask_user` requires reliable Native function calling; it is an enhancement, not a dependency.
- Compatible parsing increases tolerance for local-model structured output but cannot turn arbitrary prose or fundamentally invalid questions into a safe quiz.
- Fullscreen, HTML downloads, and MathJax can be restricted by browser, iframe sandbox, network, or Open WebUI CSP configuration.
- Quiz state lives in the rendered interaction; v1.1 is not a permanent learning analytics database.
- Automatic long-term mastery tracking is not included.

## Security

Open WebUI Functions execute Python inside the Open WebUI server process. Review source before enabling community Functions and restrict Function management to trusted administrators.

Quiz data is validated and length-bounded before rendering. Model-provided question/answer text is inserted as text rather than trusted HTML. Script-embedded JSON escapes `<`, `>`, `&`, U+2028, and U+2029. The renderer does not use `eval()` or `new Function()`.

MathJax is the only optional external browser dependency introduced in v1.1 and is disabled by default.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Versioning

Recommended Git tag for this release:

```text
study-mode-v1.1.0
```

## License

Released under the repository's [MIT License](../../../LICENSE).
