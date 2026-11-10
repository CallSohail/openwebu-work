# Chat Files Changelog

All notable changes to the Chat Files Event Function are documented here.
This project follows [Semantic Versioning](https://semver.org/).

Released as `chat-files-v<version>`. See [RELEASING.md](../../../RELEASING.md) for the release process.

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
