import html
from datetime import datetime, timezone
from urllib.parse import urlparse

import pytz

from config import config


def _esc(value) -> str:
    """HTML-escape a value for safe rendering in ParseMode.HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _short_host(url: str) -> str:
    return urlparse(url).hostname or url


def fmt_date(iso: str) -> str:
    """'2026-10-15…' → '15.10.2026'."""
    try:
        return datetime.strptime(iso[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso or "N/A"


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list) -> str:
    """Unicode sparkline for response-time trends: ▂▃▂▁▅▂. Empty string
    when there isn't enough data to say anything."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return ""
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return SPARK_CHARS[1] * len(vals)
    return "".join(
        SPARK_CHARS[min(7, int((v - lo) / (hi - lo) * 7 + 0.5))] for v in vals)


def now_local() -> datetime:
    """Current time in the configured timezone — the container itself runs
    in UTC, so naive datetime.now() would show server time to the user."""
    return datetime.now(pytz.timezone(config.timezone))


# ── One-line per site (compact) ──────────────────────────────────────────────

def _avail_chip(r: dict) -> str:
    if r.get("status") == "ok":
        ms = r.get("response_time_ms")
        return f"{ms}ms" if ms else "ok"
    return f"❌ {_esc(r.get('error', 'down'))}"


def _ssl_chip(r: dict) -> str:
    """Empty string while healthy — the compact line stays short on mobile;
    the chip appears only when expiry is close enough to care."""
    info = r.get("ssl_info")
    if not info:
        return "SSL ?"
    days = info["days_left"]
    if days < 0:
        return f"SSL ⛔ ({abs(days)}д назад)"
    if days <= 14:
        return f"SSL ⚠ {days}д"
    return ""


def _domain_chip(r: dict) -> str:
    info = r.get("domain_info")
    if not info or info.get("days_left") is None:
        return "домен ?"
    days = info["days_left"]
    if days < 0:
        return f"домен ⛔ ({abs(days)}д назад)"
    if days <= 30:
        return f"домен ⚠ {days}д"
    return ""


def format_compact_status_report(availability: list[dict],
                                 incidents: list[dict],
                                 ssl_results: list[dict] | None = None,
                                 domain_results: list[dict] | None = None,
                                 report_type: str = "status",
                                 extras: list[str] | None = None) -> str:
    """One concise message: header + one line per site + incidents (if any)."""
    now = now_local().strftime("%d.%m %H:%M")
    if report_type == "morning":
        header = f"🌅 Доброе утро · {now}"
    elif report_type == "evening":
        header = f"🌙 Вечер · {now}"
    else:
        header = f"📊 Статус · {now}"

    ssl_by_url = {r["url"]: r for r in (ssl_results or [])}

    # Domain results are deduped by registrable domain — index by host root.
    def _root(host: str) -> str:
        parts = (host or "").split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else host

    domain_by_root = {}
    for r in (domain_results or []):
        domain_by_root[_root(_short_host(r.get("url", "")))] = r

    lines: list[str] = [header, ""]

    any_problem = False
    for r in availability:
        host = _short_host(r["url"])
        avail = _avail_chip(r)
        ssl = _ssl_chip(ssl_by_url.get(r["url"], {})) if ssl_results else ""
        dom = _domain_chip(domain_by_root.get(_root(host), {})) if domain_results else ""

        is_ok = (
            r.get("status") == "ok"
            and "⛔" not in ssl and "⚠" not in ssl
            and "⛔" not in dom and "⚠" not in dom
            and "?" not in ssl and "?" not in dom
        )
        icon = "✅" if is_ok else "⚠️"
        if not is_ok:
            any_problem = True

        chips = " · ".join(c for c in [avail, ssl, dom] if c)
        lines.append(f"{icon} {_esc(host)} — {chips}")

    if incidents:
        lines.append("")
        lines.append(f"⚠️ Активных проблем: {len(incidents)}")
        for inc in incidents[:5]:
            sev = "🔴" if inc["severity"] == "critical" else "⚠️"
            lines.append(
                f"  {sev} {_esc(_short_host(inc['url']))} "
                f"[{_esc(inc['check_type'])}]: {_esc(inc['message'])}"
            )
        if len(incidents) > 5:
            lines.append(f"  … и ещё {len(incidents) - 5}")
    elif not any_problem:
        lines.append("")
        lines.append("Всё спокойно 🐾")

    if extras:
        lines.append("")
        lines.extend(extras)

    return "\n".join(lines)


# Backwards-compat alias used by interactive /menu_status handler.
format_status_report = format_compact_status_report


# ── Single-event alerts ──────────────────────────────────────────────────────

def format_availability_alert(result: dict) -> str:
    if result["status"] != "error":
        return ""
    return (
        f"🚨 САЙТ НЕДОСТУПЕН\n"
        f"{_esc(result['url'])}\n"
        f"Ошибка: {_esc(result.get('error', 'Unknown'))}\n"
        f"Код: {_esc(result.get('status_code', 'N/A'))}\n"
        f"Время ответа: {_esc(result.get('response_time_ms', 'N/A'))}ms"
    )


def _parse_sqlite_utc(value: str) -> datetime | None:
    """Parse sqlite's datetime('now') format ('YYYY-MM-DD HH:MM:SS', UTC)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def incident_duration_line(incident: dict | None) -> str | None:
    """'лежал 12 мин (14:03–14:15)' — one line summarising the outage window
    in the user's local timezone. None when the data isn't parseable."""
    if not incident:
        return None
    started = _parse_sqlite_utc(incident.get("created_at", ""))
    if not started:
        return None
    ended = datetime.now(timezone.utc)
    minutes = max(1, round((ended - started).total_seconds() / 60))
    tz = pytz.timezone(config.timezone)
    fmt = "%H:%M" if minutes < 24 * 60 else "%d.%m %H:%M"
    window = (f"{started.astimezone(tz).strftime(fmt)}–"
              f"{ended.astimezone(tz).strftime(fmt)}")
    if minutes < 60:
        dur = f"{minutes} мин"
    else:
        dur = f"{minutes // 60} ч {minutes % 60} мин"
    return f"Длительность: {dur} ({window})"


def format_recovery_alert(result: dict) -> str:
    lines = [
        f"✅ САЙТ ВОССТАНОВЛЕН",
        f"{_esc(result['url'])}",
        f"Код: {_esc(result.get('status_code', 'N/A'))}\n"
        f"Время ответа: {_esc(result.get('response_time_ms', 'N/A'))}ms",
    ]
    # Post-incident summary: how long it was down and what the problem was.
    incident = result.get("resolved_incident")
    duration = incident_duration_line(incident)
    if duration:
        lines.append(duration)
    if incident and incident.get("message"):
        lines.append(f"Причина: {_esc(incident['message'])}")
    return "\n".join(lines)


def format_dns_change(change: dict) -> str:
    icon = "🚨" if change.get("critical") else "⚠️"
    header = ("DNS: СМЕНИЛИСЬ NS-ЗАПИСИ (проверь, не угнали ли домен!)"
              if change.get("critical") else "DNS-запись изменилась")
    return (
        f"{icon} {header}\n"
        f"{_esc(change['rtype'])} {_esc(change['host'])}\n"
        f"Было: {_esc(change['old'])}\n"
        f"Стало: {_esc(change['new'])}"
    )


def format_ssl_alert(result: dict) -> str:
    if result["status"] not in ("error", "critical", "warning"):
        return ""
    icon = "🔴" if result["status"] in ("error", "critical") else "⚠️"
    return f"{icon} SSL: {_esc(result['url'])}\n{_esc(result['error'])}"


def format_domain_alert(result: dict) -> str:
    if result["status"] not in ("error", "critical", "warning"):
        return ""
    if not result.get("error"):
        return ""
    icon = "🔴" if result["status"] in ("error", "critical") else "⚠️"
    return (
        f"{icon} Домен: {_esc(result.get('domain', result['url']))}\n"
        f"{_esc(result['error'])}"
    )


def format_links_report(result: dict) -> str:
    internal = result.get("broken_internal") or []
    external = result.get("broken_external") or []

    if not internal and not external:
        return ""

    lines: list[str] = []
    if internal:
        lines.append(
            f"🔗 Битые внутренние ссылки на {_esc(_short_host(result['url']))} "
            f"({len(internal)} шт.):"
        )
        for b in internal[:10]:
            code = b.get("status_code") or b.get("error", "N/A")
            lines.append(f"  • {_esc(b['url'])} — {_esc(code)}")
        if len(internal) > 10:
            lines.append(f"  … и ещё {len(internal) - 10}")

    if external and not internal:
        # Only mention external if there's no internal — otherwise user already
        # has actionable items. (And external alone never triggers an alert.)
        lines.append(
            f"ℹ️ Внешних ресурсов недоступно: {len(external)} "
            "(чужие домены — обычно ничего делать не нужно)"
        )
    elif external:
        lines.append("")
        lines.append(
            f"ℹ️ Также {len(external)} внешних ресурса недоступны "
            "(чужие домены — обычно не критично)"
        )

    return "\n".join(lines)


def format_feedback(site_url: str, page_url: str, message: str) -> str:
    return (
        f"📩 Новое сообщение от пользователя\n"
        f"Сайт: {_esc(site_url)}\n"
        f"Страница: {_esc(page_url)}\n"
        f"Сообщение: {_esc(message)}"
    )


def format_uptime(url: str, stats: dict) -> str:
    return (
        f"📈 Uptime: {_esc(url)}\n"
        f"Доступность: {stats['uptime_pct']}%\n"
        f"Проверок: {stats['total_checks']}\n"
        f"Среднее время ответа: {stats['avg_response_ms']}ms"
    )


def format_seo_alert(result: dict) -> str:
    problems = result.get("problems") or []
    if not problems:
        return ""
    has_critical = any(p["severity"] == "critical" for p in problems)
    icon = "🔴" if has_critical else "⚠️"
    lines = [f"{icon} SEO/GEO: {_esc(_short_host(result['url']))} — "
             f"проблем: {len(problems)}"]
    for p in problems[:8]:
        sev = "🔴" if p["severity"] == "critical" else "⚠️"
        lines.append(f"  {sev} {_esc(p['message'])}")
    if len(problems) > 8:
        lines.append(f"  … и ещё {len(problems) - 8}")
    return "\n".join(lines)
