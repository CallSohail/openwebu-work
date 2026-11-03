# Generative UI: architecture, research and audit

## 1. Integration choice

Open WebUI offers Tools, Functions (Filter, Pipe, Action, Event), Pipelines, MCP/OpenAPI tool servers and front-end changes. What each can do for in-chat UI, checked against the Open WebUI source (`main`, package version 0.12.5):

| Option | Can it put interactive UI in a message? | Verdict |
| --- | --- | --- |
| **Python Tool returning `HTMLResponse`** with `Content-Disposition: inline` | Yes. `utils/middleware.py` (`process_tool_result`) moves the HTML into the message's `embeds`; `ResponseMessage.svelte` renders each embed with `FullHeightIframe` (`allow-scripts`, `allow-popups`, `allow-forms` by user setting, `allow-same-origin` only when the user opted in). Returning `(HTMLResponse, context)` gives the model a custom result string. | **Chosen.** Documented, survives reloads (embeds are stored with the message), works with any model that can call tools. |
| Filter / Pipe emitting HTML in the text | Only as Markdown/HTML code blocks or artifacts; no stateful widget beside the answer. | Not enough for interactive components. |
| Action Function | Runs when the user clicks a button under a message. | Useful later for "re-render" actions; not the main path. |
| Custom Svelte components inside Open WebUI | Possible only by forking the front end. | Rejected: breaks on every upgrade. |
| MCP / OpenAPI tool server | Tool results are text/JSON; embeds are supported for external tools via response headers, but a separate service must be deployed. | Possible future packaging; the single-file Tool is simpler to install. |

Messages from the embed to Open WebUI (from `FullHeightIframe.svelte` and `Chat.svelte`):

- `{type: "iframe:height", height}` resizes the iframe (the embed has an opaque origin, so Open WebUI cannot measure it).
- `{type: "input:prompt", text}` fills the chat input; `{type: "input:prompt:submit", text}` submits it after Open WebUI shows a "Confirm Prompt from Embed" dialog. Other message types from embeds are ignored.
- Theme: the embed cannot read the parent document. The Tool asks the browser once through `__event_call__` (`execute`) for the current theme and locale and bakes them in; when the user enabled same-origin iframes, the renderer also follows live theme switches.
- An administrator can set `IFRAME_CSP`, which Open WebUI injects into every embed.

Constraints that shaped the design: no network from the embed should be required (CSP may block CDNs, and offline servers are common), the embed is stored with every message (size matters), and the iframe is the security boundary.

## 2. Pipeline

```text
render_ui(spec)
  parse_spec       fences, smart quotes, trailing commas, truncated output, double-encoded JSON, Python literals
  normalize_spec   type and field aliases, legacy Frontier UI migration, per-field schema, limits, duplicate ids,
                   URL and expression filters, state registration for inputs, computed values
  _resolve         weather (Open-Meteo), image queries (Wikipedia), place geocoding; concurrent, bounded, cached
  build_html       CSP + CSS + validated JSON + renderer, single-pass templating, ASCII-only output
  return           (HTMLResponse inline, context for the model: notes, repairs, live-data provenance, summary)
```

## 3. Protocol (version 1)

Top level: `version`, `title`, `subtitle`, `icon`, `badge`, `frame` (`card` or `plain`), `data_source {kind, label, as_of}`, `state {var: scalar}`, `computed {var: expr}`, `components [...]`, `sources [{title, url}]`, `followups [str]`, `footnote`.

Every component: `type`, optional `id` (stable, de-duplicated), `visible_if` (expression), `span` (1 to 4 inside a grid). The per-type fields are declared in `SCHEMAS` and `ITEMS` in `generative_ui.py`; anything not declared is dropped, every kept field is coerced to its declared kind (`str`, `md`, `num`, `int:lo:hi`, `bool`, `enum`, `tone`, `icon`, `url`, `img`, `expr`, `val`, `range`, `points`, `rows`, `children`, ...).

State and data binding:

- `state` holds scalars. Inputs (`slider`, `number`, `select`, `segmented`, `toggle`, `text_input`, `date`, selectable `list`, `checklist`, `tabs`) read and write their `bind` variable; the server registers a default for every bound variable, so the browser never starts with an undefined value.
- `computed` variables are re-evaluated in declaration order before every update.
- Values that start with `=` and `{{...}}` templates in text are expressions. The evaluator is a Pratt parser that compiles to closures: literals, declared variables, whitelisted functions, arithmetic, comparison, logical and ternary operators. No property access, no `eval`, no `Function`. Errors render as an em dash with the error in a tooltip; they never break the component.
- Updates are batched in a microtask; every bound element registered a watcher, wrapped in its own try/catch.

Actions are declarative: `prompt`, `set`, `reset`, `copy`, `link`, `download`.

Versioning: the server stamps `"version": "1"`. Future breaking changes will add a migration step in `normalize_spec`, as is already done for Frontier UI 1.x `blocks`.

## 4. Renderer

A single vanilla JavaScript module (about 100 KB, no dependencies) with a component registry `R[type]`. Each component render is wrapped in an error boundary. Charts are drawn as SVG with nice-number axes, dashed recessive grids, a crosshair tooltip that follows the mouse and the arrow keys, a "table view" toggle, and a color-blind-checked categorical palette with separate light and dark steps. The diagram component layers nodes by longest path, orders them by barycenter, and switches from left-to-right to top-to-bottom when the container is narrow. Layout uses CSS container queries, so cards adapt to the chat column rather than the window.

Accessibility: keyboard operation for tabs, segmented controls, lists, charts, diagram nodes, accordions and steps; ARIA roles (`tablist`, `radiogroup`, `listbox`, `switch`, `checkbox`, `progressbar`), `aria-sort` on tables, labels on every input, `prefers-reduced-motion`, forced-colors fallbacks, status never conveyed by color alone (icons and labels).

## 5. Security model

Trust boundaries:

1. **Model output is untrusted.** It is parsed as data, validated against an allow-list and never executed in the native path.
2. **The embed iframe** is the main boundary. By default it has an opaque origin: no access to Open WebUI cookies, `localStorage`, tokens or DOM. The renderer's own CSP adds `connect-src 'none'`, `form-action 'none'`, `base-uri 'none'`, `object-src 'none'`, `img-src https: data: blob:`, and allows frames only from `www.openstreetmap.org`.
3. **The `app` component** runs inside a second sandbox (`allow-scripts` only, `default-src 'none'`), so even model-written JavaScript has no network, no storage, no navigation of the top window and no access to the renderer.
4. **Server-side requests** go only to fixed hosts (Open-Meteo, Wikipedia) with model-provided text as query parameters; there is no user-supplied URL fetching, so no SSRF surface. Requests are bounded by timeouts and concurrency and cached.

Threats and mitigations:

| Threat | Mitigation |
| --- | --- |
| XSS through text, labels, Markdown, table cells | DOM APIs with `textContent`; the Markdown subset builds elements; no `innerHTML` with spec data (icons are a fixed server table). Tested with a hostile spec. |
| Script break-out from the embedded JSON | ASCII JSON with `<`, `>`, `&` escaped; single-pass placeholder substitution so text like `__JS__` stays text. |
| `javascript:` / `data:` URLs | Allow-list: `https`/`http`/`mailto` for links, `https` or base64 raster `data:` for images; checked on the server and again in the browser. |
| CSS injection | No style values from the spec; colors come from tone names. |
| Prototype pollution | No dunder names for variables or ids; state is a null-prototype object; the evaluator has no property access. |
| Resource exhaustion | Size, depth, node, row, point, series and expression-length limits; table pagination; per-request timeouts; overall live-data deadline. |
| Prompt injection through tool output | The context returned to the model is a summary of the validated spec and explicit renderer notes; actions that send prompts go through Open WebUI's confirmation dialog. |
| Cross-user leakage | No server-side state except a small cache of public weather, geocoding and image lookups; specs are not stored outside the message. |

## 6. Research comparison

Open WebUI behavior was verified against its source code during this work. The other entries summarize each project's public documentation as known at the time of writing; the sandbox used for this release could not reach most project websites, so these were not re-verified here.

| Project | What it offers | Use here |
| --- | --- | --- |
| Vercel AI SDK (generative UI, `streamUI`), AI Elements | React Server Components streamed from tool calls; React chat components. | Pattern adopted (tool call produces UI, text continues around it). Code not reusable: Open WebUI is Svelte and embeds are static HTML. |
| CopilotKit | React hooks for agent-rendered components and shared state. | Same pattern; React-only. |
| A2UI (Google) | Declarative JSON UI messages with a component catalog that clients render natively; agents never send code. | Closest in spirit: declarative, allow-listed catalog, data binding. Our protocol follows the same principle in a smaller, Open WebUI-specific form. |
| OpenUI, Thesys C1 | Generate UI or UI specs from prompts; C1 is a hosted API. | Not used: generated HTML is what we avoid, and a hosted API would send chat data to a third party. |
| json-render | JSON spec rendered by a component catalog with schema validation. | Same architecture; React-based. |
| shadcn/ui, Radix, Bits UI, Flowbite Svelte, Tailwind | Component systems and styling. | Visual vocabulary only (radii, neutral palette, segmented controls). Shipping them in each embed would add size and a build step. |
| ECharts, Recharts, Plotly, Vega-Lite, Chart.js, D3 | Charting. | Not bundled: each is 100 KB to 3 MB and would be stored with every message or loaded from a CDN that `IFRAME_CSP` may block. A small SVG renderer covers the needed chart types with tooltips and keyboard access. Open WebUI itself can inject Chart.js into same-origin embeds, but not into the default sandbox. |
| Mermaid, Cytoscape.js, Svelte Flow | Diagrams and graphs. | Not bundled for the same reason; the `diagram` component does layered auto-layout for architecture and flow diagrams. Open WebUI already renders Mermaid code blocks in plain answers. |
| MapLibre GL, Leaflet, OpenStreetMap, OpenRouteService | Interactive maps and routing. | OpenStreetMap embed (no key, no JS library) plus stop list and directions link. Full vector maps need a library or a CDN; routing needs a provider. Listed as a limitation. |
| Sandpack, WebContainers, Pyodide, Monaco | In-browser code execution and editors. | Out of scope; Open WebUI already has code execution. The `app` component covers small HTML/JS widgets. |
| MCP, JSON Schema, Pydantic | Tool transport and validation. | Validation is hand-written per field kind so it can repair, coerce and report rather than reject outright, which matters for small local models. |

## 7. Audit of `frontier_ui.py`

The previous Tool (Frontier UI 1.3.1, about 2,650 lines) had good ideas worth keeping: lenient JSON parsing, a block-based spec, keyless live weather, Wikipedia photos and a calculator with expressions. Problems found:

| Area | Finding | Resolution |
| --- | --- | --- |
| Correctness | Imports in the middle of the module (`time`, `os`, `base64`, ...) after functions that use them; works only by import order. HTML template patched with string replacements after definition. | Single clean module, imports at the top, pyflakes clean. |
| Cross-user data exposure | A global in-memory `_CARDS` dict keyed by an 8-hex-digit id; any user could export another user's card by id through `craft_file` / `save_note`. | Removed. No cross-request state except public lookup caches. |
| Scope | Gmail drafts, a separate "Craft" desktop and bridge service, Office file generation and editing, page screenshots through headless Chromium, embeddings reranking: most need infrastructure that is not part of Open WebUI and were untestable. | Removed; the Tool does one job. |
| Third-party terms | Unofficial Yahoo Finance endpoints for quotes; several scraped image providers. | Removed. Live data limited to Open-Meteo (documented free API) and Wikipedia (official API with a descriptive User-Agent). |
| Front-end dependencies | MapLibre and tiles from a CDN; breaks under `IFRAME_CSP` or offline. | Self-contained renderer; OpenStreetMap embed is optional. |
| Theme | Defaulted to dark when detection failed. | Falls back to the OS preference. |
| Security | Model HTML and URLs handled in many places; no CSP of its own beyond a nonce. | Central allow-list, CSP, sandboxed `app`, hostile-input tests. |
| Interactivity | Calculator and budget blocks only. | General state, computed values, inputs, `visible_if` and actions for every component. |
| Tests | None. | 44 Python tests, 66 browser checks, CI workflow. |
| Attribution | Author field referenced another project and a model name. | Repository author and license headers. |
