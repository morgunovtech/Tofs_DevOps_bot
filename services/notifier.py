"""The one door every message to the admin goes through.

Three priorities decide what happens with mute, quiet hours and per-site
pauses; nothing else in the codebase reasons about them:

    CRITICAL  rings; bypasses mute and quiet hours (site down, NS change…)
    NORMAL    silent delivery; deferred to the queue while muted or during
              quiet hours, flushed later as one digest
    DIGEST    silent; deferred only by an explicit mute (the morning report
              IS the digest, so it goes through quiet hours)

A site_id turns the message into a per-site alert: while that site is
paused («🔧 Я чиню») or inside a maintenance window it is dropped, whatever
the priority — unless it opens an incident (announces=<id>): then it is
HELD and said once the silence is over, if the problem is still there; and
the recovery of an announced fall (resolves=<id>) comes through the silence.
send() returns True when the message was delivered or queued — callers that
set one-shot "already alerted" flags must only do so on True.

What rings (CRITICAL) also goes to the helpers (services.helpers), as text
without buttons — the buttons steer the bot, and only the owner can.

A message Telegram did not take (no network, 5xx, flood limit) is not lost:
it goes to the outbox and is retried every minute, about one a second, with a
note of when it should have arrived; a group that became a supergroup is
followed, a short flood-control wait is sat out once a minute. Without that a lost «сайт лёг» would never come back —
the incident is already open, so the next check has nothing new to say.
"""

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

import aiohttp
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup, Message

from db.database import (
    OUTBOX_MAX,
    PENDING_DROPPED,
    PENDING_MAX,
    delete_notifications,
    get_active_incidents,
    get_state,
    get_states_like,
    incident_alerted_at,
    mark_incident_alerted,
    outbox_add,
    outbox_delete,
    outbox_peek,
    peek_notifications,
    queue_notification,
    set_state,
)
from services import health, helpers, maintenance, runtime, settings
from utils.clock import now_local, to_local
from utils.text import SAFE_LIMIT, clip, parse_iso_utc, parse_sqlite_utc, plural

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

RETRY_AFTER_MAX = 30     # s: a flood-control wait this short is sat out in place…
FLOOD_WAIT_EVERY = 60    # s: …but once a minute at most — a storm must not hold the check tick
HELPER_TIMEOUT = 15      # s: a silent helper must not hold the owner's alert
FLUSH_PACE_SEC = 1.0     # s between outbox rows: about a message a second per chat
DROPPED_KEY = "outbox_dropped"
_no_wait_until = 0.0     # monotonic: until then a 429 goes straight to the outbox


class _Maybe:
    """The request left, the answer did not come: Telegram may have shown it."""


MAYBE = _Maybe()


def _later(result) -> bool:
    return result is None or result is MAYBE


async def _call(method, **kwargs):
    """One Bot API call to the ADMIN chat that lives through the two answers
    that are not failures: «this group is a supergroup now» (follow it, retry)
    and «flood control, retry in N s» (wait once, when N is short and nobody
    waited in the last minute). Anything else raises."""
    global _no_wait_until
    try:
        return await method(chat_id=runtime.admin_chat_id(), **kwargs)
    except TelegramMigrateToChat as e:
        if await runtime.move_admin_chat(e.migrate_to_chat_id) and runtime.env_chat_outdated():
            try:
                await _bot.send_message(chat_id=runtime.admin_chat_id(), text=(
                    "Группа стала супергруппой — теперь пишу сюда. В .env замени "
                    f"TELEGRAM_ADMIN_CHAT_ID на <code>{runtime.admin_chat_id()}</code>."))
            except Exception as note_error:
                logger.warning("Could not ask to update TELEGRAM_ADMIN_CHAT_ID: %s", note_error)
    except TelegramRetryAfter as e:
        now = time.monotonic()
        if e.retry_after > RETRY_AFTER_MAX or now < _no_wait_until:
            raise
        _no_wait_until = now + FLOOD_WAIT_EVERY
        logger.warning("Telegram flood control: waiting %s s", e.retry_after)
        await asyncio.sleep(e.retry_after)
    return await method(chat_id=runtime.admin_chat_id(), **kwargs)


async def send(text: str, priority: Priority = Priority.NORMAL, *,
               reply_markup=None, site_id: int | None = None, always_ring: bool = False,
               to_helpers: bool = True, announces: int | None = None,
               resolves: int | None = None) -> Message | bool:
    """The sent Message when delivered now, True when queued, False when
    dropped or failed — so `if sent:` keeps working for one-shot flags.
    With «будить только когда сайт лёг» on, CRITICAL messages that are not
    marked always_ring are treated as NORMAL.

    announces=<incident id>: the message opens an incident — on any success
    (delivered, queued, kept for a retry) the incident is marked announced;
    a pause or a maintenance window holds it instead of dropping it.
    resolves=<incident id>: the message says the problem is gone — it passes
    a pause or a window only when the fall itself was announced (a ✅ about a
    fall nobody heard of would ring at night for nothing)."""
    if priority is Priority.CRITICAL and settings.ring_only_down() and not always_ring:
        priority = Priority.NORMAL
    # The last line of defence on length: callers that need every line
    # still split the text themselves.
    text = clip(text)
    if not runtime.admin_chat_id() or _bot is None:
        logger.warning("No admin yet, dropping message: %s", text[:60])
        return False
    if site_id is not None and await maintenance.is_paused(site_id) \
            and not (resolves is not None and await announced_at(resolves)):
        if announces is not None:
            try:
                await _hold(announces, site_id, text, reply_markup, priority, always_ring, to_helpers)
                logger.info("Site %s paused — holding until the silence ends: %s", site_id, text[:60])
            except sqlite3.OperationalError as e:
                health.db_write_failed(e)
                logger.error("Site %s paused and the database refuses the hold — dropped: %s", site_id, text[:60])
        else:
            logger.info("Site %s paused — dropping: %s", site_id, text[:60])
        return False
    if await _defer(priority) and await _queue(text, _markup_json(reply_markup)):
        logger.info("Queued (mute / quiet hours): %s", text[:60])
        result: Message | bool = True
    else:
        sent = await _deliver(text, reply_markup, priority)
        if _later(sent):
            # Into the outbox FIRST: the helpers below may take a while.
            await _keep(_maybe_note() + text if sent is MAYBE else text, reply_markup, priority)
            sent = True
        if priority is Priority.CRITICAL and to_helpers:
            await _tell_helpers(text)
        result = sent
    if result and announces is not None:
        await _mark_announced(announces)
    return result


# ── Pauses and windows postpone, they do not throw away ─────────────────────

HELD = "held:"
_released: set[int] = set()     # held alerts said this process; a refused delete must not repeat them


async def announced_at(incident_id: int) -> datetime | None:
    """When the owner was told about this incident (delivered, queued or
    kept for a retry); None — never. The one place that reads the mark:
    incidents.alerted_at, which reports.scheduler.deliver_missed_alerts
    uses to re-send what is still owed."""
    return parse_sqlite_utc(await incident_alerted_at(incident_id))


async def _mark_announced(incident_id: int):
    """Best effort: the message is out, a database that refuses the write
    must not turn into «not announced» and a repeat (PLAN-09 watches the disk)."""
    try:
        await mark_incident_alerted(incident_id)
    except sqlite3.OperationalError as e:
        logger.warning("Could not mark incident %s as announced: %s", incident_id, e)


async def _hold(incident_id: int, site_id: int, text: str, reply_markup, priority: Priority,
                always_ring: bool, to_helpers: bool):
    await set_state(f"{HELD}{incident_id}", json.dumps({
        "site_id": site_id, "text": text, "markup": _markup_json(reply_markup), "priority": priority.value,
        "always_ring": always_ring, "to_helpers": to_helpers, "at": datetime.now(UTC).isoformat()}))


async def _forget_held(key: str):
    try:
        await set_state(key, None)
    except sqlite3.OperationalError as e:
        logger.warning("Could not drop %s: %s", key, e)


async def release_held():
    """Every minute: an alert held by a «🔧» pause or a maintenance window
    is said once the silence is over — if its problem is still open. A
    problem that came and went inside the silence is forgotten quietly."""
    held = await get_states_like(HELD)
    if not held:
        return
    open_ids = {inc["id"] for inc in await get_active_incidents()}
    for key, raw in held.items():
        incident_id = int(key.removeprefix(HELD))
        if incident_id in _released:            # said already; only the delete failed
            await _forget_held(key)
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            item = None
        if incident_id not in open_ids or not isinstance(item, dict):
            await _forget_held(key)
            continue
        if await maintenance.is_paused(item["site_id"]):
            continue
        at = parse_iso_utc(item.get("at"))
        note = (f"🔕 Это случилось в {to_local(at).strftime('%H:%M')}, пока про сайт было тихо "
                f"(«🔧 Я чиню» или плановые работы), и до сих пор не прошло:\n\n") if at else ""
        sent = await send(clip(note + item["text"]), Priority(item["priority"]),
                          reply_markup=_markup(item.get("markup")), site_id=item["site_id"],
                          always_ring=item.get("always_ring", False), to_helpers=item.get("to_helpers", False),
                          announces=incident_id)
        if sent:
            _released.add(incident_id)
            await _forget_held(key)


def _maybe_note() -> str:
    return (f"⚠️ Если это сообщение уже было — это повтор: в {now_local():%H:%M} Telegram не подтвердил "
            "доставку.\n\n")


def _late_note(late: datetime) -> str:
    local = to_local(late)
    at = f"{local:%H:%M}" if local.date() == now_local().date() else f"{local:%d.%m %H:%M}"
    return f"⏳ Задержалось с {at}, отправляю сейчас:\n\n"


@dataclass
class _Unsent:
    """A message Telegram did not take while the database refused the outbox
    row: kept in memory (gone after a restart) instead of being dropped."""
    text: str
    reply_markup: str | None
    priority: str
    created: datetime


_unsent: deque[_Unsent] = deque(maxlen=OUTBOX_MAX)
# Outbox rows already delivered whose DELETE the database refused — not sent twice.
_delivered_rows: set[int] = set()


async def send_direct(text: str) -> bool:
    """Straight to the owner with no database on the way (no pause check, no
    quiet queue, no outbox): for «не могу записать в базу» itself. Rings —
    unless the owner asked to be woken only by «сайт лёг»."""
    if not runtime.admin_chat_id() or _bot is None:
        return False
    priority = Priority.NORMAL if settings.ring_only_down() else Priority.CRITICAL
    return bool(await _deliver(text, None, priority))


async def _queue(text: str, markup_json: str | None) -> bool:
    """Into the quiet queue; False when the database refused it — the caller
    then delivers right away (silently): late beats lost."""
    if health.db_failing():
        return False
    try:
        await queue_notification(text, markup_json)
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
        logger.warning("Quiet queue unavailable (%s) — delivering now: %s", e, text[:60])
        return False
    return True


async def _keep(text: str, reply_markup, priority: Priority):
    """Into the outbox; into memory while the database refuses writes."""
    markup_json = _markup_json(reply_markup)
    logger.warning("Telegram did not take the message — kept for a retry: %s", text[:60])
    if not health.db_failing():
        try:
            dropped = await outbox_add(text, markup_json, priority.value)
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)
        else:
            if dropped:
                logger.warning("Outbox full: dropped %d undelivered message(s)", dropped)
                try:
                    await set_state(DROPPED_KEY, str(await _dropped() + dropped))
                except sqlite3.OperationalError as e:
                    health.db_write_failed(e)
            return
    if len(_unsent) == _unsent.maxlen:
        logger.error("In-memory outbox is full — the oldest message is dropped: %s", _unsent[0].text[:60])
    _unsent.append(_Unsent(text, markup_json, priority.value, datetime.now(UTC)))


async def _dropped() -> int:
    try:
        return int(await get_state(DROPPED_KEY) or 0)
    except ValueError:
        return 0


async def send_to(chat_id: str, text: str) -> bool:
    """One line to someone who is not the admin (a helper being removed)."""
    if _bot is None:
        return False
    try:
        await _bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as e:
        logger.warning("Could not tell %s: %s", chat_id, e)
        return False


async def _tell_helper(chat_id: str, text: str):
    try:
        await _bot.send_message(chat_id=chat_id, text=text, disable_notification=False,
                                request_timeout=HELPER_TIMEOUT)
    except TelegramBadRequest:
        await _bot.send_message(chat_id=chat_id, text=text, parse_mode=None, disable_notification=False,
                                request_timeout=HELPER_TIMEOUT)


async def _tell_helpers(text: str):
    """Best effort, all at once, 15 s each: the owner's copy is the one that is retried."""
    targets = await helpers.helpers()
    results = await asyncio.gather(*(_tell_helper(h["chat_id"], text) for h in targets),
                                   return_exceptions=True)
    for h, result in zip(targets, results, strict=True):
        if isinstance(result, Exception):
            logger.warning("Helper %s did not get the alert: %s", h["chat_id"], result)


async def _defer(priority: Priority) -> bool:
    """Mute holds everything but critical; quiet hours hold NORMAL only."""
    if priority is Priority.CRITICAL:
        return False
    return await is_muted() or (priority is Priority.NORMAL and in_quiet_hours())


def _markup_json(reply_markup) -> str | None:
    return reply_markup.model_dump_json(exclude_none=True) if reply_markup is not None else None


async def _deliver(text: str, reply_markup, priority: Priority) -> Message | bool | _Maybe | None:
    """The sent Message; False when it can never be delivered (refused as
    HTML and as plain text, or the bot is blocked); None or MAYBE when
    Telegram may take it later. Only critical messages ring."""
    silent = priority is not Priority.CRITICAL
    try:
        try:
            sent = await _call(_bot.send_message, text=text, reply_markup=reply_markup,
                               disable_notification=silent)
        except TelegramBadRequest as e:
            # Our own markup Telegram cannot parse: plain text still says what happened.
            logger.error("Telegram rejected a message (%s) — sending it as plain text", e)
            sent = await _call(_bot.send_message, text=text, reply_markup=reply_markup, parse_mode=None,
                               disable_notification=silent)
        health.delivery_ok()
        return sent
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logger.error("Telegram will never take this message: %s", e)
        if isinstance(e, TelegramForbiddenError) or "chat not found" in str(e).lower():
            health.delivery_failed()       # blocked, or thrown out of the admin chat: /health goes red
        return False
    except TelegramNetworkError as e:
        cause = e.__cause__
        maybe = isinstance(cause, (TimeoutError, aiohttp.ServerDisconnectedError, aiohttp.ClientPayloadError))
        logger.error("Failed to send message to admin: %s (%s)", e, "may have arrived" if maybe else "not sent")
        return MAYBE if maybe else None
    except Exception as e:     # 5xx, a long flood-control wait, no network: later
        logger.error("Failed to send message to admin: %s", e)
        return None


async def flush_outbox():
    """Every minute: what Telegram did not take, oldest first, about one a
    second, each with the time it should have arrived. Stops at the first
    failure — Telegram is still away. A silent message that now falls into
    quiet hours or mute joins the quiet queue like any other."""
    if _bot is None or not runtime.admin_chat_id():
        return
    # Memory first: what the database could not take while it was failing.
    while _unsent:
        u = _unsent[0]
        if not await _retry(u.text, u.reply_markup, Priority(u.priority), u.created):
            return
        _unsent.popleft()
    for i, row in enumerate(await outbox_peek()):
        if i:
            await asyncio.sleep(FLUSH_PACE_SEC)
        if row["id"] not in _delivered_rows and not await _retry(
                row["text"], row["reply_markup"], Priority(row["priority"]), parse_sqlite_utc(row["created_at"])):
            return
        try:
            await outbox_delete(row["id"])
            _delivered_rows.discard(row["id"])
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)
            _delivered_rows.add(row["id"])     # delivered: never again, even if the row stays
    lost = await _dropped()
    if lost and not _later(await _deliver(
            f"⚠️ Пока Telegram не принимал сообщения, {lost} из них не поместились в очередь и потерялись. "
            "Что сейчас не так — на 🏠 Главной.", None, Priority.NORMAL)):
        try:
            await set_state(DROPPED_KEY, None)
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)


async def _retry(text: str, markup_json: str | None, priority: Priority, late: datetime | None) -> bool:
    """One kept message, with the time it should have arrived; False while
    Telegram is still away. A silent message that now falls into quiet hours
    or mute joins the quiet queue like any other."""
    text = clip((_late_note(late) if late else "") + text)
    if await _defer(priority) and await _queue(text, markup_json):
        return True
    return not _later(await _deliver(text, _markup(markup_json), priority))


async def edit(message_id: int, text: str, reply_markup=None) -> bool:
    """Update an alert in place (follow-up re-checks). Silent on 'not modified'."""
    if not runtime.admin_chat_id() or _bot is None:
        return False
    try:
        await _call(_bot.edit_message_text, text=text, message_id=message_id, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as e:
        return "not modified" in str(e)
    except Exception as e:
        logger.warning("Failed to edit message %s: %s", message_id, e)
        return False


async def send_document_to_owner(document, caption: str) -> str | None:
    """A file only the owner may hold (a copy of the database) goes to the
    owner's private chat — never to a group admin chat, where every member
    would get it. None when sent; otherwise why not, in words for the owner.
    Not through _call(): a private chat never becomes a supergroup."""
    chat_id = runtime.owner_private_chat()
    if _bot is None:
        return "бот ещё не запущен"
    if not chat_id:
        return "не знаю твой личный чат со мной: укажи TELEGRAM_ADMIN_USER_ID в настройках сервера"
    try:
        await _bot.send_document(chat_id=chat_id, document=document, caption=caption,
                                 disable_notification=True)
        return None
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        # A bot cannot write first: until the owner opens the private chat
        # Telegram answers 403 «bot can't initiate conversation» or 400 «chat
        # not found» [not verified against the official Bot API text].
        logger.warning("Could not send a file to the owner's private chat: %s", e)
        return "ты ещё не открывал личный чат со мной — открой его и нажми /start"
    except Exception as e:
        # Not str(e): aiohttp's messages can carry the API address with the bot token.
        logger.error("Failed to send a file to the owner: %r", type(e).__name__)
        return "Telegram не принял файл (сеть или сбой на его стороне)"


async def flush_queue():
    """Deliver deferred notifications as a digest — several messages when
    the backlog is long, deleting only the rows that were actually sent."""
    if _bot is None or not runtime.admin_chat_id() or in_quiet_hours() or await is_muted() \
            or health.db_failing():
        return      # a delivered batch could not be deleted: it would come again in ten minutes
    rows = await peek_notifications()
    if not rows:
        return
    header = "🌙 Накопилось, пока было тихо:\n\n"
    dropped = int(await get_state(PENDING_DROPPED) or 0)
    if dropped > 0:
        header += (f"(ещё {dropped} {plural(dropped, 'старое сообщение', 'старых сообщения', 'старых сообщений')} "
                   f"не сохранил — очередь держит последние {PENDING_MAX})\n\n")
    batch: list[dict] = []
    text = header
    for row in rows:
        markup = _markup(row.get("reply_markup"))
        if markup is not None:
            # A message with buttons goes alone, with them, in its place.
            if batch:
                if not await _deliver_batch(text, batch):
                    return
                text, batch = "🌙 (продолжение)\n\n", []
            if not await _deliver_batch(clip(row["text"]), [row], markup):
                return
            continue
        # Each item is cut HTML-safely to fit one message; a batch is never cut.
        item = clip(f"— {row['text']}", SAFE_LIMIT - len(header) - 60) + "\n\n"
        if batch and len(text) + len(item) > SAFE_LIMIT:
            if not await _deliver_batch(text, batch):
                return
            text, batch = "🌙 (продолжение)\n\n", []
        text += item
        batch.append(row)
    if batch and not await _deliver_batch(text, batch):
        return
    if dropped:
        await set_state(PENDING_DROPPED, None)      # said once — and only when the digest got through


def _markup(raw: str | None) -> InlineKeyboardMarkup | None:
    if not raw:
        return None
    try:
        return InlineKeyboardMarkup.model_validate_json(raw)
    except ValueError:
        return None


async def _deliver_batch(text: str, batch: list[dict], markup=None) -> bool:
    """True when the rows are done with — delivered, or dropped because
    Telegram will never take them (a stored text must not jam the queue
    forever). False only when Telegram may take them later."""
    attempts = [{"reply_markup": markup}, {"reply_markup": markup, "parse_mode": None}]
    if markup is not None:
        attempts.append({"reply_markup": None, "parse_mode": None})     # the buttons are the problem
    for kwargs in attempts:
        try:
            await _call(_bot.send_message, text=text.rstrip(), disable_notification=True, **kwargs)
            break
        except TelegramBadRequest as e:
            logger.error("Quiet queue: Telegram rejected a batch (%s) — trying a simpler form", e)
        except Exception as e:
            logger.error("Failed to flush quiet queue: %s", e)
            return False
    else:
        logger.error("Quiet queue: dropping %d message(s) Telegram will never take", len(batch))
        await delete_notifications([r["id"] for r in batch])
        await _deliver("⚠️ Одно отложенное сообщение Telegram так и не принял — я его убрал, чтобы "
                       "не задерживать остальные.", None, Priority.NORMAL)
        return True
    await delete_notifications([r["id"] for r in batch])
    return True
