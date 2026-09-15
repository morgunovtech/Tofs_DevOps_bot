"""Local-time helpers. The container runs in UTC; everything shown to the
user goes through the configured IANA timezone (stdlib zoneinfo)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from config import config
from utils.text import parse_sqlite_utc


def local_tz() -> ZoneInfo:
    return ZoneInfo(config.timezone)


def now_local() -> datetime:
    return datetime.now(local_tz())


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(local_tz())


def fmt_local(sqlite_utc: str | None, fmt: str = "%d.%m %H:%M") -> str:
    dt = parse_sqlite_utc(sqlite_utc)
    if not dt:
        return (sqlite_utc or "")[:16]
    return to_local(dt).strftime(fmt)


def local_at(hour: int, minute: int = 0) -> datetime:
    """Next occurrence (today or tomorrow) of HH:MM local, as an aware dt.
    zoneinfo handles DST natively — no localize()/normalize() dance."""
    now = now_local()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def utcnow() -> datetime:
    return datetime.now(UTC)
