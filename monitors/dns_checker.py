"""DNS monitoring: snapshot A/AAAA/CNAME per host + NS/MX per registrable
domain, alert when answers change (panel misconfig, expired zone, hijack).

First observation of a host/rtype is stored silently as the baseline.
NS changes are critical (that's what a hijack looks like), and so is an MX
that disappeared (mail stops arriving); everything else is a warning
that respects mute/quiet hours.
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
    # NS moved: what a hijack looks like. MX gone: letters stop arriving.
    return {"host": host, "rtype": rtype, "old": previous, "new": current,
            "critical": rtype == "NS" or (rtype == "MX" and current == "—")}


async def _host_rtypes(host: str) -> tuple[str, ...]:
    """Behind a CNAME the A/AAAA answers are the provider's rotating pool
    (CloudFront, ELB, Cloudflare) — the CNAME target is what the owner
    controls, so it is the only thing watched there."""
    cname = await asyncio.to_thread(_resolve_sync, host, "CNAME")
    return ("CNAME",) if cname else HOST_RTYPES


async def check_all_dns(urls: list[str]) -> list[dict]:
    # A set: http:// and https:// of one host must not report a change twice.
    hosts = sorted({h for h in (host_of(u) for u in urls) if h})
    domains = {registrable_domain(h) for h in hosts}
    rtypes = await asyncio.gather(*(_host_rtypes(h) for h in hosts), return_exceptions=True)
    for h, t in zip(hosts, rtypes, strict=True):
        if isinstance(t, BaseException):
            logger.error("DNS CNAME lookup for %s failed: %r", h, t)
    # One host's unexpected error must not cost every other host its DNS check.
    rtypes = [HOST_RTYPES if isinstance(t, BaseException) else t for t in rtypes]
    tasks = [_check_record(h, t) for h, types in zip(hosts, rtypes, strict=True) for t in types]
    tasks += [_check_record(d, t) for d in sorted(domains) for t in DOMAIN_RTYPES]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    changes = []
    for r in results:
        if isinstance(r, BaseException):
            logger.error("DNS check failed: %r", r)
        elif r:
            changes.append(r)
    return changes
