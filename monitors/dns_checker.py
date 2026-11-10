"""DNS monitoring: snapshot A/AAAA/CNAME per host + NS/MX per registrable
domain, alert when answers change (panel misconfig, expired zone, hijack).

Only names of the owner's are watched: an IP has no records to watch, and
user.github.io or nas.synology.me belongs to the platform — a change of
its NS is not the owner's to act on.

First observation of a host/rtype is stored silently as the baseline.
NS changes are critical (that's what a hijack looks like), and so is an MX
that disappeared (mail stops arriving); everything else is a warning
that respects mute/quiet hours. A/AAAA behind a rotating pool (a CDN
answers with two of its twenty addresses) are not a change: a move is an
answer with none of the addresses seen in POOL_DAYS, POOL_CHECKS checks in
a row. A name that stops answering (SERVFAIL hour after hour: dead NS
servers, a DS record left after a change of NS) is said once after
FAILS_TO_SAY failures in a row — unless nothing resolves at all, which is
the bot's own network.
"""

import asyncio
import json
import logging
from datetime import UTC, date, datetime, timedelta

import dns.asyncresolver
import dns.exception
import dns.resolver

from db.database import get_dns_state, get_state, set_dns_state, set_state
from utils.urls import host_of, owned_domain

logger = logging.getLogger(__name__)

HOST_RTYPES = ("A", "AAAA", "CNAME")
DOMAIN_RTYPES = ("NS", "MX")
LOOKUP_LIFETIME = 8      # seconds for one name, retries included
DNS_SLOTS = 10           # queries in flight at once (the DNS and the mail monitor together)
POOL_DAYS = 7            # addresses seen this recently belong to the same rotating pool
POOL_CHECKS = 2          # an answer outside them must hold this many hourly checks in a row
FAILS_TO_SAY = 3         # failed lookups in a row before «не отвечает» is said (once)
_slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None
# Failed lookups in a row per (name, rtype) — in memory: a restart starts the count over.
_failures: dict[tuple[str, str], int] = {}


def failing(name: str, rtype: str) -> int:
    """How many lookups of name/rtype in a row have failed (0 = it answers)."""
    return _failures.get((name, rtype), 0)


def _dns_slots() -> asyncio.Semaphore:
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(DNS_SLOTS))
    return _slots[1]


def _resolver() -> dns.asyncresolver.Resolver:
    """dnspython's asyncio resolver: no threads — a domain whose NS servers
    are dead no longer holds the pool where every HTTP check resolves its
    site, and a stop cancels the query instead of waiting it out."""
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = LOOKUP_LIFETIME
    return resolver


async def query(name: str, rtype: str):
    """The dnspython answer; [] = NXDOMAIN/no answer; None = lookup failed
    (network/timeout — not evidence of change)."""
    async with _dns_slots():
        try:
            return await _resolver().resolve(name, rtype)
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            return []
        except dns.exception.DNSException as e:
            logger.debug("DNS %s %s failed: %s", rtype, name, e)
            return None


async def resolve(host: str, rtype: str) -> list[str] | None:
    """Sorted answer strings; [] = NXDOMAIN/no answer; None = lookup failed."""
    answer = await query(host, rtype)
    return None if answer is None else sorted(r.to_text().rstrip(".") for r in answer)


async def _pool_moved(host: str, rtype: str, values: list[str], previous: str) -> bool:
    """A/AAAA behind a CDN or a load balancer rotate within a pool: two of
    twenty addresses per answer. The pool is every address seen in the
    last POOL_DAYS (the baseline always among them); an answer that shares
    none of them is a move only when it holds POOL_CHECKS checks in a row.
    State: JSON under dns_pool:<rtype>:<host> in bot_state, no migration."""
    key = f"dns_pool:{rtype}:{host}"
    raw = await get_state(key)
    try:
        pool = json.loads(raw or "{}")
    except ValueError:
        pool = {}
    if not isinstance(pool, dict):
        pool = {}
    today = datetime.now(UTC).date()
    seen: dict[str, str] = {}
    for ip, day in (pool.get("seen") or {}).items():
        try:
            if today - date.fromisoformat(day) < timedelta(days=POOL_DAYS):
                seen[ip] = day
        except (TypeError, ValueError):
            continue
    seen.update(dict.fromkeys((ip for ip in previous.split(", ") if ip != "—"), today.isoformat()))
    outside = not set(values) & set(seen)
    strikes = (pool.get("strikes") or 0) + 1 if outside else 0
    moved = strikes >= POOL_CHECKS
    if not outside or moved:
        seen.update(dict.fromkeys(values, today.isoformat()))
        strikes = 0
    new = json.dumps({"seen": dict(sorted(seen.items())), "strikes": strikes})
    if new != raw:
        await set_state(key, new)      # at most once a day for a steady answer
    return moved


async def _check_record(host: str, rtype: str) -> dict | None:
    """A change dict, or None when nothing changed / lookup failed."""
    values = await resolve(host, rtype)
    if values is None:
        n = _failures[(host, rtype)] = failing(host, rtype) + 1
        if n == FAILS_TO_SAY:
            # Said once; the baseline stays — it is the last known good answer.
            return {"host": host, "rtype": rtype, "old": await get_dns_state(host, rtype) or "—",
                    "new": "не отвечает", "critical": rtype == "NS", "failing": True}
        return None
    _failures.pop((host, rtype), None)
    current = ", ".join(values) if values else "—"
    previous = await get_dns_state(host, rtype)
    if previous is None:
        await set_dns_state(host, rtype, current)
        logger.info("DNS baseline %s %s: %s", rtype, host, current)
        return None
    if rtype in ("A", "AAAA") and values:
        # Asked even when nothing changed: an answer back in the pool resets the count.
        moved = await _pool_moved(host, rtype, values, previous)
        if previous == current or not moved:
            return None
    elif previous == current:
        return None
    # A record that vanished (values == []) is reported at once.
    # The baseline is NOT updated here — the scheduler commits it only after
    # the alert is delivered/queued, so a failed send cannot swallow a change.
    # NS moved: what a hijack looks like. MX gone: letters stop arriving.
    return {"host": host, "rtype": rtype, "old": previous, "new": current,
            "critical": rtype == "NS" or (rtype == "MX" and current == "—")}


async def _host_rtypes(host: str) -> tuple[str, ...]:
    """Behind a CNAME the A/AAAA answers are the provider's rotating pool
    (CloudFront, ELB, Cloudflare) — the CNAME target is what the owner
    controls, so it is the only thing watched there."""
    cname = await resolve(host, "CNAME")
    return ("CNAME",) if cname else HOST_RTYPES


async def check_all_dns(urls: list[str]) -> list[dict]:
    # A set: http:// and https:// of one host must not report a change twice.
    # Names of the owner's only: an IP has no records, x.github.io is the platform's.
    hosts = sorted({h for h in (host_of(u) for u in urls) if h and owned_domain(h)})
    domains = {owned_domain(h) for h in hosts}
    rtypes = await asyncio.gather(*(_host_rtypes(h) for h in hosts), return_exceptions=True)
    for h, t in zip(hosts, rtypes, strict=True):
        if isinstance(t, BaseException):
            logger.error("DNS CNAME lookup for %s failed: %r", h, t)
    # One host's unexpected error must not cost every other host its DNS check.
    rtypes = [HOST_RTYPES if isinstance(t, BaseException) else t for t in rtypes]
    pairs = [(h, t) for h, types in zip(hosts, rtypes, strict=True) for t in types]
    pairs += [(d, t) for d in sorted(domains) for t in DOMAIN_RTYPES]
    results = await asyncio.gather(*(_check_record(n, t) for n, t in pairs), return_exceptions=True)
    changes = []
    for r in results:
        if isinstance(r, BaseException):
            logger.error("DNS check failed: %r", r)
        elif r:
            changes.append(r)
    if pairs and all(failing(n, t) for n, t in pairs):
        # Not one name answers: that is the bot's own resolver or network, not
        # the sites — one line in the log, no alert about anyone's DNS.
        logger.warning("DNS watch is blind: none of %d lookups answered — the bot's own network", len(pairs))
        changes = [c for c in changes if not c.get("failing")]
    return changes
