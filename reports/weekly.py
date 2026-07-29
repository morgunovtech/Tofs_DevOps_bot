"""
Weekly report: per-site uptime summary + a response-time chart image,
sent every Sunday.
"""

import logging
import os

import matplotlib
matplotlib.use("Agg")  # headless — must be set before pyplot import
import matplotlib.pyplot as plt

from config import config
from db.database import get_all_sites, get_daily_availability, get_incidents_since
from reports.formatter import _short_host

logger = logging.getLogger(__name__)


def chart_path() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "weekly.png")


def render_chart(series: dict[str, list[dict]], path: str) -> bool:
    """series: {host: [{day, avg_ms, ...}]}. Returns True if a chart was drawn."""
    has_data = any(rows for rows in series.values())
    if not has_data:
        return False
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    for host, rows in series.items():
        if not rows:
            continue
        days = [r["day"][5:] for r in rows]  # MM-DD
        ms = [round(r["avg_ms"] or 0) for r in rows]
        ax.plot(days, ms, marker="o", linewidth=2, label=host)
    ax.set_title("Среднее время ответа за неделю, ms")
    ax.set_ylabel("ms")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
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
        host = _short_host(s["url"])
        series[host] = rows
        total = sum(r["total"] for r in rows)
        ok = sum(r["ok"] for r in rows)
        if total:
            uptime = round(ok / total * 100, 2)
            avg_ms = round(
                sum((r["avg_ms"] or 0) * r["total"] for r in rows) / total
            )
            icon = "✅" if uptime >= 99.9 else ("⚠️" if uptime >= 99 else "🔴")
            lines.append(f"{icon} {host} — uptime {uptime}%, ~{avg_ms}ms")
        else:
            lines.append(f"⏳ {host} — нет данных")

    incidents = await get_incidents_since(days=7)
    if incidents:
        unresolved = sum(1 for i in incidents if not i["resolved"])
        lines.append(
            f"\n⚠️ Инцидентов за неделю: {len(incidents)}"
            + (f" (открытых: {unresolved})" if unresolved else " (все решены)")
        )
        for inc in incidents[:5]:
            lines.append(
                f"  • {inc['created_at'][5:16]} {_short_host(inc['url'])} "
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
