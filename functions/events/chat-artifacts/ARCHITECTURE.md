# How Artifacts works

Artifacts is built like its companions: one Python file whose server half only serves a script through Classic298's Shared Assets Protocol (copied byte for byte), and whose browser half is the whole interface. The script is assembled from parts in this order; each section below is one of them.

## 1. Finding artifacts (`artifactsOf`)

`load` reads the open chat, or the shared one on `/s/` pages. `branchOf` follows `history.currentId` back to the first message, so only the branch on screen counts. For each answer, `candidatesOf`:

1. strips `<details>` blocks (reasoning, tool calls);
2. parses fenced blocks as CommonMark does (`parseBlocks`): a longer fence holds a shorter one, and an unclosed block runs to the end and is marked unfinished;
3. gives each block a kind (`typeOf`) from its language and content, and a name (`titleOf`): a strong one when it is said outright (a first-line `artifact:` marker, a file name in the info, a `<title>`), a weak one when it is guessed (an `<h1>`, a component's name, a document's heading);
4. adds Markdown tables of three rows or more as spreadsheets, and a long, structured answer as a document.

Grouping into versions: a candidate with the same kind and name as an earlier artifact is its next version. Otherwise, if it is the only one of its kind in the answer, and the previous answer with artifacts had exactly one of that kind, and the two aren't both strongly named, it continues that one. Identical text isn't added twice. An artifact's key is its first version's message id and position (`a<message>-<n>`), so it survives sharing and copying.

## 2. Keeping things in the browser

Drafts are stored per person and chat under `owui-chat-artifacts:<user>:<chat>`, keyed by artifact and version; blank artifacts sit in the same record. A 3 MB budget is enforced by dropping the oldest chats. `window.storage` values live under `owui-chat-artifacts:store:<user>:<artifact>`.

## 3. Running untrusted code (`makeFrame`)

Pages, apps and diagrams run in `srcdoc` frames with `sandbox="allow-scripts allow-modals allow-downloads allow-forms allow-popups"`; without `allow-same-origin` their origin is opaque. Each document starts with:

- a `Content-Security-Policy` meta built by `policy()` from the `network` valve and the library addresses, plus Open WebUI's `iframe_csp`;
- `BOOT`, which reports errors, failed loads and console errors, and provides `window.claude`, `window.ai` and `window.storage` over `postMessage` with a per-frame nonce.

Apps: Babel standalone turns JSX/TypeScript into an ES module; bare imports are rewritten to `esm_cdn` URLs with React pinned and shared (`?deps=react@18.3.1,react-dom@18.3.1`); `@/components/ui/*` and `@/lib/utils` map to a small shadcn/ui stand-in; the module is imported from a blob URL and its default export rendered inside an error boundary. Diagrams render with Mermaid at `securityLevel: 'strict'` and post the SVG back for downloads.

The parent accepts a message only from a registered frame's own window with that frame's nonce. AI calls ask once per artifact, are rate-limited, and send only the prompt to `/api/chat/completions`.

## 4. Viewers

Everything shown outside frames is built from elements and text: `renderMarkdown` (documents, slides, notes), `sheetView` with `sheetEngine` (a tokenizer and recursive-descent parser for formulas, with cycle detection and Excel-style errors; no `eval`), `chartSvg`, `dataView` (lazy `<details>` tree), `codeView` (a textarea with a line gutter) and `diffView` (line LCS after trimming the common start and end).

## 5. The panel

`render` paints the list or the viewer. The viewer's header has two rows (title and actions; versions and views); the footer says where the version came from, whether it is a draft, and any error with *Try fixing*. Downloads build their files in the browser; Excel files are written as a minimal OOXML package with the same ZIP writer the companions use.

## 6. Fitting into Open WebUI

- The panel is `position: fixed` on the right; room is made by padding `#chat-container`, so Open WebUI's own layout (absolutely positioned tab panes on 0.13.3, flex on 0.13.0) shrinks with it.
- A rAF-throttled MutationObserver keeps the button beside ⋯ and the ⋯ menu entry in place; a 700 ms timer draws cards and Open buttons, closes the built-in Artifacts tab when `replace_builtin` is on, and watches the newest message.
- Streaming is detected from the page: a change in the last message that isn't one the loaded chat already has as finished (and isn't Artifacts' own cards). Once it has been still for two ticks, the chat is read once. While it changes, the live view shows the code from the last code block on the page.
- Code blocks are matched to artifacts by their `code-textarea-<chat>-<message>-…` id, info string and first line.
- Messages to the chat use Open WebUI's `input:prompt` and `input:prompt:submit` page messages.

## 7. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves` and `_config()`, then `CONFIG.…` |
| A word or a language | `STRINGS`; the test fails if a key is missing in a language |
| What counts as which kind, and names | `typeOf`, `titleOf`, `readInfo`, `MARKER_RE` |
| Versions | `artifactsOf` |
| The sandbox and its policy | `SANDBOX`, `policy()`, `BOOT`, `reactDoc`, `mermaidDoc` |
| Formulas | `sheetEngine`, `toNumber` |
| Downloads | `downloadItems`, `xlsxOf`, `slidesHtml`, `wordHtml` |
| Where it sits in Open WebUI | `place`, `syncHeaderButton`, `syncMenu`, `syncChat` |
