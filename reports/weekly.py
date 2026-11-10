"""Weekly report: per-site downtime over the week and the month (the sites
without a single outage as one line), the response time of the week only
where it moved (a sparkline per such site, no image libraries), what is
open now and what was solved this week, facts of normal operation
(certificates that renewed by themselves), search metrics and the
dependency check — sent every Sunday. This is where everything 🟠 is
promised to show up."""

import logging
import statistics
from datetime import UTC, date, datetime, timedelta

from config import config
from db.database import (
    get_active_incidents,
    get_all_sites,
    get_daily_availability,
    get_incidents_since,
    get_uptime_over_days,
)
from services import github, gsc, humanize, index_status, integrations, sitestatus, updates, yandex_webmaster
from utils.clock import to_local
from utils.text import esc, parse_iso_utc, parse_sqlite_utc, plural, sparkline
from utils.urls import is_http_url, is_web_page, short_host, site_label

logger = logging.getLogger(__name__)

# A day at least this many times the site's usual answer time is a change
# worth a line; below it the week was level.
SPEED_JUMP = 1.3


def _pct_change(cur: int, prev: int) -> str:
    if prev <= 0:
        return "с нуля" if cur else "0"
    delta = round((cur - prev) / prev * 100)
    return f"+{delta}%" if delta >= 0 else f"{delta}%"


def _range(values: list[int]) -> str:
    """«0.12 с–0.40 с», or one number when they are all the same."""
    lo, hi = humanize.fmt_seconds(min(values)), humanize.fmt_seconds(max(values))
    return lo if lo == hi else f"{lo}–{hi}"


def speed_lines(series: dict[str, list[dict]], slow_ms: dict[str, int]) -> list[str]:
    """A line per site whose speed moved this week — a sparkline, the
    median, the name; the level ones as one line. Nothing when nothing
    moved: a chart of twenty straight lines says only «still fine»."""
    moved, steady = [], []
    for label, rows in series.items():
        vals = [r["avg_ms"] for r in rows if r["avg_ms"]]
        if len(vals) < 3:
            continue
        mid = statistics.median(vals)
        if max(vals) >= SPEED_JUMP * mid or max(vals) >= slow_ms.get(label, config.slow_response_ms):
            moved.append(f"{sparkline(vals, min_rel_range=0.3)} {humanize.fmt_seconds(mid)} · {esc(label)}")
        else:
            steady.append(round(mid))
    if moved and steady:
        moved.append(f"Ровно у остальных {len(steady)}: {_range(steady)}")
    return moved


async def search_metrics_lines(site_urls: list[str]) -> list[str]:
    """Weekly search-visibility lines from GSC + Yandex.Webmaster. Only the
    sites the Search Console property covers are asked about; a service
    that gave nothing is said so, with its reason — not left out."""
    lines: list[str] = []
    if gsc.available():
        # GSC data lags ~2 days; compare the freshest full week with the
        # week before it.
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=6)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        prop = integrations.gsc_property()
        covered = [url for url in site_urls if gsc.covers(prop, url)]
        g_lines = []
        for url in covered:
            cur = await gsc.search_totals(start.isoformat(), end.isoformat(), page_prefix=url)
            if cur is None:
                continue
            prev = await gsc.search_totals(prev_start.isoformat(), prev_end.isoformat(),
                                           page_prefix=url)
            clicks = f"{cur['clicks']} {plural(cur['clicks'], 'клик', 'клика', 'кликов')}"
            shows = f"{cur['impressions']} {plural(cur['impressions'], 'показ', 'показа', 'показов')}"
            if prev is None:
                g_lines.append(f"  {esc(short_host(url))}: {clicks}, {shows} (с прошлой неделей сравнить не удалось)")
            else:
                g_lines.append(f"  {esc(short_host(url))}: {clicks} ({_pct_change(cur['clicks'], prev['clicks'])}), "
                               f"{shows} ({_pct_change(cur['impressions'], prev['impressions'])})")
        if g_lines:
            lines += ["🔎 Google (неделя к неделе):", *g_lines]
        elif covered:
            lines.append(f"⚠️ Google Search Console: не смог получить данные — {esc(await _reason('gsc'))}")
    if yandex_webmaster.available():
        summaries = await yandex_webmaster.get_summaries()
        if summaries is None:
            lines.append(f"⚠️ Яндекс.Вебмастер: не смог получить данные — {esc(await _reason('yandex'))}")
        y_lines = []
        for host, s in (summaries or {}).items():
            chunks = []
            if s.get("searchable_pages") is not None:
                chunks.append(f"{s['searchable_pages']} стр. в поиске")
            if s.get("sqi") is not None:
                chunks.append(f"ИКС {s['sqi']}")
            if s["alert_problems"]:
                icon = humanize.level_icon(index_status.yandex_problem_level(s["alert_problems"]))
                n = index_status.yandex_problem_count(s["alert_problems"])
                chunks.append(f"{icon} {n} {plural(n, 'проблема', 'проблемы', 'проблем')} в Вебмастере")
            if chunks:
                y_lines.append(f"  {esc(host)}: " + ", ".join(chunks))
        if y_lines:
            lines += ["🔎 Яндекс:", *y_lines]
    return lines


async def _reason(name: str) -> str:
    """Why the service gave nothing, from its health record."""
    return (await integrations.health(name)).get("reason") or "не ответил"


async def build_weekly_report() -> tuple[str, list[str]]:
    """(summary text, speed lines) — the speed goes out as a separate
    message so a long one never pushes the summary past the limit."""
    sites = await get_all_sites()
    lines = ["📊 Итоги недели\n"]
    series: dict[str, list[dict]] = {}
    slow: dict[str, int] = {}
    clean: list[int | None] = []   # the answer times of the sites without an outage
    for s in sites:
        rows = await get_daily_availability(s["id"], days=7)
        label = site_label(s["url"])   # tcp://example.com:25 ≠ example.com
        series[label] = rows
        slow[label] = s.get("slow_ms") or config.slow_response_ms
        total = sum(r["total"] for r in rows)
        if not total:
            lines.append(f"⏳ {esc(label)} — нет данных")
            continue
        week = await get_uptime_over_days(s["id"], 7)
        month = await get_uptime_over_days(s["id"], 30)
        avg_ms = week["avg_response_ms"]
        # A site without a single outage over the week and the month is
        # one of a count below, not a line of its own twenty times over.
        if not week["down_min"] and not month["down_min"]:
            clean.append(avg_ms)
            continue
        # Last week's downtime is history, not an open problem: ✅ when there
        # was none, 📉 otherwise — never a level icon.
        icon = "📉" if week["down_min"] else "✅"
        week_txt = humanize.downtime(week)
        month_txt = humanize.downtime(month)
        verb = "Открывается" if is_web_page(s["url"], s.get("http_method")) else "Отвечает"
        lines.append(f"{icon} {esc(label)}: за неделю {week_txt}, за месяц {month_txt}. "
                     f"{verb} {humanize.speed(avg_ms)}.")
    if clean:
        n, ms = len(clean), [v for v in clean if v]
        lines.append(f"✅ {n} {plural(n, 'сайт', 'сайта', 'сайтов')} — без сбоев за неделю и за месяц"
                     + (f", {'отвечает' if n == 1 else 'отвечают'} за {_range(ms)}" if ms else ""))

    chart = speed_lines(series, slow)

    async def search() -> list[str]:
        found = await search_metrics_lines([s["url"] for s in sites if is_web_page(s["url"], s.get("http_method"))])
        return ["", *found] if found else []

    async def deps() -> list[str]:
        found = updates.summary_lines(await updates.last_check())
        return ["", *found] if found else []

    async def hygiene() -> list[str]:
        found = github.summary_lines(await github.last_report())
        return ["", *found] if found else []

    for name, make in (("Поиск", search), ("Инциденты", incidents_block),
                       ("Продления", lambda: renewals_block(sites)), ("Зависимости", deps), ("GitHub", hygiene)):
        lines += await _section(name, make)
    return "\n".join(lines), chart


async def _section(name: str, make) -> list[str]:
    """A side section that fails costs only itself: logged with its traceback,
    named in the report, and the rest of the report still goes out."""
    try:
        return await make()
    except Exception:
        logger.exception("Weekly report: section %s failed", name)
        return ["", f"⚠️ {name}: не смог собрать — подробности в логе бота"]


def _day(sqlite_dt: str | None) -> str:
    """'03.11' in the owner's timezone (SQLite stores UTC: an evening in
    Moscow is still «yesterday» there)."""
    dt = parse_sqlite_utc(sqlite_dt)
    return to_local(dt).strftime("%d.%m") if dt else ""


def _headline(inc: dict) -> str:
    return esc(humanize.incident_headline(inc["check_type"], inc.get("message"), site_label(inc["url"]),
                                          page=is_web_page(inc["url"], inc.get("http_method"))))


async def incidents_block() -> list[str]:
    """Everything open now, each with its level (however old it is — this is
    where 🟠 waits), then what was solved this week. Counters carry no
    problem icon: the icon belongs to the problem, not to the number."""
    # Every open one, 🔴 first and the oldest first within a level, no cut:
    # the report is split into messages anyway, and a cut would hide
    # exactly the old 🔴 behind five new 🟠.
    active = sorted(await get_active_incidents(), key=lambda i: (i["severity"] != "critical", i["created_at"]))
    solved = [i for i in await get_incidents_since(days=7) if i["resolved"]]
    lines: list[str] = []
    if active:
        lines.append(f"\nОткрыто сейчас ({len(active)}):")
        lines += [f"{humanize.level_icon(inc['severity'])} {_headline(inc)} — с {_day(inc['created_at'])}"
                  for inc in active]
    if solved:
        lines.append(f"\nРешено за неделю: {len(solved)}")
        lines += [f"✓ {_day(inc.get('resolved_at') or inc['created_at'])} {_headline(inc)}" for inc in solved[:5]]
        if len(solved) > 5:
            lines.append(f"… и ещё {len(solved) - 5}")
    if not active and not solved:
        lines.append("\n🎉 За неделю ни одной проблемы")
    return lines


async def renewals_block(sites: list[dict]) -> list[str]:
    """Certificates that renewed by themselves this week — a fact of normal
    operation, so it is said here and nowhere else."""
    since = datetime.now(UTC) - timedelta(days=7)
    chips: dict[str, str] = {}   # one chip per host: a site and its API share the certificate
    for s in sites:
        if not is_http_url(s["url"]):
            continue
        ssl = (await sitestatus.get(s["id"])).get("ssl") or {}
        at = parse_iso_utc(ssl.get("renewed_at"))
        if at and at >= since:
            chips.setdefault(short_host(s["url"]), f"{esc(short_host(s['url']))} ({to_local(at).strftime('%d.%m')})")
    return ["\n🔒 Сертификаты продлились сами: " + ", ".join(chips.values())] if chips else []
