"""What the process knows about its own health — in memory only: it has to
work exactly when the database does not (full disk, «database is locked»).

Four marks, all on a monotonic clock (a wall-clock jump after boot on a
machine without an RTC must not look like a stall): when the availability
tick last finished, when Telegram last answered a call, since when Telegram
refuses the token, since when delivery to the owner's chat fails — plus the
database write guard. /health, the external watchdog's mark and the
in-process watchdog in main.py all read them: a process that runs but no
longer checks sites or cannot reach Telegram must not look alive.

A write that failed less than FAILING_FOR_SEC ago means «the database is
failing». The owner hears it at most once per DB_ALERT_EVERY_SEC, and gets
one «✅» once no write has failed for FAILING_FOR_SEC."""

import asyncio
import logging
import time
from collections.abc import Callable

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramServerError,
    TelegramUnauthorizedError,
)

logger = logging.getLogger(__name__)

FAILING_FOR_SEC = 600
DB_ALERT_EVERY_SEC = 3600
TICK_STALE_SEC = 300            # the tick runs every minute and takes seconds
TELEGRAM_SILENT_SEC = 900       # polling alone gets an answer every ~10 s
REFUSED_EXIT_SEC = 600          # a revoked token does not come back by waiting
DELIVERY_FAILING_SEC = 900      # the bot blocked or thrown out of the admin chat

_clock = time.monotonic          # tests move it instead of the event loop's clock

_last_failure: float | None = None
_error = ""
_alerted_at: float | None = None
_started: float = _clock()
_tick_done: float | None = None
_telegram_ok: float | None = None
_telegram_refused_since: float | None = None
_delivery_failed_since: float | None = None


def db_write_failed(exc: BaseException) -> None:
    """A write did not go through. Logged once per episode, not per site."""
    global _last_failure, _error
    if not db_failing():
        logger.error("Database refuses writes — checks and alerts go on from memory: %s", exc)
    _last_failure, _error = _clock(), str(exc)


def db_failing() -> bool:
    return _last_failure is not None and _clock() - _last_failure < FAILING_FOR_SEC


def db_error() -> str:
    return _error


def db_alert_due() -> bool:
    """«Не могу записать в базу» should go out now."""
    return db_failing() and (_alerted_at is None or _clock() - _alerted_at >= DB_ALERT_EVERY_SEC)


def mark_db_alerted() -> None:
    global _alerted_at
    _alerted_at = _clock()


def db_recovery_due() -> bool:
    """The owner was told about the failure, and no write has failed since."""
    return _alerted_at is not None and not db_failing()


def mark_db_recovery_said() -> None:
    global _alerted_at, _last_failure, _error
    _alerted_at = _last_failure = None
    _error = ""


def reset() -> None:
    """Forget everything — a fresh process (tests)."""
    global _started, _tick_done, _telegram_ok, _telegram_refused_since, _delivery_failed_since
    mark_db_recovery_said()
    _started = _clock()
    _tick_done = _telegram_ok = _telegram_refused_since = _delivery_failed_since = None


# ── The pulse ───────────────────────────────────────────────────────────────

def tick_done() -> None:
    global _tick_done
    _tick_done = _clock()


def telegram_ok() -> None:
    global _telegram_ok, _telegram_refused_since
    _telegram_ok, _telegram_refused_since = _clock(), None


def telegram_refused() -> None:
    global _telegram_refused_since
    _telegram_refused_since = _telegram_refused_since or _clock()


def delivery_failed() -> None:
    """Telegram answers, but refuses to deliver to the owner's chat (the bot
    is blocked or was removed from the group)."""
    global _delivery_failed_since
    _delivery_failed_since = _delivery_failed_since or _clock()


def delivery_ok() -> None:
    global _delivery_failed_since
    _delivery_failed_since = None


def problems() -> list[str]:
    """What is wrong with the bot itself right now; [] — healthy."""
    now = _clock()
    out = []
    if now - (_tick_done if _tick_done is not None else _started) > TICK_STALE_SEC:
        out.append("checks stalled")
    if now - (_telegram_ok if _telegram_ok is not None else _started) > TELEGRAM_SILENT_SEC:
        out.append("telegram unreachable")
    if _telegram_refused_since is not None:
        out.append("telegram refuses the token")
    if _delivery_failed_since is not None and now - _delivery_failed_since > DELIVERY_FAILING_SEC:
        out.append("cannot deliver to the owner")
    if db_failing():
        out.append("database refuses writes")
    return out


def fatal() -> str | None:
    """A state only a restart can cure: the in-process watchdog exits on it."""
    now = _clock()
    if now - (_tick_done if _tick_done is not None else _started) > TICK_STALE_SEC:
        return "the availability check has not finished for 5 minutes (scheduler dead or stuck)"
    if _telegram_refused_since is not None and now - _telegram_refused_since > REFUSED_EXIT_SEC:
        return "Telegram has refused the bot token for 10 minutes (revoked in @BotFather?)"
    return None


CLOCK_STEP = 60                 # seconds of wall-clock jump that count as a step, not drift


def _wall() -> float:
    return time.time()


async def watchdog(every: float = 60, on_clock_step: Callable[[float], None] | None = None) -> str:
    """Runs outside APScheduler — a dead scheduler cannot stop it. Returns the
    reason to exit; main() stops polling and exits with code 3, and the
    container's restart policy brings the bot back. Also watches the wall
    clock against the monotonic one: APScheduler counts on the wall clock,
    and a step back of an hour would freeze every interval job for that
    hour. The marks here are monotonic, so a step does not touch them."""
    wall, mono = _wall(), time.monotonic()
    while True:
        await asyncio.sleep(every)
        now_wall, now_mono = _wall(), time.monotonic()
        step = (now_wall - wall) - (now_mono - mono)
        wall, mono = now_wall, now_mono
        if abs(step) > CLOCK_STEP:
            logger.warning("The wall clock stepped by %+.0f s — re-arming the schedule", step)
            if on_clock_step:
                on_clock_step(step)
        reason = fatal()
        if reason:
            return reason


class TelegramWatch(BaseRequestMiddleware):
    """Every Bot API call — getUpdates included — says whether Telegram
    still takes this bot's calls."""

    async def __call__(self, make_request, bot, method):
        try:
            response = await make_request(bot, method)
        except TelegramUnauthorizedError:
            telegram_refused()
            raise
        except (TelegramNetworkError, TelegramServerError):
            raise
        except TelegramAPIError:
            telegram_ok()          # Telegram answered: the channel works, this one request did not
            raise
        telegram_ok()
        return response
