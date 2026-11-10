# How Chat Files works

Chat Files is built exactly like [Chat Gallery](../chat-gallery/ARCHITECTURE.md): one Python file whose server half only serves a script through the Shared Assets Protocol, and whose browser half is the whole interface. Read Gallery's notes for the serving, live reload and tab-placement mechanics; this page covers what is different.

Every statement below was checked against the Open WebUI v0.13.3 source or seen in a running v0.13.0 and v0.13.3.

## 1. Collecting a chat's files

`collectChat` reads `GET /api/v1/chats/{id}` once and walks every message in `history.messages`, on every branch, oldest first. Each entry of `message.files` is sorted by its `type`, as `MessageInput.svelte` writes them:

| `type` | What it is | What Files does |
| --- | --- | --- |
| `file` | An upload, or a knowledge-base file picked on its own | A file row, keyed by its file id. The chat stores a snapshot of the file's record in `file`; name, size, type and owner come from there |
| `collection` | A whole knowledge base | A reference row that opens `/workspace/knowledge/{id}` |
| `note`, `chat`, `folder` | Dragged-in notes, chats and folders | Reference rows; notes and chats open their page |
| `web` | Web pages attached by address | A reference row that opens the page in a new tab |
| `filesystem` | A terminal file | A reference row by name |
| `image` | An image | Left to Chat Gallery |

Any `/api/v1/files/<id>/content` address in a message's text or tool output is kept too, once its record shows it is a real file.

The chat's snapshot is from upload time, so one small request per file, six at a time, asks `GET /api/v1/files/{id}/process/status` whether the file still exists (404 means deleted) and whether processing finished or failed. That route answers `pending` for files that were never processed, so only `completed` and `failed` replace what the chat says. The full record, which can hold megabytes of extracted text, is only read when it is needed: for the owner, or for the reason a file failed.

*My files* is `GET /api/v1/files/user/search?filename=*&content=false`, 200 records a page. That route always filters by the signed-in person.

## 2. Choosing a preview

`classify` gives each file a kind from its extension, then its content type; `previewKind` picks how to show it:

```text
reference ─► notice with an Open button
deleted   ─► notice
size 0    ─► notice
.pdf      ─► fetch ─► starts with %PDF-? ─► yes: <iframe> of a blob typed application/pdf
                                         └► no: treated as unknown (extracted text, or nothing)
.csv .tsv ─► first 512 KB ─► RFC 4180 parser ─► <table>, 500 rows × 50 columns
.json     ─► first 512 KB ─► JSON.parse ─► pretty-printed if complete
text/code ─► first 512 KB ─► NUL bytes? ─► yes: treated as unknown
                                       └► no: <pre>
audio/video/image ─► <audio>/<video>/<img> pointing at Open WebUI, falling back to a fetched blob
archive   ─► download button
anything else ─► GET /data/content ─► Open WebUI's extracted text, or a download button
```

- **Reading only the start.** `readStart` asks for `Range: bytes=0-524287`; Open WebUI's file responses honour it. If a server sends the whole file anyway, the reader stops after 512 KB and cancels the rest.
- **Media straight from Open WebUI.** An `<audio>` or `<video>` element sends the sign-in cookie, streams with range requests and never holds the file in memory. Without a cookie it falls back to a fetch with the token.
- **CSV delimiters.** A file whose first line has more semicolons than commas is read as semicolon-separated, as European spreadsheets save them.

## 3. Writing to the page

Everything from a file, its name, contents, cells or extracted text, is set with `textContent` or as an element attribute, never parsed as HTML. Names and text blocks get `dir="auto"`, so Arabic or Hebrew names read right to left in a left-to-right page and the reverse. The unit tests fail if `innerHTML` or a similar call appears in the script.

## 4. Colours and themes

The stylesheet defines its own variables on `.cf-dialog` from Open WebUI's `--color-gray-*` palette, and a second set under `.dark .cf-dialog`. The OLED theme redefines the gray palette and Her recolours it, so both follow without special cases. A theme switch only changes the `dark` class on `<html>`, so Files repaints at once.

Text on a tinted background (file-type badges, warning flags) uses a deeper tone in light mode and a lighter one in dark mode. The browser tests measure every text against what is actually painted under it, compositing Open WebUI's `oklch()` colours on a canvas, and require 4.5:1.

## 5. Beside Chat Gallery

Both plugins add a tab and a button to the same places, so each keeps to a fixed spot to avoid moving the other every frame:

- **Tabs:** chat tabs, then Gallery's tab, then Files' tab. Files places itself after `#owui-chat-gallery-tabs` when it is there.
- **Buttons:** Gallery's button stays nearest the edge. In the tab row Files sits just before it; in the top bar just after it.
- **Taking turns:** both listen for the `owui-panel-tab-front` event and step aside when the other announces itself.
- **Hiding what lies under them:** each uses its own marker (`data-cg-covered`, `data-cf-covered`), so neither uncovers what the other hid.
- **Images:** with `include_images` on `auto`, Files checks `data-chat-gallery` on `<html>` and leaves images out when Gallery is running.

## 6. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves` and `_config()` in Python, then `CONFIG.…` in the script |
| A word or a language | `STRINGS`; the test fails if a key is missing in a language |
| Which kinds of attachment count | `visit()` inside `collectChat` |
| Which extensions belong to which kind | `EXT` and `classify` |
| How a kind is previewed | `previewKind` and `renderPreview` |
| How much text is read | `TEXT_LIMIT` |
| Colours | the two variable blocks at the top of `FILES_CSS` |
