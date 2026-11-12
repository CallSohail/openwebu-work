# Chat Files Changelog

All notable changes to the Chat Files Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-files-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

## [2.0.0] - 2026-11-12

A redesign, in Open WebUI's own greys, with the Files tab chosen from the Tabs panel's **+** menu.

### Added

- **List and grid.** A list with Name, Kind, Size and Date columns, whose titles order it; or a grid of cards. Each person's choice is kept in their browser; `default_view` sets the first one.
- **Pictures of images** in both, when images are listed (`thumbnails`, PNG, JPEG, GIF, WebP and similar up to 8 MB).
- **Kind chips** with how many files each kind has, in place of the kind drop-down.
- **A menu on every file** (⋯, a right click, Shift+F10 or the menu key): open, download, attach to the message, show in chat, copy name, rename, delete.
- **More details:** media type, extension, exact size in bytes, when it last changed, who sent it in the chat, the file ID with a copy button, and what the preview found inside: lines, words and characters of a text, rows and columns of a table, the size of a picture, the length of a recording.
- **Keyboard:** Space picks a file, Shift-click or Shift+Space picks a range, Ctrl+A (⌘A) picks everything, Delete deletes, `/` goes to the search, arrows move in two directions in the grid.
- *My files* is grouped by date: Today, Yesterday, the previous 7 and 30 days, then by month.
- The footer adds up what is picked: how many and how large.
- The delete box names the files about to be deleted.
- `button_beside_menu`, `default_view` and `thumbnails` valves.

### Changed

- Where the tab row has a **+** menu, Files is chosen from it and no longer adds a button of its own beside it. The button stays where there is no such menu: Open WebUI's classic top bar, phones, and accounts without the Tabs panel (`header_button`; `button_beside_menu` brings it back beside the menu).
- Files follows the language chosen in Open WebUI and redraws in it the moment it changes, keeping its place.
- The tab, the chat's list, *My files*, Select mode, long jobs (zip, delete), the layout and the preview are explicit state machines, each shown on `<html>` as `data-cf-…`. A late answer, a second click or a page from an older list can no longer leave the tab, a zip or a preview half in one state and half in another.
- Escape in the search box clears it, then goes to the list, instead of setting Files aside.
- Dialogs are centred over a darker backdrop, as Open WebUI's are; focus rings follow the theme's text colour instead of blue.

### Security

- Direction-control characters (U+202A–U+202E, U+2066–U+2069, U+200E, U+200F, U+061C) are removed from every name, so a file called `invoice\u202Efdp.exe` is shown, searched, renamed, downloaded and zipped as `invoicefdp.exe`, not disguised as `invoiceexe.pdf`.
- Rename also refuses names made only of dots and the DEL character.
- A zip is refused above 65,535 files, the most a zip without its 64-bit form can hold.

## [1.0.0] - 2026-11-10

### Added

- A **Files** tab beside the chat, with two views: *This chat*, with every file attached on every branch, the files the model's answers link to, and the knowledge bases, notes, chats and web pages the chat refers to; and *My files*, with every file you own, 200 at a time.
- Previews: PDF in the browser's own viewer; CSV and TSV as a table, including quoted fields, line breaks inside quotes and semicolon-separated files; JSON pretty-printed; text and code; audio and video; images when they are listed; and, for Word, PowerPoint and similar files, the text Open WebUI extracted.
- Search by name, type or extension; a kind filter; ordering by date, name or size; a footer with the file count, total size and references.
- Rename, with the extension left out of the selection and names with slashes refused. Delete, one file or a selection, after a confirmation.
- Download one file, or a selection or the whole view as a zip; a second file with the same name becomes `name (2).ext`. Copy a file's text. Attach a file to the next message. Show in chat. Open a PDF in a new tab.
- Status flags for files Open WebUI is still processing, failed to process, or no longer has, with the reason a file failed.
- A Files button in the chat's tab row or top bar, and a Files item in the Tabs panel's + menu. On a screen with no tab row, the tab opens over the chat.
- Works beside Chat Gallery: the two take turns in front, their tabs and buttons sit in a fixed order, and images are left to Chat Gallery when it is enabled.
- English and French. Keyboard use: arrow keys, Home and End through the list, Enter to preview, arrows between previews, Escape to go back.
- Seven administrator Valves.

### Security

- The sign-in token goes only to addresses the browser resolves to the Open WebUI site.
- File names, file contents and extracted text are written to the page as text, never as HTML. HTML and SVG files are shown as source.
- Only files that start like a PDF reach the PDF viewer; an HTML page named `.pdf` is not opened.
- Web pages a chat refers to open in a new tab without a referrer or a link back to Open WebUI.

### Compatibility

- Open WebUI 0.13.0 or newer. Tested in a browser on 0.13.0 and 0.13.3, with and without Chat Gallery 1.1.0.
