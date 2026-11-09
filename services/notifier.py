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

A message Telegram did not take (no network, 5xx, flood limit) is not lost:
it goes to the outbox and is retried every minute, with a note of when it
should have arrived. Without that a lost «сайт лёг» would never come back —
the incident is already open, so the next check has nothing new to say.
"""

import logging
from datetime import UTC, datetime, timedelta
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from db.database import (
    delete_notifications,
    get_state,
    outbox_add,
    outbox_delete,
    outbox_peek,
    peek_notifications,
    queue_notification,
    set_state,
)
from services import maintenance, runtime, settings
from utils.clock import now_local, to_local
from utils.text import SAFE_LIMIT, clip, parse_iso_utc, parse_sqlite_utc

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
    if await _defer(priority):
        await queue_notification(text, _markup_json(reply_markup))
        logger.info("Queued (mute / quiet hours): %s", text[:60])
        return True
    sent = await _deliver(chat_id, text, reply_markup, priority)
    if sent is None:
        await outbox_add(text, _markup_json(reply_markup), priority.value)
        logger.warning("Telegram did not take the message — kept for a retry: %s", text[:60])
        return True
    return sent


async def _defer(priority: Priority) -> bool:
    """Mute holds everything but critical; quiet hours hold NORMAL only."""
    if priority is Priority.CRITICAL:
        return False
    return await is_muted() or (priority is Priority.NORMAL and in_quiet_hours())


def _markup_json(reply_markup) -> str | None:
    return reply_markup.model_dump_json(exclude_none=True) if reply_markup is not None else None


async def _deliver(chat_id: str, text: str, reply_markup, priority: Priority) -> Message | bool | None:
    """The sent Message; False when it can never be delivered (the text is
    broken twice over, or the bot is blocked); None when Telegram may take it
    later. Informational messages arrive silently; only critical ones ring."""
    silent = priority is not Priority.CRITICAL
    try:
        return await _bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                       disable_notification=silent)
    except TelegramBadRequest as e:
        # Our own markup Telegram cannot parse: a retry would fail the same
        # way, plain text still says what happened.
        logger.error("Telegram rejected a message (%s) — sending it as plain text", e)
        try:
            return await _bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup,
                                           parse_mode=None, disable_notification=silent)
        except Exception as e2:
            logger.error("Failed to send message to admin: %s", e2)
            return False
    except TelegramForbiddenError as e:
        logger.error("The admin blocked the bot or left the chat: %s", e)
        return False
    except Exception as e:
        logger.error("Failed to send message to admin: %s", e)
        return None


async def flush_outbox():
    """Every minute: what Telegram did not take, oldest first, each with
    the time it should have arrived. Stops at the first failure — Telegram
    is still away. A silent message that now falls into quiet hours or mute
    joins the quiet queue like any other."""
    chat_id = runtime.admin_chat_id()
    if _bot is None or not chat_id:
        return
    for row in await outbox_peek():
        priority = Priority(row["priority"])
        late = parse_sqlite_utc(row["created_at"])
        note = f"⏳ Не дошло в {to_local(late).strftime('%H:%M')} — Telegram не отвечал. Отправляю сейчас:\n\n" if late else ""
        text = clip(note + row["text"])
        if await _defer(priority):
            await queue_notification(text, row["reply_markup"])
        elif await _deliver(chat_id, text, _markup(row["reply_markup"]), priority) is None:
            return
        await outbox_delete(row["id"])


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
