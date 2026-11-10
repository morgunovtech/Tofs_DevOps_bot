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
from monitors.dns_checker import FAILS_TO_SAY
from monitors.mail_checker import MailResult
from monitors.region import region_words
from monitors.security_checker import SecurityResult
from monitors.visitor_checker import VisitorsResult
from services import crux, humanize, seo_fixes
from services.humanize import (
    UNCHECKED,
    describe_error,
    explain,
    fmt_seconds,
    level_icon,
    link_reason,
    steps_block,
    worst_severity,
)
from utils.clock import now_local, to_local
from utils.text import esc, fmt_duration, parse_sqlite_utc, plural
from utils.urls import host_of, is_http_url, is_web_page, registrable_domain, site_label


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
        header = f"🔴 {label} {'отвечает' if r.api else 'открывается'}, но показывает не то"
        what = esc(describe_error(r.error))
        situation = f"{expl.meaning} Сейчас {what}."
    else:
        noun = "не открывается" if is_http_url(r.url) and not r.api else "не отвечает"
        header = f"🔴 {label} {noun}: {esc(describe_error(r.error))}"
        situation = expl.meaning
        if r.cert_failed:
            situation += (" Проверил дважды подряд: сервер отвечает, но его сертификат не принимается — "
                          "так его видят и посетители.")
        elif r.external_ok is False:
            situation += " Проверил дважды подряд и с внешних серверов: не открывается ни у кого."
        elif r.external_region and r.external_ups:
            situation += (f" Проверил дважды подряд; снаружи открывается (с {r.external_ups} из "
                          f"{r.external_total} серверов), но не {region_words(r.external_region)} — "
                          f"посетители оттуда его тоже не видят.")
        elif r.external_region:
            situation += (f" Проверил дважды подряд; {region_words(r.external_region)} тоже не открывается, "
                          f"остальные внешние серверы не успели ответить.")
        elif r.external_ups and r.external_total >= 2:
            situation += (f" Проверил дважды подряд; снаружи открывается только с {r.external_ups} из "
                          f"{r.external_total} серверов — похоже, сайт лежит не везде.")
        elif second_opinion:
            situation += " Проверил дважды подряд; снаружи проверить не удалось — возможно, проблема на моей стороне."
        else:
            situation += " Проверил дважды подряд."
    # A phrase verdict is about a site that opens: «поднимется» would send
    # the owner to the server instead of the page.
    back = (f"когда {'в ответе' if r.api else 'на странице'} снова будет как надо" if r.keyword_failed
            else "когда поднимется")
    return (f"{header}\n{situation}\nПродолжаю проверять каждую минуту и напишу, {back}."
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
    message = (r.resolved_incident or {}).get("message") or ""
    content = message.startswith("Bad content: ")    # the site opened all along; its text was wrong
    if content:
        lines = [f"✅ На {label} снова всё как надо"]
    else:
        verb = "снова открывается" if is_http_url(r.url) and not r.api else "снова отвечает"
        lines = [f"✅ {label} {verb}"]
    duration = incident_duration_line(r.resolved_incident)
    cause = describe_error(message)
    if duration:
        lines.append(f"{'Было не так' if content else 'Лежал'} {duration}"
                     + (f", {esc(cause)}." if cause and cause != "неизвестная ошибка" else "."))
    lines.append("Ничего делать не нужно.")
    return "\n".join(lines) + _tech(f"HTTP {r.status_code}" if r.status_code else None,
                                    fmt_seconds(r.response_time_ms) if r.response_time_ms else None)


# ── Certificate / domain ─────────────────────────────────────────────────────

def format_ssl_alert(r: SslResult) -> str:
    if r.ok or not r.error:
        return ""
    label = esc(site_label(r.url))
    expl = explain("ssl", r.error)
    icon = level_icon(r.severity)
    what = esc(describe_error(r.error))
    header = f"{icon} {label}: {what}"
    note = " Автопродление не сработало: сертификат не менялся, хотя срок уже близко." if r.renewal_note else ""
    return (f"{header}\n{expl.meaning}{note}"
            + _tech(r.error, f"выдан {r.ssl_info.issuer}" if r.ssl_info and r.ssl_info.issuer else None))


def format_domain_alert(r: DomainResult) -> str:
    if r.ok or not r.error:
        return ""
    label = esc(r.domain or site_label(r.url))
    expl = explain("domain", r.error)
    icon = level_icon(r.severity)
    registrar = r.domain_info.registrar if r.domain_info and r.domain_info.registrar not in ("", "Unknown") else ""
    where = f" Продлевается у регистратора: {esc(registrar)}." if registrar else ""
    return (f"{icon} Домен {label}: {esc(describe_error(r.error))}\n{expl.meaning}{where}"
            + _tech(r.error))


# ── Links / deep / SEO / DNS ─────────────────────────────────────────────────

LINK_FIX = ("Что сделать: если страница переехала — поставь новый адрес; пропала — убери ссылку "
            "или дай копию из web.archive.org; закрытый репозиторий — открой его или убери ссылку.")


def link_lines(links: list, limit: int = 10) -> list[str]:
    """«• url — why» for each link, own site or not alike."""
    lines = [f"• {esc(b.url)} — {esc(link_reason(b.status_code, b.error, b.url))}" for b in links[:limit]]
    if len(links) > limit:
        lines.append(f"… и ещё {len(links) - limit}")
    return lines


def format_links_report(r: LinksResult) -> str:
    """The alert and the «🔗 Ссылки» screen: every link that does not open,
    wherever it points — a dead end for the visitor is the owner's to fix."""
    if not r.broken:
        return ""
    n = len(r.broken)
    lines = [f"🟠 На {esc(site_label(r.url))} {n} {plural(n, 'ссылка не открывается', 'ссылки не открываются', 'ссылок не открываются')}",
             explain("links", "").meaning, *link_lines(r.broken), LINK_FIX]
    return "\n".join(lines)


def pending_note(pending: list) -> str:
    n = len(pending)
    return (f"⚠️ Ещё {n} {plural(n, 'ссылка', 'ссылки', 'ссылок')} не {plural(n, 'открылась', 'открылись', 'открылись')} "
            f"с первого раза (таймаут или ошибка сервера) — перепроверю при следующем обходе и скажу, "
            f"если так и останется.")


def _deep_page_error(code: int) -> str:
    """A 5xx by its words; code 0 is a page that did not answer at all."""
    return describe_error(f"HTTP {code}") if code else "не ответила за 15 секунд"


def format_deep_alert(r) -> str:
    expl = explain("deep", r.error)
    errors = "\n".join(f"  • {esc(u)} — {esc(_deep_page_error(code))}" for u, code in r.errors[:5])
    return f"🟠 {esc(site_label(r.url))}: {esc(describe_error(r.error))}\n{expl.meaning}\n{errors}"


def format_seo_alert(r: SeoResult) -> str:
    """Only critical findings are alerted; the rest lives in the audit screen.
    The keyboard (a «💡» per finding) is built by the scheduler."""
    critical = [p for p in r.problems if p.severity == "critical"]
    if not critical:
        return ""
    lines = [f"🔴 {esc(site_label(r.url))} исчезает из поиска",
             "Google и Яндекс уберут сайт при следующем обходе — переходы из поиска пропадут.", ""]
    seen: set[str] = set()
    for p in critical[:6]:
        f = seo_fixes.fix(p.code)
        head = f"  • <b>{esc(f.title)}</b>" if p.code not in seen else "  •"
        seen.add(p.code)
        lines.append(f"{head} — {esc(p.message)}")
    lines += ["", "Нажми кнопку под сообщением: там шаги для твоего хостинга."]
    return "\n".join(lines)


def format_dns_failing(change: DnsChange) -> str:
    """The name stopped answering FAILS_TO_SAY hourly checks in a row — the
    other names answer, so it is not the bot's network."""
    n = FAILS_TO_SAY
    icon = level_icon("critical" if change["critical"] else "warning")
    rtype = change["rtype"]
    hint = ("Так бывает, когда перестали работать NS-серверы домена (не оплачен хостинг DNS, "
            "удалили зону) или после смены NS не обновили DNSSEC (запись DS у регистратора): "
            "браузеры тоже не находят сайт." if rtype == "NS" else
            "Так бывает, когда у DNS-серверов домена сбой или сломана зона: посетители могут не найти сайт.")
    steps = ("1. Открыть сайт в браузере: если не открывается — виноват DNS, а не хостинг.\n"
             "2. Проверить домен на dnschecker.org: отвечают ли его NS-серверы.\n"
             "3. Если DNSSEC включён у регистратора, а зона переехала — снять или обновить запись DS.")
    return (f"{icon} {esc(change['host'])}: DNS не отвечает на запрос {esc(rtype)} уже "
            f"{n} {plural(n, 'проверку', 'проверки', 'проверок')} подряд ({n} ч). "
            f"Другие имена при этом отвечают — дело не в моей сети.\n{hint}\n\n"
            f"Последний известный ответ: {esc(change['old'])}\n\n{steps}")


def format_dns_change(change: DnsChange) -> str:
    if change.get("failing"):
        return format_dns_failing(change)
    key = {"NS": "ns_change", "MX": "mx_gone" if change["new"] == "—" else "mx_change"}.get(change["rtype"],
                                                                                         "dns_change")
    expl = humanize.EXPLANATIONS[key]
    kinds = {"A": "адрес сервера (запись A)", "AAAA": "адрес сервера IPv6 (запись AAAA)",
             "CNAME": "псевдоним адреса (запись CNAME)", "NS": "NS-серверы домена",
             "MX": "почтовые серверы (запись MX)"}
    icon = level_icon("critical" if change["critical"] else "warning")
    what = kinds.get(change["rtype"], change["rtype"])
    return (f"{icon} У {esc(change['host'])} изменились {what}\n{expl.meaning}\n\n"
            f"Было: {esc(change['old'])}\nСтало: {esc(change['new'])}\n\n"
            f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(steps_block(expl))}")


# ── Mail ─────────────────────────────────────────────────────────────────────

def finding_block(n: int, f: dict) -> str:
    """One finding (monitors.base.Finding): what, why, what to do, the
    record to paste — a block of lines as <pre>, one line as <code>."""
    lines = [f"{n}. <b>{esc(f['title'])}</b> — {esc(f['why'])}.", esc(f["fix"])]
    record = f.get("record")
    if record:
        lines.append(f"<pre>{esc(record)}</pre>" if "\n" in record else f"<code>{esc(record)}</code>")
    if f.get("note"):
        lines.append(esc(f["note"]))
    return "\n".join(lines)


def format_mail_alert(r: MailResult) -> str:
    domain = esc(r.domain)
    if r.kind == "mail":
        if r.delivery.reason == "nowhere":
            what = ("Записи MX домена ведут на сервер, которого нет. Письма тебе не доходят — "
                    "отправители получают ошибку.")
        else:
            who = esc(r.provider) if r.provider else "Почтовый сервер"
            what = (f"{who} не отвечает уже две проверки подряд — проверял из нескольких стран. Письма тебе "
                    f"сейчас не доходят: серверы отправителей будут пробовать ещё несколько дней, потом вернут "
                    f"письмо с ошибкой.")
        steps = ([f"Открыть страницу статуса {esc(r.provider)} (кнопка ниже): если сбой у них — ждать, я "
                  f"напишу, когда почта заработает."] if r.status_page else [])
        steps.append("Если у почтового сервиса всё в порядке — проверить записи MX в DNS: они должны вести на "
                     "него, точные значения есть в его настройках.")
        return "\n".join([f"🔴 Почта на {domain} не принимает письма", what, "", "Что делать:",
                          *(f"{i}. {s}" for i, s in enumerate(steps, 1))])
    n = len(r.findings)
    head = (f"🟠 Почта на {domain} настроена не до конца — письма могут попадать в спам, а подделать "
            f"твой адрес проще")
    lines = [head, "Что исправить в DNS:" if n == 1 else
             f"{n} {plural(n, 'помеха', 'помехи', 'помех')}, у каждой — что исправить в DNS:", ""]
    lines += ["\n\n".join(finding_block(i, f) for i, f in enumerate(r.findings, 1)), "",
              "Изменения в DNS доходят за 5–30 минут. Я перепроверяю каждый час и напишу, когда всё будет "
              "в порядке."]
    return "\n".join(lines)


def format_mail_recovery(r: MailResult) -> str:
    domain = esc(r.domain)
    if r.kind == "mail_auth":
        return f"✅ Почта на {domain}: настройки в порядке, письмам ничего не мешает."
    started = parse_sqlite_utc((r.resolved_incident or {}).get("created_at"))
    how_long = ""
    if started:
        minutes = max(1, round((datetime.now(UTC) - started).total_seconds() / 60))
        how_long = f" (не принимала около {fmt_duration(minutes)})"
    return (f"✅ Почта на {domain} снова принимает письма{how_long}. Письма, которые слали в это время, "
            f"обычно доходят сами: серверы отправителей повторяют попытки.")


# ── Security ─────────────────────────────────────────────────────────────────

def format_security_alert(r: SecurityResult) -> str:
    label = esc(site_label(r.url))
    n = len(r.findings)
    if r.kind == "exposed":
        head = f"🔴 На {label} наружу видны служебные файлы — их может скачать любой"
        tail = "Убрал — нажми «🔄 Перепроверить» на карточке сайта, я скажу, что их больше не видно."
    else:
        head = f"🟠 {label}: защиту сайта стоит усилить"
        tail = "Я перепроверяю каждые 6 часов и напишу, когда всё будет в порядке."
    lines = [head, "Что сделать:" if n == 1 else f"{n} {plural(n, 'помеха', 'помехи', 'помех')}, у каждой — что сделать:",
             "", "\n\n".join(finding_block(i, f) for i, f in enumerate(r.findings, 1)), "", tail]
    return "\n".join(lines)


def format_security_recovery(r: SecurityResult) -> str:
    label = esc(site_label(r.url))
    if r.kind == "exposed":
        return (f"✅ На {label} служебные файлы больше не видны снаружи. Если в них были пароли или ключи — "
                f"смени их, если ещё не сменил.")
    return f"✅ {label}: защита сайта в порядке."


# ── Real visitors' speed ─────────────────────────────────────────────────────

def format_visitors_alert(r: VisitorsResult) -> str:
    expl = explain("visitors", r.error)
    return (f"🟠 {esc(site_label(r.url))}: медленно у настоящих посетителей\n"
            f"{esc(crux.describe(r.values))} — так у 3 из 4 посетителей, у остальных ещё дольше "
            f"({crux.SOURCE}).\n{esc(expl.meaning)}\n\n"
            f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(steps_block(expl))}")


def format_visitors_recovery(r: VisitorsResult) -> str:
    return f"✅ {esc(site_label(r.url))}: у посетителей снова не медленно — {esc(crux.describe(r.values))}."


# ── Status report (one line per site) ────────────────────────────────────────

def _site_line(r: AvailabilityResult, ssl: SslResult | None, dom: DomainResult | None) -> tuple[str, bool]:
    """One line per site with the worst level among its notes: 🔴 / 🟠 by
    the SSL and domain ladders, ⚠️ when something could not be checked."""
    label = esc(site_label(r.url))
    if r.status == "unknown":
        return f"{UNCHECKED} {label} — не могу проверить: {esc(describe_error(r.error))}", False
    if not r.ok:
        return f"🔴 {label} — {esc(describe_error(r.error))}", False
    notes: list[tuple[str | None, str]] = []   # (severity or None = unchecked, words)
    if ssl is not None:
        if not ssl.ssl_info:
            notes.append((None, "сертификат не удалось проверить"))
        elif ssl.ssl_info.days_left < 0:
            notes.append(("critical", f"сертификат истёк {abs(ssl.ssl_info.days_left)} дн. назад"))
        elif ssl.ssl_info.days_left <= 14:
            notes.append(("critical" if ssl.ssl_info.days_left <= 3 else "warning",
                          f"сертификат истекает через {ssl.ssl_info.days_left} дн."))
    if dom is not None and not dom.unsupported:
        if not dom.domain_info or dom.domain_info.days_left is None:
            notes.append((None, "срок домена не удалось проверить"))
        elif dom.domain_info.days_left < 0:
            notes.append(("critical", f"домен истёк {abs(dom.domain_info.days_left)} дн. назад"))
        elif dom.domain_info.days_left <= 30:
            notes.append(("critical" if dom.domain_info.days_left <= 7 else "warning",
                          f"домен истекает через {dom.domain_info.days_left} дн."))
    if notes:
        worst = worst_severity(s for s, _ in notes)
        icon = level_icon(worst) if worst else UNCHECKED
        verb = "открывается" if is_http_url(r.url) and not r.api else "отвечает"
        return f"{icon} {label} — {verb}, но {'; '.join(w for _, w in notes)}", False
    verb = "открывается" if is_http_url(r.url) and not r.api else "отвечает"
    return f"✅ {label} — {verb} {humanize.speed(r.response_time_ms)}", True


def format_compact_status_report(availability: list[AvailabilityResult],
                                 incidents: list[dict],
                                 ssl_results: list[SslResult] | None = None,
                                 domain_results: list[DomainResult] | None = None,
                                 report_type: str = "status",
                                 extras: list[str] | None = None,
                                 watch: list[tuple[str, bool]] | None = None) -> str:
    """watch: (line, fine) for the bots and tasks that mark in and for the
    bot's own watchdog — a block of their own under the sites, counted in
    «Всё спокойно» like a site."""
    now = now_local().strftime("%d.%m %H:%M")
    header = {"morning": f"🌅 Доброе утро · {now}",
              "morning_late": f"🌅 Утренняя сводка, с опозданием: утром меня не было · {now}",
              "evening": f"🌙 Вечер · {now}"}.get(report_type, f"🔎 Проверка · {now}")
    ssl_by_url = {r.url: r for r in (ssl_results or [])}
    domain_by_root = {registrable_domain(host_of(r.url)): r for r in (domain_results or [])}
    domain_urls = {r.url for r in (domain_results or [])}
    lines: list[str] = [header, ""]
    all_ok = True
    for r in availability:
        http = is_http_url(r.url)
        # An API check carries the certificate and domain notes only when it
        # was checked for its host itself — next to its site they would be
        # the same notes twice.
        dom = domain_by_root.get(registrable_domain(host_of(r.url))) if (domain_results is not None and http) else None
        if r.api and r.url not in domain_urls:
            dom = None
        line, ok = _site_line(r, ssl_by_url.get(r.url) if (ssl_results is not None and http) else None, dom)
        lines.append(line)
        all_ok &= ok
    if watch:
        lines += ([""] if availability else []) + [line for line, _ in watch]
        all_ok &= all(ok for _, ok in watch)
    if incidents:
        worst = worst_severity(i.get("severity") for i in incidents)
        lines += ["", f"{level_icon(worst)} Открытых проблем: {len(incidents)}"]
        # No leading spaces: Telegram wraps a long line back to column 0,
        # and the indent only makes the wrap look crooked.
        lines += [f"{level_icon(inc.get('severity'))} {esc(incident_line(inc))}" for inc in incidents[:5]]
        if len(incidents) > 5:
            lines.append(f"… и ещё {len(incidents) - 5}")
    elif all_ok:
        lines += ["", "Всё спокойно 🐕"]
    if extras:
        lines += ["", *extras]
    return "\n".join(lines)


def incident_line(inc: dict) -> str:
    """'example.com не открывается: хостинг отвечает ошибкой 502'."""
    return humanize.incident_headline(inc["check_type"], inc.get("message"), site_label(inc["url"]),
                                      page=is_web_page(inc["url"], inc.get("http_method")))


# Direction overrides and isolates (U+202A–U+202E, U+2066–U+2069) turn «exe.png» into «gnp.exe».
_BIDI = dict.fromkeys([*range(0x202A, 0x202F), *range(0x2066, 0x206A)])


def _one_line(value: str) -> str:
    """A visitor's field as ONE line: no line breaks or control characters —
    it cannot draw lines that look like the bot's own."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value).translate(_BIDI))
    return esc(" ".join(text.split()))


def format_feedback(site_url: str, page_url: str, message: str,
                    feedback_id: int, ip_address: str, verified: bool = False) -> str:
    """The visitor's words stay inside a quote; the lines around it are the bot's."""
    note = "" if verified else " (не подтверждено: так написал отправитель)"
    text = "\n".join(_one_line(line) for line in str(message).translate(_BIDI).splitlines())
    return (f"📩 Сообщение с сайта\n"
            f"Сайт: {_one_line(site_url)}{note}\nСтраница: {_one_line(page_url)}\n"
            f"Сообщение:\n<blockquote>{text}</blockquote>\n"
            f"🆔 #{feedback_id} · 🌍 {_one_line(ip_address)}")
