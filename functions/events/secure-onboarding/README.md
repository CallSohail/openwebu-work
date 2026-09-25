# Secure Dynamic Onboarding Rich UI

Version **9.2.1**, for Open WebUI **0.11.3 or newer**.

A multilingual, role-aware Event Function that creates a persistent onboarding chat for every eligible user. The chat contains an interactive Rich UI guide that mirrors Open WebUI, explains only permitted features, and updates in place when permissions or guide content change.

## What it provides

The guide has two connected experiences:

- **Guided tour**, a chapter-by-chapter walkthrough with clickable interface mockups.
- **Features library**, a visual catalog of features available to the current user, with paths, instructions, examples, and safety notes.

The interface supports:

- French and English, with contributor-ready JSON catalogs for more languages;
- automatic locale selection from the user’s Open WebUI language setting;
- light and dark themes through the platform preference;
- Left and Right keyboard navigation;
- desktop, tablet, and mobile layouts;
- reduced-motion preferences;
- saved progress and resume through local storage;
- a close state that users can reopen;
- “Try it in the chat” actions that fill the composer but never submit automatically.

## Tutorial coverage

The tour is assembled dynamically. Depending on permissions and enabled Valves, it can explain:

- the home screen, suggested questions, and clear prompts;
- every composer control;
- the + menu and nested attachment selectors;
- Tool Permissions, including Full access and Ask for approval;
- Integrations, Tools, toggle functions, Web Search, and Code Interpreter;
- assistant/model selection and model information;
- response actions, including organization-provided Action Functions;
- positive and negative feedback;
- sidebar navigation and chat history;
- the user menu;
- Notes, the editor, sharing, and the note actions menu;
- Folders and folder creation;
- Channels, mentions, threads, reactions, and pinned messages;
- Calendar and event creation;
- Automations, schedules, Run now, pause, and execution logs;
- built-in assistant tools;
- administrator-only areas;
- responsible use and verification.

The Features tab can also cover uploads, prompts, knowledge, memory, voice, image generation, multiple-model comparison, status, Workspace sections, Playground, and Admin Panel.

## Permission model

The function never treats a configured feature as proof that a user can access it.

For each user it combines:

1. effective Open WebUI permissions from **get_permissions**;
2. the user-filtered model catalog;
3. the user-filtered Tool catalog;
4. accessible prompts and knowledge bases;
5. global instance feature switches;
6. the current account role;
7. the administrator’s section Valves.

If any catalog source fails or times out, that source becomes empty and its related tutorial is hidden.

Administrators can see the Administration chapter. Regular users cannot. Globally disabled features remain hidden even for administrators.

The server also removes administrator-only tutorial definitions and their translated strings from regular-user output. Changing `is_admin` in browser DevTools cannot restore content that was never sent, and it never grants a platform permission.

## Privacy behavior

The browser payload contains only the minimum public metadata needed to build the guide.

Included when enabled:

- public Tool names and short descriptions for Tools the user can access;
- public filter/function names exposed by accessible models;
- public response Action names exposed by accessible models;
- booleans describing effective feature access.

Never included:

- Tool schemas, source code, Valves, or secrets;
- prompt bodies;
- knowledge documents or chunks;
- private user messages;
- passwords, tokens, cookies, or sessions;
- resources the user cannot access.

The embedded iframe:

- loads no third-party scripts, fonts, images, or CDNs;
- uses a restrictive Content Security Policy;
- performs no network request;
- renders dynamic text with DOM text nodes;
- serializes snapshot data as inert JSON;
- escapes parser-sensitive characters before embedding JSON;
- posts only iframe height and optional composer prompt events to its parent.

## Files

| File | Purpose |
| --- | --- |
| [secure_onboarding.py](secure_onboarding.py) | Complete Event Function and embedded Rich UI |
| [locales/en.json](locales/en.json) | English source catalog and translation template |
| [locales/fr.json](locales/fr.json) | French translation catalog |
| [locales/es.json](locales/es.json) | Spanish translation catalog, contributed by [delfireinoso](https://github.com/delfireinoso) |
| [locales/ca.json](locales/ca.json) | Catalan translation catalog, contributed by [delfireinoso](https://github.com/delfireinoso) |
| [locales/README.md](locales/README.md) | Translation contribution guide |
| [sync_locales.py](sync_locales.py) | Catalog validator and single-file embedding tool |
| [test_secure_onboarding.py](test_secure_onboarding.py) | Python security, permission, and snapshot tests |
| [smoke_secure_onboarding.mjs](smoke_secure_onboarding.mjs) | Browser DOM test for the full interactive guide |
| [README.md](README.md) | Installation, rollout, and operating guide |

## Installation

1. Sign in to Open WebUI as an administrator.
2. Open **Admin Panel > Functions**.
3. Create a new **Event Function**.
4. Paste the complete contents of **secure_onboarding.py**.
5. Save the Function.
6. Keep **production_enabled** set to false.
7. Configure the branding and test-account Valves.
8. Enable the Function.
9. Run the test rollout described below.
10. Enable production only after checking every important role and group.

The implementation uses internal Open WebUI chat, user, permission, Tool, prompt, model, and knowledge APIs. Validate it against your exact Open WebUI 0.11.3 deployment before production.

## Safe test rollout

Use one real non-production account first.

| Valve | Test value |
| --- | --- |
| **enabled** | true |
| **production_enabled** | false |
| **test_user** | Test user email or user ID |
| **test_revision** | Increase by 1 |
| **test_assign_group** | Usually false |
| **deploy_to_all_users** | false |
| **deployment_revision** | 0 |

Save the Valves. A **function.valves_updated** event rebuilds the guide for the test account. Increasing **test_revision** updates the existing test guide instead of creating repeated chats.

Check every embedded language, light and dark modes, desktop and mobile widths, normal and restricted users, important groups, administrators, hidden features, composer menus, feature workspaces, and the close/reopen behavior.

## Production delivery

### New users

Set **production_enabled = true** and **create_on_signup = true**.

The guide is created for auth.signup and user.created events. When **skip_pending_users** is enabled, pending users are skipped until they become active.

### Pending users after approval

Keep **create_on_approval = true**. When Open WebUI changes an account role from **pending** to **user** or **admin**, the **user.role_updated** event creates the missing guide immediately. This covers approval from the admin panel and role changes from API, SCIM, OAuth, or trusted headers. The login handler remains a fallback.

### Existing users at next login

Set **production_enabled = true** and **create_on_first_login = true**.

Users without a valid guide receive it when they next sign in.

### Immediate deployment

To update existing guides only:

1. Keep **deploy_to_all_users = false**.
2. Increase **deployment_revision**.
3. Save the Valves.

To also create missing guides for all eligible users:

1. Set **deploy_to_all_users = true**.
2. Increase **deployment_revision**.
3. Save the Valves.

Deployment runs in batches. Each user and revision is tracked, so repeated saves and restarts do not create duplicate work.

## Updating guide content

When changing text, links, branding, or enabled sections:

1. Increase **guide_revision**.
2. Optionally set **update_notes_fr** and **update_notes_en**.
3. Save the Valves.
4. Wait for login refresh, or increase **deployment_revision** for an immediate update.

The existing assistant message and Rich UI embed are updated in place. A new chat is not created.

**refresh_on_login** also refreshes a guide when the permission/catalog snapshot changes. **refresh_interval_minutes** controls routine checks. A changed guide or template revision bypasses the interval.

## Deleted guides

Deleting the welcome chat is respected by default:

- **recreate_deleted_guides = false**, the guide stays deleted.
- **recreate_deleted_guides = true**, login or deployment can create it again.

Use recreation only when your organization has decided the guide must return.

## Delivery Valves

| Valve | Default | Purpose |
| --- | ---: | --- |
| **enabled** | true | Master switch |
| **production_enabled** | false | Allows real-user signup, login, and deployment processing |
| **create_on_signup** | true | Creates the guide for new accounts |
| **create_on_first_login** | true | Creates missing guides for existing users at login |
| **create_on_approval** | true | Creates a missing guide immediately after a pending account is approved |
| **skip_pending_users** | true | Skips accounts that cannot use the platform yet |
| **include_admins** | true | Includes administrators and their admin chapter |
| **deploy_to_all_users** | false | Allows immediate deployment to create missing guides |
| **deployment_revision** | 0 | Increase to launch an idempotent deployment |
| **deployment_batch_size** | 50 | Users processed before a short pause |
| **deployment_batch_delay_seconds** | 0.5 | Pause between deployment batches |
| **recreate_deleted_guides** | false | Controls whether deleted guides can return |
| **guide_revision** | 1 | Content version for in-place updates |
| **refresh_on_login** | true | Refreshes changed guides and access catalogs |
| **refresh_interval_minutes** | 60 | Minimum interval for routine login checks |

## Welcome chat Valves

| Valve | Default | Purpose |
| --- | ---: | --- |
| **title_emoji** | 👋 | Optional chat-title icon |
| **welcome_title_en** | {emoji} Welcome to {product} | English title template |
| **welcome_title_fr** | {emoji} Bienvenue sur {product} | French title template |
| **update_title_on_refresh** | true | Applies the current title during an update |
| **pin_welcome_chat** | true | Pins only when the guide is first created |
| **default_language** | fr | Fallback language |
| **use_user_interface_language** | true | Uses the user’s Open WebUI locale when that catalog is embedded |
| **preferred_welcome_model_id** | empty | Used only when the user can access that model |
| **default_group_id** | empty | Optional group for brand-new users only |

## Branding and links

| Valve | Default | Purpose |
| --- | ---: | --- |
| **product_name** | AI Assistant | Public service name |
| **organization_name** | empty | Optional organization label |
| **primary_color** | #1F4E79 | Header and primary actions |
| **secondary_color** | #0EA5B7 | Highlights, progress, and dark-mode actions |
| **support_url** | empty | HTTPS support link |
| **privacy_url** | empty | HTTPS privacy link |
| **acceptable_use_url** | empty | HTTPS acceptable-use link |
| **feedback_url** | empty | HTTPS guide-feedback link |

Only clean HTTPS URLs without embedded credentials are included.

## Tutorial section Valves

Every section is independently switchable. A section still stays hidden when the user lacks access.

| Group | Valves |
| --- | --- |
| Entry | show_cover, show_welcome, show_suggestions |
| Composer | show_composer, show_plus_menu, show_tool_permissions |
| Integrations | show_integrations, show_tools, show_web_search |
| Assistants | show_models, show_multiple_models |
| Responses | show_response_actions, show_feedback |
| Navigation | show_sidebar_navigation, show_user_menu |
| Work areas | show_notes, show_folders, show_channels |
| Scheduling | show_calendar, show_automations |
| Capabilities | show_builtin_tools, show_file_upload, show_knowledge, show_prompts, show_memory, show_image_generation, show_code_interpreter, show_voice |
| Administration | show_admin_section |
| Safety | show_safety |
| Interface | show_features_tab, show_language_switch, show_try_buttons |

## Disclosure and limits

| Valve | Default | Purpose |
| --- | ---: | --- |
| **expose_tool_names** | true | Shows public names/descriptions of permitted Tools |
| **expose_function_names** | true | Shows public names/descriptions of permitted filters and Actions |
| **max_items_per_section** | 8 | Maximum exposed items per menu |
| **catalog_timeout_seconds** | 20 | Timeout per access-catalog source |

Turning off the exposure Valves keeps the general tutorials but removes organization-specific names.

## Adding a translation

Translations live in [`locales/`](locales/). To add Spanish, Catalan, or another language:

1. Copy `locales/en.json` to the locale code, for example `locales/es.json`.
2. Update `_meta.code`, `_meta.name`, and `_meta.native_name`.
3. Translate only the values inside `messages`.
4. Run `python sync_locales.py` to validate and embed the locale into the self-contained Function.
5. Run `python sync_locales.py --check` and the test suite.

The guide first tries the full Open WebUI locale, then its base language. For example, `es-ES` uses `es` when `es.json` is embedded. Unsupported locales fall back to `default_language`. See [`locales/README.md`](locales/README.md) for the complete contributor workflow.

## Idempotency and multi-replica behavior

A marker in user settings records the guide chat ID, message ID, version, catalog hash, and refresh data. It prevents duplicate guides.

Redis is used when available for per-user locks, deployment locks, and protection across multiple Open WebUI replicas.

If Redis is unavailable, the function reconnects once, then falls back to a process-local lock. Use shared Redis for multi-replica production.

## Verification

From this directory:

~~~bash
python -m py_compile secure_onboarding.py
python sync_locales.py --check
python -m unittest -v test_secure_onboarding.py
npm install --no-save --ignore-scripts jsdom@24
node smoke_secure_onboarding.mjs
npm uninstall --no-save --ignore-scripts jsdom
~~~

The Python suite covers metadata, safe defaults, CSP and script-breakout protection, private snapshot fields, HTTPS links, sanitization, RBAC and global switches, server-side administrator-content stripping, permission-filtered catalogs, test-user gating, marker validation, localization, and public metadata limits.

The DOM test renders the full administrator tour, walks every step, switches language, opens and closes a feature tutorial, and verifies the dismissed state.

## Troubleshooting

### No guide appears

Check that the Function is enabled, production targeting is correct, the account is eligible, the relevant delivery Valve is enabled, and the test account resolves. Review Open WebUI logs for **openwebui.secure_onboarding**.

### A feature is missing

The feature must be globally enabled, allowed by the user’s role/group permissions, enabled by its tutorial Valve, and present in the filtered catalog when it depends on a Tool, prompt, model, or knowledge base.

### Existing guides are not changing

Increase **guide_revision**. For immediate rollout, also increase **deployment_revision**.

### Multiple server replicas

Configure shared Redis. Process-local fallback cannot coordinate separate processes.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). The current release is `secure-onboarding-v9.2.1`; see [RELEASING.md](../../../RELEASING.md) for the release process.

## License

Released under the repository’s [MIT License](../../../LICENSE).
