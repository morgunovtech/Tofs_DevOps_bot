"""Mail on the sites' domains: does the domain accept letters, and will
its own letters land in inboxes rather than in spam.

Hourly, per registrable domain of the monitored sites:
- MX — who receives the mail (the provider, by its MX hosts) and that
  those servers exist;
- delivery — port 25 of the MX opens from outside. Asked of check-host.net:
  cloud hosts, Railway included, block outgoing port 25, so a probe from
  here would lie. A control server is asked too before «does not accept»
  is believed. Two failed checks in a row are a 🔴 «mail» incident;
- SPF / DKIM / DMARC — what sends the domain's letters to spam or lets
  anyone send in its name: a 🟠 «mail_auth» incident whose message holds
  the exact record to paste.

The result is the "mail" section of the snapshot (services.sitestatus) of
the domain's carrier site — example.com rather than www.example.com — and
incidents are opened on that site too. A domain without MX and without
SPF has no mail: nothing to check, nothing to say.

No verdict is never «fixed»: a domain gone from DNS (NXDOMAIN), lookups
that failed, a check-host that did not answer leave the snapshot and the
incidents as they were. MX that vanished close «mail» silently — nothing
got fixed, dns_checker rings about them. A spam finding the owner has not
heard of yet, inside an open «mail_auth», is said (ladder.apply_findings).
"""

import asyncio
import logging
from dataclasses import dataclass, field

from db.database import (
    get_active_host_urls,
    get_dns_record,
    get_or_create_site,
    resolve_incident,
)
from monitors.base import CheckResult, Finding
from monitors.dns_checker import query, resolve
from monitors.ladder import apply_findings, apply_incident, told_after, told_before
from monitors.second_opinion import BUDGET_SEC, tcp_by_node
from services import hosting, humanize, integrations, sitestatus
from utils.text import plural
from utils.urls import host_of, owned_domain, registrable_domain

logger = logging.getLogger(__name__)

MAIL_CHECKS = ("mail", "mail_auth")   # incident types: delivery, spam
# Two failed delivery checks in a row before «does not accept» is said.
FAILS_TO_ALERT = 2
# A receiving server gives up on an SPF record past ten DNS lookups.
SPF_LOOKUP_LIMIT = 10
# One domain's check: up to three check-host calls (two MX, the control
# server), each within its budget, plus the DNS. A domain that takes
# longer keeps its last result and must not hold the other domains.
DOMAIN_BUDGET_SEC = 3 * BUDGET_SEC + 30


@dataclass(frozen=True)
class Provider:
    name: str
    mx: tuple[str, ...]          # MX host suffixes
    spf: tuple[str, ...]         # include: domains that authorise its servers (first = the one to add)
    dkim: tuple[str, ...] = ()   # selectors it publishes; () = owner-chosen, not checked
    sends: bool = True           # False: forwarding only, sends nothing in the domain's name
    status: str | None = None    # its status page


PROVIDERS: tuple[Provider, ...] = (
    Provider("Proton", ("protonmail.ch", "proton.me"), ("_spf.protonmail.ch",),
             ("protonmail", "protonmail2", "protonmail3"), status="https://status.proton.me"),
    Provider("Google Workspace", ("google.com", "googlemail.com"), ("_spf.google.com",), ("google",),
             status="https://www.google.com/appsstatus/dashboard/"),
    Provider("Microsoft 365", ("mail.protection.outlook.com",), ("spf.protection.outlook.com",),
             ("selector1", "selector2"), status="https://status.cloud.microsoft"),
    Provider("Яндекс 360", ("yandex.net", "yandex.ru"), ("_spf.yandex.net",), ("mail",)),
    Provider("VK WorkMail", ("mail.ru",), ("_spf.mail.ru",), ("mailru",)),
    Provider("Zoho Mail", ("zoho.com", "zoho.eu", "zoho.in", "zohomail.com"),
             ("zohomail.com", "zoho.com", "zoho.eu", "zoho.in", "zohomail.eu"), status="https://status.zoho.com"),
    Provider("iCloud", ("mail.icloud.com",), ("icloud.com",), ("sig1",),
             status="https://www.apple.com/support/systemstatus/"),
    Provider("Fastmail", ("messagingengine.com",), ("spf.messagingengine.com",), ("fm1", "fm2", "fm3"),
             status="https://fastmailstatus.com"),
    Provider("Cloudflare Email Routing", ("mx.cloudflare.net",), ("_spf.mx.cloudflare.net",), sends=False,
             status="https://www.cloudflarestatus.com"),
)

# Asked when every MX of a domain fails: if this one fails too, the
# verdict service is what is broken, not the owner's mail.
CONTROL_MX = ("gmail-smtp-in.l.google.com", "mail.protonmail.ch")


def _matches(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def provider_for(mx: list[str]) -> Provider | None:
    """The mail service behind the domain, by its first recognised MX."""
    for host in mx:
        for p in PROVIDERS:
            if any(_matches(host, s) for s in p.mx):
                return p
    return None


def provider_by_include(domain: str) -> Provider | None:
    return next((p for p in PROVIDERS if domain.lower() in p.spf), None)


# ── DNS ──────────────────────────────────────────────────────────────────────

async def _txt(name: str) -> list[str] | None:
    """TXT strings with their chunks joined; [] = none; None = lookup failed."""
    answer = await query(name, "TXT")
    return None if answer is None else [b"".join(r.strings).decode("utf-8", "replace") for r in answer]


async def _mx(domain: str) -> list[str] | None:
    """MX hosts, most preferred first; [] = no mail (a null MX «.» too);
    None = the lookup failed — or the domain does not exist (NXDOMAIN),
    which is not «no mail»: dns_checker and domain_checker say that."""
    answer = await query(domain, "MX", nxdomain_none=True)
    if answer is None:
        return None
    hosts = [r.exchange.to_text().rstrip(".").lower() for r in sorted(answer, key=lambda r: r.preference)]
    return [h for h in hosts if h]


async def _exists(host: str) -> bool | None:
    """Has an address; None = could not tell."""
    answers = await asyncio.gather(resolve(host, "A"), resolve(host, "AAAA"))
    if any(answers):
        return True
    return None if None in answers else False


def spf_records(txts: list[str]) -> list[str]:
    return [t.strip() for t in txts if t.strip().lower() == "v=spf1" or t.strip().lower().startswith("v=spf1 ")]


def sends_mail(spf: list[str]) -> bool:
    """SPF that names any sender — «v=spf1 -all» alone says «nobody sends
    in this domain's name», which is the right lock for a domain without mail."""
    return any(t.lstrip("+-~?").lower() != "all" for rec in spf for t in _terms(rec))


def dmarc_records(txts: list[str]) -> list[str]:
    return [t.strip() for t in txts if t.strip().lower().replace(" ", "").startswith("v=dmarc1")]


def dmarc_policy(record: str) -> str | None:
    """none | quarantine | reject; None when the record has no valid p=."""
    for tag in record.split(";"):
        key, _, value = tag.strip().partition("=")
        if key.strip().lower() == "p" and value.strip().lower() in ("none", "quarantine", "reject"):
            return value.strip().lower()
    return None


def _terms(record: str) -> list[str]:
    return record.split()[1:]


async def spf_expand(record: str) -> tuple[int, set[str]]:
    """(DNS lookups the record costs a receiving server, every domain it
    includes — nested ones too). Every term is counted; DNS is asked level
    by level, in parallel, and not at all once the limit is passed: «too
    long» is known then, and the top-level senders are still named."""
    lookups, included, level = 0, set(), [record]
    while level:
        names: list[str] = []
        for rec in level:
            for term in _terms(rec):
                t = term.lstrip("+-~?").lower()
                if t.startswith("include:"):
                    name = t[len("include:"):]
                elif t.startswith("redirect="):
                    name = t[len("redirect="):]
                elif t in ("a", "mx", "ptr") or t.startswith(("a:", "a/", "mx:", "mx/", "ptr:", "exists:")):
                    name = ""
                else:
                    continue
                lookups += 1
                if name and name not in included and "%" not in name:
                    included.add(name)
                    if lookups <= SPF_LOOKUP_LIMIT:
                        names.append(name)
        if lookups > SPF_LOOKUP_LIMIT:
            return lookups, included
        answers = await asyncio.gather(*(_txt(n) for n in names))
        level = [nested[0] for nested in (spf_records(a or []) for a in answers) if len(nested) == 1]
    return lookups, included


async def _dkim_found(domain: str, selectors: tuple[str, ...]) -> bool | None:
    """A key under any of the selectors (a CNAME is followed); None = the
    lookups failed, so nothing can be said."""
    if not selectors:
        return None
    answers = await asyncio.gather(*(_txt(f"{s}._domainkey.{domain}") for s in selectors))
    if any(any("p=" in t.replace(" ", "") for t in a or []) for a in answers):
        return True
    return None if None in answers else False


# ── Findings ─────────────────────────────────────────────────────────────────

def merge_spf(records: list[str], provider: Provider | None) -> str:
    """One record out of several: every mechanism once (a bare «mx» and
    «+mx» are the same), the provider's include first, the softest «all»
    the records had (never +all). A redirect= becomes an include: of the
    same senders — a second redirect= would break the record; exp= is
    dropped, it only names an explanation."""
    terms: list[str] = []
    alls = []
    for rec in records:
        for t in _terms(rec):
            bare = t.lstrip("+-~?").lower()
            if bare == "all":
                alls.append(t[0] if t[0] in "+-~?" else "+")
                continue
            if bare.startswith("redirect="):
                t = "include:" + t.split("=", 1)[1]
            elif bare.startswith("exp="):
                continue
            if t.lstrip("+").lower() not in {x.lstrip("+").lower() for x in terms}:
                terms.append(t)
    if provider and provider.sends and not any(t.lower() == f"include:{s}" for s in provider.spf for t in terms):
        terms.insert(0, f"include:{provider.spf[0]}")
    final = "-all" if alls and all(q == "-" for q in alls) else "~all"
    return " ".join(["v=spf1", *terms, final])


def _with_include(record: str, include: str) -> str:
    head, _, rest = record.partition(" ")
    return f"{head} include:{include} {rest}".strip()


def _names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def assess(domain: str, mx: list[str], spf: list[str], spf_lookups: int, spf_includes: set[str],
           dkim: bool | None, dmarc: list[str], where: str = "в DNS домена") -> list[Finding]:
    """What makes the domain's letters land in spam or lets anyone send in
    its name — each with what to do. Pure: the lookups are done before."""
    if not mx and not sends_mail(spf):
        return []   # no mail on this domain
    provider = provider_for(mx)
    sending = provider if provider and provider.sends else None
    out: list[Finding] = []
    spam = "письма с домена могут уходить в спам"
    if not spf:
        record = f"v=spf1 include:{sending.spf[0]} ~all" if sending else "v=spf1 mx ~all"
        out.append(Finding("spf_missing", "Нет записи SPF",
                           "почтовые сервисы не знают, кто вправе слать письма от имени домена: твои письма "
                           "чаще попадают в спам, а подделать твой адрес проще",
                           f"Добавь {where} запись TXT для {domain}:", record))
    elif len(spf) > 1:
        merged = merge_spf(spf, provider)
        others = list(dict.fromkeys(p.name for t in merged.split() if t.lower().startswith("include:")
                                    for p in [provider_by_include(t[len("include:"):])] if p and p != provider))
        note = (f"Если через {_names(others)} ты больше не отправляешь почту — убери "
                f"{'его' if len(others) == 1 else 'их'} из записи." if others else "")
        n = len(spf)
        out.append(Finding("spf_multiple", f"{n} {plural(n, 'запись', 'записи', 'записей')} SPF вместо одной",
                           f"почтовые сервисы считают это ошибкой и не доверяют ни одной, поэтому {spam}",
                           f"Оставь {where} одну запись TXT, начинающуюся с v=spf1, — вот такую, а остальные "
                           f"удали:", merged, note))
    else:
        # One corrected record for every SPF finding: the owner pastes it
        # once, not one version per finding.
        terms = [t.lower() for t in _terms(spf[0])]
        open_all = "+all" in terms or "all" in terms
        no_provider = bool(sending and not spf_includes & set(sending.spf))
        fixed = spf[0]
        if open_all:
            fixed = " ".join(["v=spf1", *(t for t in _terms(fixed) if t.lstrip("+").lower() != "all"), "~all"])
        if no_provider:
            fixed = _with_include(fixed, sending.spf[0])
        if open_all:
            out.append(Finding("spf_open", "SPF разрешает слать письма от твоего имени кому угодно",
                               "в записи стоит «+all» — подделать твой адрес может любой",
                               f"Замени {where} запись SPF на такую:", fixed))
        if spf_lookups > SPF_LOOKUP_LIMIT:
            out.append(Finding("spf_too_long", f"В SPF больше {SPF_LOOKUP_LIMIT} вложенных проверок",
                               f"почтовые сервисы бросают такую запись на полпути и считают ошибкой, поэтому {spam}",
                               "Убери из записи SPF сервисы, через которые ты больше не отправляешь почту: "
                               "каждый include стоит одну или несколько проверок."))
        if no_provider:
            out.append(Finding("spf_no_provider", f"В SPF нет {sending.name}",
                               f"письма, которые ты отправляешь через {sending.name}, не проходят проверку, поэтому "
                               f"могут уходить в спам",
                               f"Запись SPF из пункта выше уже включает {sending.name} — вставь её." if open_all
                               else f"Добавь {sending.name} в запись SPF {where} — получится так:",
                               "" if open_all else fixed))
    if sending and dkim is False:
        out.append(Finding("dkim_missing", "Письма не подписаны (нет DKIM)",
                           "без подписи письма с домена чаще попадают в спам, а подделку от них не отличить",
                           f"Включи DKIM в настройках {sending.name}: он покажет одну–три записи (CNAME или TXT) "
                           f"— добавь их {where}.", "",
                           f"Искал по стандартным именам: {', '.join(f'{s}._domainkey' for s in sending.dkim)}."))
    if not dmarc:
        policy = "none" if out else "quarantine"
        out.append(Finding("dmarc_missing", "Нет DMARC",
                           "почтовые сервисы не знают, что делать с письмами, которые подделывают твой адрес, — "
                           "и пропускают их",
                           f"Добавь {where} запись TXT с именем _dmarc.{domain}:", f"v=DMARC1; p={policy}",
                           "Когда починишь остальное, поменяй p=none на p=quarantine — тогда подделки будут "
                           "уходить в спам." if policy == "none" else ""))
    elif len(dmarc) > 1:
        n = len(dmarc)
        out.append(Finding("dmarc_multiple", f"{n} {plural(n, 'запись', 'записи', 'записей')} DMARC вместо одной",
                           "почтовые сервисы считают это ошибкой и не применяют ни одну",
                           f"Оставь {where} одну запись TXT для _dmarc.{domain} — например, эту, — а остальные "
                           f"удали:", dmarc[0]))
    elif dmarc_policy(dmarc[0]) is None:
        out.append(Finding("dmarc_invalid", "Запись DMARC с ошибкой",
                           "в ней нет правила p=, поэтому почтовые сервисы её не применяют",
                           f"Замени {where} запись TXT для _dmarc.{domain} на такую:", "v=DMARC1; p=quarantine"))
    return out


def summary(findings: list[dict]) -> str:
    """The incident message: «почта — нет DMARC и ещё 1 помеха»."""
    rest = len(findings) - 1
    more = f" и ещё {rest} {plural(rest, 'помеха', 'помехи', 'помех')}" if rest else ""
    return f"почта — {humanize.lower_first(findings[0]['title'])}{more}"


# ── Delivery ─────────────────────────────────────────────────────────────────

@dataclass
class Delivery:
    ok: bool | None = None       # accepts mail? None = could not tell
    reason: str | None = None    # «nowhere»: the MX lead to no server; «silent»: they do not answer
    backup_only: bool = False    # the main server is silent, a backup one accepts


async def delivery(mx: list[str]) -> Delivery:
    """Does port 25 of the MX open from outside. «Silent» only when every
    node that reached no MX does open the control server: a node that
    cannot open port 25 at all says nothing about the owner's mail."""
    exists = await asyncio.gather(*(_exists(h) for h in mx))
    if exists and all(e is False for e in exists):
        return Delivery(False, "nowhere")
    if not integrations.second_opinion_enabled():
        return Delivery()
    candidates = [h for h, e in zip(mx, exists, strict=True) if e is not False][:2]
    failed: set[str] = set()       # nodes that reached no MX
    complete = True
    for host in candidates:
        by_node = await tcp_by_node(f"{host}:25")
        if any(by_node.values()):
            return Delivery(True, backup_only=host != mx[0])
        if not by_node or None in by_node.values():
            complete = False
        failed.update(n for n, ok in by_node.items() if ok is False)
    if not complete or not failed:
        return Delivery()
    google = any(_matches(mx[0], s) for s in ("google.com", "googlemail.com"))
    control = CONTROL_MX[1] if google else CONTROL_MX[0]
    # The control server is asked on the SAME nodes.
    ctrl = await tcp_by_node(f"{control}:25", nodes=sorted(failed))
    if not any(ctrl.values()):
        logger.info("Mail delivery for %s: control %s fails on the same nodes — no verdict", mx[0], control)
        return Delivery()
    return Delivery(False, "silent")


# ── The check ────────────────────────────────────────────────────────────────

@dataclass
class MailResult(CheckResult):
    domain: str = ""
    provider: str | None = None
    delivery: Delivery = field(default_factory=Delivery)
    findings: list[dict] = field(default_factory=list)
    status_page: str | None = None
    # «mail» (delivery) or «mail_auth» (spam): which incident this result is about.
    kind: str = "mail"


def carriers(urls: list[str]) -> dict[str, str]:
    """Registrable domain → the site whose card speaks for its mail: the
    bare domain, then www., then the first by name."""
    by_domain: dict[str, list[str]] = {}
    for url in urls:
        # An IP, a bare name, x.pages.dev: no domain of the owner's, no mail of theirs.
        domain = owned_domain(host_of(url))
        if domain:
            by_domain.setdefault(domain, []).append(url)
    out = {}
    for domain, group in by_domain.items():
        def rank(u: str, d: str = domain) -> tuple[int, str]:
            h = host_of(u)
            return (0 if h == d else 1 if h == f"www.{d}" else 2, u)
        out[domain] = min(group, key=rank)
    return out


async def dns_where(domain: str) -> str:
    """«в DNS у Cloudflare» — where the records are edited."""
    ns = await get_dns_record(domain, "NS")
    return f"в DNS у {hosting.dns_provider(ns[0])}" if ns and ns[0] and ns[0] != "—" else "в DNS домена"


async def carrier_of(url: str) -> str | None:
    """The site whose card speaks for the mail of url's domain."""
    return carriers(await get_active_host_urls()).get(registrable_domain(host_of(url)))


async def check_mail(domain: str, url: str, manage: bool = True) -> list[MailResult]:
    """Both verdicts for one domain: [delivery, spam]. Lookups that failed
    leave the previous snapshot and the incidents as they were."""
    site_id = await get_or_create_site(url)
    mx = await _mx(domain)
    txt = await _txt(domain)
    dmarc_txt = await _txt(f"_dmarc.{domain}")
    if mx is None or txt is None or dmarc_txt is None:
        logger.info("Mail check for %s: DNS lookups failed, keeping the last result", domain)
        return []
    provider = provider_for(mx)
    spf = spf_records(txt)
    lookups, includes = await spf_expand(spf[0]) if len(spf) == 1 else (0, set())
    dkim = await _dkim_found(domain, provider.dkim) if provider and provider.sends else None
    findings = [f.as_dict() for f in assess(domain, mx, spf, lookups, includes, dkim,
                                            dmarc_records(dmarc_txt), await dns_where(domain))]
    deliv = await delivery(mx) if mx else Delivery()

    previous = (await sitestatus.get(site_id)).get("mail") or {}
    codes = {f["code"] for f in findings}
    told = told_before(previous)
    fails = previous.get("fails") or 0   # no verdict: the count stands
    if deliv.ok is False:
        fails += 1
    elif deliv.ok or not mx:
        fails = 0
    dmarc = dmarc_records(dmarc_txt)
    await sitestatus.update(
        site_id, "mail", domain=domain, mx=mx, provider=provider.name if provider else None,
        accepts=deliv.ok, reason=deliv.reason, backup_only=deliv.backup_only, fails=fails,
        sends_only=not mx and sends_mail(spf), locked=not mx and bool(spf) and not sends_mail(spf),
        spf=len(spf), dkim=dkim,
        dmarc=dmarc_policy(dmarc[0]) if len(dmarc) == 1 else None, findings=findings,
        status_page=provider.status if provider else None, told=told_after(told, codes, manage))

    base = dict(url=url, site_id=site_id, domain=domain, provider=provider.name if provider else None,
                status_page=provider.status if provider else None)
    down = MailResult(**base, delivery=deliv, kind="mail")
    spam = MailResult(**base, findings=findings, kind="mail_auth")
    if deliv.ok is False and fails >= FAILS_TO_ALERT:
        down.status, down.error = "critical", down_message(deliv.reason, provider.name if provider else None)
    if findings:
        spam.status, spam.error = "warning", summary(findings)
    # No MX any more, or the delivery check is switched off: «does it accept
    # letters» stopped applying — close silently, nothing got fixed (a vanished
    # MX is dns_checker's 🔴). No mail at all: the same for the spam settings.
    if not mx or (deliv.ok is None and not integrations.second_opinion_enabled()):
        await resolve_incident(site_id, "mail")
    else:
        await apply_incident(down, "mail", settled=bool(deliv.ok), open_new=manage)
    if not mx and not sends_mail(spf):
        await resolve_incident(site_id, "mail_auth")
    else:
        await apply_findings(spam, "mail_auth", codes, told, open_new=manage)
    return [down, spam]


def down_message(reason: str | None, provider: str | None) -> str:
    if reason == "nowhere":
        return "почта не принимает письма — записи MX ведут на сервер, которого нет"
    return f"почта не принимает письма — {provider or 'почтовый сервер'} не отвечает"


async def check_all_mail(urls: list[str], manage: bool = True) -> list[MailResult]:
    out: list[MailResult] = []
    for domain, url in sorted(carriers(urls).items()):
        try:
            async with asyncio.timeout(DOMAIN_BUDGET_SEC):
                out += await check_mail(domain, url, manage=manage)
        except TimeoutError:
            logger.warning("Mail check for %s took over %s s — keeping the last result", domain, DOMAIN_BUDGET_SEC)
        except Exception:   # one domain must not stop the others
            logger.exception("Mail check for %s failed", domain)
    return out
