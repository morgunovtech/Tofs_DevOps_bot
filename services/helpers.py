"""👥 Helpers: up to three people who get only the alerts that ring (🔴) —
the owner on holiday or with a dead phone must not mean that nobody knows
a site is down. A helper cannot steer the bot. The owner invites with a
one-off link that lives a day and removes a helper with one tap; a helper
leaves with /stop."""

import json
import secrets
from datetime import UTC, datetime, timedelta

from db.database import get_state, set_state
from utils.text import parse_iso_utc

MAX_HELPERS = 3
INVITE_TTL = timedelta(hours=24)
PAYLOAD_PREFIX = "helper_"


async def helpers() -> list[dict]:
    """[{"chat_id": "123", "name": "Аня"}] in the order they joined."""
    try:
        return json.loads(await get_state("helpers") or "[]")
    except ValueError:
        return []


async def _save(items: list[dict]):
    await set_state("helpers", json.dumps(items, ensure_ascii=False) if items else None)


async def create_invite() -> str:
    """A fresh one-off token; the previous link stops working."""
    token = secrets.token_urlsafe(9)
    until = (datetime.now(UTC) + INVITE_TTL).isoformat()
    await set_state("helper_invite", json.dumps({"token": token, "until": until}))
    return token


async def accept(token: str, chat_id: int | str, name: str) -> str:
    """'ok' · 'already' · 'invalid' (wrong, used or expired link) · 'full'."""
    try:
        invite = json.loads(await get_state("helper_invite") or "{}")
    except ValueError:
        invite = {}
    until = parse_iso_utc(invite.get("until"))
    if not invite.get("token") or not secrets.compare_digest(invite["token"], token) \
            or not until or until < datetime.now(UTC):
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
    items = await helpers()
    gone = next((h for h in items if h["chat_id"] == str(chat_id)), None)
    if gone:
        await _save([h for h in items if h is not gone])
    return gone


async def is_helper(chat_id: int | str | None) -> bool:
    return chat_id is not None and any(h["chat_id"] == str(chat_id) for h in await helpers())
