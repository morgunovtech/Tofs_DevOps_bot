"""
Weekly report: per-site uptime summary + a response-time chart image,
sent every Sunday.
"""

import logging
import os
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")  # headless — must be set before pyplot import
import matplotlib.pyplot as plt

from config import config
from db.database import get_all_sites, get_daily_availability, get_incidents_since
from reports.formatter import _short_host, site_label, sparkline, plural
from services import gsc, yandex_webmaster

logger = logging.getLogger(__name__)


def _pct_change(cur: int, prev: int) -> str:
    if prev <= 0:
        return "с нуля" if cur else "0"
    delta = round((cur - prev) / prev * 100)
    return f"+{delta}%" if delta >= 0 else f"{delta}%"


async def search_metrics_lines(site_urls: list[str]) -> list[str]:
    """Weekly search-visibility lines from GSC + Yandex.Webmaster.
    Empty list when neither integration is configured."""
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
            cur = await gsc.search_totals(
                start.isoformat(), end.isoformat(), page_prefix=url)
            prev = await gsc.search_totals(
                prev_start.isoformat(), prev_end.isoformat(), page_prefix=url)
            if cur is None:
                continue
            prev = prev or {"clicks": 0, "impressions": 0}
            g_lines.append(
                f"  {_short_host(url)}: "
                f"{cur['clicks']} {plural(cur['clicks'], 'клик', 'клика', 'кликов')} "
                f"({_pct_change(cur['clicks'], prev['clicks'])}), "
                f"{cur['impressions']} {plural(cur['impressions'], 'показ', 'показа', 'показов')} "
                f"({_pct_change(cur['impressions'], prev['impressions'])})"
            )
        if g_lines:
            lines.append("🔎 Google (неделя к неделе):")
            lines.extend(g_lines)

    if yandex_webmaster.available():
        summaries = await yandex_webmaster.get_summaries()
        y_lines = []
        for host, s in (summaries or {}).items():
            chunks = []
            if s.get("searchable_pages") is not None:
                chunks.append(f"{s['searchable_pages']} стр. в поиске")
            if s.get("sqi") is not None:
                chunks.append(f"ИКС {s['sqi']}")
            if s["alert_problems"]:
                chunks.append(f"⚠️ проблем: {len(s['alert_problems'])}")
            if chunks:
                y_lines.append(f"  {host}: " + ", ".join(chunks))
        if y_lines:
            lines.append("🔎 Яндекс:")
            lines.extend(y_lines)

    return lines


def chart_path() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "weekly.png")


def render_chart(series: dict[str, list[dict]], path: str) -> bool:
    """series: {host: [{day, avg_ms, ...}]}. Returns True if a chart was drawn."""
    has_data = any(rows for rows in series.values())
    if not has_data:
        return False
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    # Terrier-wheaten first — the one accent color of the project.
    palette = ["#C1573B", "#37698A", "#8F7E4F", "#6B6B6B"]
    for i, (host, rows) in enumerate(series.items()):
        if not rows:
            continue
        days = [r["day"][5:] for r in rows]  # MM-DD
        ms = [round(r["avg_ms"] or 0) for r in rows]
        ax.plot(days, ms, marker="o", linewidth=2, label=host,
                color=palette[i % len(palette)])
    ax.set_title("Среднее время ответа за неделю, ms",
                 fontsize=11, color="#333333")
    ax.set_ylabel("ms", color="#666666", fontsize=9)
    ax.grid(True, alpha=0.25, linewidth=0.6)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#999999")
    ax.tick_params(colors="#666666", labelsize=8)
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    # Signature for those who look closely.
    fig.text(0.995, 0.012, "TofsDevOps", ha="right", va="bottom",
             fontsize=8, color="#C1573B", alpha=0.45)
    fig.savefig(path)
    plt.close(fig)
    return True


async def build_weekly_report() -> tuple[str, str | None]:
    """Returns (text, chart_file_path | None)."""
    sites = await get_all_sites()
    lines = ["📊 Итоги недели\n"]

    series: dict[str, list[dict]] = {}
    for s in sites:
        rows = await get_daily_availability(s["id"], days=7)
        # site_label, not bare host: tcp://example.com:25 must not
        # overwrite example.com's chart series.
        host = site_label(s["url"])
        series[host] = rows
        total = sum(r["total"] for r in rows)
        ok = sum(r["ok"] for r in rows)
        if total:
            uptime = round(ok / total * 100, 2)
            avg_ms = round(
                sum((r["avg_ms"] or 0) * r["total"] for r in rows) / total
            )
            icon = "✅" if uptime >= 99.9 else ("⚠️" if uptime >= 99 else "🔴")
            spark = sparkline([r["avg_ms"] for r in rows])
            lines.append(f"{icon} {host} — uptime {uptime}%, ~{avg_ms}ms"
                         + (f"  {spark}" if spark else ""))
        else:
            lines.append(f"⏳ {host} — нет данных")

    try:
        search_lines = await search_metrics_lines(
            [s["url"] for s in sites
             if s["url"].startswith(("http://", "https://"))])
        if search_lines:
            lines.append("")
            lines.extend(search_lines)
    except Exception as e:
        logger.error(f"Search metrics for weekly report failed: {e}")

    incidents = await get_incidents_since(days=7)
    if incidents:
        unresolved = sum(1 for i in incidents if not i["resolved"])
        lines.append(
            f"\n⚠️ Инцидентов за неделю: {len(incidents)}"
            + (f" (открытых: {unresolved})" if unresolved else " (все решены)")
        )
        for inc in incidents[:5]:
            lines.append(
                f"  • {inc['created_at'][5:16]} {site_label(inc['url'])} "
                f"[{inc['check_type']}]"
            )
        if len(incidents) > 5:
            lines.append(f"  … и ещё {len(incidents) - 5}")
    else:
        lines.append("\n🎉 Ни одного инцидента за неделю")

    path = chart_path()
    chart_ok = False
    try:
        chart_ok = render_chart(series, path)
    except Exception as e:
        logger.error(f"Weekly chart rendering failed: {e}")

    return "\n".join(lines), (path if chart_ok else None)
