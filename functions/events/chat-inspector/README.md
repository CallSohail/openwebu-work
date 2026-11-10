# Chat Inspector

A read-only, on-demand indicator for Open WebUI v0.13.3. Open a saved chat and select **ⓘ** in its header to see the last assistant response in the server-returned branch, reported input/output/total tokens, the server's context-compaction estimate when available, and the response model's explicitly configured capability flags.

Version 0.1.0 is an initial release. It does not determine the current unsent model selection, effective tool permissions, selected tools, provider quota, spending, billing, or lifetime usage. Model flags are configuration, not evidence that a model or user can actually use a feature. Missing fields are shown as not reported or not explicitly set. A response in progress can be incomplete. An Open WebUI server may overlay active stream data on a chat response, so this is a server snapshot, not a historical accounting record. Do not add the token numbers together across responses to estimate context usage or a bill. Open WebUI's native Settings > Data and message usage controls may offer additional usage information.

## Install

1. Back up your Open WebUI configuration and test on a non-production instance first. Only a trusted administrator should install functions: they execute Python inside the Open WebUI server.
2. In **Admin Panel > Functions**, create an Event Function, paste `chat_inspector.py`, save and enable it.
3. Hard-refresh the browser. Open a saved chat and select **ⓘ**, then **Refresh** to request a new snapshot.
4. To roll back, disable the function and refresh the page. Disabling cannot remove a script from tabs already loaded; reload those tabs. Follow your deployment's normal multi-worker/Redis setup.

The header integration uses Open WebUI's internal DOM, not a supported tab extension API. It may stop appearing after an upstream UI change. If it is absent, disable this function and check compatibility before upgrading. New and temporary chats cannot be inspected; direct connections and terminal agents may not appear in the server model catalog. Multiple-model chats show only the model recorded on the last assistant response on the server's current branch. Selected tools and other live settings are intentionally not inferred.

## Privacy and security

- No API requests occur until a user opens or refreshes the inspector. It makes read-only same-origin GET requests to `/api/v1/chats/{id}` and, if a response model exists, `/api/models`. Open WebUI's normal server-side authentication and access checks apply. The chat endpoint returns the chat, including its content, to the browser even though the inspector displays only usage and model ID. The model endpoint can cause Open WebUI to consult configured model providers, according to its own configuration.
- The browser sends the existing session bearer token only to relative same-origin endpoints. Redirects are rejected, not followed. Cookie authentication can work where supported. No external endpoints, analytics, storage, new server routes, or outbound requests are added by the inspector itself. Open WebUI's own logs and providers remain subject to their existing policies.
- Data is rendered as text, not HTML. The panel clears its contents and aborts requests on close or navigation. It does not persist the displayed data. As with other page scripts, the Event function's code runs in the trusted Open WebUI page; this is **not** an isolation boundary for untrusted plugins.
- Requests time out after 15 seconds. Network errors, access denial, unavailable models and unreported values are presented as unknown; the indicator never grants access or alters configuration.
- The script is installed using the same Shared Assets Protocol and live reload blocks as Chat Gallery, allowing both to share `/static/loader.js`. Those blocks are by Classic298 (BSD-3-Clause); see Chat Gallery's attribution.

## Development and tests

`inspector.js` is the readable browser source; `chat_inspector.py` is the self-contained installation file. Rebuild after changing JavaScript or the shared protocol source:

```sh
python functions/events/chat-inspector/build.py
python -m unittest discover -s functions/events/chat-inspector/tests -v
node functions/events/chat-inspector/tests/browser.cjs
```

The Python suite requires Pydantic, Starlette and httpx. The browser suite requires Playwright and Chromium; set `CHROMIUM_PATH` if it is not `/usr/bin/chromium`. It runs against a simulated server, not a live Open WebUI instance. Before production deployment, validate on the exact Open WebUI release and topology in use, with regular-user and shared-chat permissions, mobile layout, Gallery enabled, and your reverse proxy and authentication settings.
