"""One set of value rules for every door a value comes through: .env, a
button in the chat, the JSON import, a restored database and the scheduler
that finally uses it. A rule returns the clean value or None — the caller
says what it refused and falls back to its default. No project imports
beyond utils.parse: config imports this module."""

import json

from utils.parse import parse_hour_range

HOURS = (0, 23)
CHECK_INTERVAL_MIN = (1, 1440)     # minutes between two checks of a site
FAIL_THRESHOLD = (1, 10)           # failed checks in a row before an alert
SLOW_MS = (100, 120_000)           # «slow» threshold, milliseconds
HB_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


def int_in(value, lo: int, hi: int) -> int | None:
    """An int — or a string of ASCII digits — within lo..hi; anything else
    (True, '²', 1.5, None, 10**12) → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw.isascii() or not raw.lstrip("-").isdigit():
            return None
        value = int(raw)
    return value if isinstance(value, int) and lo <= value <= hi else None


def hour(value) -> int | None:
    """0..23: anything else crashes CronTrigger at every start."""
    return int_in(value, *HOURS)


def quiet_hours(value) -> str | None:
    """A '23-8' range utils.parse understands, else None."""
    return value.strip() if isinstance(value, str) and parse_hour_range(value.strip()) else None


def job_name(name) -> bool:
    """Latin, digits, «-» and «_», up to 20: the name goes into the ping URL
    and into 64-byte callback_data (20 CJK characters would be 60 bytes)."""
    return isinstance(name, str) and 0 < len(name) <= 20 and set(name) <= HB_NAME_CHARS


def headers_json(value) -> str | None:
    """A JSON object of string → string, as the «📡 Заголовки» screen saves it."""
    if not isinstance(value, str) or len(value) > 4000:
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    ok = isinstance(parsed, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items())
    return value if ok else None
