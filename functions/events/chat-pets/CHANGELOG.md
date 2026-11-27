# Chat Pets Changelog

All notable changes to Chat Pets are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-11-27

### Added

- **Follows the AI.** The pet listens (read-only) to Open WebUI's live socket and acts out each step of an answer, with a badge: web search, documents and knowledge, notes, memory, tasks, calendar, automations, code, images, sub-agents and channels, skills, other tools (tool servers, MCP, Function tools), reasoning, a tool asking you something, context compaction. 16 new animations: sniff, read, write, type, paint, ponder, listen, wait, worried, puzzled, celebrate, alarm, offline, shrug, squish.
- **Problems in plain words.** Failed tools, errors (rate limit, context too long, access refused, model not found, timeout, network, or Open WebUI's message), error and warning toasts, stopped and empty answers, lost connection (the pet greys out until it is back).
- **Notices.** Automation started, done or failed; calendar reminders; a file that couldn't be read; a note or memory saved; an image ready; an answer ready in another chat, with a link to it. Uploads and voice input get their own reactions.
- **Pet settings** dialog: nickname, size, place, how often it talks (often, normal, rarely, never), wander, nap, animations, follow the AI, report problems, notices.
- **Who gets a pet:** `access_mode` (`everyone` or `selected`), `allowed_roles`, `allowed_groups` (sub-groups count), `allowed_users`, `blocked_users` and `custom_pets_for`. Lists reach the browser only as salted SHA-256 hashes; server notices are checked on the server.
- Admin valves `react_to_activity`, `react_to_errors` and `notices`.
- `tests/fake_model.py`: a model that streams, reasons, calls built-in tools and fails on demand.
- 52 unit tests and 129 browser checks against a real Open WebUI 0.13.5.

### Changed

- The menu now holds the shortcuts, Pet settings, Change pet, Hide for an hour and Remove; the toggles moved to the settings dialog.
- Saved preferences gained new fields; 1.0 saves are read and upgraded.
- Answers are tracked per chat from socket events; the 1.0 page watcher remains as a fallback.

### Security

- The socket and microphone hooks only listen: they never send, click, or change a frame or a message.
- Server notices go only to the `user:<id>` room of the person concerned, carry only a type, a 60-character title, a chat UUID and minutes, and are skipped for people not allowed a pet. A failure in the relay never affects Open WebUI.

## [1.0.0] - 2026-11-27

First version.

### Added

- Six pets with a job and their own lines: Plume the owl (mail), Noisette the squirrel (notes and memory), Filou the sheepdog (tasks), Roux the fox (research), Minuit the cat (calendar) and Sage the turtle (study).
- **Choose your pet:** a picker that invites people without a pet, and **Add your own** with a name, an animal, a colour, a job and an optional photo, shrunk to 128 px in the browser.
- The pet sits on the message box and moves with it, or in the corner where there is none. It can be dragged, or moved with the arrow keys.
- States: idle (breathing, blinking), watching while you type, thinking while the model answers, a hop when the answer is ready, wandering, and asleep after inactivity.
- A menu with four shortcuts per pet that fill the message box without sending, plus change pet, speech bubbles, animations, place, hide for an hour and remove.
- A daily greeting, and the odd remark when an answer is ready.
- The choice is saved in the account (`owui_pets`) and in the browser, follows the person to other devices, syncs between tabs, and retries a failed save when back online.
- Admin valves for the allowed pets, own pets and photos, a default pet, the invitation, the paw, bubbles and their frequency, sleep delay, shortcuts and shortcut overrides, default place, phones and language.
- French and English.
- 33 unit tests and 85 browser checks against a real Open WebUI 0.13.5.
