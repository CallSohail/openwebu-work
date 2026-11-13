"""Protocol tests: a stand-in Open WebUI connects to Pulse over Socket.IO.

Run:  pip install "oix>=0.8.0" pytest pytest-asyncio  &&  pytest test_pulse.py
"""

import asyncio
import importlib
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import socketio
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
NS = "/apps"
ADA = {"id": "u-ada", "name": "Ada", "is_admin": False}
BOB = {"id": "u-bob", "name": "Bob", "is_admin": False}
ROOT = {"id": "u-root", "name": "Root", "is_admin": True}


class FakeWebUI:
    """Plays Open WebUI's side: handshake, events, and answers to app:request."""

    def __init__(self):
        self.sio = socketio.AsyncClient(reconnection=False)
        self.sio.on("app:request", self.request, namespace=NS)
        self.events, self.posted = {}, []
        self.new_message = asyncio.Event()

    async def request(self, data):
        operation, args = data["operation"], data["arguments"]
        if operation == "ping":
            return {"status": 200, "data": {"received": True}}
        if operation == "authorize":
            event = self.events[args["token"]]
            return {
                "status": 200,
                "data": {
                    "authorized": True,
                    "operation_id": event["id"],
                    "actor": event["actor"],
                    "channel_id": event["channel"]["id"],
                    "type": event["type"],
                    "data": event["data"],
                },
            }
        if operation == "create_message":
            message = {"id": f"m-{len(self.posted)}", "etag": "1", **args}
            self.posted.append(message)
            self.new_message.set()
            return {"status": 200, "data": message}
        return {"status": 400, "error": "Unsupported operation"}

    async def connect(self, url, api_key):
        await self.sio.connect(
            url,
            socketio_path="events/socket.io",
            headers={"Authorization": f"Bearer {api_key}"},
            namespaces=[NS],
            transports=["websocket"],
        )
        hello = await self.sio.call(
            "app:connect",
            {"app_id": "app-1", "challenge": str(uuid.uuid4()), "connection_token": None},
            namespace=NS,
        )
        assert hello["status"] == 200, hello
        self.installation_id = hello["data"]["installation_id"]
        ready = await self.sio.call("app:ready", {}, namespace=NS)
        assert ready == {"status": 200, "data": {"received": True}}

    async def emit(self, type_, actor, data):
        token = uuid.uuid4().hex
        event = {
            "id": uuid.uuid4().hex,
            "type": type_,
            "actor": actor,
            "channel": {"id": "c-1"},
            "data": data,
            "execution_token": token,
            "source": None,
            "app_id": "app-1",
            "installation_id": self.installation_id,
        }
        self.events[token] = event
        return await self.sio.call("app:event", event, namespace=NS)

    async def mention(self, text, actor=ADA):
        self.new_message.clear()
        receipt = await self.emit(
            "channel.mention",
            actor,
            {"text": f"<@A:app-1|Pulse> {text}", "message_id": "m-user", "parent_id": None},
        )
        assert receipt == {"status": 200, "data": {"received": True}}
        await asyncio.wait_for(self.new_message.wait(), 5)
        return self.posted[-1]

    async def click(self, message, label, actor=ADA):
        action = next(a for a in message["actions"] if a["label"] == label)
        ack = await self.emit(
            "channel.action",
            actor,
            {
                "message_id": message["id"],
                "etag": "1",
                "parent_id": None,
                "action_id": action["action_id"],
                "context": action["context"],
            },
        )
        assert ack["status"] == 200, ack
        return ack["data"]


@pytest_asyncio.fixture
async def webui(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("API_KEY", raising=False)
    pulse = importlib.reload(importlib.import_module("pulse"))
    app = pulse.build_app()
    with patch("oix.app.web.run_app") as run:
        app.run()
    runner = web.AppRunner(run.call_args.args[0])
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    fake = FakeWebUI()
    fake.app, fake.pulse = app, pulse
    await fake.connect(f"http://127.0.0.1:{port}", app._api_key)
    try:
        yield fake
    finally:
        await fake.sio.disconnect()
        await runner.cleanup()


def test_parse_poll():
    from pulse import parse_poll

    assert parse_poll("Lunch? | Pizza | Sushi | Pizza") == ("Lunch?", ["Pizza", "Sushi"])
    assert isinstance(parse_poll("Lunch?"), str)
    assert isinstance(parse_poll("Lunch? | Pizza"), str)
    assert isinstance(parse_poll("Q | " + " | ".join(map(str, range(11)))), str)


def test_credentials_are_created_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("API_KEY", raising=False)
    pulse = importlib.reload(importlib.import_module("pulse"))
    first = pulse.load_credentials()
    assert len(first[0]) == 64 and "PRIVATE KEY" in first[1]
    assert pulse.load_credentials() == first


@pytest.mark.asyncio
async def test_help_card(webui):
    message = await webui.mention("help")
    assert message["embeds"][0]["title"] == "Pulse"
    assert message["reply_to_id"] == "m-user"
    assert [a["label"] for a in message["actions"]][:3] == [
        "Private reply",
        "Download a file",
        "Slow job (5 s)",
    ]
    private = await webui.click(message, "Private reply")
    assert private["visibility"] == "private" and "Ada" in private["content"]
    download = await webui.click(message, "Download a file")
    assert download["type"] == "download" and download["filename"] == "pulse-sample.md"


@pytest.mark.asyncio
async def test_bad_poll_gets_usage(webui):
    message = await webui.mention("poll Lunch?")
    assert "at least two options" in message["content"]


@pytest.mark.asyncio
async def test_poll_vote_move_withdraw_close(webui):
    poll = await webui.mention("poll Lunch? | Pizza | Sushi")
    assert poll["embeds"][0]["title"] == "Lunch?"
    assert [a["label"] for a in poll["actions"]] == ["Pizza", "Sushi", "Close poll", "Results (.md)"]

    update = await webui.click(poll, "Pizza")
    assert update["type"] == "update" and update["visibility"] == "channel"
    assert "1 vote ·" in update["embeds"][0]["footer"]["text"]
    await webui.click(poll, "Pizza", BOB)
    update = await webui.click(poll, "Sushi", BOB)  # Bob moves his vote
    assert "2 votes" in update["embeds"][0]["footer"]["text"]
    assert "1 (50%)" in update["embeds"][0]["description"]
    update = await webui.click(poll, "Sushi", BOB)  # and withdraws it
    assert "1 vote ·" in update["embeds"][0]["footer"]["text"]

    report = await webui.click(poll, "Results (.md)")
    assert report["type"] == "download" and "| Pizza | 1 | 100% | Ada |" in report["content"]

    denied = await webui.click(poll, "Close poll", BOB)
    assert denied["type"] == "error"
    closed = await webui.click(poll, "Close poll", ROOT)  # admins may close
    assert [a["label"] for a in closed["actions"]] == ["Results (.md)"]
    late = await webui.click(poll, "Pizza")
    assert late["type"] == "error" and "closed" in late["content"]


@pytest.mark.asyncio
async def test_polls_survive_restart(webui):
    poll = await webui.mention("poll Tea? | Yes | No")
    await webui.click(poll, "Yes")
    reloaded = webui.pulse.Polls(webui.pulse.DATA_DIR / "polls.json")
    saved = next(iter(reloaded.items.values()))
    assert saved["question"] == "Tea?" and saved["votes"] == {"u-ada": 0}


@pytest.mark.asyncio
async def test_slow_job_acks_then_posts(webui, monkeypatch):
    real_sleep = asyncio.sleep
    monkeypatch.setattr(webui.pulse.asyncio, "sleep", lambda s: real_sleep(0))
    message = await webui.mention("help")
    webui.new_message.clear()
    assert await webui.click(message, "Slow job (5 s)") == {"type": "ack"}
    await asyncio.wait_for(webui.new_message.wait(), 5)
    assert "slow job Ada started is done" in webui.posted[-1]["content"]
