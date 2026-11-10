"""Local-time helpers. The container runs in UTC; everything shown to the
user goes through the configured IANA timezone (stdlib zoneinfo)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from config import config
from utils.text import parse_sqlite_utc

_override: str | None = None


def set_timezone(key: str | None):
    """Runtime override (from the chat); None falls back to TIMEZONE."""
    global _override
    _override = key or None


def timezone_key() -> str:
    return _override or config.timezone


def local_tz() -> ZoneInfo:
    return ZoneInfo(timezone_key())


def now_local() -> datetime:
    return datetime.now(local_tz())


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def zone_label(dt: datetime | None = None) -> str:
    """Short name of the owner's zone for people elsewhere: «MSK», «CET»;
    «UTC+05» where the zone has no letter abbreviation."""
    name = (dt or now_local()).strftime("%Z")
    return f"UTC{name}" if name[:1] in "+-" else name


def fmt_local(sqlite_utc: str | None, fmt: str = "%d.%m %H:%M") -> str:
    dt = parse_sqlite_utc(sqlite_utc)
    if not dt:
        return (sqlite_utc or "")[:16]
    return to_local(dt).strftime(fmt)


def utcnow() -> datetime:
    return datetime.now(UTC)
