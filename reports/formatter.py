from datetime import datetime


def format_status_report(availability: list[dict], incidents: list[dict],
                         ssl_results: list[dict] | None = None,
                         domain_results: list[dict] | None = None,
                         report_type: str = "status") -> str:
    """Format a comprehensive status report for Telegram (MarkdownV2-safe plain text)."""
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = []

    if report_type == "morning":
        lines.append(f"🌅 Утренний отчёт — {now}\n")
    elif report_type == "evening":
        lines.append(f"🌙 Вечерний отчёт — {now}\n")
    else:
        lines.append(f"📊 Статус сайтов — {now}\n")

    # Availability
    lines.append("━━━ Доступность ━━━")
    for r in availability:
        icon = "✅" if r["status"] == "ok" else "🔴"
        time_str = f" ({r['response_time_ms']}ms)" if r.get("response_time_ms") else ""
        code_str = f" [{r['status_code']}]" if r.get("status_code") else ""
        error_str = f"\n   ⚠️ {r['error']}" if r.get("error") else ""
        lines.append(f"{icon} {r['url']}{code_str}{time_str}{error_str}")

    # SSL
    if ssl_results:
        lines.append("\n━━━ SSL-сертификаты ━━━")
        for r in ssl_results:
            if r.get("ssl_info"):
                days = r["ssl_info"]["days_left"]
                if days > 14:
                    icon = "🔒"
                elif days > 3:
                    icon = "⚠️"
                else:
                    icon = "🔴"
                lines.append(f"{icon} {r['url']} — {days} дн.")
            else:
                lines.append(f"🔴 {r['url']} — {r.get('error', 'N/A')}")

    # Domain
    if domain_results:
        lines.append("\n━━━ Домены ━━━")
        for r in domain_results:
            if r.get("domain_info") and r["domain_info"]["days_left"] is not None:
                days = r["domain_info"]["days_left"]
                if days > 30:
                    icon = "🌐"
                elif days > 7:
                    icon = "⚠️"
                else:
                    icon = "🔴"
                lines.append(f"{icon} {r['domain']} — {days} дн. (до {r['domain_info']['expiration_date'][:10]})")
            else:
                lines.append(f"⚠️ {r.get('domain', r['url'])} — {r.get('error', 'N/A')}")

    # Active incidents
    if incidents:
        lines.append("\n━━━ Активные проблемы ━━━")
        for inc in incidents:
            sev_icon = "🔴" if inc["severity"] == "critical" else "⚠️"
            lines.append(f"{sev_icon} [{inc['check_type']}] {inc['url']}: {inc['message']}")
    else:
        lines.append("\n✅ Активных проблем нет")

    return "\n".join(lines)


def format_availability_alert(result: dict) -> str:
    """Format an alert for a single site."""
    if result["status"] == "error":
        return (
            f"🚨 САЙТ НЕДОСТУПЕН\n"
            f"URL: {result['url']}\n"
            f"Ошибка: {result.get('error', 'Unknown')}\n"
            f"Код: {result.get('status_code', 'N/A')}\n"
            f"Время ответа: {result.get('response_time_ms', 'N/A')}ms"
        )
    return ""


def format_recovery_alert(result: dict) -> str:
    """Format a recovery notification."""
    return (
        f"✅ САЙТ ВОССТАНОВЛЕН\n"
        f"URL: {result['url']}\n"
        f"Код: {result.get('status_code', 'N/A')}\n"
        f"Время ответа: {result.get('response_time_ms', 'N/A')}ms"
    )


def format_ssl_alert(result: dict) -> str:
    if result["status"] in ("error", "critical", "warning"):
        icon = "🔴" if result["status"] in ("error", "critical") else "⚠️"
        return f"{icon} SSL: {result['url']}\n{result['error']}"
    return ""


def format_domain_alert(result: dict) -> str:
    if result["status"] in ("error", "critical", "warning") and result.get("error"):
        icon = "🔴" if result["status"] in ("error", "critical") else "⚠️"
        return f"{icon} Домен: {result.get('domain', result['url'])}\n{result['error']}"
    return ""


def format_links_report(result: dict) -> str:
    if result.get("broken_links"):
        broken = result["broken_links"]
        lines = [f"🔗 Битые ссылки на {result['url']} ({len(broken)} шт.):"]
        for b in broken[:10]:
            code = b.get("status_code") or b.get("error", "N/A")
            lines.append(f"  • {b['url']} — {code}")
        if len(broken) > 10:
            lines.append(f"  ... и ещё {len(broken) - 10}")
        return "\n".join(lines)
    return ""


def format_feedback(site_url: str, page_url: str, message: str) -> str:
    return (
        f"📩 Новое сообщение от пользователя\n"
        f"Сайт: {site_url}\n"
        f"Страница: {page_url}\n"
        f"Сообщение: {message}"
    )


def format_uptime(url: str, stats: dict) -> str:
    return (
        f"📈 Uptime: {url}\n"
        f"Доступность: {stats['uptime_pct']}%\n"
        f"Проверок: {stats['total_checks']}\n"
        f"Среднее время ответа: {stats['avg_response_ms']}ms"
    )
