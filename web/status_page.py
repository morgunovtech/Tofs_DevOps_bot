"""Public status page, its JSON twin and the SVG uptime badge.

Privacy model: OFF by default (the page reveals the monitored site list).
It shows current state, uptime numbers and an anonymised incident history
(type + time window, never raw error messages). STATUS_PAGE_SLUG turns the
URL into a shareable secret.

Data comes strictly from the DB — a page hit never triggers live checks,
so the endpoint can't be used to make the bot hammer the monitored sites.
"""

import html
import time
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from db.database import (
    get_all_sites,
    get_incidents_since,
    get_last_check,
    get_site,
    get_uptime_over_days,
    get_uptime_stats,
)
from services import maintenance
from utils.clock import to_local
from utils.text import fmt_duration, parse_sqlite_utc
from utils.urls import site_label

_CACHE_TTL_SEC = 30
_cache: tuple[float, dict] | None = None
_TEMPLATE = Template((Path(__file__).parent / "templates" / "status.html").read_text("utf-8"))

# Public names for incident types — deliberately generic.
_TYPE_RU = {
    "availability": "Недоступность", "performance": "Медленные ответы",
    "ssl": "Проблема с SSL-сертификатом", "domain": "Проблема с доменом",
    "links": "Битые ссылки", "deep": "Ошибки на части страниц",
    "seo": "Проблема с индексацией",
}
_STATE = {
    "up": ("dot-up", "работает"), "down": ("dot-down", "недоступен"),
    "maint": ("dot-maint", "тех. работы"), "pending": ("dot-pending", "ждёт первой проверки"),
}


def _fmt(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    return to_local(dt).strftime(fmt) if dt else "—"


async def build_payload() -> dict:
    sites, active_ids, any_down = [], set(), False
    for s in await get_all_sites():
        active_ids.add(s["id"])
        last = await get_last_check(s["id"], "availability")
        if maintenance.maintenance_now(s["id"]):
            state = "maint"
        elif last is None:
            state = "pending"
        elif last["status"] == "ok":
            state = "up"
        else:
            state, any_down = "down", True
        sites.append({
            "label": site_label(s["url"]),
            "state": state,
            "ms": (last or {}).get("response_time_ms"),
            "up24": (await get_uptime_stats(s["id"], hours=24))["uptime_pct"],
            "up7d": (await get_uptime_over_days(s["id"], 7))["uptime_pct"],
            "up30d": (await get_uptime_over_days(s["id"], 30))["uptime_pct"],
            "up90d": (await get_uptime_over_days(s["id"], 90))["uptime_pct"],
            "checked_at": _fmt(parse_sqlite_utc((last or {}).get("checked_at")), "%H:%M"),
        })
    incidents = []
    for inc in reversed(await get_incidents_since(14)):
        if len(incidents) >= 10:
            break
        if inc["site_id"] not in active_ids:  # removed sites must not haunt the page
            continue
        created = parse_sqlite_utc(inc.get("created_at"))
        resolved = parse_sqlite_utc(inc.get("resolved_at")) if inc.get("resolved") else None
        minutes = round((resolved - created).total_seconds() / 60) if created and resolved else None
        incidents.append({
            "label": site_label(inc["url"]),
            "type": _TYPE_RU.get(inc["check_type"], inc["check_type"]),
            "open": not inc.get("resolved"),
            "started": _fmt(created),
            "duration": fmt_duration(max(1, minutes)) if minutes is not None else "",
        })
    return {"sites": sites, "incidents": incidents, "any_down": any_down,
            "any_maint": maintenance.maintenance_now(None),
            "now": _fmt(datetime.now(UTC)),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}


async def payload() -> dict:
    """Cached for 30s: a popular page must not hammer SQLite."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]
    data = await build_payload()
    _cache = (now, data)
    return data


def uptime_class(pct: float) -> str:
    return "good" if pct >= 99 else ("warn" if pct >= 95 else "bad")


def render(p: dict) -> str:
    if p["any_down"]:
        banner_cls, banner = "banner-down", "Часть систем недоступна"
    elif p["any_maint"]:
        banner_cls, banner = "banner-maint", "Идут технические работы"
    elif not p["sites"]:
        banner_cls, banner = "banner-maint", "Пока нечего показывать"
    else:
        banner_cls, banner = "banner-up", "Все системы работают"

    rows = []
    for s in p["sites"]:
        dot_cls, state_txt = _STATE[s["state"]]
        ms = f"{s['ms']} ms" if s["ms"] is not None else "—"
        spans = "".join(
            f'<span class="{uptime_class(s[k])}">{lbl}: {s[k]}%</span>'
            for k, lbl in (("up24", "24ч"), ("up7d", "7д"), ("up30d", "30д"), ("up90d", "90д")))
        rows.append(
            f'<div class="site"><div class="site-head"><span class="dot {dot_cls}"></span>'
            f'<span class="site-name">{html.escape(s["label"])}</span>'
            f'<span class="site-state">{state_txt} · {ms}</span></div>'
            f'<div class="uptime">{spans}</div></div>')

    inc_rows = []
    for i in p["incidents"]:
        status = ("<span class='inc-open'>сейчас</span>" if i["open"] else html.escape(i["duration"]))
        inc_rows.append(f"<tr><td>{html.escape(i['started'])}</td><td>{html.escape(i['label'])}</td>"
                        f"<td>{html.escape(i['type'])}</td><td>{status}</td></tr>")
    incidents_html = ""
    if inc_rows:
        incidents_html = ("<h2>Инциденты за 14 дней</h2><table class=\"incidents\">"
                          "<tr><th>Начало</th><th>Сервис</th><th>Что случилось</th>"
                          "<th>Длительность</th></tr>" + "".join(inc_rows) + "</table>")
    return _TEMPLATE.substitute(banner_cls=banner_cls, banner=banner,
                                sites_html="".join(rows), incidents_html=incidents_html,
                                now=html.escape(p["now"]))


async def status_html() -> str:
    return render(await payload())


async def status_json() -> dict:
    p = await payload()
    return {"status": "down" if p["any_down"] else ("maintenance" if p["any_maint"] else "up"),
            "generated_at": p["generated_at"], "sites": p["sites"], "incidents": p["incidents"]}


# ── Uptime badge (shields.io style) ──────────────────────────────────────────

_BADGE_TTL_SEC = 60
_badge_cache: dict[int, tuple[float, bool, float | None]] = {}
_BADGE_COLORS = {"good": "#4c1", "warn": "#dfb317", "bad": "#e05d44"}


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
    label = "uptime 7d"
    if pct is None:
        value, color = "n/a", "#9f9f9f"
    else:
        value, color = f"{pct}%", _BADGE_COLORS[uptime_class(pct)]
    lw = int(len(label) * 6.1) + 12   # ~6.1px per char at font-size 11
    vw = int(len(value) * 6.1) + 12
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
  <clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/><rect width="{w}" height="20" fill="url(#s)"/></g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2}" y="14">{label}</text><text x="{lw + vw / 2}" y="14">{value}</text>
  </g>
</svg>"""
