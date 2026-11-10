"""Is the bot's own network up? Asked before a failed check is blamed on a
site: when the bot's server loses its uplink or its DNS resolver, every site
«goes down» at once — and the external second opinion is unreachable for the
same reason.

Canaries come from NET_CANARIES: an IP address gets a TCP connect on :443, a
name gets a DNS lookup. The network counts as up when one IP answers AND one
name resolves — IPs alone would pass with a broken resolver. An empty
NET_CANARIES switches the self-check off.

Fail-open: a site that answered (any HTTP code, a TCP connect, a ping reply)
within the last ANSWER_FRESH_SEC proves the network is up whatever the
canaries say — a firewall that cuts 1.1.1.1:443 must not silence real
alerts. If the canaries never answer while the sites do, the self-check
switches itself off for this process (check_own_network_at_start)."""

import asyncio
import ipaddress
import logging
import time
from datetime import UTC, datetime

from config import config
from db.database import get_state, set_state
from utils.text import parse_iso_utc

logger = logging.getLogger(__name__)

PROBE_SEC = 3
# One verdict per tick: the checks of a tick finish within seconds of each
# other, the next tick (a minute later) must ask again.
CACHE_SEC = 30
ANSWER_FRESH_SEC = 120
OUTAGE_KEY = "net_outage_since"

_lock = asyncio.Lock()
_checked_at: float | None = None
_last_ok = True
_last_answer_at: float | None = None
_disabled = False


def reset():
    """Forget the cached verdict (tests; a config change)."""
    global _checked_at, _last_answer_at, _disabled
    _checked_at = None
    _last_answer_at = None
    _disabled = False


def note_answer():
    """Some check just got an answer from the network: it is up."""
    global _last_answer_at
    _last_answer_at = time.monotonic()


def recently_answered() -> bool:
    return _last_answer_at is not None and time.monotonic() - _last_answer_at < ANSWER_FRESH_SEC


def disable():
    """Canaries unreachable while the sites answer: never blame the network."""
    global _disabled
    _disabled = True


def enabled() -> bool:
    return not _disabled and bool(config.net_canaries)


def _split(canaries) -> tuple[list[str], list[str]]:
    ips, names = [], []
    for c in canaries:
        try:
            ipaddress.ip_address(c)
        except ValueError:
            names.append(c)
        else:
            ips.append(c)
    return ips, names


async def _tcp(ip: str) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, 443), PROBE_SEC)
    except (OSError, TimeoutError):
        return False
    writer.close()
    return True


async def _resolves(name: str) -> bool:
    try:
        await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(name, 443), PROBE_SEC)
    except (OSError, TimeoutError):      # socket.gaierror (EAI_AGAIN …) is an OSError
        return False
    return True


async def probe() -> bool:
    """Ask the canaries now, without the cache."""
    ips, names = _split(config.net_canaries)
    if not ips and not names:
        return True
    answers = await asyncio.gather(*(_tcp(ip) for ip in ips), *(_resolves(n) for n in names))
    tcp_ok = not ips or any(answers[:len(ips)])
    dns_ok = not names or any(answers[len(ips):])
    if not (tcp_ok and dns_ok):
        logger.warning("Own network check failed: tcp=%s dns=%s (NET_CANARIES)", tcp_ok, dns_ok)
    return tcp_ok and dns_ok


async def own_network_ok() -> bool:
    """probe(), at most once per CACHE_SEC: a tick with 20 failing sites asks
    once. A fresh answer from any site makes the question moot."""
    global _checked_at, _last_ok
    if not enabled() or recently_answered():
        return True
    async with _lock:
        if _checked_at is None or time.monotonic() - _checked_at >= CACHE_SEC:
            _last_ok = await probe()
            _checked_at = time.monotonic()
        return _last_ok


async def outage_since() -> datetime | None:
    """When the current «no network on the bot's server» episode began."""
    return parse_iso_utc(await get_state(OUTAGE_KEY))


async def mark_outage():
    if not await get_state(OUTAGE_KEY):
        await set_state(OUTAGE_KEY, datetime.now(UTC).isoformat(timespec="seconds"))
        logger.warning("The bot's own network is down: site checks give no verdict until it is back")


async def end_outage():
    await set_state(OUTAGE_KEY, None)
