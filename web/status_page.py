"""
Public status page + uptime badge (Uptime-Kuma-style, one server-rendered
HTML file, zero JS).

Privacy model: the page is OFF by default (it reveals the monitored site
list). It shows current state, uptime numbers and an anonymised incident
history (type + time window, never raw error messages — those can contain
internal details). STATUS_PAGE_SLUG turns the URL into a shareable secret.

Data comes strictly from the DB (last saved checks) — a page hit never
triggers live checks, so the endpoint can't be used to make the bot hammer
the monitored sites.
"""

import html
import time
from datetime import datetime, timezone

import pytz

from config import config
from db.database import (
    get_all_sites, get_site, get_last_check, get_uptime_stats,
    get_incidents_since,
)
from reports.formatter import site_label, _parse_sqlite_utc
from services import settings

# Rendered-page cache: (monotonic timestamp, html). 30s keeps a popular
# page from hammering SQLite while staying fresh enough for a status page.
_CACHE_TTL_SEC = 30
_cache: tuple[float, str] | None = None

# Public names for incident types — deliberately generic: the page must not
# leak raw error strings or internal paths.
_TYPE_RU = {
    "availability": "Недоступность",
    "performance": "Медленные ответы",
    "ssl": "Проблема с SSL-сертификатом",
    "domain": "Проблема с доменом",
    "links": "Битые ссылки",
    "deep": "Ошибки на части страниц",
    "seo": "Проблема с индексацией",
}


def _fmt_local(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    if not dt:
        return "—"
    return dt.astimezone(pytz.timezone(config.timezone)).strftime(fmt)


def _duration(created: datetime | None, resolved: datetime | None) -> str:
    if not created or not resolved:
        return ""
    minutes = max(1, round((resolved - created).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 24 * 60:
        return f"{minutes // 60} ч {minutes % 60} мин"
    return f"{minutes // 1440} дн {minutes % 1440 // 60} ч"


async def _payload() -> dict:
    sites = []
    active_ids = set()
    any_down = False
    any_maint = settings.maintenance_now(None)
    # Raw checks only survive RETENTION_DAYS — with a shorter retention the
    # "30d" column would silently cover less than it claims, so clamp the
    # window AND the label.
    long_days = max(1, min(30, config.retention_days))
    for s in await get_all_sites():
        active_ids.add(s["id"])
        last = await get_last_check(s["id"], "availability")
        maint = settings.maintenance_now(s["id"])
        if maint:
            state = "maint"
        elif last is None:
            state = "pending"
        elif last["status"] == "ok":
            state = "up"
        else:
            state = "down"
            any_down = True
        sites.append({
            "label": site_label(s["url"]),
            "state": state,
            "ms": (last or {}).get("response_time_ms"),
            "up24": (await get_uptime_stats(s["id"], hours=24))["uptime_pct"],
            "up7d": (await get_uptime_stats(s["id"], hours=168))["uptime_pct"],
            "up30d": (await get_uptime_stats(
                s["id"], hours=long_days * 24))["uptime_pct"],
            "checked_at": _fmt_local(
                _parse_sqlite_utc((last or {}).get("checked_at") or ""), "%H:%M"),
        })

    incidents = []
    for inc in reversed(await get_incidents_since(14)):
        if len(incidents) >= 10:
            break
        # Sites removed from monitoring must not haunt the PUBLIC history.
        if inc["site_id"] not in active_ids:
            continue
        created = _parse_sqlite_utc(inc.get("created_at") or "")
        resolved = (_parse_sqlite_utc(inc.get("resolved_at") or "")
                    if inc.get("resolved") else None)
        incidents.append({
            "label": site_label(inc["url"]),
            "type": _TYPE_RU.get(inc["check_type"], inc["check_type"]),
            "open": not inc.get("resolved"),
            "started": _fmt_local(created),
            "duration": _duration(created, resolved),
        })

    return {
        "sites": sites,
        "incidents": incidents,
        "any_down": any_down,
        "any_maint": any_maint,
        "long_days": long_days,
        "now": _fmt_local(datetime.now(timezone.utc)),
    }


def _uptime_class(pct: float) -> str:
    if pct >= 99:
        return "good"
    if pct >= 95:
        return "warn"
    return "bad"


_STATE = {
    "up": ("dot-up", "работает"),
    "down": ("dot-down", "недоступен"),
    "maint": ("dot-maint", "тех. работы"),
    "pending": ("dot-pending", "ждёт первой проверки"),
}


def _render(p: dict) -> str:
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
        rows.append(f"""
      <div class="site">
        <div class="site-head">
          <span class="dot {dot_cls}"></span>
          <span class="site-name">{html.escape(s['label'])}</span>
          <span class="site-state">{state_txt} · {ms}</span>
        </div>
        <div class="uptime">
          <span class="{_uptime_class(s['up24'])}">24ч: {s['up24']}%</span>
          <span class="{_uptime_class(s['up7d'])}">7д: {s['up7d']}%</span>
          <span class="{_uptime_class(s['up30d'])}">{p['long_days']}д: {s['up30d']}%</span>
        </div>
      </div>""")

    inc_rows = []
    for i in p["incidents"]:
        status = ("<span class='inc-open'>сейчас</span>" if i["open"]
                  else html.escape(i["duration"]))
        inc_rows.append(
            f"<tr><td>{html.escape(i['started'])}</td>"
            f"<td>{html.escape(i['label'])}</td>"
            f"<td>{html.escape(i['type'])}</td>"
            f"<td>{status}</td></tr>")
    incidents_html = ""
    if inc_rows:
        incidents_html = f"""
    <h2>Инциденты за 14 дней</h2>
    <table class="incidents">
      <tr><th>Начало</th><th>Сервис</th><th>Что случилось</th><th>Длительность</th></tr>
      {''.join(inc_rows)}
    </table>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta http-equiv="refresh" content="60">
<title>Статус сервисов</title>
<style>
  :root {{
    --bg: #f6f7f9; --card: #ffffff; --text: #1a1d21; --muted: #70757d;
    --up: #22a55b; --down: #dc3f45; --maint: #3577d4; --warn: #d9822b;
    --border: #e4e6ea;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a; --card: #1d2026; --text: #e8eaed; --muted: #9aa0a8;
      --border: #2a2e35;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font: 16px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
    max-width: 640px; margin: 0 auto; padding: 24px 16px 48px;
  }}
  .banner {{
    border-radius: 12px; padding: 16px 20px; font-weight: 600;
    color: #fff; margin-bottom: 20px;
  }}
  .banner-up {{ background: var(--up); }}
  .banner-down {{ background: var(--down); }}
  .banner-maint {{ background: var(--maint); }}
  .site {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
  }}
  .site-head {{ display: flex; align-items: center; gap: 10px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; flex: none; }}
  .dot-up {{ background: var(--up); }}
  .dot-down {{ background: var(--down); }}
  .dot-maint {{ background: var(--maint); }}
  .dot-pending {{ background: var(--muted); }}
  .site-name {{ font-weight: 600; overflow-wrap: anywhere; }}
  .site-state {{ margin-left: auto; color: var(--muted); font-size: 14px;
                 white-space: nowrap; }}
  .uptime {{ margin-top: 6px; display: flex; gap: 14px; font-size: 14px;
             flex-wrap: wrap; }}
  .good {{ color: var(--up); }} .warn {{ color: var(--warn); }}
  .bad {{ color: var(--down); }}
  h2 {{ font-size: 16px; margin: 28px 0 10px; }}
  .incidents {{ width: 100%; border-collapse: collapse; font-size: 14px;
                background: var(--card); border: 1px solid var(--border);
                border-radius: 12px; overflow: hidden; }}
  .incidents th, .incidents td {{
    text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border);
  }}
  .incidents tr:last-child td {{ border-bottom: none; }}
  .incidents th {{ color: var(--muted); font-weight: 500; }}
  .inc-open {{ color: var(--down); font-weight: 600; }}
  footer {{ margin-top: 28px; color: var(--muted); font-size: 13px; }}
</style>
</head>
<body>
  <div class="banner {banner_cls}">{banner}</div>
  {''.join(rows)}
  {incidents_html}
  <footer>Обновлено {p['now']} · страница обновляется автоматически ·
    TofsDevOps 🐕</footer>
</body>
</html>"""


async def status_html() -> str:
    """Rendered page with a small TTL cache."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _CACHE_TTL_SEC:
        return _cache[1]
    page = _render(await _payload())
    _cache = (now, page)
    return page


# ── Uptime badge (shields.io style) ──────────────────────────────────────────

# Per-site badge cache: {site_id: (monotonic_ts, found, pct)} — anonymous
# badge hits must not each run a 7-day aggregate scan on the shared SQLite
# connection.
_BADGE_TTL_SEC = 60
_badge_cache: dict[int, tuple[float, bool, float | None]] = {}


async def badge_pct(site_id: int) -> tuple[bool, float | None]:
    """(site exists and is active, uptime % over 7 days or None if no data),
    cached for a minute."""
    now = time.monotonic()
    hit = _badge_cache.get(site_id)
    if hit and now - hit[0] < _BADGE_TTL_SEC:
        return hit[1], hit[2]
    site = await get_site(site_id)
    if not site or not site["active"]:
        found, pct = False, None
    else:
        found = True
        # No checks yet → grey "n/a" instead of a scary 0%.
        pct = None
        if await get_last_check(site_id, "availability"):
            pct = (await get_uptime_stats(site_id, hours=168))["uptime_pct"]
    if len(_badge_cache) > 256:  # bound memory against id scans
        _badge_cache.clear()
    _badge_cache[site_id] = (now, found, pct)
    return found, pct



_BADGE_COLORS = {"good": "#4c1", "warn": "#dfb317", "bad": "#e05d44"}


def badge_svg(pct: float | None) -> str:
    """Two-cell SVG badge: 'uptime 7d | 99.98%'. pct=None → grey 'n/a'."""
    label = "uptime 7d"
    if pct is None:
        value, color = "n/a", "#9f9f9f"
    else:
        value = f"{pct}%"
        color = _BADGE_COLORS[_uptime_class(pct)]
    # ~6.1px per char at font-size 11 — the shields.io approximation.
    lw = int(len(label) * 6.1) + 12
    vw = int(len(value) * 6.1) + 12
    w = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="20" role="img" aria-label="{label}: {value}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r"><rect width="{w}" height="20" rx="3" fill="#fff"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="20" fill="#555"/>
    <rect x="{lw}" width="{vw}" height="20" fill="{color}"/>
    <rect width="{w}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
    <text x="{lw / 2}" y="14">{label}</text>
    <text x="{lw + vw / 2}" y="14">{value}</text>
  </g>
</svg>"""
