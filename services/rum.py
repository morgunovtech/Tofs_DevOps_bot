"""Real visitors' speed: what the owner's visitors actually wait for, not
what the bot's own request sees.

Two sources:
- the owner's own measurement — one line on the site (web/rum.js) sends
  three numbers per page view to the bot: how soon the page appears (LCP),
  how soon it answers a tap (INP), how much the layout jumps (CLS). No
  cookies, no address, no page path, no visitor — the numbers and «phone
  or not». Kept 28 days;
- Google's Chrome UX Report (services.crux) — the same three numbers from
  Chrome users, for sites with enough of them.

The verdict uses Google's thresholds at the 75th percentile, like Search
Console does: good / can be faster / slow.
"""

import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import quote

from db.database import add_rum_sample, get_rum_samples
from services import humanize, public_urls, secrets
from utils.text import esc, plural

WINDOW_DAYS = 28
# Fewer page views say more about chance than about the site.
MIN_VISITS = 30


@dataclass(frozen=True)
class Metric:
    key: str
    good: float          # at most this is good
    poor: float          # above this is slow
    limit: float         # a sample above this is junk (a tab left open for an hour)


METRICS = (Metric("lcp", 2500, 4000, 60000), Metric("inp", 200, 500, 20000), Metric("cls", 0.1, 0.25, 10))
_BY_KEY = {m.key: m for m in METRICS}


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


# ── The owner's own measurement ──────────────────────────────────────────────

def sig(site_id: int) -> str:
    """Signed, so the beacon address neither enumerates the sites nor lets
    a stranger pour numbers into another one."""
    key = secrets.heartbeat_secret().encode()
    return hmac.new(key, f"rum:{site_id}".encode(), hashlib.sha256).hexdigest()[:16]


def sig_ok(site_id: int, value: str) -> bool:
    return hmac.compare_digest(sig(site_id), value)


def snippet(site_id: int) -> str:
    """The one line the owner puts into the site's <head>."""
    return f'<script src="{public_urls.base_url()}/rum.js" data-site="{sig(site_id)}.{site_id}" defer></script>'


def clean(payload: dict) -> dict | None:
    """The numbers of one page view, or None when they are not believable."""
    if not isinstance(payload, dict):
        return None
    out: dict = {}
    for m in METRICS:
        v = payload.get(m.key)
        if v is None:
            out[m.key] = None
        elif isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= m.limit:
            out[m.key] = round(float(v), 3) if m.key == "cls" else int(v)
        else:
            return None
    ttfb = payload.get("ttfb")
    out["ttfb"] = int(ttfb) if isinstance(ttfb, int | float) and not isinstance(ttfb, bool) and 0 <= ttfb <= 60000 \
        else None
    out["mobile"] = bool(payload.get("mobile"))
    if out["lcp"] is None and out["inp"] is None and out["ttfb"] is None:
        return None
    return out


async def save(site_id: int, values: dict):
    await add_rum_sample(site_id, values["lcp"], values["inp"], values["cls"], values["ttfb"], values["mobile"])


def p75(values: list[float]) -> float | None:
    """The value 3 of 4 visitors get or better — Google's yardstick."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.75 * len(ordered)))]


async def summary(site_id: int, days: int = WINDOW_DAYS) -> dict:
    """{"visits", "mobile", "last_at", "lcp", "inp", "cls", "ttfb"} — p75 of
    each over the window; visits = page views measured."""
    rows = await get_rum_samples(site_id, days)
    out: dict = {"visits": len(rows), "mobile": sum(r["mobile"] for r in rows),
                 "last_at": max((r["at"] for r in rows), default=None)}
    for key in ("lcp", "inp", "cls", "ttfb"):
        out[key] = p75([r[key] for r in rows if r[key] is not None])
    return out


# ── The verdict for a card ───────────────────────────────────────────────────

async def best_source(site_id: int, crux: dict | None) -> tuple[str | None, dict]:
    """("own" | "chrome" | None, values): the owner's measurement once it has
    enough page views (it sees every browser), else Chrome's."""
    own = await summary(site_id)
    if own["visits"] >= MIN_VISITS:
        return "own", own
    if crux and any(crux.get(k) is not None for k in _BY_KEY):
        return "chrome", crux
    return None, own


def source_words(source: str, values: dict) -> str:
    if source == "own":
        n = values["visits"]
        return f"{n} {plural(n, 'просмотр', 'просмотра', 'просмотров')} за {WINDOW_DAYS} дней, твой замер"
    return f"посетители Chrome за {WINDOW_DAYS} дней, данные Google"


def card_line(source: str | None, values: dict, crux_on: bool) -> str:
    if source:
        return f"{verdict_icon(values)} У посетителей: {esc(describe(values))} · {source_words(source, values)}"
    n = values.get("visits") or 0
    if n:
        return (f"👥 У посетителей: пока {n} {plural(n, 'просмотр', 'просмотра', 'просмотров')} — чтобы судить, "
                f"нужно хотя бы {MIN_VISITS}")
    if crux_on:
        return "👥 У посетителей: у Google пока мало данных по сайту — поставь свой замер (⚙️ Настройки сайта)"
    return "👥 Скорость у посетителей не измеряю — одна строка на сайт, и буду (⚙️ Настройки сайта)"


def pagespeed_url(url: str) -> str:
    """Google's own report on what slows the page down."""
    return f"https://pagespeed.web.dev/analysis?url={quote(url, safe='')}"
