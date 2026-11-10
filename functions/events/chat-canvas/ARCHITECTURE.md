# How Chat Canvas works

Chat Canvas is built like [Chat Gallery](../chat-gallery/ARCHITECTURE.md) and [Chat Files](../chat-files/ARCHITECTURE.md): one Python file whose server half only serves a script through the Shared Assets Protocol, and whose browser half is the whole interface. Read Gallery's notes for the serving, live reload and tab-placement mechanics; this page covers what is different.

Every statement below was checked against the Open WebUI v0.13.3 source or seen in a running v0.13.0 and v0.13.3.

## 1. From a chat to canvases

`loadChat` reads `GET /api/v1/chats/{id}` once. `itemsOf` walks every message, oldest first:

1. `stripDetails` removes `<details>` blocks. Open WebUI stores the model's reasoning and tool calls that way, and their code isn't part of the answer.
2. `parseBlocks` splits the text into fenced blocks and prose, line by line, the way CommonMark does: a fence is three or more backticks or tildes; it closes on a fence of the same character at least as long; a fence inside a list item or a quote loses that indentation; a fence left open runs to the end and is marked unfinished.
3. `readInfo` works out a language and a title from the fence's first line (`python title="app.py"`, `js:app.js`, `app.py`), a file-name comment on the block's first line, or an HTML `<title>`. XML that starts with `<svg` is treated as SVG.
4. A block shorter than `min_code_lines` is skipped. An assistant message with at least `min_document_chars` of prose also becomes one Markdown document holding the whole answer.

Each canvas has a key, `<message id>:<block number>` or `<message id>:doc`, which is how an edit finds its canvas again.

## 2. Edits

An edit never touches the chat. `persist` writes it to the browser's local storage:

```text
owui-chat-canvas:<user id>:<chat id>  →  { at, drafts: { <key>: { text, at } }, locals: [ { id, title, lang, text, at } ] }
```

- `drafts` holds edits to the chat's canvases; an edit equal to the original is removed. `locals` holds blank canvases the person started.
- Writes are debounced by 400 ms and flushed when the editor closes, the tab is set aside, or the page is hidden.
- `makeRoom` keeps all Canvas keys under about 3 MB by dropping the oldest chats' edits first, and retries once if the browser refuses a write. If it still refuses, or `keep_drafts` is off, the edit lives in memory and the status line says so. An edit over 400,000 characters is never stored.
- `loadChat(true)` re-reads the chat under an open editor and keeps the same objects. A canvas the person hasn't touched follows its original, so an answer still being written keeps growing on screen; one they have edited keeps their text.

## 3. The editor

A `<textarea>` beside a `<pre>` of line numbers, both with the same whole-pixel line height (13 px text, 20 px lines) so the numbers stay level through tens of thousands of lines. The gutter is hidden when lines wrap.

- **Direction.** `dir="auto"` on a large text box costs Chromium seconds, because it resolves every line. `directionOf` looks at the first letter with a direction in the first 4,000 characters instead.
- **Tab.** `onAreaKey` indents through `document.execCommand('insertText')`, which keeps the browser's undo history. Escape releases Tab so keyboard users aren't trapped.

## 4. Preview

| Language | How it is shown | Why it is safe |
| --- | --- | --- |
| HTML | `<iframe sandbox="allow-scripts" srcdoc>` | No `allow-same-origin`, so the page has an opaque origin: no access to the parent, cookies or storage. No top navigation, pop-ups or forms |
| SVG | `<img>` of a blob | Scripts in an image don't run and it can't load other resources |
| Markdown | `renderMarkdown` | Creates text nodes and a fixed set of elements; links limited to `http`, `https`, `mailto` |
| JSON | `JSON.parse`, then pretty-printed text | Text only |
| CSV, TSV | A table of text cells | Text only |

## 5. Changes

`diffLines` sets aside the lines the two texts share at the start and the end, then finds the longest common subsequence of the rest in a `Uint16Array` table. It gives up when that table would pass four million cells. The view keeps three lines of context around each change and folds longer unchanged runs.

## 6. Back to the chat

`toComposer` builds a message and hands it to Open WebUI's own message box with `window.postMessage({type: 'input:prompt', text})`, which `Chat.svelte` accepts from its own origin. It never uses the submitting message types.

Two cases go as an attached file instead of text, through the message box's own file input:

- a canvas over 6,000 characters, to keep the message readable;
- any text containing `{{`, because Open WebUI replaces `{{VARIABLES}}` in text set this way (`textVariableHandler`), which would rewrite templates in code.

Code is wrapped in a fence one backtick longer than the longest run of backticks inside it.

## 7. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves` and `_config()` in Python, then `CONFIG.…` in the script |
| A word or a language | `STRINGS`; the test fails if a key is missing in a language |
| What counts as a canvas | `itemsOf`, `parseBlocks`, `readInfo` |
| Languages, extensions, kinds | `ALIAS`, `EXTS`, `kindOf` |
| What can be previewed | `canPreview` and `paintPreview` |
| The wording sent to the model | the `p_…` strings and `toComposer` |
| Storage limits | `DRAFT_LIMIT`, `STORE_LIMIT` |
