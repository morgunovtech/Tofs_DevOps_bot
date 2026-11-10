"""Domain registration expiry via RDAP (see monitors.rdap) with an alert
ladder that starts earlier than SSL — registrars are slower.

One lookup per domain of the owner's, asked on the domain's «carrier» site
(the bare domain, else www., else the first by address — as mail picks
its carrier); the snapshot goes to every site of the domain, the ladder
and the incident live on the carrier. An IP, an internal name or an
address on a platform's domain (user.github.io, nas.synology.me) has no
registration of the owner's to watch: no lookup, no ladder.
"""

import asyncio
import ipaddress
import logging

from db.database import get_or_create_site, save_check
from monitors import rdap
from monitors.base import DomainResult, gather_checks
from monitors.ladder import apply_ladder
from services import integrations, sitestatus
from utils.net import is_private_target
from utils.text import err_text
from utils.urls import PLATFORM_DOMAINS, host_of, owned_domain, registrable_domain

logger = logging.getLogger(__name__)

DOMAIN_THRESHOLDS = [0, 1, 3, 7, 14, 30]
WARN_DAYS, CRITICAL_DAYS = 30, 7     # the ladder's levels, as in monitors.ssl_checker
DOMAIN_PARALLEL = 4        # lookups at once: RDAP servers and whois.tcinet.ru rate-limit


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def no_domain_reason(host: str) -> str | None:
    """Why there is no registration of the owner's to watch behind host."""
    if owned_domain(host):
        return None
    if _is_ip(host):
        return "это IP-адрес, домена нет"
    domain = registrable_domain(host)
    if domain in PLATFORM_DOMAINS:
        return f"адрес на домене платформы {domain} — его регистрацию продлевает она"
    return "внутреннее имя, домена нет"


def domain_groups(urls: list[str]) -> dict[str, list[str]]:
    """Owned registrable domain → its sites, the carrier first: the bare
    domain, then www., then the first by address — as mail picks its carrier."""
    groups: dict[str, list[str]] = {}
    for url in urls:
        host = host_of(url)
        domain = owned_domain(host) if host else None
        if domain:
            groups.setdefault(domain, []).append(url)
    for domain, group in groups.items():
        group.sort(key=lambda u, d=domain: (0 if host_of(u) == d else 1 if host_of(u) == f"www.{d}" else 2, u))
    return groups


async def _no_domain(r: DomainResult, reason: str) -> DomainResult:
    r.unsupported, r.error = True, reason
    await save_check(r.site_id, "domain", "ok", details=reason)
    await sitestatus.update(r.site_id, "domain", days_left=None, unsupported=True, error=reason)
    return r


async def check_domain(url: str, manage: bool = True) -> DomainResult:
    host = host_of(url)
    site_id = await get_or_create_site(url)
    domain = registrable_domain(host) if host else url
    r = DomainResult(url=url, site_id=site_id, domain=domain)
    if not host:
        r.status, r.error, r.transient = "warning", "Некорректный URL", True
        await save_check(site_id, "domain", r.status, details=r.error)
        return r
    if _is_ip(host):
        return await _no_domain(r, "это IP-адрес, домена нет")
    if is_private_target(url):
        # nas.local, pihole.lan, a single-label name: no registration to
        # look up, and the name must not go to rdap.org.
        return await _no_domain(r, "внутренний адрес — регистрации домена нет")
    reason = no_domain_reason(host)
    if reason:
        # A platform's domain (user.github.io): not the owner's to renew.
        return await _no_domain(r, reason)
    try:
        info = await rdap.lookup(domain)
        r.domain_info = info
        days = info.days_left
        if days is None:
            r.unsupported = True
            r.error = "срок регистрации не сообщается"
        elif days < 0:
            r.status, r.error = "error", f"Domain expired {abs(days)} days ago!"
        elif days <= CRITICAL_DAYS:
            r.status, r.error = "critical", f"Domain expires in {days} days!"
        elif days <= WARN_DAYS:
            r.status, r.error = "warning", f"Domain expires in {days} days"
    except rdap.RdapUnsupported as e:
        r.unsupported, r.error = True, str(e)
    except Exception as e:
        # Lookup failures are flaky (DomainLookupError or anything else) —
        # log, don't manage incidents on them.
        r.status, r.error, r.transient = "warning", f"Lookup failed: {err_text(e)}", True
        await integrations.record_result("rdap", False, err_text(e))
    else:
        await integrations.record_result("rdap", True)

    if r.domain_info and r.domain_info.days_left is not None:
        details = r.error or f"OK, {r.domain_info.days_left} days left"
    else:
        details = r.error
    await save_check(site_id, "domain", r.status, details=details)
    if r.transient:
        logger.warning("Domain lookup for %s failed: %s", domain, r.error)
        if ((await sitestatus.get(site_id)).get("domain") or {}).get("days_left") is not None:
            # One 429 or a timeout must not turn «осталось 5 дн.» into «не удалось
            # узнать срок»: keep the last known expiry and registrar; the
            # incident stays as it is.
            return r
    info = r.domain_info
    await sitestatus.update(site_id, "domain", days_left=info.days_left if info else None,
                            expiration=info.expiration_date if info else None,
                            registrar=info.registrar if info else None, unsupported=r.unsupported,
                            error=None if info and info.days_left is not None else r.error,
                            transient=r.transient)

    if manage and not r.transient and not r.unsupported:
        await apply_ladder(r, "domain", r.domain_info.days_left if r.domain_info else None,
                           DOMAIN_THRESHOLDS)
    return r


async def check_all_domains(urls: list[str], manage: bool = True) -> list[DomainResult]:
    """One lookup per owned domain, on its carrier; the snapshot is copied
    to the other sites of the domain (app.example.com shares the
    registration of example.com). Addresses without a domain of the
    owner's are marked so, without a lookup. At most DOMAIN_PARALLEL
    lookups at once."""
    groups = {g[0]: g for g in domain_groups(urls).values()}
    rest = [u for u in urls if host_of(u) and not owned_domain(host_of(u))]
    sem = asyncio.Semaphore(DOMAIN_PARALLEL)

    async def one(url: str) -> DomainResult:
        group = groups.get(url, [url])
        async with sem:
            r = await check_domain(url, manage=manage)
        snap = (await sitestatus.get(r.site_id)).get("domain")
        if snap:
            fields = {k: v for k, v in snap.items() if k != "at"}
            for other in group[1:]:
                await sitestatus.update(await get_or_create_site(other), "domain", **fields)
        return r

    return await gather_checks([*groups, *rest], one, "domain")
