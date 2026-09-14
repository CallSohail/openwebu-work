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
    path = Path(__file__).with_name("evegpt_onboarding_v4.py")
    spec = importlib.util.spec_from_file_location("evegpt_onboarding_v4", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


class FakeChatForm:
    def __init__(self, chat):
        self.chat = chat


class OnboardingV4Tests(unittest.TestCase):
    def setUp(self):
        self.event = MODULE.Event()

    @staticmethod
    def base_snapshot():
        return {
            "schema": 1,
            "generated_at": 1,
            "generated_at_label": {"fr": "01/01/2026", "en": "2026-01-01"},
            "user": {
                "name": "Test",
                "role": "user",
                "role_label": {"fr": "Utilisateur", "en": "User"},
            },
            "brand": {"product": "EveGPT", "organization": ""},
            "ui": {
                "default_language": "en",
                "font": "Arial, Helvetica, sans-serif",
                "show_models": True,
                "show_prompts": True,
                "show_tools": True,
                "show_skills": True,
                "show_knowledge": True,
                "show_notes": True,
                "show_channels": True,
                "show_sources": True,
                "show_organization": True,
                "show_media": True,
                "show_creation_guides": True,
                "resource_names": True,
            },
            "links": {
                "support_url": "",
                "privacy_url": "",
                "acceptable_use_url": "",
                "feedback_url": "",
            },
            "selected_model_id": "m1",
            "counts": {
                "models": 1,
                "prompts": 0,
                "tools": 0,
                "skills": 0,
                "knowledge": 0,
                "channels": 0,
                "features": 0,
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
        self.assertIn("prefers-color-scheme:dark", rendered)

    def test_only_clean_https_links_are_allowed(self):
        self.assertEqual(self.event._safe_http_url("javascript:alert(1)"), "")
        self.assertEqual(self.event._safe_http_url("http://example.org"), "")
        self.assertEqual(self.event._safe_http_url("https://user:pass@example.org"), "")
        self.assertEqual(
            self.event._safe_http_url("https://support.example.org/help"),
            "https://support.example.org/help",
        )

    def test_public_model_metadata_excludes_sensitive_fields(self):
        model = {
            "id": "m1",
            "name": "Safe",
            "info": {
                "params": {"system": "SECRET"},
                "meta": {
                    "description": "Public",
                    "capabilities": {"vision": True},
                },
            },
            "actions": [{
                "id": "a",
                "name": "Action",
                "description": "Runs a review",
                "valves": {"token": "SECRET"},
            }],
        }
        public = self.event._model_public(model)
        raw = json.dumps(public)
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("params", raw)
        self.assertNotIn("valves", raw)
        self.assertEqual(public["capabilities"], ["vision"])

    def test_prompt_skill_and_knowledge_bodies_are_not_exposed(self):
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
        raw = json.dumps({
            "prompt": self.event._prompt_public(prompt),
            "skill": self.event._skill_public(skill),
            "knowledge": self.event._knowledge_public(knowledge),
        })
        self.assertNotIn("PROMPT_SECRET", raw)
        self.assertNotIn("SKILL_SECRET", raw)
        self.assertNotIn("DOCUMENT_SECRET", raw)

    def test_preferred_model_is_used_only_when_accessible(self):
        async def run(preferred, expose_names=True):
            event = MODULE.Event()
            event.valves.preferred_welcome_model_id = preferred
            event.valves.expose_resource_names = expose_names
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
            event._load_prompts = lambda user: asyncio.sleep(0, result=[])
            event._load_tools = lambda request, user: asyncio.sleep(0, result=[])
            event._load_skills = lambda request, user: asyncio.sleep(0, result=[])
            event._load_knowledge = lambda user: asyncio.sleep(0, result=[])
            event._load_channels = lambda user: asyncio.sleep(0, result=[])
            return await event._build_snapshot(
                SimpleNamespace(id="u", name="User", role="user"),
                object(),
                SimpleNamespace(app=object()),
            )

        snapshot = asyncio.run(run("forbidden"))
        self.assertEqual(snapshot["selected_model_id"], "allowed")
        self.assertTrue(snapshot["access"]["workspace"]["knowledge"])
        self.assertTrue(snapshot["access"]["features"]["notes"])
        hidden = asyncio.run(run("allowed", expose_names=False))
        self.assertEqual(hidden["resources"]["models"], [])
        self.assertEqual(hidden["counts"]["models"], 1)

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
                __id__="evegpt",
            )
        )
        self.event._maybe_run_test.assert_not_awaited()
        asyncio.run(
            self.event.event(
                {"subject": {"id": "evegpt"}},
                __event_name__="function.valves_updated",
                __id__="evegpt",
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
