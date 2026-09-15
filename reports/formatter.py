"""Telegram message formatting for reports and single-event alerts.
Everything user-controlled or site-controlled goes through esc()."""

from datetime import UTC, datetime

from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainResult,
    LinksResult,
    SeoResult,
    SslResult,
)
from utils.clock import now_local, to_local
from utils.text import esc, fmt_duration, parse_sqlite_utc
from utils.urls import host_of, is_http_url, registrable_domain, short_host, site_label

# ── One line per site (compact) ──────────────────────────────────────────────

def _avail_chip(r: AvailabilityResult) -> str:
    if r.ok:
        return f"{r.response_time_ms}ms" if r.response_time_ms else "ok"
    return f"❌ {esc(r.error or 'down')}"


def _ssl_chip(r: SslResult | None) -> str:
    """Empty while healthy — the compact line stays short on mobile."""
    if r is None:
        return ""
    if not r.ssl_info:
        return "SSL ?"
    days = r.ssl_info.days_left
    if days < 0:
        return f"SSL ⛔ ({abs(days)}д назад)"
    return f"SSL ⚠ {days}д" if days <= 14 else ""


def _domain_chip(r: DomainResult | None) -> str:
    if r is None or r.unsupported:
        return ""
    if not r.domain_info or r.domain_info.days_left is None:
        return "домен ?"
    days = r.domain_info.days_left
    if days < 0:
        return f"домен ⛔ ({abs(days)}д назад)"
    return f"домен ⚠ {days}д" if days <= 30 else ""


def format_compact_status_report(availability: list[AvailabilityResult],
                                 incidents: list[dict],
                                 ssl_results: list[SslResult] | None = None,
                                 domain_results: list[DomainResult] | None = None,
                                 report_type: str = "status",
                                 extras: list[str] | None = None) -> str:
    """One concise message: header + one line per site + incidents (if any)."""
    now = now_local().strftime("%d.%m %H:%M")
    header = {"morning": f"🌅 Доброе утро · {now}",
              "evening": f"🌙 Вечер · {now}"}.get(report_type, f"📊 Статус · {now}")
    ssl_by_url = {r.url: r for r in (ssl_results or [])}
    # Domain results are deduped by registrable domain — index by it.
    domain_by_root = {registrable_domain(host_of(r.url)): r for r in (domain_results or [])}

    lines: list[str] = [header, ""]
    any_problem = False
    for r in availability:
        http = is_http_url(r.url)
        ssl = _ssl_chip(ssl_by_url.get(r.url)) if (ssl_results is not None and http) else ""
        dom = (_domain_chip(domain_by_root.get(registrable_domain(host_of(r.url))))
               if (domain_results is not None and http) else "")
        is_ok = r.ok and not any(m in ssl + dom for m in ("⛔", "⚠", "?"))
        any_problem |= not is_ok
        chips = " · ".join(c for c in (_avail_chip(r), ssl, dom) if c)
        lines.append(f"{'✅' if is_ok else '⚠️'} {esc(site_label(r.url))} — {chips}")

    if incidents:
        lines += ["", f"⚠️ Активных проблем: {len(incidents)}"]
        for inc in incidents[:5]:
            sev = "🔴" if inc["severity"] == "critical" else "⚠️"
            lines.append(f"  {sev} {esc(site_label(inc['url']))} "
                         f"[{esc(inc['check_type'])}]: {esc(inc['message'])}")
        if len(incidents) > 5:
            lines.append(f"  … и ещё {len(incidents) - 5}")
    elif not any_problem:
        lines += ["", "Всё спокойно 🐕"]
    if extras:
        lines += ["", *extras]
    return "\n".join(lines)


# ── Single-event alerts ──────────────────────────────────────────────────────

def format_availability_alert(r: AvailabilityResult) -> str:
    if r.ok:
        return ""
    if r.keyword_failed:
        # The site answers fine — it's the CONTENT that's wrong.
        header = "🚨 САЙТ ОТВЕЧАЕТ, НО СОДЕРЖИМОЕ НЕ В ПОРЯДКЕ"
    else:
        header = f"🚨 {'САЙТ' if is_http_url(r.url) else 'СЕРВИС'} НЕДОСТУПЕН"
    lines = [header, esc(r.url), f"Ошибка: {esc(r.error or 'Unknown')}"]
    if r.status_code is not None:  # tcp/ping have no HTTP status
        lines.append(f"Код: {r.status_code}")
    lines.append(f"Время ответа: {r.response_time_ms if r.response_time_ms is not None else '—'}ms")
    return "\n".join(lines)


def incident_duration_line(incident: dict | None) -> str | None:
    """'Длительность: 12 мин (14:03–14:15)' in the user's timezone."""
    if not incident:
        return None
    started = parse_sqlite_utc(incident.get("created_at"))
    if not started:
        return None
    ended = datetime.now(UTC)
    minutes = max(1, round((ended - started).total_seconds() / 60))
    fmt = "%H:%M" if minutes < 24 * 60 else "%d.%m %H:%M"
    window = f"{to_local(started).strftime(fmt)}–{to_local(ended).strftime(fmt)}"
    return f"Длительность: {fmt_duration(minutes)} ({window})"


def format_recovery_alert(r: AvailabilityResult) -> str:
    lines = [f"✅ {'САЙТ' if is_http_url(r.url) else 'СЕРВИС'} ВОССТАНОВЛЕН", esc(r.url)]
    if r.status_code is not None:
        lines.append(f"Код: {r.status_code}")
    lines.append(f"Время ответа: {r.response_time_ms if r.response_time_ms is not None else '—'}ms")
    duration = incident_duration_line(r.resolved_incident)
    if duration:
        lines.append(duration)
    if r.resolved_incident and r.resolved_incident.get("message"):
        lines.append(f"Причина: {esc(r.resolved_incident['message'])}")
    return "\n".join(lines)


def format_dns_change(change: DnsChange) -> str:
    icon = "🚨" if change["critical"] else "⚠️"
    header = ("DNS: СМЕНИЛИСЬ NS-ЗАПИСИ (проверь, не угнали ли домен!)"
              if change["critical"] else "DNS-запись изменилась")
    return (f"{icon} {header}\n{esc(change['rtype'])} {esc(change['host'])}\n"
            f"Было: {esc(change['old'])}\nСтало: {esc(change['new'])}")


def format_ssl_alert(r: SslResult) -> str:
    if r.ok or not r.error:
        return ""
    icon = "🔴" if r.severity == "critical" else "⚠️"
    text = f"{icon} SSL: {esc(r.url)}\n{esc(r.error)}"
    if r.renewal_note:
        text += f"\n🔁 {esc(r.renewal_note)}"
    return text


def format_domain_alert(r: DomainResult) -> str:
    if r.ok or not r.error:
        return ""
    icon = "🔴" if r.severity == "critical" else "⚠️"
    return f"{icon} Домен: {esc(r.domain or r.url)}\n{esc(r.error)}"


def format_links_report(r: LinksResult) -> str:
    internal, external = r.broken_internal, r.broken_external
    if not internal and not external:
        return ""
    lines: list[str] = []
    if internal:
        lines.append(f"🔗 Битые внутренние ссылки на {esc(short_host(r.url))} ({len(internal)} шт.):")
        lines += [f"  • {esc(b.url)} — {esc(b.reason)}" for b in internal[:10]]
        if len(internal) > 10:
            lines.append(f"  … и ещё {len(internal) - 10}")
        if external:
            lines += ["", f"ℹ️ Также недоступно внешних ресурсов: {len(external)} "
                          "(чужие домены — обычно не критично)"]
    else:
        lines.append(f"ℹ️ Внешних ресурсов недоступно: {len(external)} "
                     "(чужие домены — обычно ничего делать не нужно)")
    return "\n".join(lines)


def format_seo_alert(r: SeoResult) -> str:
    if not r.problems:
        return ""
    icon = "🔴" if r.has_critical else "⚠️"
    lines = [f"{icon} SEO/GEO: {esc(short_host(r.url))} — проблем: {len(r.problems)}"]
    lines += [f"  {'🔴' if p.severity == 'critical' else '⚠️'} {esc(p.message)}"
              for p in r.problems[:8]]
    if len(r.problems) > 8:
        lines.append(f"  … и ещё {len(r.problems) - 8}")
    return "\n".join(lines)


def format_feedback(site_url: str, page_url: str, message: str,
                    feedback_id: int, ip_address: str) -> str:
    return (f"📩 Сообщение с сайта\n"
            f"Сайт: {esc(site_url)}\nСтраница: {esc(page_url)}\n"
            f"Сообщение: {esc(message)}\n\n"
            f"🆔 #{feedback_id} · 🌍 {esc(ip_address)}")
