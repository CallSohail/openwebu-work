# Chat Gallery Changelog

All notable changes to the Chat Gallery Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-gallery-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [1.1.0] - 2026-11-10

A maintained fork of Chat Gallery 1.0.0 by [@G30](https://openwebui.com/u/g30).

### Added

- **My images**: the second view now lists every image you uploaded or generated, in any chat. It replaces All generated, which was empty on a server without image generation. The `library` valve switches back to generated images only.
- A Gallery button in the chat's tab row (or in the top bar on small screens), so people whose account has no + menu can open the gallery. The `header_button` valve removes it.
- On a screen with no Tabs panel open, such as a phone, the gallery opens over the chat with its own close button.
- A kind filter (generated, uploaded, in replies and linked), a newest or oldest first button, and a Refresh button. Returning to the Gallery tab also picks up images the chat gained meanwhile.
- French interface, chosen from each person's Open WebUI language. The `language` valve forces one language.
- You can delete your own uploaded images as well as generated ones. The `allow_delete` valve turns deleting off.
- The `load_linked_images` valve keeps the gallery from loading images hosted on other sites.
- Download can be cancelled while a zip is being prepared.
- Retry buttons where loading failed, and a file name in the details of an uploaded image.

### Changed

- The grid shows small pictures made in the browser instead of the full files, four at a time and only for tiles near the screen. The `thumbnail_px` valve sets their size; 0 restores the old behaviour.
- Searching, filtering and loading another page move the existing tiles instead of rebuilding the grid, and search waits for a pause in typing.
- Requests to Open WebUI time out after 30 seconds (60 for image files) and a failed read is tried once more.
- Deleting several images runs four requests at a time.
- Closing the gallery releases its pictures and cached records.
- The Gallery tab follows the tab padding Open WebUI 0.13.2 introduced.
- The live reload block is updated to Classic298's version 2.

### Fixed

- Switching chats with the viewer open left the previous chat's image on screen.

### Security

- The sign-in token is now sent only to addresses the browser resolves to this site. 1.0.0 treated any address that began with a single slash as local, so an image address such as `/\other-site.example/a.png` in a chat message, which browsers resolve to `other-site.example`, received the token when the gallery opened, copied, downloaded or zipped that image.
- Only `http`, `https` and inline `data:image` addresses are collected from a chat.
- A linked image opened in a new tab no longer passes a referrer.

### Compatibility

- Open WebUI 0.13.0 or newer. Tested in a browser on 0.13.0 and 0.13.3.

### Upgrade notes

- Paste the new `chat_gallery.py` over the old function and hard-refresh open tabs. The saved `max_zip_mb` value is kept; the new valves start at their defaults.
- If you want exactly the 1.0.0 scope, set `library` to `generated` and `header_button` off.

## [1.0.0]

The original release by @G30: the Gallery tab in the Tabs panel, This chat and All generated views, search, the viewer with zoom and generation details read from PNG files, copy, download, zip, attach to message, show in chat, and delete for generated images.
