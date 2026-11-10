"""Second-opinion availability check via check-host.net (free, keyless).

Before paging about "site down" we ask external nodes to fetch the URL.
tcp:// and ping:// monitors are routed to check-tcp / check-ping.

Three-valued result: True — at least one external node reached the site
(problem is on the bot's side, don't page); False — external nodes can't
reach it either (confirmed down); None — the verdict service itself was
unreachable (fail open: alert, but mark as unconfirmed).
"""

import asyncio
import ipaddress
import json
import logging
from urllib.parse import urlparse

import aiohttp

from utils.urls import PAGE_METHODS, is_http_url

logger = logging.getLogger(__name__)

API = "https://check-host.net"
TIMEOUT = aiohttp.ClientTimeout(total=20)


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


def _is_private_host(host: str | None) -> bool:
    """Targets that must never be sent to an external checking service:
    RFC1918/loopback/link-local IPs, single-label LAN names, .local hosts."""
    if not host:
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    return "." not in host or host.endswith((".local", ".internal", ".lan"))


def endpoint_for(url: str) -> tuple[str, str] | None:
    """(check-host endpoint, host param); None = not applicable."""
    parsed = urlparse(url)
    if _is_private_host(parsed.hostname) or (is_http_url(url) and _carries_secret(url)):
        return None
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


async def second_opinion_up(url: str) -> bool | None:
    target = endpoint_for(url)
    if not target:
        return None
    endpoint, host_param = target
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT,
                                         headers={"Accept": "application/json"}) as session:
            async with session.get(f"{API}/{endpoint}",
                                   params={"host": host_param, "max_nodes": 3}) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            request_id = data.get("request_id")
            if not request_id:
                return None
            for _ in range(4):  # nodes report asynchronously; poll a few times
                await asyncio.sleep(4)
                async with session.get(f"{API}/check-result/{request_id}") as resp:
                    if resp.status != 200:
                        continue
                    results = await resp.json()
                verdicts, pending = [], 0
                for node_result in results.values():
                    if node_result is None:
                        pending += 1
                        continue
                    verdict = node_up(endpoint, node_result)
                    if verdict is not None:
                        verdicts.append(verdict)
                # One reachable node is enough to call the site UP; but
                # "confirmed down" needs ALL nodes to have reported.
                if any(verdicts):
                    return True
                if verdicts and not pending:
                    return False
            return None
    except Exception as e:
        logger.warning("Second opinion for %s failed: %s", url, e)
        return None
