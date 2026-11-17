# Mods Changelog

All notable changes to the Mods Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-mods-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.0.0] - 2026-11-17

First release.

### Added

- **Mods**: small features people add to Open WebUI themselves. A mod is a short text (name, description, icon, version, permissions, then JavaScript) that runs in its own sandboxed frame and reaches Open WebUI only through a `mod` object, call by call, within the permissions it was granted.
- **12 permissions**: read or write the message box, read or send in the chat, a label, a bar, a panel, styles, `/` commands, buttons under answers, asking a model, saving files.
- **8 ready-made mods**, in English and French: Word Counter, Prompt Shortcuts, Focus Mode, Pomodoro Timer, Bookmarks, Readability, Export to Markdown, Warm Reading Theme.
- **Create**: describe a feature, choose a model, see the mod it writes with its permissions and code, try it without saving, ask for changes, install it. Paste or import a mod, and export one as a `.mod` file.
- **`/mod`** in the message box: `/mod <description>` opens Create and starts writing the mod; `/mod` alone opens Create. Mods can't take `/mod` for themselves.
- **A permission dialog** before anything is installed, tried or given more access, with reading and sending in the chat flagged.
- **Publishing**: administrators publish a mod to everyone, and can turn it on for everyone; it is installed once per person and stays removed for those who remove it.
- **The Mods window** from a chat's ⋯ menu, the tab row's + menu, or Ctrl+Shift+. (⌘⇧.), in light and dark mode, full screen on phones, keyboard-friendly.
- **Safe mode**: `?mods=off` runs nothing in that tab.
- **Protections**: a content policy with no network, messages checked by frame, key and permission, frames that navigate away stopped, crash loops switched off, styles that could load anything or hide Mods refused, rates and sizes capped, every stored mod validated on the server.
- Valves: `enabled`, `allow_user_mods`, `allowed_permissions`, `max_mods_per_user`, `ai_calls_per_minute`, `studio_model`, `show_starters`, `show_in_chat_menu`, `show_in_tab_menu`, `language`, `catalog`.
