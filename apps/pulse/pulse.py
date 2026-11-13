"""
title: Pulse
description: A small Open WebUI channel app: polls with live vote buttons, plus a tour of what apps can do.
version: 0.1.1
requirements: oix>=0.8.0
required_open_webui_version: 0.13.3

Run:  pip install "oix>=0.8.0"  &&  python pulse.py
Then install it in Open WebUI (Settings > Admin > Apps) with the App URL and
API key it prints. See README.md.
"""

import asyncio
import json
import logging
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import oix.socket
from oix import Action, App, AppChannelReply, Embed

log = logging.getLogger("pulse")

DATA_DIR = Path(os.environ.get("PULSE_DATA_DIR") or Path(__file__).parent / "data")
PORT = int(os.environ.get("PORT", "8000"))
COLOR_OPEN, COLOR_CLOSED = 0x44545B, 0x8C9A9E
MAX_OPTIONS = 10
MENTION = re.compile(r"<@A:[^>|]*(\|[^>]*)?>")
CLOCK_LEEWAY = 30


class _BackdatedClock:
    """Date connection proofs CLOCK_LEEWAY seconds in the past.

    oix signs a proof valid for 60 seconds from "now", and Open WebUI rejects
    one whose start is in its future. A PC a few seconds ahead of the server
    then fails with "App connection failed". Backdating by 30 seconds lets the
    two clocks differ by up to 30 seconds either way.
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

    API_KEY and APP_PRIVATE_KEY_FILE win when set, as in the oix README.
    Otherwise they live in DATA_DIR and are never regenerated: Open WebUI pins
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


# Polls


class Polls:
    """Polls kept in memory and saved to one JSON file after every change."""

    def __init__(self, file: Path):
        self.file = file
        try:
            self.items = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = {}

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    def create(self, question: str, options: list[str], actor: dict) -> dict:
        poll = {
            "id": uuid.uuid4().hex[:12],
            "question": question,
            "options": options,
            "votes": {},  # user id -> option index
            "names": {},  # user id -> display name
            "creator": {"id": actor.get("id"), "name": actor.get("name", "Someone")},
            "closed": False,
        }
        self.items[poll["id"]] = poll
        self.save()
        return poll

    def vote(self, poll: dict, actor: dict, option: int) -> str:
        """Record, move or (on a second click) withdraw a vote. Returns what happened."""
        user = actor["id"]
        previous = poll["votes"].get(user)
        if previous == option:
            del poll["votes"][user]
            outcome = "withdrawn"
        else:
            poll["votes"][user] = option
            poll["names"][user] = actor.get("name", "Someone")
            outcome = "moved" if previous is not None else "recorded"
        self.save()
        return outcome


def parse_poll(text: str) -> tuple[str, list[str]] | str:
    """Parse 'Question | A | B'. Returns (question, options) or an error message."""
    parts = [p.strip() for p in text.split("|")]
    question, options = parts[0], [p for p in parts[1:] if p]
    options = list(dict.fromkeys(options))  # drop duplicates, keep order
    if not question or len(options) < 2:
        return "A poll needs a question and at least two options: `poll Lunch? | Pizza | Sushi`"
    if len(options) > MAX_OPTIONS:
        return f"A poll takes at most {MAX_OPTIONS} options."
    if len(question) > 200 or any(len(o) > 80 for o in options):
        return "Keep the question under 200 characters and each option under 80."
    return question, options


def tally(poll: dict) -> list[int]:
    counts = [0] * len(poll["options"])
    for option in poll["votes"].values():
        counts[option] += 1
    return counts


def poll_embed(poll: dict) -> Embed:
    counts, total = tally(poll), len(poll["votes"])
    lines = []
    for option, count in zip(poll["options"], counts):
        share = count / total if total else 0
        bar = "█" * round(share * 12) + "░" * (12 - round(share * 12))
        lines.append(f"**{option}**\n`{bar}` {count} ({share:.0%})")
    state = "Closed" if poll["closed"] else "Click an option to vote; click it again to withdraw"
    return Embed(
        title=poll["question"],
        description="\n\n".join(lines),
        color=COLOR_CLOSED if poll["closed"] else COLOR_OPEN,
        footer={"text": f"{total} vote{'s' if total != 1 else ''} · by {poll['creator']['name']} · {state}"},
    )


def poll_actions(poll: dict) -> list[Action]:
    # Open WebUI finds the clicked button by action_id, so IDs are unique per message.
    context = {"poll": poll["id"]}
    actions = []
    if not poll["closed"]:
        actions = [
            Action(action_id=f"vote:{i}", label=option, context=context)
            for i, option in enumerate(poll["options"])
        ]
        actions.append(Action(action_id="close", label="Close poll", context=context))
    actions.append(Action(action_id="results", label="Results (.md)", context=context))
    return actions


def poll_report(poll: dict) -> str:
    counts, total = tally(poll), len(poll["votes"])
    rows = ["| Option | Votes | Share | Voters |", "| --- | ---: | ---: | --- |"]
    for i, (option, count) in enumerate(zip(poll["options"], counts)):
        voters = ", ".join(sorted(poll["names"][u] for u, o in poll["votes"].items() if o == i))
        rows.append(f"| {option} | {count} | {count / total if total else 0:.0%} | {voters or '-'} |")
    return (
        f"# {poll['question']}\n\n"
        f"Started by {poll['creator']['name']}. {total} vote{'s' if total != 1 else ''}. "
        f"{'Closed' if poll['closed'] else 'Still open'}.\n\n" + "\n".join(rows) + "\n"
    )


# App

HELP = Embed(
    title="Pulse",
    description=(
        "Mention me with a command:\n\n"
        "- `poll Lunch? | Pizza | Sushi | Salad` starts a poll\n"
        "- `help` shows this card\n\n"
        "The buttons below show the three ways an app can answer a click."
    ),
    color=COLOR_OPEN,
    footer={"text": "Pulse 0.1.1 · built with oix"},
)
HELP_ACTIONS = [
    Action(action_id="demo:private", label="Private reply"),
    Action(action_id="demo:download", label="Download a file"),
    Action(action_id="demo:slow", label="Slow job (5 s)"),
    Action(label="Apps docs", url="https://docs.openwebui.com/features/channels/apps"),
]


def build_app() -> App:
    api_key, private_key = load_credentials()
    polls = Polls(DATA_DIR / "polls.json")
    jobs: set[asyncio.Task] = set()
    app = App(
        manifest_id="pulse",
        name="Pulse",
        description="Polls with live vote buttons, and a tour of what channel apps can do.",
        # The least an app needs to be mentioned and to answer. It never sees
        # other channel messages or history.
        scopes=["channels:mentions:read", "channels:messages:write"],
        api_key=api_key,
        private_key=private_key,
    )
    app.polls = polls

    @app.on("channel.mention")
    async def mention(event):
        text = MENTION.sub("", event.content or "").strip()
        command, _, rest = text.partition(" ")
        if command.lower() == "poll":
            parsed = parse_poll(rest)
            if isinstance(parsed, str):
                await event.reply(parsed)
                return
            poll = polls.create(*parsed, event.actor)
            await event.reply(embeds=[poll_embed(poll)], actions=poll_actions(poll))
        else:
            await event.reply(embeds=[HELP], actions=HELP_ACTIONS)

    @app.on("channel.action")
    async def action(event):
        action_id = event.data.get("action_id", "")

        if action_id == "demo:private":
            return AppChannelReply(
                title="Private reply",
                content=f"Hi {event.actor.get('name', 'there')}. Only you can see this, and it needs no write scope.",
            )
        if action_id == "demo:download":
            return AppChannelReply(
                type="download",
                filename="pulse-sample.md",
                content="# Hello from Pulse\n\nApps can hand you a Markdown file after a click.\n",
            )
        if action_id == "demo:slow":
            # A click must be answered within 3 seconds: acknowledge now, post later.
            task = asyncio.create_task(slow_job(event))
            jobs.add(task)
            task.add_done_callback(jobs.discard)
            return AppChannelReply(type="ack")

        poll = polls.items.get((event.data.get("context") or {}).get("poll"))
        if poll is None:
            return AppChannelReply(type="error", content="This poll no longer exists.")

        if action_id == "results":
            return AppChannelReply(type="download", filename="poll-results.md", content=poll_report(poll))
        if poll["closed"]:
            return AppChannelReply(type="error", content="This poll is closed.")
        if action_id == "close":
            if event.actor.get("id") != poll["creator"]["id"] and not event.actor.get("is_admin"):
                return AppChannelReply(type="error", content="Only the poll's creator or an admin can close it.")
            poll["closed"] = True
            polls.save()
        elif action_id.startswith("vote:"):
            option = int(action_id.split(":")[1])
            if not 0 <= option < len(poll["options"]):
                return AppChannelReply(type="error", content="Unknown option.")
            polls.vote(poll, event.actor, option)
        else:
            return AppChannelReply(type="error", content="Unknown button.")
        # Replace the poll message in place for everyone in the channel.
        return AppChannelReply(
            type="update", visibility="channel", embeds=[poll_embed(poll)], actions=poll_actions(poll)
        )

    async def slow_job(event):
        try:
            await asyncio.sleep(5)
            # Recheck that the user may still use the app here before acting for them.
            await event.client.authorize(event.execution_token)
            await event.client.create_message(
                event.channel_id,
                content=f"The slow job {event.actor.get('name', 'someone')} started is done.",
                parent_id=event.parent_id,
            )
        except Exception:
            log.exception("Slow job failed")

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    pulse = build_app()
    print(
        "\nPulse is starting.\n"
        f"  App URL  http://127.0.0.1:{PORT}/events\n"
        f"  API key  {pulse._api_key}\n"
        f"Open WebUI must allow this address: APP_ALLOWED_PRIVATE_ORIGINS=http://127.0.0.1:{PORT}\n",
        flush=True,
    )
    pulse.run(port=PORT)
