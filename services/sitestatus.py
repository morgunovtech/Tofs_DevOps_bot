"""Structured snapshot of the last check results per site.

The main screen and the site card read this — never the network, never a
parsed string. Every monitor writes its section after a check, manage or
not, because an observation is an observation.

    {"avail": {"status", "ms", "error", "at"},
     "ssl": {"days_left", "issuer", "not_after", "error", "at"},
     "domain": {"days_left", "expiration", "registrar", "unsupported", "error", "at"},
     "links": {"status", "internal", "external", "at"},
     "seo": {"status", "critical", "improve", "problems": [{"code", "severity", "message"}],
             "infos": [...], "pages", "no_js_chars", "at"},
     "region": {"ms", "name", "at"},          # response time from the audience's region
     "hosting": "cloudflare"}
"""

import json
from datetime import UTC, datetime

from db.database import get_state, set_state


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def get(site_id: int) -> dict:
    raw = await get_state(f"status:{site_id}")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def update(site_id: int, section: str, **fields):
    """Replace one section (plus its timestamp) and keep the rest."""
    data = await get(site_id)
    data[section] = {**fields, "at": _now()}
    await set_state(f"status:{site_id}", json.dumps(data, ensure_ascii=False))


async def set_field(site_id: int, key: str, value):
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
