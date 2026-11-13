"""Protocol tests: a stand-in Open WebUI connects to Longshot over Socket.IO.

Run:  pip install "oix[fastapi]>=0.8.0" "uvicorn[standard]" pytest pytest-asyncio
      pytest test_longshot.py
"""

import asyncio
import os
import socket
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
import socketio
import uvicorn

os.environ["LONGSHOT_DATA_DIR"] = tempfile.mkdtemp(prefix="longshot-test-")
os.environ.pop("API_KEY", None)
sys.path.insert(0, str(Path(__file__).parent))
import longshot  # noqa: E402

NS = "/apps"
ADA = {"id": "u-ada", "name": "Ada", "is_admin": False}
BOB = {"id": "u-bob", "name": "Bob", "is_admin": False}
CY = {"id": "u-cy", "name": "Cy", "is_admin": False}
ROOT = {"id": "u-root", "name": "Root", "is_admin": True}


class Clock:
    """Stands in for the time module inside longshot, so tests can pass deadlines."""

    def __init__(self):
        self.now = 1_800_000_000.0  # 15 Jan 2027

    def time(self):
        return self.now


class FakeWebUI:
    """Plays Open WebUI's side: handshake, events, and answers to app:request."""

    def __init__(self):
        self.sio = socketio.AsyncClient(reconnection=False)
        self.sio.on("app:request", self.request, namespace=NS)
        self.events, self.posted = {}, []
        self.new_message = asyncio.Event()
        self.refuse_posts = None

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
            if self.refuse_posts:
                return {"status": 403, "error": self.refuse_posts}
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

    async def emit(self, type_, actor, data, channel):
        token = uuid.uuid4().hex
        event = {
            "id": uuid.uuid4().hex,
            "type": type_,
            "actor": actor,
            "channel": {"id": channel},
            "data": data,
            "execution_token": token,
            "source": None,
            "app_id": "app-1",
            "installation_id": self.installation_id,
        }
        self.events[token] = event
        return await self.sio.call("app:event", event, namespace=NS)

    async def mention(self, text, actor=ADA, channel="c-1", parent_id=None):
        self.new_message.clear()
        receipt = await self.emit(
            "channel.mention",
            actor,
            {"text": f"<@A:app-1|Longshot> {text}", "message_id": "m-user", "parent_id": parent_id},
            channel,
        )
        assert receipt == {"status": 200, "data": {"received": True}}
        await asyncio.wait_for(self.new_message.wait(), 5)
        return self.posted[-1]

    async def click(self, message, label, actor=ADA, channel="c-1"):
        action = next(a for a in message["actions"] if a["label"] == label)
        ack = await self.emit(
            "channel.action",
            actor,
            {
                "message_id": message.get("id", "m-updated"),
                "etag": "1",
                "parent_id": None,
                "action_id": action["action_id"],
                "context": action["context"],
            },
            channel,
        )
        assert ack["status"] == 200, ack
        return ack["data"]


@pytest_asyncio.fixture
async def webui(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(longshot, "time", clock)
    app = longshot.build_app(longshot.Store(":memory:"))
    monkeypatch.setattr(longshot, "TICK_SECONDS", 3600)  # tests call announce_due themselves
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(longshot.build_server(app), log_level="error", lifespan="on"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        await asyncio.sleep(0.01)
    fake = FakeWebUI()
    fake.app, fake.clock = app, clock
    await fake.connect(f"http://127.0.0.1:{listener.getsockname()[1]}", app._api_key)
    try:
        yield fake
    finally:
        await fake.sio.disconnect()
        server.should_exit = True
        await task


def labels(message):
    return [a["label"] for a in message["actions"]]


def field(message, index):
    return message["embeds"][0]["fields"][index]


def test_parse_deadline():
    now = 1_800_000_000.0
    assert longshot.parse_deadline("90d", now) == now + 90 * 86400
    assert longshot.parse_deadline("in 6 mo", now) == now + 180 * 86400
    assert longshot.parse_deadline("2min", now) == now + 120
    assert longshot.parse_deadline("2027-07-01", now) == 1814486399.0  # end of that day, UTC
    for bad in ("soon", "2027-13-01", "2020-01-01", "30y", "0d", ""):
        assert isinstance(longshot.parse_deadline(bad, now), str), bad


def test_payout_and_points():
    assert longshot.payout(1, 3) == (30.0, -10.0)  # alone and right takes the pot
    assert longshot.payout(3, 1) == (10 / 3, -10.0)
    assert longshot.payout(2, 0) == (0.0, 0.0)  # nobody to collect from
    assert longshot.payout(0, 2) == (0.0, 0.0)
    assert [longshot.points(v) for v in (30.0, 10 / 3, -10.0, 0.0)] == ["+30", "+3.3", "−10", "0"]


def test_show_time_is_exact_only_when_near():
    now = 1_800_000_000.0
    assert longshot.show_time(now + 3600, now) == "15 Jan 2027, 09:00 UTC"
    assert longshot.show_time(now + 90 * 86400, now) == "15 Apr 2027"


def test_credentials_are_created_once():
    first = longshot.load_credentials()
    assert len(first[0]) == 64 and "PRIVATE KEY" in first[1]
    assert longshot.load_credentials() == first


@pytest.mark.asyncio
async def test_help_and_empty_cards(webui):
    card = await webui.mention("what is this?")
    assert card["embeds"][0]["title"] == "Longshot" and card["reply_to_id"] == "m-user"
    assert "Nothing is running" in (await webui.mention("list"))["embeds"][0]["description"]
    assert "nobody has a record" in (await webui.mention("board"))["embeds"][0]["description"]
    assert "no bet with that number" in (await webui.mention("show 99"))["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, expected",
    [
        ("bet", "needs a claim and a deadline"),
        ("bet It will rain tomorrow", "needs a claim and a deadline"),
        ("bet Rain | 2d", "8 to 200 characters"),
        ("bet It will rain tomorrow | someday", "could not read that deadline"),
        ("bet It will rain tomorrow | 2020-01-01", "already passed"),
        ("bet It will rain tomorrow | 30y", "Twenty years"),
    ],
)
async def test_bad_bets_get_a_reason(webui, text, expected):
    assert expected in (await webui.mention(text))["content"]


@pytest.mark.asyncio
async def test_bet_lifecycle_and_scoreboard(webui):
    bet = await webui.mention("bet The new library opens before the summer | 2027-06-30")
    embed = bet["embeds"][0]
    assert embed["title"] == "The new library opens before the summer"
    assert "Open until 30 Jun 2027" in embed["description"]
    assert field(bet, 0) == {"name": "Yes (1)", "value": "Ada", "inline": True}  # the opener starts on Yes
    assert labels(bet) == ["Yes", "No", "Settle: Yes", "Settle: No", "Void"]

    await webui.click(bet, "No", BOB)
    await webui.click(bet, "Yes", CY)
    update = await webui.click(bet, "No", CY)  # Cy switches
    assert update["type"] == "update" and update["visibility"] == "channel"
    assert field(update, 0)["value"] == "Ada" and field(update, 1)["value"] == "Bob, Cy"
    update = await webui.click(bet, "No", CY)  # and steps out
    assert field(update, 1) == {"name": "No (1)", "value": "Bob", "inline": True}
    assert update["content"] == ""
    await webui.click(bet, "No", CY)
    await webui.click(bet, "No", ROOT)

    listing = (await webui.mention("list"))["embeds"][0]["description"]
    assert "`#1` The new library opens before the summer" in listing and "1 yes, 3 no" in listing

    denied = await webui.click(bet, "Settle: Yes", BOB)
    assert denied["type"] == "error" and "Only Ada" in denied["content"]

    settled = await webui.click(bet, "Settle: Yes", ADA)
    assert "Settled **Yes** by Ada" in settled["embeds"][0]["description"]
    assert field(settled, 0) == {"name": "Right (1)", "value": "Ada · +30 each", "inline": True}
    assert field(settled, 1)["value"] == "Bob, Cy, Root · −10 each"
    assert labels(settled) == ["Reopen"]
    assert (await webui.click(bet, "Yes", BOB))["type"] == "error"
    assert "already settled" in (await webui.click(bet, "Settle: No", ADA))["content"]

    board = (await webui.mention("board"))["embeds"][0]["description"].split("\n")
    assert board[0] == "`1` **Ada** · +30 pts · 1 of 1 right"
    assert board[1] == "`2` **Bob** · −10 pts · 0 of 1 right"
    assert "Nothing is running" in (await webui.mention("list"))["embeds"][0]["description"]

    assert (await webui.click(settled, "Reopen", BOB))["type"] == "error"
    reopened = await webui.click(settled, "Reopen", ROOT)  # an admin corrects a wrong call
    assert labels(reopened)[:2] == ["Yes", "No"]
    assert "nobody has a record" in (await webui.mention("board"))["embeds"][0]["description"]
    voided = await webui.click(bet, "Void", ADA)
    assert "Voided by Ada" in voided["embeds"][0]["description"]
    assert "nobody has a record" in (await webui.mention("board"))["embeds"][0]["description"]


@pytest.mark.asyncio
async def test_one_sided_bet_moves_no_points(webui):
    bet = await webui.mention("bet Everyone agrees on this one | 3d")
    await webui.click(bet, "Yes", BOB)
    settled = await webui.click(bet, "Settle: Yes")
    assert "no points moved" in settled["embeds"][0]["description"]
    assert field(settled, 0)["value"] == "Ada, Bob"
    board = (await webui.mention("board"))["embeds"][0]["description"]
    assert "**Ada** · 0 pts · 1 of 1 right" in board


@pytest.mark.asyncio
async def test_deadline_closes_picks_and_posts_one_notice(webui):
    bet = await webui.mention("bet The build goes green within the hour | 1h", parent_id="thread-1")
    await webui.click(bet, "No", BOB)
    await webui.app.announce_due()
    assert len(webui.posted) == 1  # nothing is due yet

    webui.clock.now += 3601
    late = await webui.click(bet, "Yes", CY)  # the old card refreshes instead of taking the pick
    assert late["type"] == "update" and labels(late) == ["Settle: Yes", "Settle: No", "Void"]
    assert "Waiting for Ada or an admin" in late["embeds"][0]["description"]
    assert field(late, 0)["value"] == "Ada"

    await webui.app.announce_due()
    notice = webui.posted[-1]
    assert notice["content"].startswith("Time is up on bet #1. Ada, how did it turn out?")
    assert field(notice, 0)["value"] == "Ada" and field(notice, 1)["value"] == "Bob"
    assert notice["parent_id"] == "thread-1" and labels(notice) == ["Settle: Yes", "Settle: No", "Void"]
    await webui.app.announce_due()
    assert len(webui.posted) == 2  # announced once

    settled = await webui.click(notice, "Settle: No", ROOT)
    assert "Settled **No** by Root" in settled["embeds"][0]["description"]
    assert field(settled, 0)["value"] == "Bob · +10 each"
    assert settled["content"].startswith("Bet #1 reached its deadline on 15 Jan 2027")  # the question is gone


@pytest.mark.asyncio
async def test_notice_waits_for_a_connection_but_not_for_a_refusal(webui):
    await webui.mention("bet The first notice cannot be delivered | 1h")
    webui.clock.now += 3601
    await webui.sio.disconnect()
    await asyncio.sleep(0.1)
    await webui.app.announce_due()
    assert len(webui.app.store.due_unannounced(webui.clock.now)) == 1  # kept for the next tick

    again = FakeWebUI()
    again.refuse_posts = "Invite the app into this channel first"
    await again.connect(webui.sio.connection_url, webui.app._api_key)
    try:
        # A different installation cannot receive it either; the bet stays queued.
        await webui.app.announce_due()
        assert len(webui.app.store.due_unannounced(webui.clock.now)) == 1
        webui.app.store.db.execute("UPDATE bets SET installation_id = ?", (again.installation_id,))
        await webui.app.announce_due()  # refused by Open WebUI: give up
        assert webui.app.store.due_unannounced(webui.clock.now) == []
    finally:
        await again.sio.disconnect()


@pytest.mark.asyncio
async def test_channels_are_separate(webui):
    bet = await webui.mention("bet This bet belongs to channel one | 5d")
    assert "Nothing is running" in (await webui.mention("list", channel="c-2"))["embeds"][0]["description"]
    assert "no bet with that number" in (await webui.mention("show 1", channel="c-2"))["content"]
    stray = await webui.click(bet, "Yes", BOB, channel="c-2")
    assert stray["type"] == "error" and "no longer exists" in stray["content"]
    shown = await webui.mention("show #1")
    assert shown["embeds"][0]["title"] == "This bet belongs to channel one"


@pytest.mark.asyncio
async def test_channel_limit(webui, monkeypatch):
    monkeypatch.setattr(longshot, "MAX_OPEN_PER_CHANNEL", 2)
    await webui.mention("bet The first of too many bets | 5d")
    await webui.mention("bet The second of too many bets | 5d")
    assert "already has 2 unsettled bets" in (await webui.mention("bet The third of too many bets | 5d"))["content"]


def test_bets_survive_restart(tmp_path):
    class Event:
        installation_id, channel_id, parent_id, actor = "i-1", "c-1", None, ADA

    store = longshot.Store(tmp_path / "longshot.db")
    bet_id = store.create(Event, "This bet outlives the process", 2_000_000_000.0, 1_800_000_000.0)
    store.pick(bet_id, BOB, "no", 1_800_000_001.0)
    store.close()
    store = longshot.Store(tmp_path / "longshot.db")
    assert store.bet(bet_id, 1_800_000_002.0)["claim"] == "This bet outlives the process"
    assert store.picks(bet_id) == {"yes": ["Ada"], "no": ["Bob"]}
