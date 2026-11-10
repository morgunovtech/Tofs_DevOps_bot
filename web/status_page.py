"""Public status page, its JSON twin and the SVG uptime badge.

Privacy model: OFF by default (the page reveals the monitored site list).
It shows current state, uptime numbers and an anonymised incident history
(type + time window, never raw error messages). STATUS_PAGE_SLUG turns the
URL into a shareable secret. A monitor is published under its PUBLIC label:
the host of a web page — never a path, a port, a tcp/ping target or an
internal address (public_label).

State is the bot's verdict, not one raw check: «down» only with an open
incident, «перепроверяю» after a failed check without one, «нет свежих
данных» when the checks stopped or could not reach anything (the bot's own
network is down). Data comes strictly from the DB — a page hit never
triggers live checks, so the endpoint can't be used to make the bot hammer
the monitored sites.
"""

import asyncio
import html
import time
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from config import config
from db.database import (
    get_active_incidents,
    get_all_sites,
    get_incidents_since,
    get_last_check,
    get_site,
    get_state,
    get_uptime_over_days,
    get_uptime_stats,
    get_uptime_windows,
)
from services import maintenance
from utils.clock import to_local, zone_label
from utils.net import is_private_target
from utils.text import fmt_duration, incident_start, parse_iso_utc, parse_sqlite_utc
from utils.urls import host_of, is_api_check, is_http_url

FORMAT_VERSION = 1
_CACHE_TTL_SEC = 30
_cache: tuple[float, dict] | None = None
_lock = asyncio.Lock()
_TEMPLATE = Template((Path(__file__).parent / "templates" / "status.html").read_text("utf-8"))

# Everything a visitor reads, per STATUS_PAGE_LANG (read on every call, not
# at import: the owner's .env decides, a test may switch it). Incident
# «types» are public names — deliberately generic — and a WHITE list: a type
# that is not here (exposed, security, mail_auth, anything new) is never
# published — a list of a site's weak spots is a gift to an attacker. The
# badge keeps the shields.io words in both languages: it sits in READMEs.
_TEXTS = {
    "ru": {
        "title": "Статус сервисов", "html_lang": "ru",
        "types": {
            "availability": "Недоступность", "performance": "Медленные ответы",
            "ssl": "Проблема с SSL-сертификатом", "domain": "Проблема с доменом",
            "links": "Битые ссылки", "deep": "Ошибки на части страниц",
            "seo": "Проблема с индексацией", "mail": "Почта не принимала письма",
            "visitors": "Медленно у посетителей",
        },
        "state": {
            "down": "недоступен", "checking": "перепроверяю", "degraded": "работает с проблемами",
            "stale": "нет свежих данных", "nodata": "нет свежих данных", "maint": "тех. работы",
            "pending": "ждёт первой проверки", "up": "работает",
        },
        "down_since": "с {t}",
        "banner": {
            "down": "Часть систем недоступна", "degraded": "Есть проблемы — подробности ниже",
            "stale": "Нет свежих данных: проверки сейчас не идут", "maintenance": "Идут технические работы",
            "empty": "Пока нечего показывать", "pending": "Идёт первая проверка", "up": "Все системы работают",
        },
        "periods": ("24ч", "7д", "30д", "90д"), "open": "сейчас",
        "incidents": "Инциденты за 14 дней",
        "columns": ("Длительность", "Начало", "Сервис", "Что случилось"),
        "checked_ago": "последняя проверка {ago} назад", "checked_now": "последняя проверка только что",
        "checked_none": "проверок ещё не было",
        "built": "страница собрана {now} и обновляется сама раз в минуту",
        "units": ("мин", "ч", "дн"), "badge": "uptime 7d", "no_data": "n/a",
    },
    "en": {
        "title": "Service status", "html_lang": "en",
        "types": {
            "availability": "Outage", "performance": "Slow responses",
            "ssl": "SSL certificate problem", "domain": "Domain problem",
            "links": "Broken links", "deep": "Errors on some pages",
            "seo": "Search indexing problem", "mail": "Mail was not accepted",
            "visitors": "Slow for visitors",
        },
        "state": {
            "down": "down", "checking": "re-checking", "degraded": "degraded",
            "stale": "no fresh data", "nodata": "no fresh data", "maint": "maintenance",
            "pending": "awaiting first check", "up": "operational",
        },
        "down_since": "since {t}",
        "banner": {
            "down": "Some systems are down", "degraded": "Some issues — details below",
            "stale": "No fresh data: checks are not running right now", "maintenance": "Maintenance in progress",
            "empty": "Nothing to show yet", "pending": "First check in progress", "up": "All systems operational",
        },
        "periods": ("24h", "7d", "30d", "90d"), "open": "ongoing",
        "incidents": "Incidents in the last 14 days",
        "columns": ("Duration", "Started", "Service", "What happened"),
        "checked_ago": "last check {ago} ago", "checked_now": "last check just now",
        "checked_none": "no checks yet",
        "built": "page built {now}, refreshes itself every minute",
        "units": ("min", "h", "d"), "badge": "uptime 7d", "no_data": "n/a",
    },
}
_PUBLIC_TYPES = frozenset(_TEXTS["ru"]["types"])         # the same keys in every language
# Every state the verdict can produce, the worst first, with its dot colour.
_DOT = {
    "down": "dot-down", "checking": "dot-warn", "degraded": "dot-warn", "stale": "dot-pending",
    # the monitoring server itself could not check (its own network): not «недоступен»
    "nodata": "dot-pending", "maint": "dot-maint", "pending": "dot-pending", "up": "dot-up",
}
_ORDER = {state: i for i, state in enumerate(_DOT)}     # the worst first
ALIVE_STALE_MIN = 3        # the scheduler marks alive_at every minute (reports/scheduler.py, run_alive)


def public_label(url: str, method: str | None = None) -> str | None:
    """What the public may see of a monitor, or None — not published:
    the host of a web page or an API (with «API» beside it, never its path:
    tokens live in paths too), nothing for tcp/ping monitors and internal
    addresses (a map of the owner's network is not for visitors)."""
    if not is_http_url(url) or is_private_target(url):
        return None
    host = host_of(url)
    return f"{host} · API" if is_api_check(url, method) else host


async def public_labels() -> list[str]:
    """What the page would list right now — the owner sees it before publishing."""
    return [label for s in await get_all_sites() if (label := public_label(s["url"], s.get("http_method")))]


def _t() -> dict:
    return _TEXTS.get(config.status_page_lang, _TEXTS["ru"])


def _fmt(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    return to_local(dt).strftime(fmt) if dt else "—"


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat(timespec="seconds") if dt else None


async def _scheduler_alive() -> bool:
    """False when the per-minute mark is old: the checks are not running. No
    mark yet (a fresh install, tests) is not a verdict."""
    alive = parse_iso_utc(await get_state("alive_at"))
    return alive is None or (datetime.now(UTC) - alive).total_seconds() / 60 <= ALIVE_STALE_MIN


async def build_payload() -> dict:
    t = _t()
    now = datetime.now(UTC)
    open_inc: dict[int, dict[str, dict]] = {}
    for inc in await get_active_incidents():
        if inc["check_type"] in _PUBLIC_TYPES:
            open_inc.setdefault(inc["site_id"], {})[inc["check_type"]] = inc
    alive = await _scheduler_alive()
    sites, labels = [], {}
    for s in await get_all_sites():
        label = public_label(s["url"], s.get("http_method"))
        if label is None:
            continue
        labels[s["id"]] = label
        last = await get_last_check(s["id"], "availability")
        checked = parse_sqlite_utc((last or {}).get("checked_at"))
        age_min = (now - checked).total_seconds() / 60 if checked else None
        interval = s.get("check_interval_min") or config.check_interval_minutes
        incidents = open_inc.get(s["id"], {})
        down_since = incident_start(incidents["availability"]) if "availability" in incidents else None
        if maintenance.maintenance_now(s["id"]):
            state = "maint"
        elif last is None:
            state = "pending"
        elif not alive or age_min is None or age_min > 3 * interval + 5:
            state = "stale"
        elif "availability" in incidents:
            state = "down"
        elif last["status"] == "unknown":
            state = "nodata"             # the bot could not check (its own network), not the site's fault
        elif last["status"] != "ok":
            state = "checking"           # one failed check is not an outage: the bot is re-checking
        elif incidents:
            state = "degraded"
        else:
            state = "up"
        has_data = last is not None
        # 7/30/90 days from one pass over the longest window; «—» for a new site.
        windows = (await get_uptime_windows(s["id"], (7, 30, 90)) if has_data
                   else {days: {"uptime_pct": None} for days in (7, 30, 90)})
        sites.append({
            "label": label,
            "api": is_api_check(s["url"], s.get("http_method")),
            "state": state,
            "ms": (last or {}).get("response_time_ms") if state not in ("down", "checking", "nodata") else None,
            "up24": (await get_uptime_stats(s["id"], hours=24))["uptime_pct"] if has_data else None,
            **{f"up{days}d": stats["uptime_pct"] for days, stats in windows.items()},
            "problems": sorted(t["types"][k] for k in incidents if k != "availability"),
            "checked_at": _fmt(checked, "%H:%M"),
            "checked_at_iso": _iso(checked),
            "checked_ago_min": round(age_min) if age_min is not None else None,
            "down_since": _fmt(down_since, "%H:%M") if down_since else None,
            "down_since_iso": _iso(down_since),
        })
    sites.sort(key=lambda x: (_ORDER[x["state"]], x["label"]))
    incidents = []
    for inc in reversed(await get_incidents_since(14)):
        if len(incidents) >= 10:
            break
        if inc["site_id"] not in labels or inc["check_type"] not in _PUBLIC_TYPES:
            continue   # removed or unpublished monitors, weak spots — not on the page
        created = incident_start(inc)
        resolved = parse_sqlite_utc(inc.get("resolved_at")) if inc.get("resolved") else None
        minutes = round((resolved - created).total_seconds() / 60) if created and resolved else None
        incidents.append({
            "label": labels[inc["site_id"]],
            "type": t["types"][inc["check_type"]],
            "type_code": inc["check_type"],
            "open": not inc.get("resolved"),
            "started": _fmt(created),
            "started_iso": _iso(created),
            "resolved_iso": _iso(resolved),
            "duration": fmt_duration(max(1, minutes), t["units"]) if minutes is not None else "",
            "duration_min": max(1, minutes) if minutes is not None else None,
        })
    ages = [s["checked_ago_min"] for s in sites if s["checked_ago_min"] is not None]
    return {"sites": sites, "incidents": incidents,
            "any_down": any(s["state"] == "down" for s in sites),
            # «перепроверяю» (one failed check, no incident) does not colour the banner:
            # the bot itself calls a single failure normal.
            "any_degraded": any(s["state"] == "degraded" for s in sites),
            "any_stale": any(s["state"] in ("stale", "nodata") for s in sites),
            "all_pending": bool(sites) and all(s["state"] == "pending" for s in sites),
            "any_maint": maintenance.maintenance_now(None),
            "last_check_ago_min": min(ages) if ages else None,
            "now": f"{_fmt(now)} {zone_label()}",        # visitors may live in another zone
            "generated_at": now.isoformat(timespec="seconds")}


async def payload() -> dict:
    """Cached for 30s, and built once at a time: while one request builds,
    the others get the previous data or wait for this build — a burst of
    hits must not become a burst of rebuilds on the one DB connection."""
    global _cache
    if _cache and time.monotonic() - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]
    if _cache and _lock.locked():
        return _cache[1]
    async with _lock:
        if _cache and time.monotonic() - _cache[0] < _CACHE_TTL_SEC:
            return _cache[1]
        data = await build_payload()
        _cache = (time.monotonic(), data)
        return data


def uptime_class(pct: float | None) -> str:
    if pct is None:
        return "na"
    return "good" if pct >= 99 else ("warn" if pct >= 95 else "bad")


def _status(p: dict) -> str:
    if p["any_down"]:
        return "down"
    if p["any_degraded"]:
        return "degraded"
    if p["any_stale"]:
        return "stale"
    if p["any_maint"]:
        return "maintenance"
    return "up"


def _banner(p: dict) -> tuple[str, str]:
    words = _t()["banner"]
    status = _status(p)
    if status == "down":
        return "banner-down", words["down"]
    if status == "degraded":
        return "banner-warn", words["degraded"]
    if status == "stale":
        return "banner-stale", words["stale"]
    if status == "maintenance":
        return "banner-maint", words["maintenance"]
    if not p["sites"]:
        return "banner-maint", words["empty"]
    if p["all_pending"]:
        return "banner-maint", words["pending"]
    return "banner-up", words["up"]


def render(p: dict) -> str:
    t = _t()
    banner_cls, banner = _banner(p)
    rows = []
    for s in p["sites"]:
        dot_cls, state_txt = _DOT[s["state"]], t["state"][s["state"]]
        if s["state"] == "down" and s.get("down_since"):
            state_txt += " " + t["down_since"].format(t=s["down_since"])
        elif s["ms"] is not None:
            state_txt += f" · {s['ms']} ms"
        spans = "".join(
            f'<span class="{uptime_class(s[k])}">{lbl}: {"—" if s[k] is None else str(s[k]) + "%"}</span>'
            for k, lbl in zip(("up24", "up7d", "up30d", "up90d"), t["periods"], strict=True))
        problems = (f'<div class="problems">{html.escape(", ".join(s["problems"]))}</div>'
                    if s["problems"] else "")
        rows.append(
            f'<div class="site"><div class="site-head"><span class="dot {dot_cls}"></span>'
            f'<span class="site-name">{html.escape(s["label"])}</span>'
            f'<span class="site-state">{html.escape(state_txt)}</span></div>'
            f'{problems}<div class="uptime">{spans}</div></div>')

    inc_rows = []
    for i in p["incidents"]:
        status = (f"<span class='inc-open'>{t['open']}</span>" if i["open"] else html.escape(i["duration"]))
        inc_rows.append(f"<tr><td>{status}</td><td>{html.escape(i['started'])}</td>"
                        f"<td>{html.escape(i['label'])}</td><td>{html.escape(i['type'])}</td></tr>")
    incidents_html = ""
    if inc_rows:
        duration, started, service, what = t["columns"]
        incidents_html = (f"<h2>{t['incidents']}</h2><table class=\"incidents\"><thead><tr>"
                          f"<th scope=\"col\">{duration}</th><th scope=\"col\">{started} ({zone_label()})</th>"
                          f"<th scope=\"col\">{service}</th><th scope=\"col\">{what}</th></tr></thead>"
                          "<tbody>" + "".join(inc_rows) + "</tbody></table>")
    ago = p["last_check_ago_min"]
    checked = (t["checked_ago"].format(ago=fmt_duration(max(1, ago), t["units"])) if ago else
               t["checked_now"] if ago == 0 else t["checked_none"])
    # The owner's name for the page goes in as text, above the banner — not a second <h1>.
    heading = (f'<p class="page-title">{html.escape(config.status_page_title)}</p>'
               if config.status_page_title else "")
    return _TEMPLATE.substitute(lang=t["html_lang"], title=html.escape(config.status_page_title or t["title"]),
                                heading=heading, banner_cls=banner_cls, banner=banner,
                                sites_html="".join(rows), incidents_html=incidents_html,
                                checked=html.escape(checked),
                                built=html.escape(t["built"].format(now=p["now"])))


async def status_html() -> str:
    return render(await payload())


async def status_json() -> dict:
    p = await payload()
    return {"version": FORMAT_VERSION, "status": _status(p), "generated_at": p["generated_at"],
            "last_check_ago_min": p["last_check_ago_min"], "sites": p["sites"], "incidents": p["incidents"]}


# ── Uptime badge (shields.io style) ──────────────────────────────────────────

_BADGE_TTL_SEC = 60
_badge_cache: dict[int, tuple[float, bool, float | None]] = {}
_BADGE_COLORS = {"good": "#4c1", "warn": "#dfb317", "bad": "#e05d44", "na": "#9f9f9f"}
# White on these fills reads at 2:1–3.6:1 — dark text everywhere (4.8:1 on
# red, 6.6:1 on grey, 8:1 and more on green and yellow).
_BADGE_TEXT = {"good": "#1a1a1a", "warn": "#1a1a1a", "bad": "#1a1a1a", "na": "#1a1a1a"}


async def badge_pct(site_id: int) -> tuple[bool, float | None]:
    """(site exists and is active, uptime % over 7 days or None if no data),
    cached for a minute — anonymous hits must not each run an aggregate scan."""
    now = time.monotonic()
    hit = _badge_cache.get(site_id)
    if hit and now - hit[0] < _BADGE_TTL_SEC:
        return hit[1], hit[2]
    site = await get_site(site_id)
    if not site or not site["active"]:
        found, pct = False, None
    else:
        found, pct = True, None
        if await get_last_check(site_id, "availability"):
            pct = (await get_uptime_over_days(site_id, 7))["uptime_pct"]
    if len(_badge_cache) > 256:  # bound memory against id scans
        _badge_cache.clear()
    _badge_cache[site_id] = (now, found, pct)
    return found, pct


def badge_svg(pct: float | None) -> str:
    t = _t()
    label = t["badge"]
    cls = uptime_class(pct)
    value = t["no_data"] if pct is None else f"{pct}%"
    color, value_fill = _BADGE_COLORS[cls], _BADGE_TEXT[cls]
    lw = int(len(label) * 6.1) + 12   # ~6.1px per char at font-size 11
    vw = int(len(value) * 6.1) + 12
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/><rect width="{w}" height="20" fill="url(#s)"/></g>
  <g text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2}" y="14" fill="#fff">{label}</text><text x="{lw + vw / 2}" y="14" fill="{value_fill}">{value}</text>
  </g>
</svg>"""
