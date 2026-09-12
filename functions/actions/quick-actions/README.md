# Open WebUI Quick Actions v3.0

A compact, context-aware Action Function for Open WebUI that adds one **Quick Actions** button to assistant messages. It lets users transform, verify, explore, create from, study, or humanize a response without adding a row of separate toolbar buttons.

## Compatibility

- Open WebUI **0.11.1+**
- Function type: **Action**
- No extra Python packages
- No direct LLM/API call from the Action itself
- Uses Open WebUI `__event_call__` for the compact menu, input dialogs, confirmations, and composer interaction

## v3 highlights

- Compact command-menu UI instead of a large modal
- Responsive desktop popover and mobile bottom sheet
- Light, dark, system-theme, higher-contrast, and reduced-motion handling
- Action-specific inline SVG icons
- Context-aware suggestions without an extra model call
- New **Humanize** section
- Full built-in English, French, and German catalogs for labels, descriptions, dialogs, and model instructions
- Admin language default plus optional per-user language override
- Native Open WebUI multiselect Valves for enabling/hiding sections
- Admin **Team actions** and per-user **My actions**
- Dynamic custom actions with `{input}` or `{input:Label}`
- Always-available **Custom instruction...** fallback
- Safe preview mode by default
- Draft protection before replacing unsent composer text
- Bounded targeting fingerprint when an older assistant message is selected
- Intentional cancel is distinguished from frontend failure, so closing the menu does not open a fallback modal

## Built-in sections

- Understand
- Rewrite
- Humanize
- Verify
- Create
- Explore
- Code
- Data
- Study
- Utility

The first screen shows only a few context-aware recommendations and the main categories. Less common sections are grouped under **More**.

## Humanize section

The Humanize actions are designed for natural user-facing writing while preserving facts, uncertainty, quotations, code, legal text, citations, and required technical terms.

Included actions:

- Humanize
- Humanize professionally
- Make more conversational
- Remove AI-style patterns
- Adapt to my voice...

The prompts favor direct wording, natural sentence variation, less filler, fewer canned transitions, less promotional language, and less mechanical formatting. They are not framed as AI-detector evasion.

## Multilingual behavior

Admin Valves include **Language** with:

- `English`
- `Français`
- `Deutsch`

Selecting French or German changes the Quick Actions menu, category labels, search text, dialogs, notifications, target instructions, and all 48 built-in action prompts to that language. The German catalog addresses users informally (`du`).

Users can optionally override the admin language in User Valves. User-authored and team-authored custom instructions are intentionally **not automatically translated**; they are executed exactly as written.

The localization layer is catalog-based, so more languages can be added later without redesigning the Action.

## Admin Valves

The v3 configuration is deliberately smaller than v2.

- **Language**: English, French, or German
- **Default behavior**: preview or send
- **Enabled sections**: native multiselect for built-in sections
- **Context suggestions**: on/off
- **Suggested actions count**: 1-5
- **Custom instruction**: on/off
- **User-defined actions**: on/off
- **Team actions**: organization-wide reusable actions
- **Protect unsent drafts**: confirmation before overwrite
- **Menu timeout**
- **Success notifications**
- **Toolbar priority**
- **Debug logging**

Open WebUI Valves and UserValves are the native configuration mechanism for Functions, and current Open WebUI supports `json_schema_extra` input types including multiselect.

## User Valves

Each user gets a small personal configuration surface:

- **Language**: inherit admin / English / French / German
- **Behavior**: inherit / preview / send
- **Hidden sections**: native multiselect
- **Suggest my actions**: pin up to two personal actions into Suggested
- **My actions**: reusable personal transformations

## Custom action syntax

One action per line:

```text
Teams reply :: Rewrite this as a concise Microsoft Teams reply under 80 words.
Executive brief :: Turn this into five bullets for senior management.
```

Ask for a value when the action runs:

```text
Adapt for audience :: Rewrite this for {input}.
```

Use a named input for a clearer dialog:

```text
Adapt for audience :: Rewrite this for {input:Audience}.
Change format :: Convert this into {input:Format}.
```

Blank lines and lines starting with `#` are ignored. Duplicate labels are ignored case-insensitively. Labels and prompts are bounded before entering the menu/prompt pipeline.

## Installation

1. Open **Admin Panel → Functions**.
2. Create/import an Action Function.
3. Paste `quick_actions.py`.
4. Save and enable it.
5. Enable **Global** if it should appear for all models, or assign it to specific models.
6. Open the Function Valves and choose the language, enabled sections, and any team actions.

## Recommended initial settings

```text
Language: English
Default behavior: preview
Context suggestions: on
Suggested actions count: 3
Custom instruction: on
User-defined actions: on
Protect unsent drafts: on
Success notifications: off
```

For a French or German deployment, change only **Language → Français** or **Language → Deutsch**. Users can still override their own Quick Actions language if desired.

## Security and privacy design

- User/team custom action text is prompt data, never executable browser JavaScript.
- Dynamic labels are rendered with `textContent`, not `innerHTML`.
- No `eval()` or `new Function()` is used.
- The compact UI uses fixed JavaScript authored in the Function.
- The Action itself does not call external APIs or invoke a second model just to classify the message/menu.
- The original assistant message is not modified.
- Existing unsent drafts are protected by confirmation.
- Older-message targeting uses only a bounded text fingerprint rather than copying the whole response into the follow-up instruction.
- Only enable community Functions you trust: Open WebUI Functions execute server-side Python with the permissions of the Open WebUI process.

## Testing

Before release, the implementation was checked for:

- Python import/compilation
- built-in English/French/German prompt coverage
- Humanize prompts
- context detection
- Valves/UserValves schema and native multiselect metadata
- custom and named-input action parsing
- team/user action routing
- admin/user section controls
- older-message targeting bounds
- dynamic-label DOM safety
- no `innerHTML`, `eval`, or dynamic Function execution
- individual icon wiring
- dark/light accessibility hooks
- JavaScript syntax
- cancel behavior without accidental fallback modal
- simulated French and German Humanize → composer flow

## Upgrade notes from v2

v3 intentionally replaces the many `show_*` category Valves with a single **Enabled sections** multiselect. Existing `team_actions` and `my_actions` syntax remains compatible. If upgrading from v2, review the Function Valves once after saving v3 because the category configuration schema changed.
