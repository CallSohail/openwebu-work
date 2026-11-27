# How Chat Pets works

Chat Pets is built like [Chat Inspector](../chat-inspector/ARCHITECTURE.md): one Python file whose server half only serves a script through the Shared Assets Protocol, and whose browser half is the whole feature. It adds no routes and no database tables. It stores each person's choice in their own Open WebUI settings. Since 2.0 the server half also forwards a few server events to the person they concern (section 8).

## 1. Server half

`Event._config()` turns the valves into a small JSON object: the allowed pets in catalogue order, the default pet (only if allowed), flags, the access rules (lists hashed, section 9), and the shortcut overrides that `_parse_actions` validated (known pets only, up to 8 shortcuts of 160 characters each). `_loader_fragment()` puts that JSON and the CSS into `LOADER_SCRIPT`, escaping `</`, and caches the result until a valve changes. `register()` publishes it in `/static/loader.js`, which Open WebUI loads on every page.

## 2. Boot

The script runs once per page (`window.__owuiChatPets`). Without a sign-in token it does nothing. With one, it asks `/api/v1/auths/` who is signed in, then reads two copies of the saved state:

- the browser's, under `owui-pets:<user id>`;
- the account's, `owui_pets` in `GET /api/v1/users/user/settings?raw=true`.

The newer one (by `updatedAt`) wins. If the browser's copy is newer, it is pushed to the account. Every change is written to the browser at once and to the account 600 ms later, through `POST /api/v1/users/user/settings/update` with only `{owui_pets: …}`. Open WebUI patches settings field by field and its own saves only touch `ui`, so neither side overwrites the other. A failed save is retried on the next change or when the browser comes back online. A `storage` event updates other tabs.

`sanitize()` checks every state it reads: the pet must be an allowed animal, or an allowed own pet with a name, a `#rrggbb` colour and, if photos are allowed, a `data:image/(png|webp|jpeg)` URL under 70 KB. Anything else is dropped.

## 3. Where the pet sits

`layout()` runs every 400 ms while the tab is visible, and on resize. It finds `#message-input-container`:

- **On the box:** the pet's feet sit 4 px into the top border. Its position along the box is a ratio from 0 to 1 (`prefs.x`), so it keeps its place when the box changes width. Positions within 70 px of the middle are pushed aside.
- **In the corner:** there is no visible box (Channels, settings pages), or the person chose the corner.

The pet is hidden on `/auth`, shared chats (`/s/…`), error pages, in full screen, while napping (`sessionStorage`, one hour) and, if the admin chose, below 640 px. When there is no pet, the paw takes its place.

The pet is placed with `transform` (smooth walking). The bubble, menu and paw are placed with `left` and `top`, because their own animations use `transform`.

## 4. What the pet does

### Listening to Open WebUI

`loader.js` is a deferred script in the page head, so it runs before Open WebUI opens its Socket.IO connection. `hookSocket()` wraps `window.WebSocket`: for sockets to the same host's `/ws/socket.io`, it adds a `message` listener and nothing else. It never sends, never changes a frame and leaves every other socket alone. `handleFrame()` parses `42["events", {chat_id, message_id, data: {type, data}}]` and `42["owui-pets", …]` frames, inside a `try`.

| Event | What the pet does |
| --- | --- |
| `chat:active` true / false | an answer starts / ends |
| `response:completion` → `response.output_item.added` with a `function_call` | `classify(name, tool_type)` against `TOOL_RULES` gives an animation, a badge and a label; one activity per call ID |
| `…output_item.done` with `status: failed`, or a `function_call_output` holding `{"error": …}` | puzzled, "the tool didn't work" |
| `reasoning` items, `reasoning_text.delta` | ponders |
| `output_text.delta`, `chat:completion` | thinking; `done` ends the answer |
| `chat:message:error`, a completion with `error` | `onError()` → `explain()` |
| `status` | `done: false` with an action shows the activity; an error status is a warning |
| `source` / `citation`, `chat:message:files`, `chat:message:tasks` | pleased about sources, images, tasks done |
| `request:user_input` | waits, "needs you" |
| `open_webui:context_compaction` | ponders |
| `chat:title` | remembers the title for background notices |

**Which chat is ours.** Frames arrive for every chat of the person, in every tab. `ours(chatId)` accepts a frame only for the chat in the address bar. On a new chat, which has no ID yet, it adopts the first chat that starts within 15 s of pressing Send in this tab (`markSent`), and drops that on any route change. An answer that ends in another chat gives the "ready in another chat" notice with a link, once per tab that is visible.

**Page signals** fill the gaps: Open WebUI's toasts (`[data-sonner-toaster]`, read through a `MutationObserver`; error and warning toasts only), file inputs, drops and pastes on the message box, `getUserMedia` for the microphone (`hookMic`, which only wraps the returned stream's `stop`), `offline` / `online`, and clicks on the Stop button. If no socket frame has been seen, the 1.0 DOM watcher (`watchDom`: Stop button and growing text) still drives thinking and done.

### Reactions

`acts` is a map of running activities (a tool call, reasoning, listening, an upload); the newest one sets the animation and the badge. `react(anim, icon, tone, ms)` is a short reaction on top (done, failed, error, notice) that always wins while it lasts, even over walking. `show()` writes `data-state` on the pet and `data-tone` on the badge. Errors and the offline state end every activity.

States: `idle`, `walk`, `watch`, `think`, `happy`, `greet`, `sleep`, and since 2.0 `sniff`, `read`, `write`, `type`, `paint`, `ponder`, `listen`, `wait`, `worried`, `puzzled`, `celebrate`, `alarm`, `offline`, `shrug`, `squish`. All movement is CSS. `owp-calm` (reduced motion, or the person's setting) keeps the badges and dots and stops the motion. `owp-paused` freezes everything while the tab is hidden.

**Bubbles.** `say(text, {tone, force, go, ms})`: spontaneous lines respect the person's frequency and `bubble_interval_minutes`; errors and notices (`force`) don't, but still respect "never". `go` adds a link that navigates inside Open WebUI (same-site paths only).

**Asleep** after `sleep_after_minutes` without pointer, key, wheel or touch activity, unless an answer or activity is running. `maybeWalk()` walks at about 42 px a second, never while busy, with the menu open, just after a drag, or on a phone.

## 5. Drawings

Each animal is a 64 × 64 SVG built from a small template with named parts (`owp-body`, `owp-head`, `owp-eyes`, `owp-pupils`, `owp-tail`, `owp-leg-a`, `owp-leg-b`), so one set of animations fits all six. Templates take only colours and numbers. An own pet with a colour gets a belly and outline mixed from that colour. An own pet with a photo shows the photo in a coloured circle with feet, the photo set by `setAttribute('href', …)`.

## 6. Menu, shortcuts and picker

- **The menu** is a `role="menu"` popover next to the pet, keyboard navigable, closed by Escape or a click elsewhere: the shortcuts, then Pet settings, Change pet, Hide for an hour, Remove.
- **Pet settings** is a modal dialog built by `openModal()` (focus trap, Escape, focus returned). Every control saves at once into `prefs`, which `sanitize()` checks like the rest: `size`, `place`, `bubbles`, `walk`, `sleep`, `motion`, `activity`, `errors`, `notices`, `nickname` (24 characters, text only).
- **A shortcut** puts its text at the end of the message box with `insertText`, which Open WebUI's editor treats like typing. Nothing is submitted. From a page without a message box, it keeps the text in `sessionStorage`, opens a new chat and inserts it there.
- **The picker** is a modal dialog with a focus trap. It has a grid of pets (`role="radio"` cards) and the "Add your own" form. The photo is read with `createObjectURL`, drawn square on a 128 px canvas and exported as WebP (PNG, then JPEG, as fallbacks), staying under 60 KB.

## 7. Server notices

`Event.event()` sees every event Open WebUI dispatches to Event Functions. `_relay()` keeps only `RELAYED_EVENTS` (automation started, completed, failed; calendar alert; an upload that couldn't be processed; a note or a memory created; an image or video generated) whose notice kind is in the `notices` valve. It finds the person (`actor.id`, or `data.user_id` for calendar alerts), checks `_allowed()`, then emits a small payload with `sio.emit('owui-pets', …, room=f'user:{id}')`: the event type, a title cut to 60 characters, a chat ID only if it is a UUID, and the minutes before a calendar event. Nothing else from the event leaves the server. The whole relay is wrapped so that a failure is logged and never reaches Open WebUI.

## 8. Who gets a pet

The access valves become `access: {mode, roles, groups, users, blocked, custom}` in the config. Group and person entries are lower-cased, trimmed and hashed: `sha256("owui-pets:v2:" + value)`, the same in Python (`_hash_id`) and in the browser (`sha256`, written in plain JavaScript so it also works on plain-HTTP sites without `crypto.subtle`). So the page holds no list of names or emails.

- **In the browser,** `accessOf(me, groups)` asks `/api/v1/users/groups?include_inherited=true` (only when groups matter), and decides `{pets, custom}`: blocked by ID, email, group ID or group name → no; `everyone` → yes; `selected` → role, group or person listed. Not allowed: the script tears down and shows nothing. This is a convenience, not a lock: someone can always edit their own page.
- **On the server,** `_allowed(user_id)` applies the same rules with `Users.get_user_by_id` and `Groups.get_groups_by_member_id(…, include_inherited=True)`, caching each answer for 60 s (5,000 people at most). It is what guards the notices, the only data the pet sends.

## 9. Where to change things


| To change | Look at |
| --- | --- |
| A valve | `Event.Valves`, `_config()`, then `CONFIG.…` in the script |
| A word or a language | `STRINGS`; a test fails if a key is missing in a language |
| A pet's name, job, lines or shortcuts | `SPECIES` |
| A drawing | `DRAW` |
| Animations | `PETS_CSS`, the `owp-*` keyframes and `[data-state=…]` rules |
| How answers and tools are noticed | `handleFrame`, `onEvent`, `onResponse`, `ours`; the DOM fallback `watchDom` |
| Which tool gives which animation and badge | `TOOL_RULES`, `classify`, `EMOTE` |
| Error wording | `explain`, the `e_*` strings |
| Server notices | `RELAYED_EVENTS`, `_payload`, `_relay`, `onRelay` |
| Access | `_allowed`, `_hash_id`; `accessOf`, `idHash` in the script |
| What is saved | `sanitize`, `sanitizePet`, `loadState`, `save` |
| The settings dialog | `openSettings`, the `s_*` strings |
