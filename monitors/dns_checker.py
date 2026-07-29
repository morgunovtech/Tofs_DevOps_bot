"""
DNS monitoring: snapshot A/AAAA/CNAME per host + NS/MX per registrable domain,
alert when answers change (panel misconfig, expired zone, hijack).

First observation of a host/rtype is stored silently as the baseline.
NS changes are treated as critical (that's what a hijack looks like);
everything else is a warning that respects mute/quiet hours.
"""

import asyncio
import logging
from urllib.parse import urlparse

import dns.resolver
import dns.exception

from db.database import get_dns_state, set_dns_state

logger = logging.getLogger(__name__)

HOST_RTYPES = ("A", "AAAA", "CNAME")
DOMAIN_RTYPES = ("NS", "MX")


def _registrable(host: str) -> str:
    # Same naive "last two labels" heuristic as elsewhere in the project —
    # fine for example.com, wrong for multi-part public suffixes (co.uk).
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) > 2 else host


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
        logger.debug(f"DNS {rtype} {host} failed: {e}")
        return None


async def _check_record(host: str, rtype: str) -> dict | None:
    """Compare current answers with the stored baseline.
    Returns a change dict or None when nothing changed / lookup failed."""
    loop = asyncio.get_event_loop()
    values = await loop.run_in_executor(None, _resolve_sync, host, rtype)
    if values is None:
        return None
    current = ", ".join(values) if values else "—"
    previous = await get_dns_state(host, rtype)
    if previous is None:
        await set_dns_state(host, rtype, current)
        logger.info(f"DNS baseline {rtype} {host}: {current}")
        return None
    if previous == current:
        return None
    await set_dns_state(host, rtype, current)
    return {
        "host": host,
        "rtype": rtype,
        "old": previous,
        "new": current,
        "critical": rtype == "NS",
    }


async def check_all_dns(urls: list[str]) -> list[dict]:
    """Returns the list of detected changes across all hosts/domains."""
    hosts = []
    domains = set()
    for url in urls:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            continue
        hosts.append(host)
        domains.add(_registrable(host))

    tasks = []
    for host in hosts:
        for rtype in HOST_RTYPES:
            tasks.append(_check_record(host, rtype))
    for domain in sorted(domains):
        for rtype in DOMAIN_RTYPES:
            tasks.append(_check_record(domain, rtype))

    results = await asyncio.gather(*tasks)
    return [r for r in results if r]
