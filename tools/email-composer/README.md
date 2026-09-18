# Rich Email Composer

A production-oriented Open WebUI Tool that turns email drafting requests into an interactive Rich UI card inside the chat.

The card is intentionally neutral and portable. It does not contain organization-specific names, domains, logos, fonts, or brand colors.

## What it does

- renders an editable email card directly in chat;
- lets users edit **To**, **Cc**, **Bcc**, **Subject**, and the full email body;
- opens an **Ask for changes** control for AI-assisted revisions;
- shows a contextual formatting toolbar when the user edits or selects body text;
- supports headings, bold, italic, underline, strikethrough, lists, checklists, inline code, and emoji;
- supports light and dark themes;
- validates recipient addresses and supports optional allow/block domain policies;
- copies the draft as rich text plus a plain-text representation;
- exports standards-oriented `.eml` drafts with plain-text and HTML alternatives;
- opens the user's local mail application through `mailto:`;
- falls back to `.eml` when a mailto URL would be too large;
- works on desktop and mobile layouts;
- uses no external JavaScript, CSS, fonts, CDNs, analytics, or remote APIs.

## Important sending behavior

The blue **Send** button does **not** transmit mail from the Open WebUI server.

By default it opens the draft in the user's configured mail application. This keeps the Tool credential-free and prevents a draft UI from silently becoming a mail-sending backend.

If you need actual server-side sending, implement a separate authenticated mail Tool with an explicit confirmation step.

## Installation

1. Open **Workspace > Tools** in Open WebUI.
2. Create a new Tool.
3. Copy the contents of [`email_composer.py`](email_composer.py) into the editor.
4. Save the Tool.
5. Review and configure the Valves.
6. Enable the Tool for the model or chat where email composition should be available.
7. Start a new chat and test with one of the prompts below.

The Tool has no additional Python package requirement beyond packages already used by Open WebUI for Tools (`fastapi` and `pydantic`).

## Portable single-file build

Open WebUI imports Workspace Tools as one Python file. The public `email_composer.py` is therefore shipped as a portable single-file distribution. Its reviewed implementation, including the embedded HTML/CSS/JavaScript UI, is gzip-compressed and Base64-encoded inside the file, then unpacked locally with Python standard-library modules when the Tool loads.

This packaging is **not encryption or obfuscation for secrecy**. It keeps a large Rich UI implementation in one pasteable file and performs no network download.

To inspect the exact readable implementation without executing the Tool:

```bash
python unpack_source.py
sha256sum email_composer_readable.py
cat SOURCE_SHA256
```

The expected source digest is recorded in [`SOURCE_SHA256`](SOURCE_SHA256). `unpack_source.py` parses the distribution file with Python's AST and extracts `_PAYLOAD` without importing or executing the Tool.

## Rich UI rendering

The primary rendering path emits a message-level `embeds` event. This keeps the user-facing card independent from grouped native/reasoning tool-call presentation.

If the execution path does not provide `__event_emitter__`, the Tool falls back to an inline `HTMLResponse`.

The card reports its height back to the host and does not require same-origin iframe access for its core features.

### Recommended iframe posture

Keep iframe same-origin access disabled unless another trusted extension explicitly requires it. The composer does not need same-origin access for normal editing, copy/export, recipient validation, or mail-client handoff.

## User interaction

### Direct editing

The draft fields are editable immediately. Users can click directly into:

- recipients;
- subject;
- body text.

There is no separate "edit mode" required for manual changes.

### AI-assisted editing

The **Edit** control opens an **Ask for changes** input.

Examples:

- `Make the second paragraph shorter.`
- `Make this more formal but keep the same subject.`
- `Add a polite request for confirmation.`

The revision request includes the current on-screen draft, so manual edits made before asking the AI are preserved as context.

If the revision input is empty, the submit control simply closes the revision UI instead of sending an empty prompt.

### Formatting toolbar

When the body is focused or text is selected, a contextual toolbar appears. It supports:

- H1, H2, H3;
- bulleted lists;
- numbered lists;
- checklists;
- bold;
- italic;
- underline;
- strikethrough;
- inline code;
- emoji insertion.

The Tool preserves the active selection while toolbar controls are used, so formatting is applied to the intended text rather than losing selection focus.

## Valves

| Valve | Default | Purpose |
| --- | --- | --- |
| `theme_mode` | `auto` | `auto`, `light`, or `dark` |
| `locale` | `auto` | UI language, currently auto/English/French |
| `show_sender_field` | `false` | Show an editable From field |
| `prefill_sender_from_openwebui_user` | `true` | Prefill From with the signed-in user's valid account email when the field is enabled |
| `allow_cc_bcc` | `true` | Allow Cc and Bcc editing |
| `enable_copy` | `true` | Show Copy |
| `copy_include_headers` | `true` | Include headers in the plain-text clipboard representation |
| `enable_open_in_email` | `true` | Show the mail-client handoff action |
| `enable_send_button` | `true` | Show the blue Send button |
| `enable_eml_download` | `true` | Enable `.eml` download from the More menu |
| `enable_ask_for_changes` | `true` | Enable AI-assisted revision requests |
| `enable_formatting_toolbar` | `true` | Enable the contextual body toolbar |
| `require_valid_to_for_send` | `true` | Require at least one valid To recipient before Send |
| `long_mailto_behavior` | `download_eml` | `download_eml` or `warn` when mailto is too large |
| `max_recipients_per_field` | `25` | Recipient limit for each field |
| `max_subject_chars` | `240` | Subject length limit |
| `max_body_chars` | `60000` | Body length limit |
| `max_revision_request_chars` | `800` | AI revision instruction limit |
| `mailto_soft_limit_chars` | `1800` | Soft threshold before the large-mailto fallback |
| `allowed_recipient_domains` | empty | Optional comma-separated allowlist |
| `blocked_recipient_domains` | empty | Optional comma-separated blocklist |

### Domain policy examples

Allow only selected domains:

```text
example.org, company.com
```

Block selected domains:

```text
blocked.example, disposable.example
```

An empty allowlist means unrestricted unless the address is covered by the blocklist.

## Security design

The Tool is designed to minimize trust in model-generated and browser-edited content.

- Model/tool payloads are Base64-encoded before insertion into the HTML template so arbitrary text cannot terminate the script element.
- Server-side subject cleaning removes control characters and CR/LF header injection opportunities.
- Recipient addresses are normalized and validated before the initial card is created.
- Recipient limits are enforced in the browser and on initial input.
- Email body content is sanitized before copy/export.
- Rich HTML paste is converted to plain text instead of being inserted directly.
- Drop-based HTML injection is blocked.
- Exported HTML uses a restricted element allowlist.
- No remote scripts, styles, fonts, trackers, or network calls are used.
- No mail credentials are stored in source code or Valves.
- The Tool does not directly send email.

## Privacy and data flow

For normal drafting, the Tool does not send the draft to an external service by itself.

The draft can leave the iframe only when the user intentionally performs an action such as:

- asking the AI to revise it, which sends the current draft back into the current Open WebUI chat;
- opening the local mail application through `mailto:`;
- downloading an `.eml` file;
- copying the draft to the clipboard.

Your model/provider configuration may itself send chat content outside your Open WebUI server. That is a deployment-level consideration and is separate from this Tool.

## Error and edge-case handling

The Tool handles or guards against:

- missing recipients;
- malformed recipients;
- duplicate recipients;
- too many recipients;
- optional domain policies;
- empty subject/body values;
- overlong subject/body/revision input;
- long `mailto:` URLs;
- Unicode names and subjects in `.eml` export;
- browser clipboard failures;
- mobile toolbar overflow;
- lost text selection when formatting;
- empty AI-revision submissions;
- grouped tool-call presentation;
- execution paths without an event emitter.

## Test prompts

Basic draft:

```text
Write a short professional email to alex@example.org asking for a project update.
```

Unknown recipient address:

```text
Write an email to Alex asking whether next Tuesday works for a meeting.
```

Expected behavior: the Tool should not invent an email address. The To field should remain empty until the user enters one.

Revision:

```text
Make the email warmer and shorten the second paragraph.
```

Multiple recipients:

```text
Write a project update to alex@example.org and sam@example.org, cc manager@example.org.
```

Formatting:

```text
Draft an email with a short heading and a three-item checklist for the launch tasks.
```

## Validation

From this directory:

```bash
python -m py_compile email_composer.py
pytest -q test_email_composer.py
python unpack_source.py
sha256sum -c SOURCE_SHA256
```

The repository workflow also runs compile and regression checks for changes to this Tool.

## Known limitations

- `mailto:` behavior depends on the browser, operating system, and configured default mail application.
- Rich formatting support after mail-client handoff varies by client. For long or formatted drafts, `.eml` export is the more reliable path.
- Clipboard capabilities vary by browser security context. A plain-text fallback is used when rich clipboard APIs are unavailable.
- The contextual editor uses browser editing primitives for broad compatibility, so small formatting differences can exist between browser engines.
- This Tool is a draft composer, not an authenticated mail-sending system.

## Files

```text
tools/email-composer/
├── README.md
├── CHANGELOG.md
├── SOURCE_SHA256
├── email_composer.py
├── test_email_composer.py
└── unpack_source.py
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Released as `email-composer-v1.0.0`; see [RELEASING.md](../../RELEASING.md) for the release process.

## License

MIT, see the repository-level [`LICENSE`](../../LICENSE).
