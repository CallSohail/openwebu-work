# EveGPT onboarding v4 for Open WebUI 0.11.3

A production-oriented Open WebUI Event Function that creates a persistent Rich UI onboarding chat for each user. The interface uses a neutral product design, follows the browser/platform light or dark mode by default, and only teaches resources and features that survive Open WebUI's server-side access checks.

## What changed in v4

- Neutral white/charcoal interface with one accessible accent color; no university color palette.
- Responsive left-step navigation inspired by enterprise onboarding flows.
- Automatic platform theme plus optional Auto, Light, and Dark controls inside the guide.
- English/French language selector.
- Detailed tutorials for model selection, reusable prompts (/), skills ($), knowledge (#), Notes, web search, files, tools, Channels, organization, media, voice, and safety.
- Knowledge creation covers Workspace → Knowledge, upload, access control, processing, and model attachment.
- Notes covers creation, rich editing, AI-assisted updates, native Notes tools, pinning, and chat attachment.
- Channels covers @user, @model, #channel, threads, reactions, pins, and files.
- Ready-to-use examples fill the Open WebUI composer through the documented input:prompt Rich UI event; they do not auto-submit.
- Real accessible model, prompt, tool, skill, knowledge, and channel names can be shown.
- Creation instructions are shown only when the corresponding Workspace permission is present.
- An admin privacy switch can teach features without exposing resource names.

## Security model

The browser never decides authorization. The Event Function obtains the current user from the Open WebUI event, loads the effective permission set, and calls Open WebUI's access-filtered catalog APIs. The iframe receives only a minimized snapshot.

| Resource | Included | Explicitly excluded |
|---|---|---|
| User | display name, role label | email, tokens, sessions |
| Models | ID, name, short description, tags, capability labels, public suggestions | system prompt, params, credentials |
| Prompts | ID, name, command, short description, tags | prompt body and variables |
| Skills | ID, name, short description, tags | skill instructions/content |
| Knowledge | ID, name, short description | files, chunks, embeddings, document text |
| Tools | ID, name, short description, connection state | schemas, source, valves, secrets, grants |
| Channels | ID, name, short description for channels already visible to the user | messages, members, files |
| Permissions | selected boolean flags | raw ACL records and group membership |

Additional controls:

- JSON is serialized into a non-executable application/json node and the HTML-sensitive characters are escaped.
- All dynamic text is inserted with textContent; no dynamic innerHTML.
- The Rich UI has a restrictive CSP and loads no remote fonts, scripts, images, or analytics.
- Support links must be HTTPS and cannot contain embedded credentials.
- Existing onboarding chats are refreshed only after verifying that the chat belongs to the same user.
- Prompt example buttons fill the composer but never submit or execute a tool.
- Redis locking prevents duplicate onboarding across replicas. A process-local lock is used only when Redis is unavailable.
- production_enabled defaults to false.

Keep Open WebUI Rich UI same-origin access disabled. This onboarding does not need it and intentionally cannot inspect or manipulate the parent Open WebUI DOM. Because of that security boundary, the tutorial shows exact interface paths rather than drawing an overlay on the live sidebar.

## Install

1. In Open WebUI, open **Admin Panel → Functions**.
2. Create a new **Event Function**.
3. Paste the complete contents of evegpt_onboarding_v4.py.
4. Save and enable the Function.
5. Open its Valves and configure the product, support URLs, language, and optional default group.
6. Leave production_enabled=false.
7. Set test_user to an existing test user's email or ID.
8. Set test_revision=1 and save the Valves. A fresh onboarding chat should be created for only that account.
9. Validate the generated chat in light and dark mode, mobile width, French and English, and with at least two users that have different group permissions.
10. Increase test_revision for each new test run.
11. Set production_enabled=true only after validation.
12. Enable create_on_first_login only if existing users should also receive v4 on their next login.

For multiple Open WebUI replicas, configure the shared Open WebUI Redis connection before enabling production.

## Valves

| Valve | Default | Purpose |
|---|---:|---|
| enabled | true | Master switch for the Event Function. |
| production_enabled | false | Allows signup/user-created/login production processing. |
| create_on_first_login | false | Creates v4 for existing users with no current marker. |
| refresh_on_login | true | Refreshes the existing Rich UI when access changes. |
| refresh_interval_minutes | 360 | Minimum interval between access catalog checks. |
| welcome_title_fr, welcome_title_en | localized defaults | Welcome chat title. |
| preferred_welcome_model_id | empty | Used only when that exact model is accessible. |
| default_group_id | empty | Optional group assigned before catalog calculation. |
| default_language | fr | Initial fr or en; user can switch in the UI. |
| product_name | EveGPT | Product label. |
| organization_name | empty | Optional subtitle; no university branding by default. |
| font_family | Arial | Local Arial stack or platform system stack. |
| support_url, privacy_url, acceptable_use_url, feedback_url | empty | Optional HTTPS-only links. |
| show_models, show_prompts, show_tools, show_skills, show_knowledge | true | Resource tutorial visibility. |
| show_notes_tutorial, show_channels_tutorial, show_sources_tutorial | true | Permission-aware feature tutorial visibility. |
| show_organization_tutorial, show_media_tutorial | true | Permission-aware tutorial groups. |
| show_creation_guides | true | Show creation steps only when Workspace access permits them. |
| expose_resource_names | true | Disable to retain counts/tutorials without listing resource names. |
| show_disabled_features | false | Normally unavailable features are completely omitted. |
| max_items_per_section | 8 | Maximum resource names embedded per section. |
| max_functions_per_model | 6 | Maximum action/filter metadata records per model. |
| max_description_chars | 220 | Maximum public description length. |
| catalog_timeout_seconds | 20 | Independent timeout for each catalog source. |
| test_user, test_revision, test_assign_group | safe defaults | Controlled pre-production test workflow. |

The four legacy v3 color valves remain only so upgrades do not break existing saved Valve payloads. The neutral v4 interface intentionally does not consume them.

## Permission behavior

The guide is assembled for each user after get_permissions and the normal resource access filters run.

- Models are filtered with get_filtered_models.
- Prompts use the user-readable prompt query.
- Tools and Skills use their authenticated router endpoints.
- Knowledge uses the user's accessible knowledge endpoint.
- Channels are loaded through the per-user channel query.
- Notes, web search, files, Channels, memory, folders, calendar, automations, images, code, multi-model, and voice tutorials are conditional on effective permission booleans.
- Knowledge creation is only taught when workspace.knowledge is true.
- A failing catalog source is logged and omitted; it does not expose an unfiltered fallback.

Permissions in Open WebUI are additive across groups. Test a user in each important role/group combination before rollout.

## Verification

From this directory:

~~~bash
python -m py_compile evegpt_onboarding_v4.py
python -m unittest -v test_evegpt_onboarding_v4.py
~~~

The tests cover script-breakout escaping, CSP/self-containment, HTTPS link validation, sensitive metadata exclusion, permission snapshot behavior, the production gate, test-event scoping, persistent embeds, and chat ownership checks.

A full staging test must still run against the exact Open WebUI 0.11.3 deployment because this Function intentionally uses its internal model/router APIs.

## Operational notes

- ONBOARDING_VERSION = 4 creates a distinct marker from v3.
- When a user's catalog hash changes, the existing onboarding message is updated rather than creating a new chat.
- Catalog failures log the source name and exception class without logging resource bodies or credentials.
- The Event Function does not change resource ACLs. The only optional write outside the onboarding chat is default_group_id.
- To avoid accidental privilege changes, leave default_group_id empty and test_assign_group=false unless group provisioning is part of the approved deployment.

## Official references

- [Event Functions](https://docs.openwebui.com/features/extensibility/plugin/functions/event/)
- [Rich UI events](https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/)
- [Role-based access control](https://docs.openwebui.com/features/authentication-access/rbac/)
- [Models](https://docs.openwebui.com/features/workspace/models/)
- [Prompts](https://docs.openwebui.com/features/workspace/prompts/)
- [Skills](https://docs.openwebui.com/features/workspace/skills/)
- [Knowledge](https://docs.openwebui.com/features/workspace/knowledge/)
- [Notes](https://docs.openwebui.com/features/notes/)
- [Channels](https://docs.openwebui.com/features/channels/)
