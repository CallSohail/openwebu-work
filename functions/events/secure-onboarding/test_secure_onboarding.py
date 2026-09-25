import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


MODULE_PATH = Path(__file__).with_name("secure_onboarding.py")
SPEC = importlib.util.spec_from_file_location("secure_onboarding", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SecureOnboardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = MODULE.Event()

    @staticmethod
    def base_snapshot():
        return {
            "schema": 2,
            "template_revision": MODULE.TEMPLATE_REVISION,
            "guide_revision": 1,
            "update_notes": {"fr": "", "en": ""},
            "generated_at": 1,
            "brand": {
                "product": "Test Assistant",
                "organization": "",
                "primary": "#1F4E79",
                "secondary": "#0EA5B7",
            },
            "progress_scope": "smoke-test",
            "ui": {
                **{flag: True for flag in MODULE.SECTION_FLAGS},
                "default_language": "en",
                "is_admin": False,
            },
            "links": {},
            "localization": {
                "supported": ["fr", "en"],
                "names": {"fr": "Français", "en": "English"},
                "translations": {},
            },
            "selected_model_id": "model-1",
            "available": {
                "models": True,
                "prompts": False,
                "tools": False,
                "knowledge": False,
            },
            "access": {"workspace": {}},
            "resources": {
                "features": [],
                "tools": [],
                "toggles": [],
                "actions": [],
            },
        }

    def test_metadata_and_safe_defaults(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("version: 9.3.0", source)
        self.assertIn("required_open_webui_version: 0.11.3", source)
        self.assertIn("author: CallSohail", source)
        self.assertFalse(self.event.valves.production_enabled)
        self.assertFalse(self.event.valves.deploy_to_all_users)
        self.assertFalse(self.event.valves.recreate_deleted_guides)
        self.assertTrue(self.event.valves.create_on_approval)
        self.assertTrue(self.event.valves.sync_existing_guides_on_valve_change)
        self.assertEqual(self.event.valves.deployment_revision, 0)
        self.assertEqual(self.event.valves.guide_revision, 1)

    def test_html_is_self_contained_and_escapes_script_terminators(self):
        snapshot = self.base_snapshot()
        snapshot["brand"]["product"] = '</script><script>alert("x")</script>'
        rendered = self.event._render_html(snapshot)
        self.assertNotIn('</script><script>alert("x")</script>', rendered)
        self.assertIn("\\u003c/script\\u003e", rendered)
        self.assertIn('id="g"', rendered)
        self.assertIn('id="gBody"', rendered)
        self.assertNotIn("<script src=", rendered)
        self.assertNotIn("fetch(", rendered)
        self.assertNotIn("document.cookie", rendered)
        self.assertIn("default-src 'none'", rendered)
        self.assertIn("prefers-color-scheme:dark", rendered)
        self.assertIn("prefers-reduced-motion:reduce", rendered)
        self.assertIn("type:'iframe:height'", rendered)
        self.assertIn("type:'input:prompt'", rendered)
        self.assertIn('id="gLangSelect"', rendered)
        self.assertNotIn('data-lang=', rendered)
        self.assertTrue(rendered.isascii())
        self.assertIn("Int\\u00e9grations", rendered)

    def test_mojibake_repair_restores_utf8_text(self):
        broken = "Int\u00c3\u00a9grations s\u00e2\u20ac\u2122applique \u00c2\u00b7 guide"
        self.assertEqual(
            self.event._repair_mojibake(broken),
            "Int\u00e9grations s\u2019applique \u00b7 guide",
        )

    def test_render_excludes_private_snapshot_keys(self):
        snapshot = self.base_snapshot()
        snapshot["_enabled"] = ["web_search"]
        rendered = self.event._render_html(snapshot)
        payload = rendered.split('<script type="application/json" id="snapshot">', 1)[1].split("</script>", 1)[0]
        decoded = json.loads(payload)
        self.assertNotIn("_enabled", decoded)

    def test_non_admin_render_strips_admin_tutorial_source(self):
        snapshot = self.base_snapshot()
        regular_html = self.event._render_html(snapshot)
        self.assertNotIn("Administrator area", regular_html)
        self.assertNotIn("Admins only: test a model", regular_html)
        self.assertNotIn("/*__ADMIN_ONLY_START__*/", regular_html)

        snapshot["ui"]["is_admin"] = True
        admin_html = self.event._render_html(snapshot)
        self.assertIn("Administrator area", admin_html)
        self.assertIn("Admins only: test a model", admin_html)
        self.assertNotIn("/*__ADMIN_ONLY_START__*/", admin_html)

    def test_non_admin_locale_payload_excludes_admin_only_messages(self):
        admin_key = MODULE.ADMIN_LOCALE_KEYS[0]
        public_key = "Bonjour\x1fHello"
        translations = {"es": {admin_key: "Administración", public_key: "Hola"}}
        with patch.object(MODULE, "EXTRA_LOCALE_TRANSLATIONS", translations):
            regular = self.event._locale_translations(False)
            admin = self.event._locale_translations(True)
        self.assertNotIn(admin_key, regular["es"])
        self.assertEqual(regular["es"][public_key], "Hola")
        self.assertEqual(admin["es"][admin_key], "Administración")

    def test_only_clean_https_links_are_allowed(self):
        self.assertEqual(self.event._safe_http_url("javascript:alert(1)"), "")
        self.assertEqual(self.event._safe_http_url("http://example.org"), "")
        self.assertEqual(self.event._safe_http_url("https://user:pass@example.org"), "")
        self.assertEqual(
            self.event._safe_http_url("https://support.example.org/help"),
            "https://support.example.org/help",
        )

    def test_color_and_text_sanitizers(self):
        self.assertEqual(self.event._safe_color("#1f4e79", "#000000"), "#1F4E79")
        self.assertEqual(self.event._safe_color("red", "#1F4E79"), "#1F4E79")
        self.assertEqual(self.event._plain("a\x00  b\n c", 20), "a b c")

    def test_feature_permissions_and_global_switches(self):
        config = SimpleNamespace(
            ENABLE_WEB_SEARCH=False,
            ENABLE_NOTES=True,
            ENABLE_CHANNELS=True,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))
        permissions = {
            "features": {"web_search": True, "notes": True, "channels": False},
            "chat": {"file_upload": True},
        }
        keys = {
            item["key"]
            for item in self.event._effective_features(permissions, False, request)
        }
        self.assertNotIn("web_search", keys)
        self.assertIn("notes", keys)
        self.assertNotIn("channels", keys)
        self.assertIn("file_upload", keys)

    def test_admin_feature_access_still_respects_global_switches(self):
        config = SimpleNamespace(ENABLE_WEB_SEARCH=False, ENABLE_NOTES=True)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))
        keys = {
            item["key"]
            for item in self.event._effective_features({}, True, request)
        }
        self.assertNotIn("web_search", keys)
        self.assertIn("notes", keys)

    async def test_snapshot_is_permission_filtered(self):
        permissions = {
            "workspace": {
                "models": True,
                "prompts": False,
                "skills": False,
                "tools": True,
                "knowledge": False,
            },
            "features": {"notes": True, "channels": False, "web_search": True},
            "chat": {"file_upload": True, "stt": False, "tts": True},
        }
        models = [{
            "id": "model-1",
            "name": "Permitted model",
            "filters": [{"id": "study", "name": "Study Mode", "description": "Guided study"}],
            "actions": [{"id": "save", "name": "Save Note", "description": "Save a note"}],
        }]
        tools = [{"id": "tool-1", "name": "Search", "meta": {"description": "Search allowed data"}}]
        self.event._load_permissions = AsyncMock(return_value=permissions)
        self.event._load_models = AsyncMock(return_value=models)
        self.event._load_prompts = AsyncMock(return_value=[])
        self.event._load_tools = AsyncMock(return_value=tools)
        self.event._load_knowledge = AsyncMock(return_value=[])

        config = SimpleNamespace(
            ENABLE_WEB_SEARCH=True,
            ENABLE_NOTES=True,
            ENABLE_CHANNELS=True,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))
        user = SimpleNamespace(
            id="user-1",
            role="user",
            settings={"ui": {"language": "fr-FR"}},
        )
        snapshot = await self.event._build_snapshot(user, None, request)

        self.assertEqual(snapshot["ui"]["default_language"], "fr")
        self.assertFalse(snapshot["ui"]["is_admin"])
        self.assertTrue(snapshot["access"]["workspace"]["models"])
        self.assertFalse(snapshot["access"]["workspace"]["knowledge"])
        self.assertEqual(snapshot["resources"]["tools"][0]["name"], "Search")
        self.assertEqual(snapshot["resources"]["toggles"][0]["name"], "Study Mode")
        self.assertEqual(snapshot["resources"]["actions"][0]["name"], "Save Note")
        enabled = {item["key"] for item in snapshot["resources"]["features"]}
        self.assertIn("notes", enabled)
        self.assertIn("web_search", enabled)
        self.assertIn("file_upload", enabled)
        self.assertIn("tts", enabled)
        self.assertNotIn("channels", enabled)

    async def test_nonproduction_allows_only_test_user(self):
        self.event.valves.production_enabled = False
        self.event.valves.test_user = "test@example.org"
        self.event._resolve_user = AsyncMock(return_value="user-1")
        self.assertTrue(await self.event._allowed_target("user-1"))
        self.assertFalse(await self.event._allowed_target("user-2"))
        self.event.valves.production_enabled = True
        self.assertTrue(await self.event._allowed_target("user-2"))

    async def test_valve_save_refreshes_existing_guides(self):
        self.event.valves.production_enabled = True
        self.event.valves.deployment_revision = 0
        self.event._maybe_run_test = AsyncMock()
        spawned = []
        self.event._spawn = spawned.append

        await self.event.event(
            {"subject": {"type": "function", "id": "secure-onboarding"}},
            __event_name__="function.valves_updated",
            __id__="secure-onboarding",
        )

        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].cr_code.co_name, "_sync_existing_guides")
        spawned[0].close()

    async def test_valve_sync_can_be_disabled(self):
        self.event.valves.production_enabled = True
        self.event.valves.sync_existing_guides_on_valve_change = False
        self.event.valves.deployment_revision = 0
        self.event._maybe_run_test = AsyncMock()
        spawned = []
        self.event._spawn = spawned.append

        await self.event.event(
            {"subject": {"type": "function", "id": "secure-onboarding"}},
            __event_name__="function.valves_updated",
            __id__="secure-onboarding",
        )

        self.assertEqual(spawned, [])

    async def test_role_approval_creates_guide_immediately(self):
        self.event.valves.production_enabled = True
        self.event._allowed_target = AsyncMock(return_value=True)
        self.event._ensure_guide = AsyncMock(return_value="created")

        await self.event.event(
            {
                "subject": {"type": "user", "id": "user-1"},
                "data": {"role": "user"},
                "source": "api",
            },
            __event_name__="user.role_updated",
        )

        self.event._ensure_guide.assert_awaited_once_with(
            "user-1",
            None,
            None,
            source="user.role_updated",
            allow_create=True,
            assign_group=True,
        )

    async def test_pending_role_update_does_not_create_guide(self):
        self.event.valves.production_enabled = True
        self.event._allowed_target = AsyncMock(return_value=True)
        self.event._ensure_guide = AsyncMock(return_value="created")

        await self.event.event(
            {
                "subject": {"type": "user", "id": "user-1"},
                "data": {"role": "pending"},
            },
            __event_name__="user.role_updated",
        )

        self.event._allowed_target.assert_not_awaited()
        self.event._ensure_guide.assert_not_awaited()

    async def test_role_update_without_role_uses_server_side_user_check(self):
        self.event.valves.production_enabled = True
        self.event._allowed_target = AsyncMock(return_value=True)
        self.event._ensure_guide = AsyncMock(return_value="created")

        await self.event.event(
            {"subject": {"type": "user", "id": "user-1"}},
            __event_name__="user.role_updated",
        )

        self.event._ensure_guide.assert_awaited_once()

    def test_marker_validation_requires_owned_chat_ids(self):
        self.assertFalse(self.event._marker_valid(None))
        self.assertFalse(self.event._marker_valid({"version": MODULE.ONBOARDING_VERSION}))
        self.assertTrue(
            self.event._marker_valid({
                "version": MODULE.ONBOARDING_VERSION,
                "chat_id": "chat-1",
                "message_id": "message-1",
            })
        )

    def test_title_uses_language_product_and_emoji(self):
        snapshot = self.base_snapshot()
        snapshot["ui"]["default_language"] = "en"
        self.assertEqual(self.event._title(snapshot), "👋 Welcome to Test Assistant")
        snapshot["ui"]["default_language"] = "fr"
        self.assertEqual(self.event._title(snapshot), "👋 Bienvenue sur Test Assistant")
        snapshot["ui"]["default_language"] = "es"
        self.assertEqual(self.event._title(snapshot), "👋 Welcome to Test Assistant")
        self.assertEqual(self.event._fallback_text(snapshot), MODULE.FALLBACK_TEXT_EN)

    def test_platform_locale_uses_exact_then_base_language(self):
        user = SimpleNamespace(settings={"ui": {"language": "es-ES"}})
        self.assertEqual(self.event._language_for(user), "es")
        user.settings["ui"]["language"] = "ca_ES"
        self.assertEqual(self.event._language_for(user), "ca")
        user.settings["ui"]["language"] = "pt-BR"
        self.assertEqual(self.event._language_for(user), "fr")

    def test_public_resource_metadata_is_limited(self):
        tool = {
            "id": "internal",
            "name": "Permitted Tool",
            "meta": {
                "description": "Public description",
                "manifest": {"secret": "hidden"},
            },
            "valves": {"token": "hidden"},
        }
        public = self.event._named(tool)
        self.assertEqual(public, {
            "name": "Permitted Tool",
            "description": "Public description",
        })
        serialized = json.dumps(public)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("token", serialized)


if __name__ == "__main__":
    unittest.main()
