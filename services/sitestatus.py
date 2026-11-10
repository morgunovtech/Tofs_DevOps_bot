"""Structured snapshot of the last check results per site.

The main screen and the site card read this — never the network, never a
parsed string. Every monitor writes its section after a check, manage or
not, because an observation is an observation.

    {"avail": {"status", "ms", "error", "at"},
     "ssl": {"days_left", "issuer", "not_after", "error", "renewed_at", "at"},
     "domain": {"days_left", "expiration", "registrar", "unsupported", "error", "at"},
     "links": {"status", "broken", "pending", "total", "at"},
     "seo": {"status", "critical", "improve", "problems": [{"code", "severity", "message"}],
             "pages", "no_js_chars", "at"},
     "google": {"verdict", "coverage", "last_crawl", "at"},     # Search Console: is the homepage indexed
     "yandex": {"searchable_pages", "sqi", "alert_problems", "at"},
     "region": {"ms", "name", "at"},          # response time from the audience's region
     "hosting": "cloudflare"}
"""

import asyncio
import json
from datetime import UTC, datetime

from db.database import get_state, set_state


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def now_iso() -> str:
    """The same format as the «at» of every section — for a field that keeps
    its own moment (when the card's picture was taken)."""
    return _now()


async def get(site_id: int) -> dict:
    raw = await get_state(f"status:{site_id}")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


# Monitors finish concurrently: without one lock around read-modify-write,
# the SEO audit and the per-minute check overwrite each other's section.
_lock = asyncio.Lock()


async def update(site_id: int, section: str, **fields):
    """Replace one section (plus its timestamp) and keep the rest."""
    async with _lock:
        data = await get(site_id)
        data[section] = {**fields, "at": _now()}
        await set_state(f"status:{site_id}", json.dumps(data, ensure_ascii=False))


async def set_field(site_id: int, key: str, value):
    async with _lock:
        data = await get(site_id)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        await set_state(f"status:{site_id}", json.dumps(data, ensure_ascii=False))


def age_minutes(section: dict | None) -> float | None:
    """Minutes since the section was written, None when unknown."""
    if not section or not section.get("at"):
        return None
    try:
        at = datetime.fromisoformat(section["at"])
    except ValueError:
        return None
    return (datetime.now(UTC) - at).total_seconds() / 60
