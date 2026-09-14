import asyncio
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def load_module():
    path = Path(__file__).with_name("secure_onboarding.py")
    spec = importlib.util.spec_from_file_location("secure_onboarding", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


class FakeChatForm:
    def __init__(self, chat):
        self.chat = chat


class SecureOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.event = MODULE.Event()

    @staticmethod
    def base_snapshot():
        return {
            "schema": 1,
            "generated_at": 1,
            "generated_at_label": {"fr": "01/01/2026", "en": "2026-01-01"},
            "user": {
                "name": "",
                "role": "",
                "role_label": {"fr": "", "en": ""},
            },
            "brand": {"product": "AI Assistant", "organization": ""},
            "ui": {
                "default_language": "en",
                "show_welcome": True,
                "show_access_counts": False,
                "font": "Arial, Helvetica, sans-serif",
                "show_models": True,
                "show_chat_basics": True,
                "show_prompts": True,
                "show_skills": True,
                "show_knowledge": True,
                "show_knowledge_creation": True,
                "show_notes": True,
                "show_web_search": True,
                "show_file_upload": True,
                "show_tools": True,
                "show_channels": True,
                "show_folders": True,
                "show_memory": True,
                "show_calendar": True,
                "show_automations": True,
                "show_image_generation": True,
                "show_code_interpreter": True,
                "show_voice": True,
                "show_multiple_models": True,
                "show_safety": True,
            },
            "links": {
                "support_url": "",
                "privacy_url": "",
                "acceptable_use_url": "",
                "feedback_url": "",
            },
            "selected_model_id": "m1",
            "counts": {},
            "available": {
                "models": True,
                "prompts": False,
                "tools": False,
                "skills": False,
                "knowledge": False,
                "channels": False,
            },
            "access": {"workspace": {}, "chat": {}, "features": {}},
            "resources": {
                "models": [{
                    "id": "m1",
                    "name": "Model",
                    "description": "",
                    "tags": [],
                    "capabilities": [],
                    "examples": [],
                    "actions": [],
                    "filters": [],
                }],
                "prompts": [],
                "tools": [],
                "skills": [],
                "knowledge": [],
                "channels": [],
                "features": [],
            },
        }

    def test_source_uses_generic_configurable_identity(self):
        source = Path(MODULE.__file__).read_text(encoding="utf-8")
        self.assertIn("title: Secure Dynamic Onboarding Rich UI", source)
        self.assertIn("author: Open WebUI administrator", source)
        self.assertIn('product_name: str = Field("AI Assistant"', source)
        self.assertIn('organization_name: str = Field(\n            "",', source)

    def test_html_is_self_contained_and_escapes_script_terminators(self):
        snapshot = self.base_snapshot()
        snapshot["user"]["name"] = '</script><script>alert("x")</script>'
        rendered = self.event._render_html(snapshot)
        self.assertNotIn('</script><script>alert("x")</script>', rendered)
        self.assertIn("\\u003c/script\\u003e", rendered)
        self.assertNotIn("<script src=", rendered)
        self.assertNotIn("fetch(", rendered)
        self.assertNotIn("document.cookie", rendered)
        self.assertIn("type:'iframe:height'", rendered)
        self.assertIn("type:'input:prompt'", rendered)
        self.assertIn('role="dialog"', rendered)
        self.assertIn("prefers-color-scheme:dark", rendered)

    def test_only_clean_https_links_are_allowed(self):
        self.assertEqual(self.event._safe_http_url("javascript:alert(1)"), "")
        self.assertEqual(self.event._safe_http_url("http://example.org"), "")
        self.assertEqual(self.event._safe_http_url("https://user:pass@example.org"), "")
        self.assertEqual(
            self.event._safe_http_url("https://support.example.org/help"),
            "https://support.example.org/help",
        )

    def test_public_metadata_excludes_sensitive_fields(self):
        model = {
            "id": "m1",
            "name": "Safe",
            "info": {
                "params": {"system": "MODEL_SECRET"},
                "meta": {
                    "description": "Public",
                    "capabilities": {"vision": True},
                },
            },
            "actions": [{
                "id": "a",
                "name": "Action",
                "description": "Allowed",
                "valves": {"token": "ACTION_SECRET"},
            }],
        }
        prompt = SimpleNamespace(
            id="p1",
            name="Prompt",
            command="/safe",
            content="PROMPT_SECRET",
            meta={"description": "Allowed"},
        )
        skill = {
            "id": "s1",
            "name": "Skill",
            "description": "Allowed",
            "content": "SKILL_SECRET",
        }
        knowledge = {
            "id": "k1",
            "name": "KB",
            "description": "Allowed",
            "documents": ["DOCUMENT_SECRET"],
        }
        channel = {
            "id": "c1",
            "name": "Channel",
            "description": "Allowed",
            "messages": ["MESSAGE_SECRET"],
            "members": ["MEMBER_SECRET"],
        }
        raw = json.dumps({
            "model": self.event._model_public(model),
            "prompt": self.event._prompt_public(prompt),
            "skill": self.event._skill_public(skill),
            "knowledge": self.event._knowledge_public(knowledge),
            "channel": self.event._channel_public(channel),
        })
        for forbidden in (
            "MODEL_SECRET",
            "ACTION_SECRET",
            "PROMPT_SECRET",
            "SKILL_SECRET",
            "DOCUMENT_SECRET",
            "MESSAGE_SECRET",
            "MEMBER_SECRET",
            "params",
            "valves",
        ):
            self.assertNotIn(forbidden, raw)

    @staticmethod
    async def build_snapshot(event):
        event._load_permissions = lambda user: asyncio.sleep(
            0,
            result={
                "workspace": {"knowledge": True},
                "chat": {"file_upload": True},
                "features": {"notes": True, "channels": True},
            },
        )
        event._load_models = lambda request, user: asyncio.sleep(
            0, result=[{"id": "allowed", "name": "Allowed"}]
        )
        event._load_prompts = lambda user: asyncio.sleep(
            0,
            result=[SimpleNamespace(
                id="p1",
                name="Sensitive project prompt",
                command="/project",
                meta={"description": "Private title"},
            )],
        )
        event._load_tools = lambda request, user: asyncio.sleep(0, result=[])
        event._load_skills = lambda request, user: asyncio.sleep(0, result=[])
        event._load_knowledge = lambda user: asyncio.sleep(
            0,
            result=[{"id": "k1", "name": "Sensitive knowledge title"}],
        )
        event._load_channels = lambda user: asyncio.sleep(
            0,
            result=[{"id": "c1", "name": "Sensitive channel title"}],
        )
        return await event._build_snapshot(
            SimpleNamespace(id="u", name="Personal Name", role="user"),
            object(),
            SimpleNamespace(app=object()),
        )

    def test_privacy_sensitive_identity_and_catalogs_are_opt_in(self):
        snapshot = asyncio.run(self.build_snapshot(MODULE.Event()))
        self.assertEqual(snapshot["user"]["name"], "")
        self.assertEqual(snapshot["user"]["role"], "")
        self.assertEqual(snapshot["counts"], {})
        self.assertEqual(snapshot["resources"]["prompts"], [])
        self.assertEqual(snapshot["resources"]["knowledge"], [])
        self.assertEqual(snapshot["resources"]["channels"], [])
        self.assertEqual(snapshot["resources"]["models"][0]["id"], "allowed")
        self.assertTrue(snapshot["available"]["prompts"])
        self.assertTrue(snapshot["available"]["knowledge"])
        self.assertTrue(snapshot["available"]["channels"])

    def test_identity_and_sensitive_names_can_be_enabled_by_admin(self):
        event = MODULE.Event()
        event.valves.show_user_name = True
        event.valves.show_role_badge = True
        event.valves.show_access_counts = True
        event.valves.expose_prompt_names = True
        event.valves.expose_knowledge_names = True
        event.valves.expose_channel_names = True
        snapshot = asyncio.run(self.build_snapshot(event))
        self.assertEqual(snapshot["user"]["name"], "Personal Name")
        self.assertEqual(snapshot["user"]["role"], "user")
        self.assertEqual(snapshot["counts"]["prompts"], 1)
        self.assertEqual(snapshot["resources"]["prompts"][0]["name"], "Sensitive project prompt")
        self.assertEqual(snapshot["resources"]["knowledge"][0]["name"], "Sensitive knowledge title")
        self.assertEqual(snapshot["resources"]["channels"][0]["name"], "Sensitive channel title")

    def test_preferred_model_is_used_only_when_accessible(self):
        event = MODULE.Event()
        event.valves.preferred_welcome_model_id = "forbidden"
        snapshot = asyncio.run(self.build_snapshot(event))
        self.assertEqual(snapshot["selected_model_id"], "allowed")
        event.valves.preferred_welcome_model_id = "allowed"
        snapshot = asyncio.run(self.build_snapshot(event))
        self.assertEqual(snapshot["selected_model_id"], "allowed")

    def test_production_gate_blocks_signup(self):
        self.event._maybe_onboard = AsyncMock()
        asyncio.run(
            self.event.event(
                {"actor": {"id": "u1"}},
                __event_name__="auth.signup",
            )
        )
        self.event._maybe_onboard.assert_not_awaited()
        self.event.valves.production_enabled = True
        asyncio.run(
            self.event.event(
                {"actor": {"id": "u1"}},
                __event_name__="auth.signup",
            )
        )
        self.event._maybe_onboard.assert_awaited_once()

    def test_valves_test_runs_only_for_this_function(self):
        self.event._maybe_run_test = AsyncMock()
        asyncio.run(
            self.event.event(
                {"subject": {"id": "other"}},
                __event_name__="function.valves_updated",
                __id__="onboarding",
            )
        )
        self.event._maybe_run_test.assert_not_awaited()
        asyncio.run(
            self.event.event(
                {"subject": {"id": "onboarding"}},
                __event_name__="function.valves_updated",
                __id__="onboarding",
            )
        )
        self.event._maybe_run_test.assert_awaited_once()

    def test_chat_contains_persistent_embed_and_marker_ids(self):
        captured = {}

        class FakeChats:
            @staticmethod
            async def insert_new_chat(chat_id, user_id, form):
                captured.update(chat_id=chat_id, user_id=user_id, chat=form.chat)
                return {"id": chat_id}

        chats = types.ModuleType("open_webui.models.chats")
        chats.ChatForm = FakeChatForm
        chats.Chats = FakeChats()
        with patch.dict(
            sys.modules,
            {
                "open_webui": types.ModuleType("open_webui"),
                "open_webui.models": types.ModuleType("open_webui.models"),
                "open_webui.models.chats": chats,
            },
        ):
            result = asyncio.run(
                self.event._create_welcome_chat(
                    SimpleNamespace(id="u1"),
                    self.base_snapshot(),
                )
            )
        self.assertEqual(captured["user_id"], "u1")
        self.assertEqual(result["chat_id"], captured["chat_id"])
        message = captured["chat"]["messages"][0]
        self.assertEqual(result["message_id"], message["id"])
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(len(message["embeds"]), 1)
        self.assertTrue(message["done"])

    def test_refresh_requires_owned_chat(self):
        fake_chats = SimpleNamespace(
            get_chat_by_id_and_user_id=AsyncMock(return_value=None),
            upsert_message_to_chat_by_id_and_message_id=AsyncMock(),
        )
        fake_users = SimpleNamespace(
            get_user_by_id=AsyncMock(
                return_value=SimpleNamespace(id="u1", name="User", role="user")
            )
        )
        chats_module = types.ModuleType("open_webui.models.chats")
        chats_module.Chats = fake_chats
        users_module = types.ModuleType("open_webui.models.users")
        users_module.Users = fake_users
        with patch.dict(
            sys.modules,
            {
                "open_webui": types.ModuleType("open_webui"),
                "open_webui.models": types.ModuleType("open_webui.models"),
                "open_webui.models.chats": chats_module,
                "open_webui.models.users": users_module,
            },
        ):
            asyncio.run(
                self.event._refresh_existing(
                    "u1",
                    {"chat_id": "not-owned", "message_id": "m1"},
                    object(),
                    None,
                )
            )
        fake_chats.upsert_message_to_chat_by_id_and_message_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
