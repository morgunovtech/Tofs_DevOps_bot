"""Telegram message formatting for reports and alerts, in the language of
someone who does not know what a 502 is. Every alert answers, in order:
what happened, what it means for visitors, what it usually is, what to
do. Technical details go last, in italics."""

from datetime import UTC, datetime

from monitors import domain_checker, ssl_checker
from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainResult,
    LinksResult,
    SeoResult,
    SslResult,
)
from monitors.dns_checker import FAILS_TO_SAY
from monitors.mail_checker import MAIL_CHECKS, MailResult
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
from utils.i18n import fmt_duration, plural, t
from utils.text import clean, defang, esc, incident_start, one_line, parse_sqlite_utc
from utils.urls import host_of, is_http_url, is_web_page, owned_domain, registrable_domain, site_label


def _tech(*parts) -> str:
    """Grey technical footer: '<i>HTTP 502 · 0.81 с</i>'."""
    bits = [humanize.technical(str(p)) for p in parts if p not in (None, "", "—")]
    return f"\n<i>{esc(' · '.join(bits))}</i>" if bits else ""


# ── Availability ─────────────────────────────────────────────────────────────

def format_availability_alert(r: AvailabilityResult, second_opinion: bool = True, *,
                              threshold: int = 2, interval_min: int = 5,
                              since: datetime | None = None, label: str | None = None) -> str:
    """threshold and interval_min — the site's own dials: the text says how
    many checks it took and how often the site is checked, not «дважды» and
    «каждую минуту» for everyone. since — when the problem began (the first
    failed check), so the alert says «не открывается с 14:03». label — how
    to name the site instead of site_label (the helpers' copy: a public name
    without the path, which may carry a token)."""
    if r.ok:
        return ""
    label = esc(label or site_label(r.url))
    expl = explain("availability", r.error)
    when = t(" с {time}").format(time=to_local(since).strftime("%H:%M")) if since else ""
    if r.keyword_failed:
        header = (t("🔴 {label} отвечает, но показывает не то{when}") if r.api
                  else t("🔴 {label} открывается, но показывает не то{when}")).format(label=label, when=when)
        what = esc(describe_error(r.error))
        situation = t("{meaning} Сейчас {what}.").format(meaning=expl.meaning, what=what)
    else:
        page = is_http_url(r.url) and not r.api
        header = (t("🔴 {label} не открывается{when}: {what}") if page
                  else t("🔴 {label} не отвечает{when}: {what}")).format(label=label, when=when,
                                                                        what=esc(describe_error(r.error)))
        situation = expl.meaning
        if r.failed_of:
            how = t("Сбой в {n} из {total} последних проверок").format(n=r.failed_of[0], total=r.failed_of[1])
        else:
            how = t("Проверил {times}").format(times=humanize.times_in_a_row(threshold))
        if r.cert_failed:
            situation += t(" {how}: сервер отвечает, но его сертификат не принимается — "
                           "так его видят и посетители.").format(how=how)
        elif r.external_ok is False:
            situation += t(" {how} и с внешних серверов: не открывается ни у кого.").format(how=how)
        elif r.external_region and r.external_ups:
            situation += t(" {how}; снаружи открывается (с {ups} из {total} серверов), но не {region} — "
                           "посетители оттуда его тоже не видят.").format(
                how=how, ups=r.external_ups, total=r.external_total, region=region_words(r.external_region))
        elif r.external_region:
            situation += t(" {how}; {region} тоже не открывается, остальные внешние серверы не успели ответить.").format(
                how=how, region=region_words(r.external_region))
        elif r.external_ups and r.external_total >= 2:
            situation += t(" {how}; снаружи открывается только с {ups} из {total} серверов — похоже, сайт лежит "
                           "не везде.").format(how=how, ups=r.external_ups, total=r.external_total)
        elif second_opinion:
            situation += t(" {how}; снаружи проверить не удалось — возможно, проблема на моей стороне.").format(how=how)
        else:
            situation += f" {how}."
    # A phrase verdict is about a site that opens: «поднимется» would send
    # the owner to the server instead of the page.
    back = ((t("когда в ответе снова будет как надо") if r.api else t("когда на странице снова будет как надо"))
            if r.keyword_failed else t("когда поднимется"))
    return (f"{header}\n{situation}\n"
            + t("Продолжаю проверять {every} и напишу, {back}.").format(every=humanize.every_minutes(interval_min), back=back)
            + _tech(r.error, fmt_seconds(r.response_time_ms) if r.response_time_ms else None))


def incident_duration_line(incident: dict | None) -> str | None:
    """'12 мин (14:03–14:15)' in the user's timezone: from the first failed
    check to the moment it was seen up. When nobody checked in between (the
    bot was down), the end is only known from below — said so."""
    if not incident:
        return None
    started = incident_start(incident)
    if not started:
        return None
    ended = parse_sqlite_utc(incident.get("resolved_at")) or datetime.now(UTC)
    minutes = max(1, round((ended - started).total_seconds() / 60))
    fmt = "%H:%M" if minutes < 24 * 60 else "%d.%m %H:%M"
    span = f"{to_local(started).strftime(fmt)}–{to_local(ended).strftime(fmt)}"
    unseen = parse_sqlite_utc(incident.get("unseen_until"))
    if unseen:
        return t("не меньше {duration} ({span}); когда именно поднялся — не знаю: "
                 "с {start} до {end} я его не проверял").format(
            duration=fmt_duration(minutes), span=span, start=to_local(ended).strftime(fmt), end=to_local(unseen).strftime(fmt))
    return f"{fmt_duration(minutes)} ({span})"


def format_recovery_alert(r: AvailabilityResult, *, label: str | None = None) -> str:
    """label — a name for the site instead of site_label (the helpers' copy)."""
    label = esc(label or site_label(r.url))
    message = (r.resolved_incident or {}).get("message") or ""
    content = message.startswith("Bad content: ")    # the site opened all along; its text was wrong
    if content:
        lines = [t("✅ {label}: снова всё как надо").format(label=label)]
    else:
        lines = [(t("✅ {label} снова открывается") if is_http_url(r.url) and not r.api
                  else t("✅ {label} снова отвечает")).format(label=label)]
    duration = incident_duration_line(r.resolved_incident)
    cause = describe_error(message)
    if duration:
        lines.append((t("Было не так {duration}") if content else t("Лежал {duration}")).format(duration=duration)
                     + (f", {esc(cause)}." if cause and cause != t("неизвестная ошибка") else "."))
    lines.append(t("Ничего делать не нужно."))
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
    note = t(" Автопродление не сработало: сертификат не менялся, хотя срок уже близко.") if r.renewal_note else ""
    return (f"{header}\n{expl.meaning}{note}"
            + _tech(r.error, t("выдан {issuer}").format(issuer=r.ssl_info.issuer) if r.ssl_info and r.ssl_info.issuer else None))


def format_domain_alert(r: DomainResult) -> str:
    if r.ok or not r.error:
        return ""
    label = esc(r.domain or site_label(r.url))
    expl = explain("domain", r.error)
    icon = level_icon(r.severity)
    registrar = r.domain_info.registrar if r.domain_info and r.domain_info.registrar not in ("", "Unknown") else ""
    where = t(" Продлевается у регистратора: {registrar}.").format(registrar=esc(registrar)) if registrar else ""
    return (f"{icon} {label}: {esc(describe_error(r.error))}\n{expl.meaning}{where}"
            + _tech(r.error))


# ── Links / deep / SEO / DNS ─────────────────────────────────────────────────

LINK_FIX = ("Что сделать: если страница переехала — поставь новый адрес; пропала — убери ссылку "
            "или дай копию из web.archive.org; закрытый репозиторий — открой его или убери ссылку.")   # via t()


def link_lines(links: list, limit: int = 10) -> list[str]:
    """«• url — why» for each link, own site or not alike."""
    lines = [f"• {esc(b.url)} — {esc(link_reason(b.status_code, b.error, b.url))}" for b in links[:limit]]
    if len(links) > limit:
        lines.append(t("… и ещё {n}").format(n=len(links) - limit))
    return lines


def format_links_report(r: LinksResult) -> str:
    """The alert and the «🔗 Ссылки» screen: every link that does not open,
    wherever it points — a dead end for the visitor is the owner's to fix."""
    if not r.broken:
        return ""
    n = len(r.broken)
    lines = [t("🟠 На {label} {n} {links}").format(
                 label=esc(site_label(r.url)), n=n,
                 links=plural(n, "ссылка не открывается", "ссылки не открываются", "ссылок не открываются")),
             explain("links", "").meaning, *link_lines(r.broken), t(LINK_FIX)]
    return "\n".join(lines)


def pending_note(pending: list) -> str:
    n = len(pending)
    return t("⚠️ Ещё {n} {links} не {opened} с первого раза (таймаут или ошибка сервера) — перепроверю при "
             "следующем обходе и скажу, если так и останется.").format(
        n=n, links=plural(n, "ссылка", "ссылки", "ссылок"), opened=plural(n, "открылась", "открылись", "открылись"))


def _deep_page_error(code: int) -> str:
    """A 5xx by its words; code 0 is a page that did not answer at all."""
    return describe_error(f"HTTP {code}") if code else t("не ответила за 15 секунд")


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
    lines = [t("🔴 {label} исчезает из поиска").format(label=esc(site_label(r.url))),
             t("Google и Яндекс уберут сайт при следующем обходе — переходы из поиска пропадут."), ""]
    seen: set[str] = set()
    for p in critical[:6]:
        f = seo_fixes.fix(p.code)
        head = f"  • <b>{esc(t(f.title))}</b>" if p.code not in seen else "  •"
        seen.add(p.code)
        lines.append(f"{head} — {esc(t(p.message))}")
    lines += ["", t("Нажми кнопку под сообщением: там шаги для твоего хостинга.")]
    return "\n".join(lines)


def format_dns_failing(change: DnsChange) -> str:
    """The name stopped answering FAILS_TO_SAY hourly checks in a row — the
    other names answer, so it is not the bot's network."""
    n = FAILS_TO_SAY
    icon = level_icon("critical" if change["critical"] else "warning")
    rtype = change["rtype"]
    hint = (t("Так бывает, когда перестали работать NS-серверы домена (не оплачен хостинг DNS, "
              "удалили зону) или после смены NS не обновили DNSSEC (запись DS у регистратора): "
              "браузеры тоже не находят сайт.") if rtype == "NS" else
            t("Так бывает, когда у DNS-серверов домена сбой или сломана зона: посетители могут не найти сайт."))
    steps = t("1. Открыть сайт в браузере: если не открывается — виноват DNS, а не хостинг.\n"
              "2. Проверить домен на dnschecker.org: отвечают ли его NS-серверы.\n"
              "3. Если DNSSEC включён у регистратора, а зона переехала — снять или обновить запись DS.")
    return (t("{icon} {host}: DNS не отвечает на запрос {rtype} уже {n} {checks} подряд ({n} ч). "
              "Другие имена при этом отвечают — дело не в моей сети.").format(
                icon=icon, host=esc(change["host"]), rtype=esc(rtype), n=n,
                checks=plural(n, "проверку", "проверки", "проверок"))
            + f"\n{hint}\n\n" + t("Последний известный ответ: {old}").format(old=esc(change["old"])) + f"\n\n{steps}")


# (what, «changed», «gone») — the verb agrees with the noun, and a record that
# is gone is said to be gone, not «changed» with «пока ничего не сломалось».
_DNS_KINDS = {"A": ("адрес сервера (запись A)", "изменился", "пропал"),
              "AAAA": ("адрес сервера IPv6 (запись AAAA)", "изменился", "пропал"),
              "CNAME": ("псевдоним адреса (запись CNAME)", "изменился", "пропал"),
              "NS": ("NS-серверы домена", "изменились", "пропали"),
              "MX": ("почтовые серверы (запись MX)", "изменились", "пропали")}


def format_dns_change(change: DnsChange) -> str:
    if change.get("failing"):
        return format_dns_failing(change)
    gone = change["new"] == "—"
    key = {"NS": "ns_change", "MX": "mx_gone" if gone else "mx_change"}.get(
        change["rtype"], "addr_gone" if gone else "dns_change")
    expl = humanize.explanation(key)
    icon = level_icon("critical" if change["critical"] else "warning")
    what, changed, vanished = _DNS_KINDS.get(change["rtype"],
                                             ("запись {rtype}", "изменилась", "пропала"))
    what = t(what).format(rtype=change["rtype"])
    return (t("{icon} У {host} {verb} {what}").format(icon=icon, host=esc(change["host"]),
                                                      verb=t(vanished if gone else changed), what=what)
            + f"\n{expl.meaning}\n\n"
            + t("Было: {old}").format(old=esc(change["old"])) + "\n" + t("Стало: {new}").format(new=esc(change["new"]))
            + "\n\n" + t("Что это обычно значит: {cause}.").format(cause=esc(expl.cause)) + f"\n\n{esc(steps_block(expl))}")


# ── Mail ─────────────────────────────────────────────────────────────────────

def finding_block(n: int, f: dict) -> str:
    """One finding (monitors.base.Finding): what, why, what to do, the
    record to paste — a block of lines as <pre>, one line as <code>."""
    lines = [f"{n}. <b>{esc(t(f['title']))}</b> — {esc(t(f['why']))}.", esc(t(f["fix"]))]
    record = f.get("record")
    if record:
        lines.append(f"<pre>{esc(record)}</pre>" if "\n" in record else f"<code>{esc(record)}</code>")
    if f.get("note"):
        lines.append(esc(t(f["note"])))
    return "\n".join(lines)


def format_mail_alert(r: MailResult) -> str:
    domain = esc(r.domain)
    if r.kind == "mail":
        if r.delivery.reason == "nowhere":
            what = t("Записи MX домена ведут на сервер, которого нет. Письма тебе не доходят — "
                     "отправители получают ошибку.")
        else:
            who = esc(r.provider) if r.provider else t("Почтовый сервер")
            what = t("{who} не отвечает уже две проверки подряд — проверял из нескольких стран. Письма тебе "
                     "сейчас не доходят: серверы отправителей будут пробовать ещё несколько дней, потом вернут "
                     "письмо с ошибкой.").format(who=who)
        steps = ([t("Открыть страницу статуса {provider} (кнопка ниже): если сбой у них — ждать, я "
                    "напишу, когда почта заработает.").format(provider=esc(r.provider))] if r.status_page else [])
        steps.append(t("Если у почтового сервиса всё в порядке — проверить записи MX в DNS: они должны вести на "
                       "него, точные значения есть в его настройках."))
        return "\n".join([t("🔴 Почта на {domain} не принимает письма").format(domain=domain), what, "", t("Что делать:"),
                          *(f"{i}. {s}" for i, s in enumerate(steps, 1))])
    n = len(r.findings)
    head = t("🟠 Почта на {domain} настроена не до конца — письма могут попадать в спам, а подделать "
             "твой адрес проще").format(domain=domain)
    lines = [head, t("Что исправить в DNS:") if n == 1 else
             t("{n} {obstacles}, у каждой — что исправить в DNS:").format(n=n, obstacles=plural(n, "помеха", "помехи", "помех")), ""]
    lines += ["\n\n".join(finding_block(i, f) for i, f in enumerate(r.findings, 1)), "",
              t("Изменения в DNS доходят за 5–30 минут. Я перепроверяю каждый час и напишу, когда всё будет "
                "в порядке.")]
    return "\n".join(lines)


def format_mail_recovery(r: MailResult) -> str:
    domain = esc(r.domain)
    if r.kind == "mail_auth":
        return t("✅ Почта на {domain}: настройки в порядке, письмам ничего не мешает.").format(domain=domain)
    started = incident_start(r.resolved_incident)
    how_long = ""
    if started:
        ended = parse_sqlite_utc((r.resolved_incident or {}).get("resolved_at")) or datetime.now(UTC)
        minutes = max(1, round((ended - started).total_seconds() / 60))
        how_long = t(" (не принимала около {duration})").format(duration=fmt_duration(minutes))
    return t("✅ Почта на {domain} снова принимает письма{how_long}. Письма, которые слали в это время, "
             "обычно доходят сами: серверы отправителей повторяют попытки.").format(domain=domain, how_long=how_long)


# ── Security ─────────────────────────────────────────────────────────────────

def format_security_alert(r: SecurityResult) -> str:
    label = esc(site_label(r.url))
    n = len(r.findings)
    if r.kind == "exposed":
        head = t("🔴 На {label} наружу видны служебные файлы — их может скачать любой").format(label=label)
        tail = t("Убрал — нажми «🔄 Проверить снова» на карточке сайта, я скажу, что их больше не видно.")
    else:
        head = t("🟠 {label}: защиту сайта стоит усилить").format(label=label)
        tail = t("Я перепроверяю каждые 6 часов и напишу, когда всё будет в порядке.")
    lines = [head, t("Что сделать:") if n == 1 else
             t("{n} {obstacles}, у каждой — что сделать:").format(n=n, obstacles=plural(n, "помеха", "помехи", "помех")),
             "", "\n\n".join(finding_block(i, f) for i, f in enumerate(r.findings, 1)), "", tail]
    return "\n".join(lines)


def format_security_recovery(r: SecurityResult) -> str:
    label = esc(site_label(r.url))
    if r.kind == "exposed":
        return t("✅ На {label} служебные файлы больше не видны снаружи. Если в них были пароли или ключи — "
                 "смени их, если ещё не сменил.").format(label=label)
    return t("✅ {label}: защита сайта в порядке.").format(label=label)


# ── Real visitors' speed ─────────────────────────────────────────────────────

def format_visitors_alert(r: VisitorsResult) -> str:
    expl = explain("visitors", r.error)
    return (t("🟠 {label}: медленно у настоящих посетителей").format(label=esc(site_label(r.url))) + "\n"
            + t("{values} — так у 3 из 4 посетителей, у остальных ещё дольше ({source}).").format(
                values=esc(crux.describe(r.values)), source=crux.source())
            + f"\n{esc(expl.meaning)}\n\n"
            + t("Что это обычно значит: {cause}.").format(cause=esc(expl.cause)) + f"\n\n{esc(steps_block(expl))}")


def format_visitors_recovery(r: VisitorsResult) -> str:
    return t("✅ {label}: у посетителей снова не медленно — {values}.").format(
        label=esc(site_label(r.url)), values=esc(crux.describe(r.values)))


# ── Status report (one line per site) ────────────────────────────────────────

def _site_line(r: AvailabilityResult, ssl: SslResult | None, dom: DomainResult | None) -> tuple[str, bool]:
    """One line per site with the worst level among its notes: 🔴 / 🟠 by
    the SSL and domain ladders, ⚠️ when something could not be checked."""
    label = esc(site_label(r.url))
    if r.status == "unknown":
        return f"{UNCHECKED} {label} — " + t("не могу проверить: {why}").format(why=esc(describe_error(r.error))), False
    if not r.ok:
        return f"🔴 {label} — {esc(describe_error(r.error))}", False
    notes: list[tuple[str | None, str]] = []   # (severity or None = unchecked, words)
    if ssl is not None:
        if not ssl.ssl_info:
            notes.append((None, t("сертификат не удалось проверить")))
        elif ssl.ssl_info.days_left < 0:
            notes.append(("critical", t("сертификат истёк {days} дн. назад").format(days=abs(ssl.ssl_info.days_left))))
        elif ssl.ssl_info.days_left <= ssl_checker.WARN_DAYS:
            notes.append(("critical" if ssl.ssl_info.days_left <= ssl_checker.CRITICAL_DAYS else "warning",
                          humanize.expires_words("сертификат", ssl.ssl_info.days_left)))
    if dom is not None and not dom.unsupported:
        if not dom.domain_info or dom.domain_info.days_left is None:
            notes.append((None, t("срок домена не удалось проверить")))
        elif dom.domain_info.days_left < 0:
            notes.append(("critical", t("домен истёк {days} дн. назад").format(days=abs(dom.domain_info.days_left))))
        elif dom.domain_info.days_left <= domain_checker.WARN_DAYS:
            notes.append(("critical" if dom.domain_info.days_left <= domain_checker.CRITICAL_DAYS else "warning",
                          humanize.expires_words("домен", dom.domain_info.days_left)))
    if notes:
        worst = worst_severity(s for s, _ in notes)
        icon = level_icon(worst) if worst else UNCHECKED
        verb = t("открывается") if is_http_url(r.url) and not r.api else t("отвечает")
        return f"{icon} {label} — " + t("{verb}, но {notes}").format(verb=verb, notes="; ".join(w for _, w in notes)), False
    verb = t("открывается") if is_http_url(r.url) and not r.api else t("отвечает")
    return f"✅ {label} — {verb} {humanize.speed(r.response_time_ms)}", True


def _site_lines(availability: list[AvailabilityResult],
                ssl_results: list[SslResult] | None,
                domain_results: list[DomainResult] | None) -> list[tuple[str, str, bool]]:
    """(url, line, fine) per site — the one place that decides what a site's
    line says, so the digest and the «already said above» set agree."""
    ssl_by_url = {r.url: r for r in (ssl_results or [])}
    domain_by_root = {registrable_domain(host_of(r.url)): r for r in (domain_results or [])}
    domain_urls = {r.url for r in (domain_results or [])}
    out = []
    for r in availability:
        http = is_http_url(r.url)
        # An API check carries the certificate and domain notes only when it
        # was checked for its host itself — next to its site they would be
        # the same notes twice.
        dom = domain_by_root.get(registrable_domain(host_of(r.url))) if (domain_results is not None and http) else None
        if r.api and r.url not in domain_urls:
            dom = None
        line, ok = _site_line(r, ssl_by_url.get(r.url) if (ssl_results is not None and http) else None, dom)
        out.append((r.url, line, ok))
    return out


def problem_site_urls(availability: list[AvailabilityResult],
                      ssl_results: list[SslResult] | None = None,
                      domain_results: list[DomainResult] | None = None) -> set[str]:
    """The sites whose line in the digest already shows a problem."""
    return {url for url, _, ok in _site_lines(availability, ssl_results, domain_results) if not ok}


DIGEST_INCIDENTS = 5
_ON_SITE_LINE = ("availability", "ssl", "domain")


def digest_incidents(incidents: list[dict], site_urls: set[str]) -> tuple[list[dict], int]:
    """(what to list under «Открытых проблем», how many more): every 🔴,
    then 🟠 up to five lines — an old 🔴 is never pushed out by five new 🟠.
    site_urls — the sites whose line above already shows the problem: what
    it said (availability, certificate, domain) is not said twice."""
    listable = [i for i in incidents if not (i["check_type"] in _ON_SITE_LINE and i["url"] in site_urls)]
    crit = [i for i in listable if i.get("severity") == "critical"]
    rest = [i for i in listable if i.get("severity") != "critical"]
    shown = crit + rest[:max(0, DIGEST_INCIDENTS - len(crit))]
    return shown, len(listable) - len(shown)


def format_compact_status_report(availability: list[AvailabilityResult],
                                 incidents: list[dict],
                                 ssl_results: list[SslResult] | None = None,
                                 domain_results: list[DomainResult] | None = None,
                                 report_type: str = "status",
                                 extras: list[str] | None = None,
                                 watch: list[tuple[str, bool]] | None = None,
                                 short: bool = False) -> str:
    """watch: (line, fine) for the bots and tasks that mark in and for the
    bot's own watchdog — a block of their own under the sites, counted in
    «Всё спокойно» like a site. short: only the lines that say something is
    wrong, and the sites in order as one count — the daily screen of the
    same «всё хорошо» teaches to scroll past the day with a 🔴 in it."""
    now = now_local().strftime("%d.%m %H:%M")
    header = {"morning": t("🌅 Доброе утро · {now}"),
              "morning_late": t("🌅 Утренняя сводка, с опозданием: утром меня не было · {now}"),
              "evening": t("🌙 Вечер · {now}")}.get(report_type, t("🔎 Проверка · {now}")).format(now=now)
    site_lines = _site_lines(availability, ssl_results, domain_results)
    lines: list[str] = [header, ""]
    all_ok = all(ok for _, _, ok in site_lines)
    if short:
        bad = [line for _, line, ok in site_lines if not ok]
        good = len(site_lines) - len(bad)
        lines += bad
        if good:
            lines.append((t("✅ Остальные в порядке: {n}") if bad else t("✅ Сайты в порядке: {n}")).format(n=good))
    else:
        lines += [line for _, line, _ in site_lines]
    if watch:
        lines += ([""] if availability else []) + [line for line, _ in watch]
        all_ok &= all(ok for _, ok in watch)
    if incidents:
        worst = worst_severity(i.get("severity") for i in incidents)
        said = {url for url, _, ok in site_lines if not ok}
        shown, more = digest_incidents(incidents, said)
        head = t("{icon} Открытых проблем: {n}").format(icon=level_icon(worst), n=len(incidents))
        lines += ["", head if shown else t("{head} — в строках сайтов выше").format(head=head)]
        # No leading spaces: Telegram wraps a long line back to column 0,
        # and the indent only makes the wrap look crooked.
        lines += [f"{level_icon(inc.get('severity'))} {esc(incident_line(inc))}" for inc in shown]
        if more:
            lines.append(t("… и ещё {n} — весь список: 🏠 Главная → «Что не так»").format(n=more))
    elif all_ok:
        lines += ["", t("Всё спокойно 🐕")]
    if extras:
        lines += ["", *extras]
    return "\n".join(lines)


def incident_line(inc: dict, label: str | None = None) -> str:
    """'example.com не открывается: хостинг отвечает ошибкой 502'. label —
    a name for the site instead of site_label (the helpers' copy). Mail is
    the domain's, not a site's: its incident lives on whichever site carries
    the domain (foundyou.example.com when example.com itself is not watched),
    and its line is named by the domain — «example.com: почта — 2 записи SPF»."""
    if inc["check_type"] in MAIL_CHECKS:
        label = owned_domain(host_of(inc["url"])) or label
    return humanize.incident_headline(inc["check_type"], inc.get("message"), label or site_label(inc["url"]),
                                      page=is_web_page(inc["url"], inc.get("http_method")))


def format_feedback(site_url: str, page_url: str, message: str,
                    feedback_id: int, ip_address: str, verified: bool = False) -> str:
    """The visitor's words stay inside a quote; the lines around it are the
    bot's (utils.text.one_line — the «📩» screen uses the same rule)."""
    note = "" if verified else t(" (не подтверждено: так написал отправитель)")
    # The site and page are the owner's own addresses and stay as they are;
    # a link inside the visitor's words is defanged (hxxps://evil[.]example).
    text = "\n".join(one_line(defang(line)) for line in clean(message).splitlines())
    return (t("📩 Сообщение с сайта") + "\n"
            + t("Сайт: {site}{note}").format(site=one_line(site_url), note=note) + "\n"
            + t("Страница: {page}").format(page=one_line(page_url)) + "\n"
            + t("Сообщение:") + f"\n<blockquote>{text}</blockquote>\n"
            f"🆔 #{feedback_id} · 🌍 {one_line(ip_address)}")
