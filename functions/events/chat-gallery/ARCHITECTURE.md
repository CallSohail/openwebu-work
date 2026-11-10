# How Chat Gallery works

Chat Gallery is one Python file with two halves. The Python half runs on the Open WebUI server and does one thing: it serves a script. The script runs in each person's browser and is the whole gallery.

Every statement below was checked against the Open WebUI v0.13.3 source or seen in a running v0.13.0 and v0.13.3. File paths are Open WebUI's.

## 1. How the script reaches the browser

Open WebUI's page loads `/static/loader.js`, normally an empty file an administrator may edit. The function replaces that route.

```text
Admin enables the function
        │  Open WebUI executes the source as module "function_chat_gallery_<hex>"
        ▼
Event.__init__  ──►  reload_bootstrap()  ──►  register(app)  ──►  asset_register(app, "/static/loader.js", …)
                                                                        │
                                              app.state._owui_static_fragments["/static/loader.js"]["function_chat_gallery"]
                                                                        │
Browser: GET /static/loader.js  ──►  serve_asset  ──►  asset_compose: file on disk + every plugin's fragment
```

- `asset_register` (Shared Assets Protocol, by Classic298) stores a *producer* in a registry on `app.state` and inserts one route ahead of Open WebUI's static files. Several plugins share that route; each contributes a fragment wrapped in its own start and end comments.
- The producer is `Event._loader_fragment`. It fills two placeholders in `LOADER_SCRIPT`: `__CSS__` with the stylesheet as a JSON string, and `__CONFIG__` with the valves as JSON. The result is cached and rebuilt only when a valve changes, because the producer runs on the event loop for every page load.
- The route answers with an `ETag`, so a reload that changes nothing costs a 304.
- The live reload block handles a server with several workers: enabling, disabling or saving valves publishes on Redis, and the other workers load or withdraw the fragment. `reload_active(app)` gates the producer, which is how disabling the function removes the gallery.
- `Event.event()` receives every Open WebUI system event (`backend/open_webui/events.py`) and passes it to `reload_on_event`. The function reacts to nothing else.

These two blocks are copied byte for byte. `tests/test_chat_gallery.py` pins their hashes, so an accidental edit fails the tests.

## 2. What the script does in the page

The script is one self-contained function. It builds its interface with `document.createElement` and sets text with `textContent`; it never parses HTML from a chat.

### Finding a place to live

Open WebUI's interface is compiled Svelte, with no plugin API for adding a tab. The script watches the page with one `MutationObserver` and, at most once per frame, runs `sync()`:

| What it looks for | Where it comes from | What the script does with it |
| --- | --- | --- |
| `.panel-tab-strip` inside `#controls-container` | `ChatTabs.svelte`, the tab rows | puts its own *Gallery* tab after the row |
| `[data-panel-group]`, `[data-panel-tab-bar]` | `ChatTabs.svelte`, split panes (0.13.1+) | measures the pane the gallery should fill |
| `body > [role="menu"] .app-dropdown-menu` | `DropdownMenu.svelte`, the + menu | appends a *Gallery* item while the menu is open |
| `#chat-container nav`, `#chat-pane` | `Navbar.svelte`, `Chat.svelte` | hosts the Gallery button and the over-the-chat layout |

The gallery itself is a `position: fixed` box appended to `<body>`. `place()` moves it over the right pane every frame while it is in front. What that pane showed is marked `data-cg-covered` (invisible and `inert`) instead of being painted over, so a page background still shows through.

Three layouts follow from what is on screen:

1. **Tab row on screen** (wide screens on 0.13.1+, or the side panel opened on 0.13.0): the gallery is a tab among Open WebUI's tabs.
2. **No tab row** (a phone, or 0.13.0 with the panel closed): the gallery lies over the chat, below the top bar, with its own close button. The code calls this *solo*.
3. **G30's Chat Tabs pane**, when that plugin is installed and the panel is closed.

### Collecting images

*This chat* (`collectChat`): one `GET /api/v1/chats/{id}`. For every message in `history.messages`, on every branch, it takes

- `message.files` entries that are images (uploads, and what Open WebUI's image generation attaches);
- Markdown images and `<img>` tags in the message text and in tool output;
- any `/api/v1/files/<id>/content` address in the text, kept only if the file's record says it is an image.

Each image with a file id then gets its record from `GET /api/v1/files/{id}`, six at a time. `applyRecord` reads the prompt, model and settings Open WebUI stored in `meta.data`, and marks the image *generated* when the file is named `generated-image…` (the name `routers/images.py` gives it) or has a prompt.

*My images* (`loadLibrary`): `GET /api/v1/files/user/search?filename=*&content=false`, 200 files a page, newest first. That route always filters by the signed-in person (`routers/files.py`), administrators included. Records that are not images are dropped in the browser.

### Showing them

- **Grid.** Each image has one tile, kept in a map and moved when the list is filtered or sorted. A tile asks for its picture only when it comes within 600 px of the visible area. Four at a time, the script fetches the file, draws it at about 320 px with a canvas, and shows that small picture from memory. Files under 90 KB, GIFs and SVGs are shown as they are.
- **Viewer.** The full file is fetched once and kept for the six most recent images. If it is a PNG, `pngText` walks its chunks and reads the `parameters` text (AUTOMATIC1111, Forge, Fooocus) or the `prompt` graph (ComfyUI).
- **Zip.** Built in the browser, stored without compression, with a CRC table computed once.

### Requests

Every call goes through `request()`, which adds a timeout and one retry for reads. `fetchBlob` attaches the sign-in token only when `new URL(address, location.href).origin` equals the page's origin.

## 3. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves` and `_config()` in Python, then `CONFIG.…` in the script |
| A word or a language | `STRINGS` in the script; the test fails if a key is missing in a language |
| Which images a chat contributes | `collectChat` and its `add()` |
| What counts as generated | `isGeneratedName` and `applyRecord` |
| Where the gallery sits after an Open WebUI update | `panel()`, `target()`, `cover()`, `syncHeader()` |
| Small-picture size and concurrency | `makeThumb`, `pumpThumbs`, the `thumbnail_px` valve |

After a change, run `tests/test_chat_gallery.py`, then the browser tests against a throwaway Open WebUI as the README describes.
