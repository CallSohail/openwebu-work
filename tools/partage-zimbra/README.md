# Partage (Zimbra) for Open WebUI

An Open WebUI Tool that lets the model work in a person's Zimbra mailbox and calendar: Partage (Renater) at Université d'Évry Paris-Saclay, or any Zimbra 8.8+ server with the SOAP API on. Each person connects with their own Partage address and password. The tool asks the person in the browser before it sends anything, deletes anything, or changes their calendar or settings.

Tested against Open WebUI 0.13.5. It needs Open WebUI 0.13.0 or later, and only `requests` and `pydantic`, which Open WebUI already ships.

## Install

1. **Workspace → Tools → +**, paste `partage.py`, save.
2. Open the tool's **Valves** (admin). Check `zimbra_server` and set the rest to match your policy (see below).
3. Turn the tool on for the models that should have it. **Native** function calling works best: it lets the model chain calls, for example `search_contacts` then `compose_email`.
4. Each user opens the tool's **User Valves** (the tool icon under the message box, or **Settings → Tools**), enters their Partage address and password, and saves. The password field is masked.
5. Ask: "Teste ma connexion Partage."

## What people can ask

| Area | Tools | Confirmation |
| --- | --- | --- |
| Read | `list_emails`, `search_emails`, `get_email`, `get_conversation`, `read_attachment` | — |
| Analyse | `count_emails`, `mailbox_analytics` | — |
| Write | `compose_email`, `reply_to_email`, `forward_email`, `send_draft` | always (drafts excepted) |
| Organise | `delete_emails`, `move_emails`, `mark_emails`, `tag_emails`, `manage_folder`, `empty_folder` | deletes always; others above `bulk_confirmation_threshold` |
| Calendar | `list_calendars`, `get_calendar_events`, `get_event_details`, `check_availability`, `find_meeting_slots` | — |
| Calendar changes | `create_calendar_event`, `update_calendar_event`, `cancel_calendar_event`, `respond_to_invitation` | always |
| Account | `list_folders`, `get_mailbox_info`, `list_identities`, `search_contacts`, `get_out_of_office`, `set_out_of_office`, `test_connection`, `help` | out-of-office changes |

Some things it handles that version 9 did not:

- **Threads and attachments.** It reads whole conversations, extracts text from PDF, DOCX, CSV, HTML and plain-text attachments, and forwards a message with its attachments.
- **Contacts.** It looks names up in the person's contacts and in the university directory (GAL), so "write to Rémy Boyer" goes to a real address instead of a guessed one.
- **Calendar.** It covers recurring events (`weekly:MO,WE`, `weekdays`, monthly...), all-day events, reminders, optional attendees, private events and shared calendars. It can change or cancel an event, or one occurrence of a series, and accept, decline or tentatively accept invitations. Conflicts show in the confirmation dialog. `find_meeting_slots` finds times when everyone is free.
- **Analytics.** Volume, unread rate, reply rate, top senders and domains, busiest days and hours, and flagged messages still waiting for a reply.
- **Organising.** Folders by name or path (`Projets/2026`), tags, spam, restoring from the trash, and the out-of-office message.
- **Dates in French and English.** "demain 14h", "mardi prochain", "semaine dernière", "du 1 au 15 décembre", "14 prochains jours", "next week".

## Security model

| Risk | What the tool does |
| --- | --- |
| Credentials leak | The password lives only in the user's own User Valves, in a masked field. It goes only into `AuthRequest`, and is never logged or shown in errors. Tokens stay in memory, keyed by an HMAC with a per-process secret, and expire with Zimbra's token lifetime or `token_cache_hours`. |
| Account lockout | After `account.AUTH_FAILED` the tool makes no new login attempt for `auth_failure_cooldown_seconds` (default 90 s), however often the model retries. |
| Model acts without consent | Sending, deleting, calendar changes, out-of-office changes and large bulk edits open Open WebUI's classic confirmation modal. Only a literal `true` proceeds. A closed tab, a timeout, an API call with no browser, or any other answer counts as "no". The modal is sent without `tool_call`, so Open WebUI's tool auto-approval cannot skip it. Permanent deletion and emptying the trash ask the person to type `SUPPRIMER`. |
| Prompt injection in emails | Bodies, attachments and event descriptions come back between `<<<DEBUT ... non fiables>>>` markers, and the tool's instructions tell the model not to follow them. The confirmation dialog shows the real recipients, and the body is quoted so it cannot pose as dialog lines. |
| Duplicate sends | `SendMsg`, `CreateAppointment`, `ModifyAppointment`, `CancelAppointment` and `SendInviteReply` are never retried once they may have reached the server. The person is told the outcome is unknown and is asked to check before trying again. Read requests retry with backoff and respect `Retry-After`. |
| Header injection | Subjects and names are stripped of CR/LF and control characters. Addresses are parsed and validated, and can be limited to `allowed_recipient_domains`. |
| Server and transport | HTTPS only unless `allow_insecure_http` is on. TLS is verified, with an optional `ca_bundle_path`. Redirects are not followed, and URLs carrying credentials or query strings are rejected. Attachment downloads are capped at `max_attachment_mb`. |
| Admin control | `enable_send`, `enable_delete`, `enable_calendar_write`, `enable_settings_write` and `enable_attachment_reading` switch whole capabilities off. `allowed_login_domains` restricts who can connect. |

## Admin Valves

| Valve | Default | Purpose |
| --- | --- | --- |
| `zimbra_server` | `https://partage.univ-evry.fr` | Server base URL |
| `default_login_domain` | empty | Domain added to a bare username (`jdupont` → `jdupont@univ-evry.fr`) |
| `allowed_login_domains` | empty | Comma list of login domains allowed to connect |
| `verify_tls`, `ca_bundle_path`, `allow_insecure_http` | on, empty, off | Transport |
| `connect_timeout`, `request_timeout`, `max_retries`, `retry_backoff_seconds` | 10, 60, 3, 0.75 | Network behaviour |
| `token_cache_hours`, `auth_failure_cooldown_seconds` | 8, 90 | Sessions |
| `enable_*` | all on | Capability switches |
| `require_confirmation_for_send` / `_delete` / `_events` | on | Confirmation dialogs. Permanent deletes always confirm. |
| `typed_confirmation_for_permanent` | on | Type `SUPPRIMER` for irreversible actions |
| `bulk_confirmation_threshold` | 20 | Moving, marking or tagging more emails than this asks first |
| `max_bulk_operations`, `max_scan_for_stats` | 1000, 3000 | Limits for bulk actions and statistics |
| `max_recipients_per_message`, `allowed_recipient_domains` | 100, empty | Sending policy |
| `max_body_chars`, `max_attachment_mb` | 12000, 10 | How much text goes to the model |
| `include_shared_calendars`, `work_day_start`, `work_day_end` | on, 08:30, 18:00 | Calendar |
| `mark_as_read_on_open` | off | Whether `get_email` marks the message read |

User Valves: `zimbra_email`, `zimbra_password`, `zimbra_username` (only if the login differs from the address), `timezone` (IANA, default `Europe/Paris`), `email_signature`.

## How it talks to Zimbra

Everything goes through the JSON form of the SOAP API at `/service/soap`. Authentication uses `AuthRequest`, and later requests carry the token in the SOAP header, with `nosession` so no server-side session is held open. Attachments come from the REST content servlet, `/service/home/~/?id=…&part=…`, with the same token as a cookie. Searches send the `en_US` locale, so `after:mm/dd/yyyy` means the same thing on every account whatever language it uses. Calendar searches expand recurring events with `calExpandInstStart` and `calExpandInstEnd`.

| Feature | Zimbra requests |
| --- | --- |
| Mail | `SearchRequest`, `GetMsgRequest`, `GetConvRequest`, `SendMsgRequest`, `SaveDraftRequest`, `MsgActionRequest` |
| Folders and tags | `GetFolderRequest`, `CreateFolderRequest`, `FolderActionRequest`, `GetTagRequest`, `CreateTagRequest` |
| Calendar | `GetAppointmentRequest`, `CreateAppointmentRequest`, `ModifyAppointmentRequest`, `CancelAppointmentRequest`, `SendInviteReplyRequest`, `GetFreeBusyRequest` |
| Account | `GetInfoRequest`, `GetIdentitiesRequest`, `GetPrefsRequest`, `ModifyPrefsRequest`, `AutoCompleteRequest`, `SearchGalRequest` |

## Known limits

- Accounts that only allow SSO or two-factor login cannot use password authentication. Zimbra refuses them, and the tool says why.
- Emails are sent as plain text. Sending a draft re-sends its text part, so formatting written in the webmail is lost.
- `update_calendar_event` changes the whole series of a recurring event. `cancel_calendar_event` can cancel a single occurrence.
- Zimbra time zone names vary by server version. The tool sends the user's IANA zone (`Europe/Paris`), which Partage accepts. Another server might need a different `timezone` value.

## Tests

```bash
pytest -q tools/partage-zimbra/test_partage.py
```

The tests run against an in-memory fake Zimbra that records every request. They check what each request contains, that confirmations are enforced, the retry and no-retry rules, the auth cooldown, and the parsing of dates and search queries.
