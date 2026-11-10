"""Second-opinion availability check via check-host.net (free, keyless).

Before paging about "site down" we ask external nodes to fetch the URL.
tcp:// and ping:// monitors are routed to check-tcp / check-ping.

Three-valued verdict: True — most external nodes reached the site (the
problem is on the bot's side or in its region: no page); False — none of
them did (confirmed down); None — no verdict: the service was unreachable
or slow, or the nodes disagree (fail open: alert, marked unconfirmed).
One node opening the site is not «everyone else»: the nodes are picked at
random, usually outside the audience's region, so a split is an alert.

Beside the three random nodes the caller may name the country of the
owner's audience: the node there is asked too, and if it cannot open the
site the verdict is never True — the audience sees the same.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp

from utils.net import is_private_target, resolves_private
from utils.urls import PAGE_METHODS, is_http_url

logger = logging.getLogger(__name__)

API = "https://check-host.net"
# One request to the service; monitors/region.py takes the same.
TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
# The whole verdict — start, polls, the region node — fits in this: the tick
# that asks runs once a minute.
BUDGET_SEC = 30
# Pauses before each poll of /check-result: nodes answer within seconds; a
# hung site keeps them busy for longer.
POLL_PAUSES = (2, 4, 8)
RANDOM_NODES = 3


def repeatable(site: dict) -> bool:
    """check-host.net sends a bare GET. Its verdict means something only for
    a monitor whose own request is a GET/HEAD without headers of its own: a
    POST to an API, or a request with a token, cannot be repeated from
    outside (a GET there answers 405 or 401, «down» for a working API) — so
    no second opinion for it; the fail threshold still filters flaps."""
    if not is_http_url(site.get("url") or ""):
        return True                      # tcp/ping are repeated as they are
    if _carries_secret(site.get("url") or ""):
        return False
    if (site.get("http_method") or "GET").upper() not in PAGE_METHODS:
        return False
    try:
        headers = json.loads(site.get("http_headers") or "{}")
    except ValueError:
        headers = {}
    return not headers


def _carries_secret(url: str) -> bool:
    """A query or a user:password in the address may be a token — such an
    address never goes to an outside service."""
    try:
        p = urlparse(url)
        return bool(p.query or p.username or p.password)
    except ValueError:
        return True


def endpoint_for(url: str) -> tuple[str, str] | None:
    """(check-host endpoint, host param); None = not applicable: an internal
    address (utils.net.is_private_target — the one rule for every third
    party) or an address that may carry a secret never leaves the bot."""
    if is_private_target(url) or (is_http_url(url) and _carries_secret(url)):
        return None
    parsed = urlparse(url)
    if parsed.scheme == "tcp":
        if not parsed.hostname or not parsed.port:
            return None
        return "check-tcp", f"{parsed.hostname}:{parsed.port}"
    if parsed.scheme == "ping":
        return ("check-ping", parsed.hostname) if parsed.hostname else None
    return "check-http", url


def node_up(endpoint: str, node_result) -> bool | None:
    """One node's verdict; None when the shape is unparseable.
      check-http: [[1, 0.12, "OK", "200", "1.2.3.4"]]
      check-tcp:  [{"address": "1.2.3.4", "time": 0.03}] or [{"error": "…"}]
      check-ping: [[["OK", 0.05, "1.2.3.4"], …]]  (4 pings)"""
    try:
        first = node_result[0]
        if endpoint == "check-http":
            return bool(first[0] == 1) if isinstance(first, (list, tuple)) and first else None
        if endpoint == "check-tcp":
            return ("error" not in first and "time" in first) if isinstance(first, dict) else None
        if endpoint == "check-ping":
            return any(p and p[0] == "OK" for p in first) if isinstance(first, (list, tuple)) else None
    except (TypeError, IndexError, KeyError):
        return None
    return None


@dataclass
class Verdict:
    up: bool | None = None
    ups: int = 0             # nodes that opened it
    total: int = 0           # nodes asked (answered + still silent)
    # The node in the owner's country was asked and could not open the site:
    # whatever the random nodes say, the audience does not see the page.
    region_down: bool = False


def tally(endpoint: str, results: dict | None) -> tuple[int, int, int]:
    """(ups, answered, pending) over one /check-result answer."""
    ups = answered = pending = 0
    for node_result in (results or {}).values():
        if node_result is None:
            pending += 1
            continue
        verdict = node_up(endpoint, node_result)
        if verdict is not None:
            answered += 1
            ups += verdict
    return ups, answered, pending


def decide(ups: int, answered: int, pending: int) -> bool | None:
    """True when most nodes opened it (pending ones count against); False
    when every node answered and none did; None otherwise — a split or
    silent nodes is no verdict."""
    if ups * 2 > answered + pending:
        return True
    if not pending and answered and not ups:
        return False
    return None


def region_node(country: str | None) -> str | None:
    """check-host node name for a country code: «ru» → ru1.node.check-host.net."""
    country = (country or "").strip().lower()
    return f"{country}1.node.check-host.net" if country.isalpha() and len(country) == 2 else None


async def _target(url: str) -> tuple[str, str] | None:
    """What goes to check-host for this address, None when nothing may: the
    one gate of every entry (second_opinion, second_opinion_up). A
    public-looking name that resolves to a LAN address here (nas.home.example.com
    → 192.168.1.10) stays home too: the nodes would judge their own network
    (a false «up») and learn the name. One DNS query, and only once the
    failure threshold is reached."""
    target = endpoint_for(url)
    if not target or await resolves_private(url):
        return None
    return target


async def second_opinion(url: str, region: str | None = None) -> Verdict:
    """Ask the external nodes; `region` is the check-host country code of
    the audience's node to ask as well (None — the random nodes only)."""
    target = await _target(url)
    if not target:
        return Verdict()
    try:
        async with asyncio.timeout(BUDGET_SEC):
            return await _ask(*target, region_node(region))
    except TimeoutError:
        logger.warning("Second opinion for %s: no verdict in %s s", url, BUDGET_SEC)
    except Exception as e:
        logger.warning("Second opinion for %s failed: %s", url, e)
    return Verdict()


async def _ask(endpoint: str, host_param: str, node: str | None) -> Verdict:
    async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"Accept": "application/json"}) as session:
        verdict, region_up = await asyncio.gather(
            _ask_random_nodes(session, endpoint, host_param),
            _ask_region_node(session, endpoint, host_param, node))
    if region_up is False:
        verdict.region_down = True
        if verdict.up is True:
            verdict.up = None            # open elsewhere, not where the visitors are
    return verdict


async def _start(session: aiohttp.ClientSession, endpoint: str, params: dict) -> str | None:
    """Begin a check; the request id to poll, None when the service refuses."""
    async with session.get(f"{API}/{endpoint}", params=params) as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    if not isinstance(data, dict):
        return None
    return data.get("request_id") or None


async def _results(session: aiohttp.ClientSession, request_id: str) -> dict | None:
    async with session.get(f"{API}/check-result/{request_id}") as resp:
        if resp.status != 200:
            return None
        data = await resp.json()
    return data if isinstance(data, dict) else None


async def _ask_random_nodes(session: aiohttp.ClientSession, endpoint: str, host_param: str) -> Verdict:
    """Three nodes of the service's choice; no verdict when it misbehaves
    (an error page instead of JSON, a refused request)."""
    try:
        request_id = await _start(session, endpoint, {"host": host_param, "max_nodes": RANDOM_NODES})
        if not request_id:
            return Verdict()
        ups = answered = pending = 0
        for pause in POLL_PAUSES:
            await asyncio.sleep(pause)
            results = await _results(session, request_id)
            if results is None:
                continue
            ups, answered, pending = tally(endpoint, results)
            up = decide(ups, answered, pending)
            if up is not None or not pending:
                return Verdict(up, ups, answered + pending)
        return Verdict(None, ups, answered + pending)
    except Exception as e:
        logger.warning("Second opinion for %s failed: %s", host_param, e)
        return Verdict()


async def _ask_region_node(session: aiohttp.ClientSession, endpoint: str, host_param: str,
                           node: str | None) -> bool | None:
    """The one node in the audience's country: True/False when it answered,
    None when there is no such node, it stayed silent or the request failed —
    the random nodes decide alone then."""
    if not node:
        return None
    try:
        request_id = await _start(session, endpoint, {"host": host_param, "node": node})
        if not request_id:
            return None
        for pause in POLL_PAUSES:
            await asyncio.sleep(pause)
            results = await _results(session, request_id)
            if results is None:
                continue
            ups, answered, pending = tally(endpoint, results)
            if answered:
                return ups > 0
            if not pending:
                return None              # no such node: an empty answer
    except Exception as e:
        logger.debug("Region node %s gave no verdict: %s", node, e)
    return None


async def second_opinion_up(url: str) -> bool | None:
    """Only the verdict — for checks that need no node counts (mail)."""
    return (await second_opinion(url)).up
