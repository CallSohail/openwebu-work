# Partage (Zimbra) Changelog

All notable changes to the Partage (Zimbra) Tool are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [11.0.0] - 2026-11-26

Adds tasks, mail rules, rooms, sharing, richer sending and visual cards. Request shapes were checked against Zimbra's own client (`@zimbra/api-client` 104).

### Added

- Tasks: `list_tasks` (open, overdue, done), `create_task`, `update_task` (including completing a task), `delete_task`.
- Mail rules: `list_mail_filters`, `create_mail_filter` (move, tag, flag, mark read, discard, forward), `manage_mail_filter` (enable, disable, delete), optional apply to the inbox; `manage_sender_lists` for blocked and allowed senders.
- Rooms: `find_rooms` with capacity and availability; `create_calendar_event(room=...)` books the room.
- Sharing: `list_shares`, `mount_shared_folder`, and a `mailbox` parameter for reading delegated mailboxes in mail, calendar, tasks and analytics.
- Sending: files attached in the chat as email attachments (`attach_chat_files`), send-later (`send_at`), priority, read receipts, and the Partage default signature.
- People: `list_contacts`, `manage_contact` (with duplicate detection), `get_list_members`.
- Visual cards (Open WebUI Rich UI embeds) for email lists, a single email, analytics, agenda, free slots, tasks, quota, and a help card with what's new. Admin valve `enable_visual_cards` and user valve `visual_cards` switch them off, returning plain text.
- Valves: `enable_visual_cards`, `enable_delegated_mailboxes`, `enable_filter_forwarding`, `use_zimbra_signature`, `max_parallel_requests_per_user`, `duplicate_send_window_minutes`, `max_upload_mb`; user valve `visual_cards`.
- 17 new tests (91 in total), and Chromium screenshots of every card in light and dark mode.

### Changed

- Parallel calls from one person share a login and are capped per account.
- HTTP connections are pooled per worker thread, and the pooled sessions never store cookies.
- `help` lists every area with clickable examples and the version's new features.

### Security

- An identical email already being sent is refused, and a recent identical send is flagged in the confirmation.
- Chat files are attached only if the caller owns them, and uploads are size-capped.
- Forwarding filters are off by default (exfiltration risk) and follow `allowed_recipient_domains`. Discard filters carry an explicit warning.
- Cards use a `default-src 'none'` Content-Security-Policy and write all data with `textContent`. Clicks only pre-fill the message box.
- Filter rule changes are serialised per account, so concurrent edits cannot drop a rule.

## [10.0.0] - 2026-11-26

A rewrite of the 9.0 tool for Open WebUI 0.13, checked against the 0.13.5 source.

### Added

- Email: `list_emails` (any folder, paging), `get_conversation`, `read_attachment` (text, CSV, HTML, PDF, DOCX), `send_draft`, drafts through `save_as_draft`, forwarding with the original attachments, `tag_emails`, `manage_folder` (create, rename, move, delete, mark read), spam / not spam, search sorting and paging.
- Analytics: `mailbox_analytics` (volume, unread and reply rates, top senders and domains, busiest days and hours, flagged emails without a reply). `count_emails` now counts unread, flagged and attachments across all pages.
- Contacts: `search_contacts`, over personal contacts and the university directory (GAL).
- Calendar: `list_calendars`, `get_event_details`, `update_calendar_event`, `cancel_calendar_event` (a series or one occurrence), `respond_to_invitation`, `find_meeting_slots`. Event creation adds all-day, recurrence, reminders, optional attendees, privacy, free/busy display, a target calendar, and a conflict check shown in the confirmation.
- Settings: `get_out_of_office` and `set_out_of_office`.
- Admin Valves for capability switches, login and recipient domain allow-lists, bulk thresholds, size limits, a CA bundle and working hours.
- French and English date parsing: weekdays, "prochain" / "dernier", ranges, "N derniers jours", month names, weekends.
- 74 regression tests against a fake Zimbra.

### Changed

- Confirmations only proceed on a literal `true` from Open WebUI's classic modal. Strings such as "yes" are no longer accepted. The modal is sent without `tool_call`, so tool auto-approval cannot skip it. Permanent deletion and emptying a folder ask the person to type `SUPPRIMER`.
- Times are shown in the user's time zone. Version 9 used the server's clock, which is often UTC.
- Searches send the `en_US` locale, so `mm/dd/yyyy` dates mean the same on French-language accounts.
- The organiser address, reply-all filtering and "you" in availability results now use the account's real address from `GetInfoRequest`, even when the login is a bare username.
- A reply is sent from the identity the original message was addressed to, and keeps the thread (`In-Reply-To`).
- Folders resolve by name, path or ID, and ambiguous names are reported.
- Quota now comes from `zimbraMailQuota`. Version 9 always showed it as unlimited.

### Fixed

- The SOAP header no longer sends the login as `account by name`, which failed for bare usernames.
- The free-text search builder no longer cuts words apart. Version 9 turned "Leclerc" into "clerc".
- An unknown period now gets a clear error. Version 9 silently fell back to the next 7 days.
- An end time given alone ("16h") lands on the start day. "Demain 11h" now counts from today, not from the start.

### Security

- Logins stop for a configurable time after `account.AUTH_FAILED`, to protect the university account from lockout.
- The token cache key is an HMAC with a per-process secret.
- Redirects are not followed. Attachment downloads are capped in size.
- Email bodies, attachments and event descriptions are wrapped in untrusted-content markers. Bodies shown in confirmation dialogs are quoted.
- `SendMsg`, `CreateAppointment`, `ModifyAppointment`, `CancelAppointment` and `SendInviteReply` are never retried once they may have reached the server.
