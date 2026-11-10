"""👥 Helpers: up to three people who get «сайт лёг» and «снова работает»
(and the first reminder of a site still down) — the owner on holiday or
with a dead phone must not mean that nobody knows a site is down. A helper
cannot steer the bot. The owner invites with a one-off link that lives a
day and removes a helper after a confirmation; a helper leaves with /stop."""

import asyncio
import json
import re
import secrets
from datetime import UTC, datetime, timedelta

from db.database import get_state, set_state
from utils.text import parse_iso_utc

MAX_HELPERS = 3
INVITE_TTL = timedelta(hours=24)
PAYLOAD_PREFIX = "helper_"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")   # what token_urlsafe gives
# aiogram handles updates concurrently: two people opening the same link at
# once must not both pass. One process — one lock is enough.
_lock = asyncio.Lock()


async def helpers() -> list[dict]:
    """[{"chat_id": "123", "name": "Аня"}] in the order they joined."""
    try:
        return json.loads(await get_state("helpers") or "[]")
    except ValueError:
        return []


async def _save(items: list[dict]):
    await set_state("helpers", json.dumps(items, ensure_ascii=False) if items else None)


async def _invite() -> dict:
    try:
        invite = json.loads(await get_state("helper_invite") or "{}")
    except ValueError:
        return {}
    return invite if isinstance(invite, dict) else {}


async def _new_invite() -> tuple[str, datetime]:
    token = secrets.token_urlsafe(9)
    until = datetime.now(UTC) + INVITE_TTL
    await set_state("helper_invite", json.dumps({"token": token, "until": until.isoformat()}))
    return token, until


async def new_invite() -> tuple[str, datetime]:
    """A fresh one-off link (token, valid until); the previous one stops working."""
    async with _lock:
        return await _new_invite()


async def create_invite() -> str:
    return (await new_invite())[0]


async def live_or_new_invite() -> tuple[str, datetime]:
    """The link shown before, while it still works: a second look at the
    screen must not kill the link already sent to someone."""
    async with _lock:
        invite = await _invite()
        until = parse_iso_utc(invite.get("until"))
        if isinstance(invite.get("token"), str) and until and until > datetime.now(UTC):
            return invite["token"], until
        return await _new_invite()


async def accept(token: str, chat_id: int | str, name: str) -> str:
    """'ok' · 'already' · 'invalid' (wrong, used or expired link) · 'full'."""
    async with _lock:
        invite = await _invite()
        until = parse_iso_utc(invite.get("until"))
        good = (isinstance(invite.get("token"), str) and _TOKEN.fullmatch(token or "") is not None
                and secrets.compare_digest(invite["token"].encode(), token.encode()))
        if not good or not until or until < datetime.now(UTC):
            return "invalid"
        items = await helpers()
        if any(h["chat_id"] == str(chat_id) for h in items):
            return "already"
        if len(items) >= MAX_HELPERS:
            return "full"
        items.append({"chat_id": str(chat_id), "name": name[:40] or "без имени"})
        await _save(items)
        await set_state("helper_invite", None)   # one link, one helper
        return "ok"


async def remove(chat_id: int | str) -> dict | None:
    """The removed helper, or None if there was none with this chat."""
    async with _lock:
        items = await helpers()
        gone = next((h for h in items if h["chat_id"] == str(chat_id)), None)
        if gone:
            await _save([h for h in items if h is not gone])
        return gone


async def is_helper(chat_id: int | str | None) -> bool:
    return chat_id is not None and any(h["chat_id"] == str(chat_id) for h in await helpers())
