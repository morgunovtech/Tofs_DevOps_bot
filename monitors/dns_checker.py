"""DNS monitoring: snapshot A/AAAA/CNAME per host + NS/MX per registrable
domain, alert when answers change (panel misconfig, expired zone, hijack).

First observation of a host/rtype is stored silently as the baseline.
NS changes are critical (that's what a hijack looks like); everything
else is a warning that respects mute/quiet hours.
"""

import asyncio
import logging

import dns.exception
import dns.resolver

from db.database import get_dns_state, set_dns_state
from utils.urls import host_of, registrable_domain

logger = logging.getLogger(__name__)

HOST_RTYPES = ("A", "AAAA", "CNAME")
DOMAIN_RTYPES = ("NS", "MX")


def _resolve_sync(host: str, rtype: str) -> list[str] | None:
    """Sorted answer strings; [] = NXDOMAIN/no answer; None = lookup failed
    (network/timeout — not evidence of change)."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 8
    try:
        answer = resolver.resolve(host, rtype)
        return sorted(r.to_text().rstrip(".") for r in answer)
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
        return []
    except dns.exception.DNSException as e:
        logger.debug("DNS %s %s failed: %s", rtype, host, e)
        return None


async def _check_record(host: str, rtype: str) -> dict | None:
    """A change dict, or None when nothing changed / lookup failed."""
    values = await asyncio.to_thread(_resolve_sync, host, rtype)
    if values is None:
        return None
    current = ", ".join(values) if values else "—"
    previous = await get_dns_state(host, rtype)
    if previous is None:
        await set_dns_state(host, rtype, current)
        logger.info("DNS baseline %s %s: %s", rtype, host, current)
        return None
    if previous == current:
        return None
    # The baseline is NOT updated here — the scheduler commits it only after
    # the alert is delivered/queued, so a failed send cannot swallow a change.
    return {"host": host, "rtype": rtype, "old": previous, "new": current,
            "critical": rtype == "NS"}


async def check_all_dns(urls: list[str]) -> list[dict]:
    hosts, domains = [], set()
    for url in urls:
        host = host_of(url)
        if host:
            hosts.append(host)
            domains.add(registrable_domain(host))
    tasks = [_check_record(h, t) for h in hosts for t in HOST_RTYPES]
    tasks += [_check_record(d, t) for d in sorted(domains) for t in DOMAIN_RTYPES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    changes = []
    for r in results:
        if isinstance(r, BaseException):
            logger.error("DNS check failed: %r", r)
        elif r:
            changes.append(r)
    return changes
