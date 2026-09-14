# Secure interactive onboarding for Open WebUI 0.11.3

A privacy-first Event Function that creates a persistent, accessible Rich UI onboarding chat for each user. Product name, organization, chat titles, language, support links, tutorial visibility, and disclosure settings are controlled from Valves.

The source contains no personal name, university name, deployment name, remote font, tracking code, or external UI dependency.

## Interface

- Neutral, brand-independent light and dark design.
- Auto mode follows the browser/platform color preference.
- Large readable typography without requiring browser zoom.
- Full-width, distraction-free tour with no persistent sidebar or table of contents.
- A centered readable content column, compact step counter, collapsible clickable dot strip, and large optional side arrows.
- Bottom Back/Next navigation, Arrow-key and Enter support, plus 200ms direction-aware transitions and reduced-motion support.
- Dynamic content is automatically split into screens of at most three fixed-height cards; long titles and copy are line-clamped without cutting words.
- Topic cards open accessible detail dialogs with interface paths, numbered steps, operational warnings, and ready-to-use examples.
- Skip is always visible, and the final screen provides a concise recap with one primary Start CTA.
- Progress, language, theme, skipped state, and completion state are stored in localStorage when permitted, with window.name and in-memory fallbacks for stricter iframe sandboxes.
- Escape, backdrop click, focus return, focus trapping, and visible keyboard focus.
- English and French switching is available inside the collapsible progress panel.
- Example buttons use input:prompt and never auto-submit.

Rich UI is intentionally kept in the default cross-origin sandbox. It reports iframe height but does not read the Open WebUI parent DOM, cookies, local storage, or session data.

## Privacy defaults

The safe defaults do not display the user's name, role, access counts, prompt titles, knowledge-base titles, or channel titles.

| Data | Default |
|---|---|
| User name | hidden |
| Role label | hidden |
| Resource counts | hidden |
| Model names | visible |
| Tool names | visible |
| Skill names | visible |
| Prompt names and commands | hidden |
| Knowledge names | hidden |
| Channel names | hidden |
| Prompt bodies | never included |
| Skill instructions | never included |
| Knowledge documents/chunks | never included |
| Channel messages/members | never included |
| Tool schemas/source/valves/secrets | never included |
| Model system prompts/parameters | never included |
| Email, tokens, sessions | never included |

A user can only receive catalog entries returned by Open WebUI's server-side access filters. The iframe cannot expand access and never calls Open WebUI APIs.

## Tutorials

Each tutorial requires both its Valve and the user's effective permission or accessible catalog item.

- Welcome and privacy model
- Model selection and exact model IDs
- Strong prompting and / $ # + selectors
- Prompts
- Skills
- Knowledge use
- Knowledge creation, upload, ACLs, processing, and model attachment
- Notes creation, Rich Text/Markdown, AI chat, Insert, native note tools, updates, pinning, drag-and-drop, and Attach Notes
- File upload
- Web search and current information
- Tools/integrations and safe OAuth
- Channels, @user, @model, #channel, threads, reactions, pins, and files
- Folders
- Memory
- Calendar
- Automations
- Image generation
- Code Interpreter
- STT, TTS, and Call
- Multiple-model comparison
- Safety, privacy, verification, human confirmation, support, and policies

## Install and controlled rollout

1. Open **Admin Panel → Functions**.
2. Create an **Event Function**.
3. Paste the complete contents of secure_onboarding.py.
4. Save and enable it.
5. Configure product_name, organization_name, titles, language, and HTTPS support links.
6. Keep production_enabled=false.
7. Set test_user to an existing test account email or ID.
8. Increase test_revision and save.
9. Review the new test chat in light/dark mode and at desktop/mobile widths.
10. Test at least one user from every important role/group combination.
11. Verify hidden and exposed catalog configurations.
12. Set production_enabled=true only after staging validation.
13. Enable create_on_first_login only if existing users should receive onboarding.

Use shared Redis before production on multiple Open WebUI replicas.

## Identity and content Valves

| Valve | Default | Purpose |
|---|---:|---|
| product_name | AI Assistant | Product label shown in the header. |
| organization_name | empty | Optional organization subtitle. |
| welcome_title_fr / welcome_title_en | Bienvenue / Welcome | Welcome-chat title. |
| default_language | fr | Initial language. |
| font_family | Arial | Arial or platform System stack; no remote font. |
| show_user_name | false | Opt-in display of the current user's name. |
| show_role_badge | false | Opt-in role label. |
| show_access_counts | false | Opt-in catalog counts. |
| expose_model_names | true | Show accessible model names and IDs. |
| expose_prompt_names | false | Opt-in prompt names and commands, never bodies. |
| expose_tool_names | true | Show tool public metadata, never schemas or secrets. |
| expose_skill_names | true | Show skill public metadata, never instructions. |
| expose_knowledge_names | false | Opt-in knowledge names, never documents. |
| expose_channel_names | false | Opt-in channel names, never messages or members. |
| max_items_per_section | 8 | Maximum public entries per catalog section. |
| max_functions_per_model | 6 | Maximum action/filter labels per model. |
| max_description_chars | 220 | Maximum public-description length. |

## Independent tutorial Valves

Every row defaults to true. Turning one off removes that tutorial even when the user has permission.

| Valve | Controls |
|---|---|
| show_welcome | Welcome/privacy section |
| show_models | Model selection and model cards |
| show_chat_basics | Prompt-writing and selector basics |
| show_prompts | Prompt guide and permitted prompt cards |
| show_skills | Skill guide and permitted skill cards |
| show_knowledge | Knowledge guide and permitted knowledge cards |
| show_knowledge_creation | Creation instructions; also requires workspace.knowledge |
| show_notes | Notes tutorials |
| show_web_search | Web Search tutorial |
| show_file_upload | File upload tutorial |
| show_tools | Tools tutorial and permitted tool cards |
| show_channels | Channels tutorial and permitted channel cards |
| show_folders | Folders tutorial |
| show_memory | Memory tutorial |
| show_calendar | Calendar tutorial |
| show_automations | Automations tutorial |
| show_image_generation | Image generation tutorial |
| show_code_interpreter | Code Interpreter tutorial |
| show_voice | STT/TTS/Call tutorial |
| show_multiple_models | Multi-model tutorial |
| show_safety | Verification, privacy, support and policies |

If every section is disabled or unavailable, the UI displays a controlled configuration message instead of failing.

## Operational Valves

| Valve | Default | Purpose |
|---|---:|---|
| enabled | true | Event Function master switch. |
| production_enabled | false | Production safety gate. |
| create_on_first_login | false | Onboard existing accounts without a current marker. |
| refresh_on_login | true | Refresh an owned onboarding message when the catalog changes. |
| refresh_interval_minutes | 360 | Minimum refresh interval. |
| preferred_welcome_model_id | empty | Used only if present in the user's filtered model list. |
| default_group_id | empty | Optional provisioning; leave empty unless approved. |
| support_url / privacy_url / acceptable_use_url / feedback_url | empty | HTTPS-only links. |
| catalog_timeout_seconds | 20 | Timeout per catalog source. |
| test_user / test_revision / test_assign_group | safe defaults | Isolated staging workflow. |

## Security and failure behavior

- The Function uses get_permissions and Open WebUI's filtered catalog methods.
- Each source has an independent timeout and fail-closed empty fallback.
- A failed source never falls back to an administrator-wide unfiltered inventory.
- Dynamic text is normalized and length-limited.
- Snapshot JSON escapes HTML parser control characters.
- Dynamic values use textContent, not innerHTML.
- The iframe has a restrictive Content Security Policy and no network access.
- Links must be HTTPS, contain a host, and contain no embedded credentials.
- The onboarding chat is refreshed only after a same-user ownership query.
- Production processing is disabled by default.
- Redis SET NX locking and token-checked release prevent replica duplicates.
- No example is automatically submitted and no tool action is automatically approved.
- Client parse errors and all-sections-disabled configurations have safe visible fallbacks.
- The tour never forces completion: Skip collapses it, while Resume restores the saved step when browser storage permits.
- ResizeObserver is feature-detected and reduced-motion preferences are respected.

## Verification

From this folder:

~~~bash
python -m py_compile secure_onboarding.py
python -m unittest -v test_secure_onboarding.py
npm install --no-save --ignore-scripts jsdom@24
node smoke_secure_onboarding.mjs
~~~

The automated checks cover Python and embedded-JavaScript syntax, an actual DOM rendering smoke test, visible navigation/cards/dialogs, identity removal, privacy defaults, opt-in catalog disclosure, script-breakout escaping, self-contained Rich UI, link validation, sensitive metadata exclusion, permission snapshots, production gating, function-event scoping, persistent embeds, and chat ownership.

A final staging run is required against the exact Open WebUI 0.11.3 deployment because the Function deliberately uses internal catalog and chat APIs.

## Official documentation

- [Event Functions](https://docs.openwebui.com/features/extensibility/plugin/functions/event/)
- [Rich UI events and iframe security](https://docs.openwebui.com/features/extensibility/plugin/development/rich-ui/)
- [Role-based access control](https://docs.openwebui.com/features/authentication-access/rbac/)
- [Models](https://docs.openwebui.com/features/workspace/models/)
- [Prompts](https://docs.openwebui.com/features/workspace/prompts/)
- [Skills](https://docs.openwebui.com/features/workspace/skills/)
- [Knowledge](https://docs.openwebui.com/features/workspace/knowledge/)
- [Notes](https://docs.openwebui.com/features/notes/)
- [Channels](https://docs.openwebui.com/features/channels/)
