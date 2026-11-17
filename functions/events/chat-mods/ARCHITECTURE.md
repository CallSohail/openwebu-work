# How Mods works

Mods is one Event Function in two halves: a small server half that keeps settings and serves a script, and the script, which runs in every Open WebUI page.

## Server half

- It uses Classic298's [Shared Assets Protocol](https://github.com/Classic298/open-webui-plugins/tree/main/shared-assets-protocol): its fragment is served inside `/static/loader.js` between `// owui-mods:start` and `// owui-mods:end`, and withdrawn when the function is disabled or deleted. The two protocol blocks are copied byte for byte and checked by hash in the tests.
- `_config()` turns the valves into the `CONFIG` object the script starts with: the function id, the switches, the allowed permissions, limits, the catalog and the ready-made mods. The fragment is rebuilt only when a valve changes. `</` and line separators are escaped so no catalog code can end or break the script.
- **Storage.** Each person's mods live in the function's `UserValves.mods`, a JSON string `{"mods": [...], "dismissed": [...]}`, saved with Open WebUI's own `POST /api/v1/functions/id/chat_mods/valves/user/update`. That route builds `UserValves(**form)`, so the `field_validator` decides what is stored: every mod is rebuilt from known fields only, and anything else is refused. The catalog is an admin valve with the same checks; only administrators can change admin valves.
- It adds no routes of its own and calls no model.

## Browser half

### Running a mod

```text
Open WebUI page                               mod frame (sandbox="allow-scripts", no origin)
───────────────                               ─────────────────────────────────────────────
start(mod)
  nonce = 16 random bytes
  srcdoc = CSP + FRAME_CSS + modBoot(nonce, theme) + async (mod) => { code }
                                  ◀── {__mods: nonce, type: 'ready'}
  theme ──────────────────────────▶ applyTheme (--mod-* variables)
                                  ◀── {type: 'call', name: 'status.set', data}
  checks: event.source is this frame,
          nonce matches, permission granted
  HANDLERS['status.set'] → paintBar()
  reply ──────────────────────────▶ promise resolves
  events (composer, message, chat,
  theme, command, button, action, pane) ──▶ mod.on(...) listeners
```

- `modBoot` defines a frozen, non-writable `window.mod` and turns each call into a message with an id; replies resolve the promise.
- Every frame stays where it was made, inside the panel (`#owui-mods-pane`), because moving an iframe reloads it. The panel shows one frame at a time by `data-shown`.
- The frame's first `load` is its own document. A second means the mod navigated: the frame is removed and the mod switched off.
- Errors from the frame are kept per mod (last 20). Five in ten seconds switch it off and mark it failed.
- `HANDLERS` maps each call to its permission and work. Rates (messages, sends, model calls) are counted per mod with `tooOften`.

### What mods show

- `#owui-mods-bar` is placed as the first child of the message box's form: bars (`ui.band`), then labels (`ui.status`) and `/` command hints. It is redrawn from the runners' state, never patched.
- Buttons under answers go in a `.md-msgrow` inside each `#response-content-container`; a signature of the buttons decides when rows need redrawing.
- Styles go in a `<style data-mod="…">` per mod, after `cleanCss`.

### Watching the page

- One `MutationObserver` on `<html>` schedules `sync()` once per frame: it notices the open chat changing, theme or language changes, the ⋯ and + menus opening, the bar or rows being redrawn away by Open WebUI, and the message box changing without an `input` event (Open WebUI's editor clears it without one).
- `/name` + Enter is caught in the capture phase before Open WebUI sends; the command's answer fills the message box (`composer.write`) or is sent (`chat.send`).
- A finished answer is detected when the last answer's text is the same on two checks a second apart and nothing is generating; answers already there when a chat opens are not announced.
- Chats are read from `GET /api/v1/chats/{id}`, following `currentId` back through `parentId`, cached for 2.5 seconds.

### Writing a mod

```text
describe ──▶ writing ──▶ checking ──┬─▶ ready      (preview, Try It, Install)
   ▲            ▲                   ├─▶ writing    (problem sent back; up to MAX_TRIES = 3)
   │            └─ Stop ─▶ previous └─▶ failed     (last problem shown)
   └─ /mod <description> in the message box
```

- The phase is a machine shown on `<html>` as `data-md-studio` (`idle`, `writing`, `checking`, `ready`, `failed`). Stop aborts the request and returns to the previous result, if any.
- `parseMod(text, {lenient: true})` reads a model's answer: it drops `<think>` blocks, finds the ```` ```mod ```` fence (or any fence whose content starts with `name:`), keeps a ```` ```js ```` block inside it, accepts bold and bulleted settings, settings without the `---` line, permission synonyms, and skips names that need no permission. `neededPermissions()` reads the code for the API it calls and adds what is missing (shown as *Added because the code uses them*). Pasted mods are read strictly.
- `lintMod()` refuses what can't work in the sandbox (network, browser storage, `alert`, the Open WebUI page, `eval`, imports), API names and events that don't exist, and promise-returning calls used without `await`.
- `testRun()` writes a throwaway frame with the same sandbox and content policy as a real mod. `smokeStub` defines a stand-in `mod` that records what is used and calls nothing outside the frame. After the code starts, it fires `chat`, `theme`, `composer` (empty, short, 360 words), `message` and `pane` events, then calls every action, command and button callback it collected. Uncaught errors and rejections are reported, with a 5-second limit for code that never finishes.
- `unmatchedSelectors()` checks the CSS a mod set during the test run against the real page; if no rule matches anything, the model is told which selectors exist.
- A panel, buttons or commands asked for but never made are a problem, except on the last try, where they become a warning.
- Each problem goes back as a user message after the model's own answer. The last try asks it to start again simply.
- The guide (`GUIDE`) describes every permission, where things go ("above the message box" means a label or a bar), the selectors that exist, the `await` rule, and two complete example mods.

### The /mod card

`paintBar()` shows `slashHelp()` while the message box starts with `/mod`: an explanation and examples when nothing follows, *Press Enter to create a mod that: …* when something does, and a note while a mod is being written. Meanwhile `data-mods-slash` on `<html>` hides Open WebUI's own suggestion popup (`[data-tippy-root]:has(#suggestions-container)`), which would otherwise offer `/model`. The keydown handler catches `/mod` + Enter in the capture phase, before Open WebUI sends.

### The window

- A `<dialog>` with three tabs, redrawn whole by `paintDialog()`, which puts focus back on the control with the same `data-focus`.
- Permission, confirmation and menu boxes are smaller dialogs stacked on it.
- Create keeps its state (`studio`) while the page is open, so closing the window loses nothing. The model gets `GUIDE` as its system message and must answer with one ```` ```mod ```` block, read by `parseMod`.
- Publishing reads the admin valves, replaces the mod in the catalog and saves them back; the server checks the result.

### States

Saving and loading a person's mods is a small machine (`idle → loading → ready ⇄ saving`, `failed`), shown on `<html>` as `data-md-store`. Writes are queued so two saves never cross. The stored object is `{mods, dismissed, paused}`; `paused` stops every mod and `/mod`.

When a tab becomes visible again (`visibilitychange`), it reloads that object and `matchRunners()` stops mods that were removed, switched off, changed or paused elsewhere, and starts ones that were added.

## Files

| File | What it is |
| --- | --- |
| `chat_mods.py` | The function: protocol blocks, `MODS_CSS`, `LOADER_SCRIPT`, validation, ready-made mods, `Event` |
| `tests/test_chat_mods.py` | Server and script tests, some run in Node |
| `tests/e2e.mjs` | Browser tests with Playwright |
| `tests/seed.py` | Sets up a throwaway Open WebUI with an admin, a person, chats and the fake model |
| `tests/fake_model.py` | An OpenAI-compatible model that writes a Character Counter mod |
