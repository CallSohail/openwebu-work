import asyncio
import base64
import gzip
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("email_composer.py")
spec = importlib.util.spec_from_file_location("email_composer", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def _implementation_source() -> str:
    """Return the actual packaged implementation, not only the distribution wrapper."""
    payload = getattr(module, "_PAYLOAD", None)
    if payload:
        return gzip.decompress(base64.b64decode(payload)).decode("utf-8")
    return MODULE_PATH.read_text(encoding="utf-8")


def test_subject_header_cleaning():
    tool = module.Tools()
    cleaned = tool._clean_text("Hello\r\nBcc: attacker@example.org", 240, True)
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert cleaned == "Hello Bcc: attacker@example.org"


def test_recipient_validation_and_deduplication():
    tool = module.Tools()
    valid, invalid = tool._normalize_recipients(
        "Alice <alice@example.org>; alice@example.org; bad-address; bob@example.org"
    )
    assert valid == ["Alice <alice@example.org>", "bob@example.org"]
    assert invalid == ["bad-address"]


def test_domain_policy():
    tool = module.Tools()
    tool.valves.allowed_recipient_domains = "example.org"
    tool.valves.blocked_recipient_domains = "blocked.example.org"

    assert tool._is_allowed_domain("alice@example.org") is True
    assert tool._is_allowed_domain("alice@team.example.org") is True
    assert tool._is_allowed_domain("alice@blocked.example.org") is False
    assert tool._is_allowed_domain("alice@elsewhere.net") is False


def test_message_level_embed_is_emitted():
    tool = module.Tools()
    events = []

    async def emitter(event):
        events.append(event)

    result = asyncio.run(
        tool.compose_email(
            subject="Project update",
            body="Hello,\n\nHere is the update.",
            to="alice@example.org",
            __event_emitter__=emitter,
        )
    )

    assert result["status"] == "success"
    assert events and events[0]["type"] == "embeds"
    html = events[0]["data"]["embeds"][0]
    assert "__PAYLOAD_B64__" not in html
    assert "contenteditable=\"true\"" in html
    assert "id=\"subjectInput\"" in html
    assert "id=\"formatBar\"" in html


def test_public_payload_has_no_deployment_branding_or_hard_version_pin():
    source = _implementation_source().lower()
    forbidden = [
        "univ-evry",
        "université d'évry",
        "universite d'evry",
        "#0a3d67",
        "#00b3c3",
        "required_open_webui_version",
        "0.11.3+",
    ]
    for marker in forbidden:
        assert marker not in source
