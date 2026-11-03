"""The one door every message to the admin goes through.

Three priorities decide what happens with mute, quiet hours and per-site
pauses; nothing else in the codebase reasons about them:

    CRITICAL  rings; bypasses mute and quiet hours (site down, NS change…)
    NORMAL    silent delivery; deferred to the queue while muted or during
              quiet hours, flushed later as one digest
    DIGEST    silent; deferred only by an explicit mute (the morning report
              IS the digest, so it goes through quiet hours)

A site_id turns the message into a per-site alert: while that site is
paused or inside a maintenance window it is dropped, whatever the priority.
send() returns True when the message was delivered or queued — callers that
set one-shot "already alerted" flags must only do so on True.
"""

import logging
from datetime import UTC, datetime, timedelta
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from db.database import (
    delete_notifications,
    get_state,
    peek_notifications,
    queue_notification,
    set_state,
)
from services import maintenance, runtime, settings
from utils.clock import now_local
from utils.text import SAFE_LIMIT, clip, parse_iso_utc

logger = logging.getLogger(__name__)


class Priority(Enum):
    CRITICAL = "critical"
    NORMAL = "normal"
    DIGEST = "digest"


def priority_for(severity: str | None) -> Priority:
    """Delivery class of an incident level: 🔴 (critical) rings, 🟠 is silent.
    Keeps the icon in the text and the way it arrives in agreement."""
    return Priority.CRITICAL if severity == "critical" else Priority.NORMAL


_bot: Bot | None = None


def configure(bot: Bot):
    global _bot
    _bot = bot


# ── Mute ─────────────────────────────────────────────────────────────────────

async def mute_until() -> datetime | None:
    """Aware UTC deadline while muted, else None."""
    deadline = parse_iso_utc(await get_state("mute_until"))
    if deadline and deadline > datetime.now(UTC):
        return deadline
    return None


async def is_muted() -> bool:
    return await mute_until() is not None


async def set_mute(duration: timedelta | None) -> datetime | None:
    """Mute for `duration` (None = unmute). Returns the deadline."""
    if duration is None:
        await set_state("mute_until", None)
        return None
    deadline = datetime.now(UTC) + duration
    await set_state("mute_until", deadline.isoformat())
    return deadline


def in_quiet_hours(hour: int | None = None) -> bool:
    """True during configured quiet hours (local time)."""
    qh = settings.quiet_hours()
    if not qh:
        return False
    if hour is None:
        hour = now_local().hour
    start, end = qh
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# ── Sending ──────────────────────────────────────────────────────────────────

async def send(text: str, priority: Priority = Priority.NORMAL, *,
               reply_markup=None, site_id: int | None = None, always_ring: bool = False) -> Message | bool:
    """The sent Message when delivered now, True when queued, False when
    dropped or failed — so `if sent:` keeps working for one-shot flags.
    With «будить только когда сайт лёг» on, CRITICAL messages that are not
    marked always_ring are treated as NORMAL."""
    if priority is Priority.CRITICAL and settings.ring_only_down() and not always_ring:
        priority = Priority.NORMAL
    chat_id = runtime.admin_chat_id()
    if not chat_id or _bot is None:
        logger.warning("No admin yet, dropping message: %s", text[:60])
        return False
    if site_id is not None and await maintenance.is_paused(site_id):
        logger.info("Site %s paused — dropping: %s", site_id, text[:60])
        return False
    if priority is not Priority.CRITICAL:
        muted = await is_muted()
        if muted or (priority is Priority.NORMAL and in_quiet_hours()):
            markup = reply_markup.model_dump_json(exclude_none=True) if reply_markup is not None else None
            await queue_notification(text, markup)
            logger.info("Queued (%s): %s", "muted" if muted else "quiet hours", text[:60])
            return True
    try:
        # Informational messages arrive silently; only critical ones ring.
        return await _bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                       disable_notification=priority is not Priority.CRITICAL)
    except Exception as e:
        logger.error("Failed to send message to admin: %s", e)
        return False


async def edit(message_id: int, text: str, reply_markup=None) -> bool:
    """Update an alert in place (follow-up re-checks). Silent on 'not modified'."""
    chat_id = runtime.admin_chat_id()
    if not chat_id or _bot is None:
        return False
    try:
        await _bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                     reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        return "not modified" in str(e)
    except Exception as e:
        logger.warning("Failed to edit message %s: %s", message_id, e)
        return False


async def send_document(document, caption: str) -> bool:
    chat_id = runtime.admin_chat_id()
    if not chat_id or _bot is None:
        return False
    try:
        await _bot.send_document(chat_id=chat_id, document=document,
                                 caption=caption, disable_notification=True)
        return True
    except Exception as e:
        logger.error("Failed to send document to admin: %s", e)
        return False


async def flush_queue():
    """Deliver deferred notifications as a digest — several messages when
    the backlog is long, deleting only the rows that were actually sent."""
    chat_id = runtime.admin_chat_id()
    if _bot is None or not chat_id or in_quiet_hours() or await is_muted():
        return
    rows = await peek_notifications()
    if not rows:
        return
    header = "🌙 Накопилось, пока было тихо:\n\n"
    batch: list[dict] = []
    text = header
    for row in rows:
        markup = _markup(row.get("reply_markup"))
        if markup is not None:
            # A message with buttons goes alone, with them, in its place.
            if batch:
                if not await _deliver_batch(chat_id, text, batch):
                    return
                text, batch = "🌙 (продолжение)\n\n", []
            if not await _deliver_batch(chat_id, row["text"], [row], markup):
                return
            continue
        # Each item is cut HTML-safely to fit one message; a batch is never cut.
        item = clip(f"— {row['text']}", SAFE_LIMIT - len(header) - 60) + "\n\n"
        if batch and len(text) + len(item) > SAFE_LIMIT:
            if not await _deliver_batch(chat_id, text, batch):
                return
            text, batch = "🌙 (продолжение)\n\n", []
        text += item
        batch.append(row)
    if batch:
        await _deliver_batch(chat_id, text, batch)


def _markup(raw: str | None) -> InlineKeyboardMarkup | None:
    if not raw:
        return None
    try:
        return InlineKeyboardMarkup.model_validate_json(raw)
    except ValueError:
        return None


async def _deliver_batch(chat_id: str, text: str, batch: list[dict], markup=None) -> bool:
    try:
        await _bot.send_message(chat_id=chat_id, text=text.rstrip(), reply_markup=markup,
                                disable_notification=True)
    except TelegramBadRequest as e:
        # A stored text Telegram cannot parse must not jam the queue forever:
        # the same batch would fail every ten minutes. Deliver it as plain text.
        logger.error("Quiet queue: Telegram rejected a batch (%s) — sending it as plain text", e)
        try:
            await _bot.send_message(chat_id=chat_id, text=text.rstrip(), reply_markup=markup,
                                    parse_mode=None, disable_notification=True)
        except Exception as e2:
            logger.error("Failed to flush quiet queue: %s", e2)
            return False
    except Exception as e:
        logger.error("Failed to flush quiet queue: %s", e)
        return False
    await delete_notifications([r["id"] for r in batch])
    return True
