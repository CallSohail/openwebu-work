# Chat Inspector

Chat Inspector is a live, read-only panel for Open WebUI v0.13.3. Open a saved chat and select **ⓘ** beside the tabs, in the chat header, or from the tabs' **+** menu. The panel stays open while you chat and refreshes every 30 seconds while the browser tab is visible. **Refresh** requests a snapshot immediately.

It shows:

- Open WebUI's **estimated** context use and compaction threshold, when context compaction is enabled. Otherwise it shows a **local approximation** from saved branch messages and any recorded response usage. That approximation can miss system prompts, attachments and tool schemas. The bar and percentage appear only when a threshold is provided; they are relative to that threshold, **not necessarily the model's context window**. Estimates can lag during streaming.
- Reported input, output and total tokens for the last assistant response on the server's current branch. These may be missing or incomplete during a reply and are not a bill or a conversation total.
- Counts of workspace tools, tool servers, filters and skills **only if saved chat metadata contains those lists**. They are not the live composer selection or proof that tools were used. Missing lists display “Not recorded.”
- The response model's explicit capability configuration, when the model remains in the available server catalog. A flag does not prove the feature is enabled for the current user or chat.

Open WebUI v0.13.3 does not expose the exact per-category token accounting shown in Claude's session rail through the chat API. In particular, this panel cannot attribute tokens separately to messages, system prompt, MCP schemas, skills, or deferred tools. It displays **“Not measured by Open WebUI”** instead of fabricating those numbers. Open WebUI's native context status and Settings > Data may provide other usage views.

## Install or update

1. Back up your Open WebUI configuration and test on a non-production instance first. Only a trusted administrator should install functions: they execute Python inside the Open WebUI server.
2. In **Admin Panel > Functions**, create or edit the existing Chat Inspector Event Function. Paste the entire [`chat_inspector.py`](chat_inspector.py), save and enable it.
3. Hard-refresh the browser and select **ⓘ**. The panel can stay open when a new chat becomes saved after its first message. Keep it open while sending another message; it should update after the response is saved or when you select **Refresh**.
4. To roll back, disable the function and reload open tabs. Follow your deployment's normal multi-worker/Redis setup.

The tab/header integration uses Open WebUI's internal DOM, not a supported tab extension API. It may need an update after an upstream UI change. New and temporary chats have no persisted chat snapshot; direct connections and terminal agents may not appear in the server model catalog. Multiple-model chats show the model recorded on the last assistant response on the current branch. The panel closes when the route or browser token changes.

## Privacy and security

- It makes read-only same-origin GET requests to `/api/v1/chats/{id}` and, when the last response has a model ID, `/api/models`. The first request happens when a user opens it. While the panel is open and visible, it refreshes the chat every 30 seconds; failed requests back off up to four minutes. Model metadata is refreshed at most every five minutes, or when the response model changes. Polling stops when the panel closes or the browser tab is hidden. A focus or visibility change can request a fresh snapshot.
- Open WebUI's normal server-side authentication and access checks apply. The chat endpoint returns the chat, including its content, to the browser even though the inspector displays only metrics and metadata. The model endpoint can cause Open WebUI to consult configured model providers according to its own configuration. Avoid leaving the panel open on large chats if that traffic is undesirable.
- The browser sends the existing session bearer token only to relative same-origin endpoints. Redirects are rejected. No external endpoints, analytics, storage, new server routes or outbound requests are added by the inspector itself. Open WebUI's own logs and providers remain subject to their existing policies.
- Values are rendered as text. The panel clears its contents and aborts requests on close or navigation. It does not persist displayed data. The Event function runs trusted JavaScript in the Open WebUI page; it is not an isolation boundary for untrusted plugins.
- Requests time out after 15 seconds. Network errors, access denial, unavailable models and unreported values are displayed as unknown; the inspector does not grant access or alter configuration.
- The script uses the Shared Assets Protocol and live reload blocks shared with Chat Gallery. Those blocks are by Classic298 (BSD-3-Clause); see Gallery attribution.

## Development and tests

`inspector.js` is the readable browser source; `chat_inspector.py` is the self-contained installation file. Rebuild after changing JavaScript or the shared protocol source:

```sh
python functions/events/chat-inspector/build.py
python -m unittest discover -s functions/events/chat-inspector/tests -v
node functions/events/chat-inspector/tests/browser.cjs
```

The Python suite requires Pydantic, Starlette and httpx. The browser suite requires Playwright and Chromium; set `CHROMIUM_PATH` if it is not `/usr/bin/chromium`. It runs against a simulated server, not a live Open WebUI instance. Before production deployment, validate on the exact Open WebUI release and topology in use, with regular-user and shared-chat permissions, mobile layout, Gallery enabled, and your reverse proxy and authentication settings.
