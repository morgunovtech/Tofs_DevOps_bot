"""What the process knows about its own health — in memory only: it has to
work exactly when the database does not (full disk, «database is locked»).

A write that failed less than FAILING_FOR_SEC ago means «the database is
failing». The owner hears it at most once per DB_ALERT_EVERY_SEC, and gets
one «✅» once no write has failed for FAILING_FOR_SEC."""

import logging
import time

logger = logging.getLogger(__name__)

FAILING_FOR_SEC = 600
DB_ALERT_EVERY_SEC = 3600

_clock = time.monotonic          # tests move it instead of the event loop's clock

_last_failure: float | None = None
_error = ""
_alerted_at: float | None = None


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
    """Forget everything (tests)."""
    mark_db_recovery_said()
