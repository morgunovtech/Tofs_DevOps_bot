"""Telegram message formatting for reports and alerts, in the language of
someone who does not know what a 502 is. Every alert answers, in order:
what happened, what it means for visitors, what it usually is, what to
do. Technical details go last, in italics."""

from datetime import UTC, datetime

from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainResult,
    LinksResult,
    SeoResult,
    SslResult,
)
from services import humanize
from services.humanize import describe_error, explain, fmt_seconds, steps_block
from utils.clock import now_local, to_local
from utils.text import esc, fmt_duration, parse_sqlite_utc
from utils.urls import host_of, is_http_url, registrable_domain, site_label


def _tech(*parts) -> str:
    """Grey technical footer: '<i>HTTP 502 · 0.81 с</i>'."""
    bits = [str(p) for p in parts if p not in (None, "", "—")]
    return f"\n<i>{esc(' · '.join(bits))}</i>" if bits else ""


# ── Availability ─────────────────────────────────────────────────────────────

def format_availability_alert(r: AvailabilityResult, second_opinion: bool = True) -> str:
    if r.ok:
        return ""
    label = esc(site_label(r.url))
    expl = explain("availability", r.error)
    if r.keyword_failed:
        header = f"🔴 {label} открывается, но показывает не то"
        what = esc(describe_error(r.error))
        situation = f"{expl.meaning} Сейчас {what}."
    else:
        noun = "не открывается" if is_http_url(r.url) else "не отвечает"
        header = f"🔴 {label} {noun}: {esc(describe_error(r.error))}"
        situation = expl.meaning
        if r.external_ok is False:
            situation += " Проверил дважды подряд и с внешних серверов: не открывается ни у кого."
        elif second_opinion:
            situation += " Проверил дважды подряд; внешнюю проверку сделать не удалось."
        else:
            situation += " Проверил дважды подряд."
    return (f"{header}\n{situation}\n\n"
            f"Что это обычно значит: {esc(expl.cause)}.\n\n"
            f"{esc(steps_block(expl))}"
            + _tech(r.error, fmt_seconds(r.response_time_ms) if r.response_time_ms else None))


def incident_duration_line(incident: dict | None) -> str | None:
    """'12 мин (14:03–14:15)' in the user's timezone."""
    if not incident:
        return None
    started = parse_sqlite_utc(incident.get("created_at"))
    if not started:
        return None
    ended = datetime.now(UTC)
    minutes = max(1, round((ended - started).total_seconds() / 60))
    fmt = "%H:%M" if minutes < 24 * 60 else "%d.%m %H:%M"
    return f"{fmt_duration(minutes)} ({to_local(started).strftime(fmt)}–{to_local(ended).strftime(fmt)})"


def format_recovery_alert(r: AvailabilityResult) -> str:
    label = esc(site_label(r.url))
    verb = "снова открывается" if is_http_url(r.url) else "снова отвечает"
    lines = [f"✅ {label} {verb}"]
    duration = incident_duration_line(r.resolved_incident)
    cause = describe_error((r.resolved_incident or {}).get("message"))
    if duration:
        lines.append(f"Лежал {duration}" + (f", {esc(cause)}." if cause and cause != "неизвестная ошибка" else "."))
    lines.append("Ничего делать не нужно.")
    return "\n".join(lines) + _tech(f"HTTP {r.status_code}" if r.status_code else None,
                                    fmt_seconds(r.response_time_ms) if r.response_time_ms else None)


# ── Certificate / domain ─────────────────────────────────────────────────────

def format_ssl_alert(r: SslResult) -> str:
    if r.ok or not r.error:
        return ""
    label = esc(site_label(r.url))
    expl = explain("ssl", r.error)
    icon = "🔴" if r.severity == "critical" else "⚠️"
    what = esc(describe_error(r.error))
    header = f"{icon} {label}: {what}"
    cause = expl.cause
    if r.renewal_note:
        cause = "автопродление не сработало — сертификат не менялся, хотя срок уже близко"
    return (f"{header}\n{expl.meaning}\n\nЧто это обычно значит: {esc(cause)}.\n\n{esc(steps_block(expl))}"
            + _tech(r.error, f"выдан {r.ssl_info.issuer}" if r.ssl_info and r.ssl_info.issuer else None))


def format_domain_alert(r: DomainResult) -> str:
    if r.ok or not r.error:
        return ""
    label = esc(r.domain or site_label(r.url))
    expl = explain("domain", r.error)
    icon = "🔴" if r.severity == "critical" else "⚠️"
    registrar = r.domain_info.registrar if r.domain_info and r.domain_info.registrar not in ("", "Unknown") else ""
    steps = list(expl.steps)
    if registrar:
        steps[0] = steps[0].replace("у регистратора", f"у регистратора ({registrar})")
        steps[0] = steps[0].replace("в панели регистратора", f"в панели регистратора ({registrar})")
    return (f"{icon} Домен {label}: {esc(describe_error(r.error))}\n{expl.meaning}\n\n"
            f"Что это обычно значит: {esc(expl.cause)}.\n\n"
            + esc("Что делать:\n" + "\n".join(f"• {s}" for s in steps))
            + _tech(r.error, f"регистратор {registrar}" if registrar else None))


# ── Links / deep / SEO / DNS ─────────────────────────────────────────────────

def format_links_report(r: LinksResult) -> str:
    internal, external = r.broken_internal, r.broken_external
    if not internal and not external:
        return ""
    label = esc(site_label(r.url))
    lines: list[str] = []
    if internal:
        expl = explain("links", "")
        n = len(internal)
        lines.append(f"⚠️ На {label} {n} {'ссылка ведёт' if n % 10 == 1 and n % 100 != 11 else 'ссылок ведут'} в никуда")
        lines.append(expl.meaning)
        lines += [f"  • {esc(b.url)} — {esc(describe_error(b.reason))}" for b in internal[:10]]
        if n > 10:
            lines.append(f"  … и ещё {n - 10}")
        lines.append("")
        lines.append(esc(steps_block(expl)))
        if external:
            lines.append(f"\nℹ️ Ещё {len(external)} ссылок на чужие сайты не открываются — это обычно не твоя проблема.")
    else:
        lines.append(f"ℹ️ На {label} {len(external)} ссылок на чужие сайты не открываются. "
                     "Чужие домены — обычно ничего делать не нужно.")
    return "\n".join(lines)


def format_deep_alert(r) -> str:
    expl = explain("deep", r.error)
    errors = "\n".join(f"  • {esc(u)} — {esc(describe_error(f'HTTP {code}'))}" for u, code in r.errors[:5])
    return (f"⚠️ {esc(site_label(r.url))}: {esc(describe_error(r.error))}\n{expl.meaning}\n{errors}\n\n"
            f"{esc(steps_block(expl))}")


def format_seo_alert(r: SeoResult) -> str:
    """Only critical findings are alerted; the rest lives in the audit screen."""
    critical = [p for p in r.problems if p.severity == "critical"]
    if not critical:
        return ""
    expl = explain("seo", "")
    lines = [f"🔴 {esc(site_label(r.url))} исчезает из поиска", expl.meaning, ""]
    lines += [f"  • {esc(p.message)}" for p in critical[:6]]
    lines += ["", f"Что это обычно значит: {esc(expl.cause)}.", "", esc(steps_block(expl))]
    return "\n".join(lines)


def format_dns_change(change: DnsChange) -> str:
    expl = humanize.EXPLANATIONS["ns_change" if change["critical"] else "dns_change"]
    kinds = {"A": "адрес сервера (запись A)", "AAAA": "адрес сервера IPv6 (запись AAAA)",
             "CNAME": "псевдоним адреса (запись CNAME)", "NS": "NS-серверы домена",
             "MX": "почтовые серверы (запись MX)"}
    icon = "🔴" if change["critical"] else "⚠️"
    what = kinds.get(change["rtype"], change["rtype"])
    return (f"{icon} У {esc(change['host'])} изменились {what}\n{expl.meaning}\n\n"
            f"Было: {esc(change['old'])}\nСтало: {esc(change['new'])}\n\n"
            f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(steps_block(expl))}")


# ── Status report (one line per site) ────────────────────────────────────────

def _site_line(r: AvailabilityResult, ssl: SslResult | None, dom: DomainResult | None) -> tuple[str, bool]:
    label = esc(site_label(r.url))
    if not r.ok:
        return f"🔴 {label} — {esc(describe_error(r.error))}", False
    warnings: list[str] = []
    if ssl is not None:
        if not ssl.ssl_info:
            warnings.append("сертификат не удалось проверить")
        elif ssl.ssl_info.days_left < 0:
            warnings.append(f"сертификат истёк {abs(ssl.ssl_info.days_left)} дн. назад")
        elif ssl.ssl_info.days_left <= 14:
            warnings.append(f"сертификат истекает через {ssl.ssl_info.days_left} дн.")
    if dom is not None and not dom.unsupported:
        if not dom.domain_info or dom.domain_info.days_left is None:
            warnings.append("срок домена не удалось проверить")
        elif dom.domain_info.days_left < 0:
            warnings.append(f"домен истёк {abs(dom.domain_info.days_left)} дн. назад")
        elif dom.domain_info.days_left <= 30:
            warnings.append(f"домен истекает через {dom.domain_info.days_left} дн.")
    if warnings:
        return f"⚠️ {label} — открывается, но {'; '.join(warnings)}", False
    return f"✅ {label} — в порядке, {humanize.speed(r.response_time_ms)}", True


def format_compact_status_report(availability: list[AvailabilityResult],
                                 incidents: list[dict],
                                 ssl_results: list[SslResult] | None = None,
                                 domain_results: list[DomainResult] | None = None,
                                 report_type: str = "status",
                                 extras: list[str] | None = None) -> str:
    now = now_local().strftime("%d.%m %H:%M")
    header = {"morning": f"🌅 Доброе утро · {now}",
              "evening": f"🌙 Вечер · {now}"}.get(report_type, f"🔎 Проверка · {now}")
    ssl_by_url = {r.url: r for r in (ssl_results or [])}
    domain_by_root = {registrable_domain(host_of(r.url)): r for r in (domain_results or [])}
    lines: list[str] = [header, ""]
    all_ok = True
    for r in availability:
        http = is_http_url(r.url)
        line, ok = _site_line(
            r, ssl_by_url.get(r.url) if (ssl_results is not None and http) else None,
            domain_by_root.get(registrable_domain(host_of(r.url))) if (domain_results is not None and http) else None)
        lines.append(line)
        all_ok &= ok
    if incidents:
        lines += ["", f"🔴 Открытых проблем: {len(incidents)}"]
        lines += [f"  • {esc(incident_line(inc))}" for inc in incidents[:5]]
        if len(incidents) > 5:
            lines.append(f"  … и ещё {len(incidents) - 5}")
    elif all_ok:
        lines += ["", "Всё спокойно 🐕"]
    if extras:
        lines += ["", *extras]
    return "\n".join(lines)


def incident_line(inc: dict) -> str:
    """'example.com не открывается: хостинг отвечает ошибкой 502'."""
    return humanize.incident_headline(inc["check_type"], inc.get("message"), site_label(inc["url"]))


def format_feedback(site_url: str, page_url: str, message: str,
                    feedback_id: int, ip_address: str) -> str:
    return (f"📩 Сообщение с сайта\n"
            f"Сайт: {esc(site_url)}\nСтраница: {esc(page_url)}\n"
            f"Сообщение: {esc(message)}\n\n"
            f"🆔 #{feedback_id} · 🌍 {esc(ip_address)}")
