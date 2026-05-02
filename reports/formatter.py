import html
from datetime import datetime
from urllib.parse import urlparse


def _esc(value) -> str:
    """HTML-escape a value for safe rendering in ParseMode.HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def _short_host(url: str) -> str:
    return urlparse(url).hostname or url


# ── One-line per site (compact) ──────────────────────────────────────────────

def _avail_chip(r: dict) -> str:
    if r.get("status") == "ok":
        ms = r.get("response_time_ms")
        return f"{ms}ms" if ms else "ok"
    return f"❌ {_esc(r.get('error', 'down'))}"


def _ssl_chip(r: dict) -> str:
    info = r.get("ssl_info")
    if not info:
        return "SSL ?"
    days = info["days_left"]
    if days < 0:
        return f"SSL ⛔ ({abs(days)}д назад)"
    if days <= 7:
        return f"SSL ⚠ {days}д"
    if days <= 14:
        return f"SSL ⚠ {days}д"
    return f"SSL {days}д"


def _domain_chip(r: dict) -> str:
    info = r.get("domain_info")
    if not info or info.get("days_left") is None:
        return "домен ?"
    days = info["days_left"]
    if days < 0:
        return f"домен ⛔ ({abs(days)}д назад)"
    if days <= 30:
        return f"домен ⚠ {days}д"
    return f"домен {days}д"


def format_compact_status_report(availability: list[dict],
                                 incidents: list[dict],
                                 ssl_results: list[dict] | None = None,
                                 domain_results: list[dict] | None = None,
                                 report_type: str = "status") -> str:
    """One concise message: header + one line per site + incidents (if any)."""
    now = datetime.now().strftime("%d.%m %H:%M")
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
        lines.append("Всё работает 👌")

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


def format_recovery_alert(result: dict) -> str:
    return (
        f"✅ САЙТ ВОССТАНОВЛЕН\n"
        f"{_esc(result['url'])}\n"
        f"Код: {_esc(result.get('status_code', 'N/A'))}\n"
        f"Время ответа: {_esc(result.get('response_time_ms', 'N/A'))}ms"
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
            f"🔗 Битые внутренние ссылки на {_esc(result['url'])} "
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
