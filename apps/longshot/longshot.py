"""
title: Longshot
description: Long bets for Open WebUI channels: a claim, a deadline, two sides, and a scoreboard of who called it.
version: 1.0.0
requirements: oix[fastapi]>=0.8.0, uvicorn[standard]
required_open_webui_version: 0.13.3

Run:  pip install "oix[fastapi]>=0.8.0" "uvicorn[standard]"  &&  python longshot.py
Then install it in Open WebUI (Settings > Admin > Apps) with the App URL and
API key it prints. See README.md.
"""

import asyncio
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from datetime import UTC, datetime
from pathlib import Path

import oix.socket
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from oix import Action, APIError, App, AppChannelReply, ConnectionUnavailable, Embed, EmbedField
from oix.fastapi import create_router

VERSION = "1.0.0"
log = logging.getLogger("longshot")

DATA_DIR = Path(os.environ.get("LONGSHOT_DATA_DIR") or Path(__file__).parent / "data")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))

STAKE = 10  # points each person puts on a bet
MAX_OPEN_PER_CHANNEL = 50
MAX_HORIZON = 20 * 365 * 86400
TICK_SECONDS = 15
COLORS = {"open": 0x44545B, "due": 0x8C9A9E, "yes": 0x11171B, "no": 0x11171B, "void": 0xC9D2D4}
MENTION = re.compile(r"<@A:[^>|]*(\|[^>]*)?>")
RELATIVE = re.compile(r"(?:in\s+)?(\d{1,4})\s*(min|h|d|w|mo|y)", re.IGNORECASE)
UNITS = {"min": 60, "h": 3600, "d": 86400, "w": 7 * 86400, "mo": 30 * 86400, "y": 365 * 86400}
CLOCK_LEEWAY = 30


class _BackdatedClock:
    """Date connection proofs CLOCK_LEEWAY seconds in the past.

    oix signs a proof valid for 60 seconds from "now", and Open WebUI rejects
    one whose start is in its future. A computer a few seconds ahead of the
    server then fails with "App connection failed". Backdating by 30 seconds
    lets the two clocks differ by up to 30 seconds either way.
    """

    def __init__(self, clock):
        self._clock = clock

    def __getattr__(self, name):
        return getattr(self._clock, name)

    def time(self):
        return self._clock.time() - CLOCK_LEEWAY


oix.socket.time = _BackdatedClock(oix.socket.time)


# Credentials


def load_credentials() -> tuple[str, str]:
    """Return (api_key, private_key_pem), creating both once on first run.

    API_KEY and APP_PRIVATE_KEY_FILE win when both are set. Otherwise the
    credentials live in DATA_DIR and are never regenerated: Open WebUI pins
    the public key at install, so a new key would break the installation.
    """
    env_key, env_pem = os.environ.get("API_KEY"), os.environ.get("APP_PRIVATE_KEY_FILE")
    if env_key and env_pem:
        return env_key, Path(env_pem).read_text(encoding="utf-8")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key_file, pem_file = DATA_DIR / "api-key.txt", DATA_DIR / "app-private-key.pem"
    if not key_file.exists() and not pem_file.exists():
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        for file, value in ((key_file, secrets.token_hex(32)), (pem_file, pem.decode())):
            file.touch(mode=0o600)
            file.write_text(value, encoding="utf-8")
    return key_file.read_text(encoding="utf-8").strip(), pem_file.read_text(encoding="utf-8")


# Dates


def parse_deadline(text: str, now: float) -> float | str:
    """Parse '2027-07-01' or '6mo' into a timestamp. Returns an error message on failure."""
    text = text.strip()
    relative = RELATIVE.fullmatch(text)
    try:
        if relative:
            closes = now + int(relative[1]) * UNITS[relative[2].lower()]
        else:
            # A date means the end of that day, UTC.
            day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC)
            closes = day.timestamp() + 86399
    except ValueError:
        return "I could not read that deadline. Use a date like `2027-07-01`, or a span like `90d`, `6mo` or `2y`."
    if closes <= now:
        return "That deadline has already passed."
    if closes - now > MAX_HORIZON:
        return "Twenty years is the longest a bet can run."
    return closes


def show_time(timestamp: float, now: float | None = None) -> str:
    moment = datetime.fromtimestamp(timestamp, UTC)
    near = abs(timestamp - (time.time() if now is None else now)) < 2 * 86400
    text = moment.strftime("%d %b %Y, %H:%M UTC" if near else "%d %b %Y")
    return text.lstrip("0")


# Storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    installation_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    parent_id TEXT,
    claim TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    creator_name TEXT NOT NULL,
    created_at REAL NOT NULL,
    closes_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',  -- open, due, yes, no, void
    settled_by TEXT,
    settled_at REAL,
    notified INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS bets_channel ON bets (installation_id, channel_id, state);
CREATE TABLE IF NOT EXISTS picks (
    bet_id INTEGER NOT NULL REFERENCES bets (id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    user_name TEXT NOT NULL,
    side TEXT NOT NULL,  -- yes, no
    placed_at REAL NOT NULL,
    PRIMARY KEY (bet_id, user_id)
);
"""


class Store:
    """Bets and picks in one SQLite file. Calls are short, so they run inline."""

    def __init__(self, file: Path | str):
        if file != ":memory:":
            Path(file).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(file, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;" + SCHEMA)

    def close(self):
        self.db.close()

    def one(self, sql, *args):
        with closing(self.db.execute(sql, args)) as cursor:
            return cursor.fetchone()

    def all(self, sql, *args):
        with closing(self.db.execute(sql, args)) as cursor:
            return cursor.fetchall()

    def create(self, event, claim: str, closes_at: float, now: float) -> int:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            bet_id = self.db.execute(
                "INSERT INTO bets (installation_id, channel_id, parent_id, claim, creator_id,"
                " creator_name, created_at, closes_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.installation_id,
                    event.channel_id,
                    event.parent_id,
                    claim,
                    event.actor["id"],
                    event.actor.get("name", "Someone"),
                    now,
                    closes_at,
                ),
            ).lastrowid
            # Whoever opens a bet is making the claim, so they start on Yes.
            self.db.execute(
                "INSERT INTO picks VALUES (?, ?, ?, 'yes', ?)",
                (bet_id, event.actor["id"], event.actor.get("name", "Someone"), now),
            )
        return bet_id

    def bet(self, bet_id, now: float):
        """Fetch a bet, first closing it if its deadline has passed."""
        self.db.execute("UPDATE bets SET state = 'due' WHERE id = ? AND state = 'open' AND closes_at <= ?", (bet_id, now))
        return self.one("SELECT * FROM bets WHERE id = ?", bet_id)

    def picks(self, bet_id) -> dict[str, list[str]]:
        sides = {"yes": [], "no": []}
        for row in self.all("SELECT user_name, side FROM picks WHERE bet_id = ? ORDER BY placed_at", bet_id):
            sides[row["side"]].append(row["user_name"])
        return sides

    def pick(self, bet_id, actor: dict, side: str, now: float):
        """Take a side, switch, or step back out by picking the same side again."""
        current = self.one("SELECT side FROM picks WHERE bet_id = ? AND user_id = ?", bet_id, actor["id"])
        if current and current["side"] == side:
            self.db.execute("DELETE FROM picks WHERE bet_id = ? AND user_id = ?", (bet_id, actor["id"]))
        else:
            self.db.execute(
                "INSERT INTO picks VALUES (?, ?, ?, ?, ?) ON CONFLICT (bet_id, user_id)"
                " DO UPDATE SET side = excluded.side, user_name = excluded.user_name, placed_at = excluded.placed_at",
                (bet_id, actor["id"], actor.get("name", "Someone"), side, now),
            )

    def settle(self, bet_id, outcome: str, actor: dict, now: float):
        self.db.execute(
            "UPDATE bets SET state = ?, settled_by = ?, settled_at = ? WHERE id = ?",
            (outcome, actor.get("name", "Someone"), now, bet_id),
        )

    def reopen(self, bet_id, now: float):
        self.db.execute(
            "UPDATE bets SET state = CASE WHEN closes_at > ? THEN 'open' ELSE 'due' END,"
            " settled_by = NULL, settled_at = NULL WHERE id = ?",
            (now, bet_id),
        )

    def open_count(self, installation_id, channel_id) -> int:
        return self.one(
            "SELECT COUNT(*) FROM bets WHERE installation_id = ? AND channel_id = ? AND state IN ('open', 'due')",
            installation_id,
            channel_id,
        )[0]

    def pending(self, installation_id, channel_id, now: float):
        self.db.execute("UPDATE bets SET state = 'due' WHERE state = 'open' AND closes_at <= ?", (now,))
        return self.all(
            "SELECT b.*, (SELECT COUNT(*) FROM picks p WHERE p.bet_id = b.id AND p.side = 'yes') AS yes,"
            " (SELECT COUNT(*) FROM picks p WHERE p.bet_id = b.id AND p.side = 'no') AS no"
            " FROM bets b WHERE installation_id = ? AND channel_id = ? AND state IN ('open', 'due')"
            " ORDER BY closes_at LIMIT 25",
            installation_id,
            channel_id,
        )

    def scoreboard(self, installation_id, channel_id) -> list[dict]:
        """Points and record per person, from every settled bet in the channel."""
        people: dict[str, dict] = {}
        bets = self.all(
            "SELECT id, state FROM bets WHERE installation_id = ? AND channel_id = ? AND state IN ('yes', 'no')",
            installation_id,
            channel_id,
        )
        for bet in bets:
            picks = self.all("SELECT user_id, user_name, side FROM picks WHERE bet_id = ?", bet["id"])
            won, lost = payout(sum(p["side"] == bet["state"] for p in picks), sum(p["side"] != bet["state"] for p in picks))
            for p in picks:
                person = people.setdefault(p["user_id"], {"name": p["user_name"], "points": 0.0, "right": 0, "bets": 0})
                person["name"] = p["user_name"]
                person["bets"] += 1
                right = p["side"] == bet["state"]
                person["right"] += right
                person["points"] += won if right else lost
        return sorted(people.values(), key=lambda p: (-p["points"], -p["right"], p["name"].lower()))

    def due_unannounced(self, now: float):
        self.db.execute("UPDATE bets SET state = 'due' WHERE state = 'open' AND closes_at <= ?", (now,))
        return self.all("SELECT * FROM bets WHERE state = 'due' AND notified = 0 ORDER BY closes_at LIMIT 20")

    def mark_notified(self, bet_id):
        self.db.execute("UPDATE bets SET notified = 1 WHERE id = ?", (bet_id,))


def payout(winners: int, losers: int) -> tuple[float, float]:
    """Points for each winner and each loser.

    Everyone stakes STAKE. The losing side's stakes are split among the
    winners, so being right against the crowd pays more than agreeing with
    it. With an empty side there is nobody to pay or be paid.
    """
    if not winners or not losers:
        return 0.0, 0.0
    return STAKE * losers / winners, -float(STAKE)


def points(value: float) -> str:
    text = f"{abs(value):.1f}".rstrip("0").rstrip(".")
    return ("+" if value > 0 else "−" if value < 0 else "") + text


# Messages


def names(people: list[str], limit: int = 12, empty: str = "Nobody") -> str:
    if not people:
        return empty
    shown = ", ".join(people[:limit])
    return shown + (f" and {len(people) - limit} more" if len(people) > limit else "")


def bet_embed(bet, sides: dict[str, list[str]]) -> Embed:
    state, closes = bet["state"], show_time(bet["closes_at"])
    if state in ("yes", "no"):
        settled = show_time(bet["settled_at"])
        winners, losers = sides[state], sides["no" if state == "yes" else "yes"]
        won, lost = payout(len(winners), len(losers))
        description = f"Settled **{state.capitalize()}** by {bet['settled_by']} on {settled}."
        if not won:
            description += " Everyone who picked was on the same side, so no points moved."
        fields = [
            EmbedField(
                name=f"Right ({len(winners)})", value=names(winners) + (f" · {points(won)} each" if won else ""), inline=True
            ),
            EmbedField(
                name=f"Wrong ({len(losers)})", value=names(losers) + (f" · {points(lost)} each" if won else ""), inline=True
            ),
        ]
    else:
        if state == "open":
            description = f"Open until {closes}. Pick a side. You can switch or step out until then."
        elif state == "due":
            description = f"Closed on {closes}. Waiting for {bet['creator_name']} or an admin to settle it."
        else:
            description = f"Voided by {bet['settled_by']} on {show_time(bet['settled_at'])}. No points moved."
        empty = "Nobody yet" if state == "open" else "Nobody"
        fields = [
            EmbedField(name=f"Yes ({len(sides['yes'])})", value=names(sides["yes"], empty=empty), inline=True),
            EmbedField(name=f"No ({len(sides['no'])})", value=names(sides["no"], empty=empty), inline=True),
        ]
    return Embed(
        title=bet["claim"],
        description=description,
        color=COLORS[state],
        fields=fields,
        footer={"text": f"Bet #{bet['id']} · opened by {bet['creator_name']} · {STAKE} points a head, no money"},
    )


def bet_actions(bet, notice: bool = False) -> list[Action]:
    # Open WebUI finds the clicked button by action_id, so IDs are unique per message.
    context = {"bet": bet["id"], "notice": True} if notice else {"bet": bet["id"]}
    settle = [
        Action(action_id="settle:yes", label="Settle: Yes", context=context),
        Action(action_id="settle:no", label="Settle: No", context=context),
        Action(action_id="settle:void", label="Void", context=context),
    ]
    if bet["state"] == "open":
        return [
            Action(action_id="pick:yes", label="Yes", context=context),
            Action(action_id="pick:no", label="No", context=context),
            *settle,
        ]
    if bet["state"] == "due":
        return settle
    return [Action(action_id="reopen", label="Reopen", context=context)]


def help_embed() -> Embed:
    return Embed(
        title="Longshot",
        description=(
            "Put a claim on the record, give it a deadline, and let the channel take sides.\n\n"
            "`bet <claim> | <deadline>` opens a bet. The deadline is a date (`2027-07-01`) "
            "or a span (`90d`, `6mo`, `2y`).\n"
            "`list` shows the bets still running here.\n"
            "`board` shows who has been right.\n"
            "`show 12` posts bet 12 again.\n\n"
            f"Everyone on a bet stakes {STAKE} points. When it is settled, the losing side's points "
            "are split among the winners. Whoever opened the bet settles it, and so can an admin."
        ),
        color=COLORS["open"],
        footer={"text": f"Longshot {VERSION} · points only, no money"},
    )


def list_embed(rows) -> Embed:
    if not rows:
        text = "Nothing is running in this channel. Open one:\n`bet It snows in Paris before New Year | 2026-12-31`"
    else:
        lines = []
        for bet in rows:
            when = f"closes {show_time(bet['closes_at'])}" if bet["state"] == "open" else "waiting to be settled"
            lines.append(f"`#{bet['id']}` {bet['claim']}\n{bet['yes']} yes, {bet['no']} no · {when}")
        text = "\n\n".join(lines)
        if len(text) > 3900:
            text = text[:3900].rsplit("\n\n", 1)[0] + "\n\nThe list is longer than one card can hold."
    return Embed(title="Bets in this channel", description=text, color=COLORS["open"])


def board_embed(people: list[dict]) -> Embed:
    if not people:
        text = "No bet has been settled here yet, so nobody has a record."
    else:
        text = "\n".join(
            f"`{rank}` **{p['name']}** · {points(p['points'])} pts · {p['right']} of {p['bets']} right"
            for rank, p in enumerate(people[:20], 1)
        )
    return Embed(
        title="Scoreboard",
        description=text,
        color=COLORS["open"],
        footer={"text": "Settled bets in this channel. Voided bets do not count."},
    )


def error(text: str) -> AppChannelReply:
    return AppChannelReply(type="error", content=text)


# App


def build_app(store: Store | None = None) -> App:
    api_key, private_key = load_credentials()
    store = store or Store(DATA_DIR / "longshot.db")
    app = App(
        manifest_id="longshot",
        name="Longshot",
        description="Long bets for channels: a claim, a deadline, two sides, and a scoreboard.",
        # Mentions in, messages out. Longshot never reads other messages or history.
        scopes=["channels:mentions:read", "channels:messages:write"],
        api_key=api_key,
        private_key=private_key,
    )
    app.store = store

    def card(bet, notice: bool = False) -> dict:
        message = {"embeds": [bet_embed(bet, store.picks(bet["id"]))], "actions": bet_actions(bet, notice)}
        if notice:
            # An update replaces the text as well, so the question does not outlive its answer.
            message["content"] = f"Bet #{bet['id']} reached its deadline on {show_time(bet['closes_at'])}."
        return message

    @app.on("channel.mention")
    async def mention(event):
        text = MENTION.sub("", event.content or "").strip()
        command, _, rest = text.partition(" ")
        command, rest, now = command.lower(), rest.strip(), time.time()
        here = (event.installation_id, event.channel_id)

        if command == "bet":
            claim, pipe, deadline = rest.rpartition("|")
            claim = " ".join(claim.split())
            if not pipe or not claim:
                await event.reply("A bet needs a claim and a deadline: `bet Our launch slips past March | 2027-03-31`")
                return
            if not 8 <= len(claim) <= 200:
                await event.reply("Write the claim in 8 to 200 characters, so people know exactly what they are betting on.")
                return
            closes = parse_deadline(deadline, now)
            if isinstance(closes, str):
                await event.reply(closes)
                return
            if store.open_count(*here) >= MAX_OPEN_PER_CHANNEL:
                await event.reply(f"This channel already has {MAX_OPEN_PER_CHANNEL} unsettled bets. Settle a few first.")
                return
            bet = store.bet(store.create(event, claim, closes, now), now)
            await event.reply(**card(bet))
        elif command == "list":
            await event.reply(embeds=[list_embed(store.pending(*here, now))])
        elif command in ("board", "scoreboard", "leaderboard"):
            await event.reply(embeds=[board_embed(store.scoreboard(*here))])
        elif command == "show":
            number = rest.lstrip("#")
            bet = store.bet(int(number), now) if number.isdigit() and len(number) < 10 else None
            if bet is None or (bet["installation_id"], bet["channel_id"]) != here:
                await event.reply("There is no bet with that number in this channel. `list` shows the ones running.")
                return
            await event.reply(**card(bet))
        else:
            await event.reply(embeds=[help_embed()])

    @app.on("channel.action")
    async def action(event):
        now, actor = time.time(), event.actor
        kind, _, choice = event.data.get("action_id", "").partition(":")
        context = event.data.get("context") or {}
        bet_id, notice = context.get("bet"), context.get("notice") is True
        bet = store.bet(bet_id, now) if isinstance(bet_id, int) else None
        if bet is None or (bet["installation_id"], bet["channel_id"]) != (event.installation_id, event.channel_id):
            return error("This bet no longer exists.")
        owner = actor.get("id") == bet["creator_id"] or actor.get("is_admin") is True
        number = f"Bet #{bet['id']}"

        if kind == "pick" and choice in ("yes", "no"):
            if bet["state"] != "open":
                # Refresh the card too: it may still show buttons from before the deadline.
                if bet["state"] == "due":
                    return AppChannelReply(type="update", visibility="channel", **card(bet, notice))
                return error(f"{number} is closed. Picks stopped on {show_time(bet['closes_at'])}.")
            store.pick(bet["id"], actor, choice, now)
        elif kind == "settle" and choice in ("yes", "no", "void"):
            if not owner:
                return error(f"Only {bet['creator_name']}, who opened this bet, or an admin can settle it.")
            if bet["state"] not in ("open", "due"):
                return error(f"{number} is already settled.")
            store.settle(bet["id"], choice, actor, now)
        elif kind == "reopen":
            if not owner:
                return error(f"Only {bet['creator_name']}, who opened this bet, or an admin can reopen it.")
            if bet["state"] in ("open", "due"):
                return error(f"{number} is not settled.")
            store.reopen(bet["id"], now)
        else:
            return error("I do not know that button.")
        # Replace the clicked message in place for everyone in the channel.
        return AppChannelReply(type="update", visibility="channel", **card(store.bet(bet["id"], now), notice))

    async def announce_due():
        """Post one notice per bet whose deadline passed, with the settle buttons."""
        for bet in store.due_unannounced(time.time()):
            sides = store.picks(bet["id"])
            try:
                await app.client(bet["installation_id"]).create_message(
                    bet["channel_id"],
                    content=(
                        f"Time is up on bet #{bet['id']}. {bet['creator_name']}, how did it turn out? An admin can settle it too."
                    ),
                    embeds=[bet_embed(bet, sides)],
                    actions=bet_actions(bet, notice=True),
                    parent_id=bet["parent_id"],
                )
            except ConnectionUnavailable:
                continue  # Open WebUI is not connected right now; try again next tick.
            except APIError as exc:
                # Removed from the channel, or the channel is gone: asking again will not help.
                log.warning("Could not announce bet %s: %s", bet["id"], exc)
            except Exception:
                # The request may have gone through. Posting twice is worse than once too few.
                log.exception("Announcing bet %s failed; not retrying", bet["id"])
            store.mark_notified(bet["id"])

    app.announce_due = announce_due
    return app


def build_server(app: App) -> FastAPI:
    async def ticker():
        while True:
            try:
                await app.announce_due()
            except Exception:
                log.exception("Deadline check failed")
            await asyncio.sleep(TICK_SECONDS)

    @asynccontextmanager
    async def lifespan(server):
        task = asyncio.create_task(ticker())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            app.store.close()

    server = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    server.include_router(create_router(app, path="/events"))

    @server.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": VERSION}

    return server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    longshot = build_app()
    print(
        "\nLongshot is starting.\n"
        f"  App URL  http://127.0.0.1:{PORT}/events\n"
        f"  API key  {longshot._api_key}\n"
        "If Open WebUI runs on another machine, give it an address it can reach instead of 127.0.0.1.\n",
        flush=True,
    )
    uvicorn.run(build_server(longshot), host=HOST, port=PORT, ws_max_size=16 * 1024 * 1024, log_level="warning")
