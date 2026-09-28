"""Weekly report: per-site uptime over 7/30/90 days, an ASCII response-time
chart (monospace, no image libraries), what is open now and what was
solved this week, facts of normal operation (certificates that renewed by
themselves), search metrics and the dependency check — sent every Sunday.
This is where everything 🟠 is promised to show up."""

import logging
from datetime import UTC, date, datetime, timedelta

from config import config
from db.database import (
    get_active_incidents,
    get_all_sites,
    get_daily_availability,
    get_incidents_since,
    get_uptime_over_days,
)
from services import gsc, humanize, index_status, sitestatus, updates, yandex_webmaster
from utils.text import bar, esc, parse_iso_utc, plural
from utils.urls import is_http_url, short_host, site_label

logger = logging.getLogger(__name__)

CHART_WIDTH = 18


def _pct_change(cur: int, prev: int) -> str:
    if prev <= 0:
        return "с нуля" if cur else "0"
    delta = round((cur - prev) / prev * 100)
    return f"+{delta}%" if delta >= 0 else f"{delta}%"


def render_ascii_chart(series: dict[str, list[dict]]) -> list[str]:
    """Per-site daily average response time as monospace bars — one <pre>
    block per site so long charts can be packed into several messages.
    One shared scale so sites are comparable."""
    peak = max((r["avg_ms"] or 0 for rows in series.values() for r in rows), default=0)
    if peak <= 0:
        return []
    blocks: list[str] = []
    for label, rows in series.items():
        if not rows:
            continue
        lines = [esc(label)]
        for r in rows:
            ms = r["avg_ms"] or 0
            day = f"{r['day'][8:10]}.{r['day'][5:7]}"
            lines.append(f"{day} {bar(ms, peak, CHART_WIDTH)} {humanize.fmt_seconds(ms):>7}")
        blocks.append("<pre>" + "\n".join(lines) + "</pre>")
    return blocks


async def search_metrics_lines(site_urls: list[str]) -> list[str]:
    """Weekly search-visibility lines from GSC + Yandex.Webmaster."""
    lines: list[str] = []
    if gsc.available():
        # GSC data lags ~2 days; compare the freshest full week with the
        # week before it.
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=6)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        g_lines = []
        for url in site_urls:
            cur = await gsc.search_totals(start.isoformat(), end.isoformat(), page_prefix=url)
            prev = await gsc.search_totals(prev_start.isoformat(), prev_end.isoformat(),
                                           page_prefix=url)
            if cur is None:
                continue
            prev = prev or {"clicks": 0, "impressions": 0}
            g_lines.append(
                f"  {esc(short_host(url))}: "
                f"{cur['clicks']} {plural(cur['clicks'], 'клик', 'клика', 'кликов')} "
                f"({_pct_change(cur['clicks'], prev['clicks'])}), "
                f"{cur['impressions']} {plural(cur['impressions'], 'показ', 'показа', 'показов')} "
                f"({_pct_change(cur['impressions'], prev['impressions'])})")
        if g_lines:
            lines += ["🔎 Google (неделя к неделе):", *g_lines]
    if yandex_webmaster.available():
        y_lines = []
        for host, s in (await yandex_webmaster.get_summaries() or {}).items():
            chunks = []
            if s.get("searchable_pages") is not None:
                chunks.append(f"{s['searchable_pages']} стр. в поиске")
            if s.get("sqi") is not None:
                chunks.append(f"ИКС {s['sqi']}")
            if s["alert_problems"]:
                icon = humanize.level_icon(index_status.yandex_problem_level(s["alert_problems"]))
                chunks.append(f"{icon} проблем в Вебмастере: {len(s['alert_problems'])}")
            if chunks:
                y_lines.append(f"  {esc(host)}: " + ", ".join(chunks))
        if y_lines:
            lines += ["🔎 Яндекс:", *y_lines]
    return lines


async def build_weekly_report() -> tuple[str, list[str]]:
    """(summary text, chart blocks) — the chart goes out as separate
    message(s) so a long one never pushes the summary past the limit."""
    sites = await get_all_sites()
    lines = ["📊 Итоги недели\n"]
    series: dict[str, list[dict]] = {}
    for s in sites:
        rows = await get_daily_availability(s["id"], days=7)
        label = site_label(s["url"])   # tcp://example.com:25 ≠ example.com
        series[label] = rows
        total = sum(r["total"] for r in rows)
        if not total:
            lines.append(f"⏳ {esc(label)} — нет данных")
            continue
        ok = sum(r["ok"] for r in rows)
        interval = s.get("check_interval_min") or config.check_interval_minutes
        month = await get_uptime_over_days(s["id"], 30)
        avg_ms = round(sum((r["avg_ms"] or 0) * r["total"] for r in rows) / total)
        # Last week's downtime is history, not an open problem: ✅ when there
        # was none, 📉 otherwise — never a level icon.
        icon = "✅" if ok == total else "📉"
        week_txt = humanize.downtime(total, ok, interval)
        month_txt = humanize.downtime(month["total_checks"], month["ok_checks"], interval)
        lines.append(f"{icon} {esc(label)}: за неделю {week_txt}, за месяц {month_txt}. "
                     f"Открывается {humanize.speed(avg_ms)}.")

    chart = render_ascii_chart(series)

    try:
        search_lines = await search_metrics_lines(
            [s["url"] for s in sites if is_http_url(s["url"])])
        if search_lines:
            lines += ["", *search_lines]
    except Exception as e:
        logger.error("Search metrics for weekly report failed: %s", e)

    lines += await incidents_block()
    lines += await renewals_block(sites)

    deps = updates.summary_lines(await updates.last_check())
    if deps:
        lines += ["", *deps]
    return "\n".join(lines), chart


def _day(sqlite_dt: str | None) -> str:
    return f"{(sqlite_dt or '')[8:10]}.{(sqlite_dt or '')[5:7]}"


def _headline(inc: dict) -> str:
    return esc(humanize.incident_headline(inc["check_type"], inc.get("message"), site_label(inc["url"])))


async def incidents_block() -> list[str]:
    """Everything open now, each with its level (however old it is — this is
    where 🟠 waits), then what was solved this week. Counters carry no
    problem icon: the icon belongs to the problem, not to the number."""
    active = await get_active_incidents()
    solved = [i for i in await get_incidents_since(days=7) if i["resolved"]]
    lines: list[str] = []
    if active:
        lines.append(f"\nОткрыто сейчас ({len(active)}):")
        lines += [f"  {humanize.level_icon(inc['severity'])} {_headline(inc)} — с {_day(inc['created_at'])}"
                  for inc in active[:5]]
        if len(active) > 5:
            lines.append(f"  … и ещё {len(active) - 5}")
    if solved:
        lines.append(f"\nРешено за неделю: {len(solved)}")
        lines += [f"  ✓ {_day(inc['created_at'])} {_headline(inc)}" for inc in solved[:5]]
        if len(solved) > 5:
            lines.append(f"  … и ещё {len(solved) - 5}")
    if not active and not solved:
        lines.append("\n🎉 За неделю ни одной проблемы")
    return lines


async def renewals_block(sites: list[dict]) -> list[str]:
    """Certificates that renewed by themselves this week — a fact of normal
    operation, so it is said here and nowhere else."""
    since = datetime.now(UTC) - timedelta(days=7)
    chips = []
    for s in sites:
        if not is_http_url(s["url"]):
            continue
        ssl = (await sitestatus.get(s["id"])).get("ssl") or {}
        at = parse_iso_utc(ssl.get("renewed_at"))
        if at and at >= since:
            chips.append(f"{esc(short_host(s['url']))} ({at.strftime('%d.%m')})")
    return ["\n🔒 Сертификаты продлились сами: " + ", ".join(chips)] if chips else []
