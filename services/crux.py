"""Real visitors' speed from Google's Chrome UX Report: how fast a site is
for its real Chrome visitors over the last 28 days, at the 75th percentile —
the numbers Search Console shows. Nothing is installed on the site; the bot
needs one free Google API key (🔌 Подключения). A site with too few Chrome
visitors has no record (404), which is not an error — most small sites.

The verdict uses Google's thresholds at the 75th percentile: good / can be
faster / slow.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import aiohttp

from services import humanize, integrations
from utils.clock import to_local
from utils.text import err_text, esc, parse_iso_utc

logger = logging.getLogger(__name__)

API = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
TIMEOUT = aiohttp.ClientTimeout(total=20)
WINDOW_DAYS = 28
STALE_DAYS = 3         # older data on the card is dated: Google has not been answering
_METRICS = {"largest_contentful_paint": "lcp", "interaction_to_next_paint": "inp",
            "cumulative_layout_shift": "cls", "experimental_time_to_first_byte": "ttfb"}


@dataclass(frozen=True)
class Metric:
    key: str
    good: float          # at most this is good
    poor: float          # above this is slow


METRICS = (Metric("lcp", 2500, 4000), Metric("inp", 200, 500), Metric("cls", 0.1, 0.25))
_BY_KEY = {m.key: m for m in METRICS}


# ── Asking Google ────────────────────────────────────────────────────────────

class KeyRejected(Exception):
    """Google answered 400/401/403: the key is wrong, revoked or the API is off."""


def available() -> bool:
    return bool(integrations.crux_key())


def parse(record: dict) -> dict:
    """{"lcp", "inp", "cls", "ttfb"} p75 values out of a CrUX record."""
    out: dict = {key: None for key in _METRICS.values()}
    for name, key in _METRICS.items():
        p75 = ((record.get("metrics") or {}).get(name) or {}).get("percentiles", {}).get("p75")
        try:
            out[key] = float(p75) if key == "cls" else int(float(p75))
        except (TypeError, ValueError):
            pass
    return out


async def query(origin: str, key: str | None = None) -> dict | None:
    """p75 values for the origin; {} when Google has too little data for it;
    None when the API could not be asked (no key, network). A rejected key
    raises KeyRejected. The health record (services.integrations) is kept
    for the configured key only — a candidate from the wizard is not it."""
    probe = key is not None
    key = key or integrations.crux_key()
    if not key:
        return None
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            # The key travels in a header, not in the URL: a URL ends up in logs and error texts.
            async with session.post(API, json={"origin": origin}, headers={"X-Goog-Api-Key": key},
                                    allow_redirects=False) as resp:
                if resp.status == 404:
                    if not probe:
                        await integrations.record_result("crux", True)
                    return {}
                if resp.status != 200:
                    logger.warning("CrUX for %s: HTTP %s", origin, resp.status)
                    refused = resp.status in (400, 401, 403)
                    if not probe:
                        await integrations.record_result("crux", False, f"Google ответил HTTP {resp.status}",
                                                         refused=refused)
                    if refused:
                        raise KeyRejected(f"HTTP {resp.status}")
                    return None
                data = await resp.json(content_type=None)
    except KeyRejected:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.warning("CrUX for %s failed: %s", origin, err_text(e))
        if not probe:
            await integrations.record_result("crux", False, f"Google не ответил ({err_text(e)})")
        return None
    if not probe:
        await integrations.record_result("crux", True)
    return parse((data if isinstance(data, dict) else {}).get("record") or {})


async def key_works(key: str) -> bool | None:
    """A live question about a site Google surely has data for: True — the
    key works, False — Google refused it, None — Google did not answer."""
    try:
        data = await query("https://www.google.com", key)
    except KeyRejected:
        return False
    return None if data is None else bool(data)


def has_data(values: dict | None) -> bool:
    return bool(values) and any(values.get(k) is not None for k in _BY_KEY)


# ── Saying it ────────────────────────────────────────────────────────────────

def level(key: str, value: float | None) -> str | None:
    """good | improve | poor; None when there is no value."""
    if value is None:
        return None
    m = _BY_KEY[key]
    return "good" if value <= m.good else "improve" if value <= m.poor else "poor"


def words(key: str, value: float, judged: bool = True) -> str:
    """The number in the owner's words, with the verdict unless judged=False."""
    lvl = level(key, value)
    if key == "cls":
        return {"good": "вёрстка не прыгает", "improve": "вёрстка заметно прыгает при загрузке",
                "poor": "вёрстка сильно прыгает при загрузке"}[lvl]
    seconds = humanize.fmt_seconds(int(value))
    text = f"страница появляется за {seconds}" if key == "lcp" else f"кнопки откликаются за {seconds}"
    return text + ({"good": "", "improve": " — можно быстрее", "poor": " — медленно"}[lvl] if judged else "")


def verdict_icon(values: dict) -> str:
    levels = {level(k, values.get(k)) for k in _BY_KEY}
    if "poor" in levels:
        return "🟠"
    return "✅" if levels <= {"good", None} else "👥"


def describe(values: dict) -> str:
    """«страница появляется за 1.8 с, кнопки откликаются за 0.1 с, вёрстка не прыгает»."""
    return ", ".join(words(k, values[k]) for k in ("lcp", "inp", "cls") if values.get(k) is not None)


def slow_parts(values: dict) -> list[str]:
    """What is slow, without the verdict: «страница появляется за 5.2 с»."""
    return [words(k, values[k], judged=False) for k in ("lcp", "inp", "cls") if level(k, values.get(k)) == "poor"]


SOURCE = f"посетители Chrome за {WINDOW_DAYS} дней, данные Google"


def _dated(values: dict) -> str:
    """« (данные на 07.11)» when the snapshot is older than STALE_DAYS."""
    at = parse_iso_utc(values.get("at"))
    if at and (datetime.now(UTC) - at) > timedelta(days=STALE_DAYS):
        return f" (данные на {to_local(at).strftime('%d.%m')})"
    return ""


def card_line(values: dict | None) -> str | None:
    """The card line once Google has answered; None before (or without a key)."""
    if has_data(values):
        return f"{verdict_icon(values)} У посетителей: {esc(describe(values))} · {SOURCE}{_dated(values)}"
    if values and values.get("none"):
        return "👥 У посетителей: у Google пока мало данных по сайту — так у большинства небольших сайтов"
    return None


def pagespeed_url(url: str) -> str:
    """Google's own report on what slows the page down."""
    return f"https://pagespeed.web.dev/analysis?url={quote(url, safe='')}"
