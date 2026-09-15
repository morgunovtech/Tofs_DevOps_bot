"""Domain registration expiry via RDAP (see monitors.rdap) with an alert
ladder that starts earlier than SSL — registrars are slower."""

import logging

from db.database import get_or_create_site, save_check
from monitors import rdap
from monitors.base import DomainResult, gather_checks
from monitors.ladder import apply_ladder
from utils.urls import host_of, registrable_domain

logger = logging.getLogger(__name__)

DOMAIN_THRESHOLDS = [0, 1, 3, 7, 14, 30]


async def check_domain(url: str, manage: bool = True) -> DomainResult:
    host = host_of(url)
    site_id = await get_or_create_site(url)
    domain = registrable_domain(host) if host else url
    r = DomainResult(url=url, site_id=site_id, domain=domain)
    if not host:
        r.status, r.error, r.transient = "warning", "Некорректный URL", True
        await save_check(site_id, "domain", r.status, details=r.error)
        return r
    try:
        info = await rdap.lookup(domain)
        r.domain_info = info
        days = info.days_left
        if days is None:
            r.unsupported = True
            r.error = "срок регистрации не сообщается"
        elif days < 0:
            r.status, r.error = "error", f"Domain expired {abs(days)} days ago!"
        elif days <= 7:
            r.status, r.error = "critical", f"Domain expires in {days} days!"
        elif days <= 30:
            r.status, r.error = "warning", f"Domain expires in {days} days"
    except rdap.RdapUnsupported as e:
        r.unsupported, r.error = True, str(e)
    except rdap.DomainLookupError as e:
        # Lookup failures are flaky — log, don't manage incidents on them.
        r.status, r.error, r.transient = "warning", f"Lookup failed: {e}", True
    except Exception as e:
        r.status, r.error, r.transient = "warning", f"Lookup failed: {e}", True

    if r.domain_info and r.domain_info.days_left is not None:
        details = r.error or f"OK, {r.domain_info.days_left} days left"
    else:
        details = r.error
    await save_check(site_id, "domain", r.status, details=details)

    if manage and not r.transient and not r.unsupported:
        await apply_ladder(r, "domain", r.domain_info.days_left if r.domain_info else None,
                           DOMAIN_THRESHOLDS)
    return r


async def check_all_domains(urls: list[str], manage: bool = True) -> list[DomainResult]:
    """One lookup per registrable domain: app.example.com and example.com
    share the registration."""
    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        host = host_of(url)
        if not host:
            continue
        domain = registrable_domain(host)
        if domain not in seen:
            seen.add(domain)
            unique.append(url)
    return await gather_checks((check_domain(u, manage=manage) for u in unique), "domain")
