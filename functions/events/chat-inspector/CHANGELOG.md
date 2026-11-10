# Changelog

## [0.2.0] - 2026-11-10

- Show the server context estimate as a progress bar, with its compaction threshold clearly labeled; fall back to an approximate count from saved messages when server usage is unavailable.
- Keep the chat usable while the inspector is open and refresh its snapshot every 30 seconds while visible.
- Show counts from saved chat metadata when present; explicitly label prompt/tool/skill token breakdown as unavailable.
- Pause polling in hidden tabs, back off on errors and stop when the panel closes.
- Keep the panel open when a new chat becomes saved after its first message.

## [0.1.1] - 2026-11-10

- Keep the inspector visible when Open WebUI replaces or hides the chat header and displays the full-width tab bar.
- Add Chat inspector to the tab bar’s + menu when that menu is available.
- Cover header-to-tab-bar transitions and rerenders in browser tests.

## [0.1.0] - 2026-11-10

- Add an on-demand read-only chat inspector for reported response usage, context-compaction estimates and explicit model capability settings.
- Reuse the Shared Assets Protocol and live reload blocks, with browser and server-side distribution tests.
