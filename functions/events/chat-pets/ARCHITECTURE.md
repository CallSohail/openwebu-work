# How Chat Pets works

Chat Pets is built like [Chat Inspector](../chat-inspector/ARCHITECTURE.md): one Python file whose server half only serves a script through the Shared Assets Protocol, and whose browser half is the whole feature. It adds no routes and no database tables. It stores each person's choice in their own Open WebUI settings.

## 1. Server half

`Event._config()` turns the valves into a small JSON object: the allowed pets in catalogue order, the default pet (only if allowed), flags, and the shortcut overrides that `_parse_actions` validated (known pets only, up to 8 shortcuts of 160 characters each). `_loader_fragment()` puts that JSON and the CSS into `LOADER_SCRIPT`, escaping `</`, and caches the result until a valve changes. `register()` publishes it in `/static/loader.js`, which Open WebUI loads on every page.

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

`watchChat()` runs every 700 ms while the tab is visible:

- **Answering** is true while the message box holds a Stop button (matched by `aria-label="Stop"` or by the square in its icon, `h4.874`, so any language works), or while the last `message-…` element's text grew in the last 1.6 s. Changing chat resets the tracking, so opening an old chat is not taken for an answer.
- When answering turns false, the pet hops (`happy`) and, now and then, says a line in character.
- **Typing** in `#chat-input` sets `watch` for 2.4 s.
- **Asleep** after `sleep_after_minutes` without pointer, key, wheel or touch activity. Any activity wakes it with a short hop.

The state is a `data-state` attribute (`idle`, `walk`, `watch`, `think`, `happy`, `greet`, `sleep`), and all movement is CSS: breathing, blinking, tail sway, head tilt and nod, steps, hops, dots, sparkles, z's. `owp-calm` (reduced motion) stops walking and hopping and shows the dots and z's still. `owp-paused` freezes everything while the tab is hidden.

`maybeWalk()` sometimes walks the pet to another spot on either side of the box, at about 42 px a second. It doesn't walk while the person is busy, the menu is open, the pet was dragged in the last minute, or on a phone.

## 5. Drawings

Each animal is a 64 × 64 SVG built from a small template with named parts (`owp-body`, `owp-head`, `owp-eyes`, `owp-pupils`, `owp-tail`, `owp-leg-a`, `owp-leg-b`), so one set of animations fits all six. Templates take only colours and numbers. An own pet with a colour gets a belly and outline mixed from that colour. An own pet with a photo shows the photo in a coloured circle with feet, the photo set by `setAttribute('href', …)`.

## 6. Menu, shortcuts and picker

- **The menu** is a `role="menu"` popover next to the pet, keyboard navigable, closed by Escape or a click elsewhere.
- **A shortcut** puts its text at the end of the message box with `insertText`, which Open WebUI's editor treats like typing. Nothing is submitted. From a page without a message box, it keeps the text in `sessionStorage`, opens a new chat and inserts it there.
- **The picker** is a modal dialog with a focus trap. It has a grid of pets (`role="radio"` cards) and the "Add your own" form. The photo is read with `createObjectURL`, drawn square on a 128 px canvas and exported as WebP (PNG, then JPEG, as fallbacks), staying under 60 KB.

## 7. Where to change things

| To change | Look at |
| --- | --- |
| A valve | `Event.Valves`, `_config()`, then `CONFIG.…` in the script |
| A word or a language | `STRINGS`; a test fails if a key is missing in a language |
| A pet's name, job, lines or shortcuts | `SPECIES` |
| A drawing | `DRAW` |
| Animations | `PETS_CSS`, the `owp-*` keyframes and `[data-state=…]` rules |
| How answers are noticed | `stopButton`, `lastMessage`, `watchChat` |
| What is saved | `sanitize`, `sanitizePet`, `loadState`, `save` |
