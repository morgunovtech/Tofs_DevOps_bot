"""Availability: http(s):// pages (status code against per-site accepted
codes, optional keyword/stop-phrase, custom method/headers/body),
tcp://host:port (port connect) and ping://host (ICMP echo)."""

import asyncio
import contextlib
import json
import logging
import re
import time
from urllib.parse import urlparse

import aiohttp

from db.database import (
    get_recent_check_statuses,
    get_site_by_url,
    resolve_incident,
    save_check,
    save_incident,
)
from monitors.base import AvailabilityResult, gather_checks
from monitors.second_opinion import second_opinion_up
from services import integrations
from utils.parse import parse_accepted_codes
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
# tcp:// and ping:// probes get a shorter budget — a port either answers
# or it doesn't; there is no slow-render excuse.
PROBE_TIMEOUT_SEC = 10
# Consecutive failures before an incident opens (per-site override in
# «⚙️ Настройки сайта»). A single transient flap should not page the user.
CONSECUTIVE_FAILURE_THRESHOLD = 2
# Keyword search looks at the first 1 MB of the page body.
KEYWORD_BODY_LIMIT = 1024 * 1024
HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def code_accepted(status: int, ranges: list[tuple[int, int]] | None) -> bool:
    if ranges is None:
        return status < 400
    return any(lo <= status <= hi for lo, hi in ranges)


_META_CHARSET_RE = re.compile(rb'charset=["\']?([a-zA-Z0-9_-]{2,20})')


def decode_body(raw: bytes, header_charset: str | None) -> str:
    """Header charset first; <meta charset=…> sniffed from the first bytes
    (classic windows-1251 sites); utf-8 as the last resort."""
    enc = header_charset
    if not enc:
        m = _META_CHARSET_RE.search(raw[:4096])
        if m:
            enc = m.group(1).decode("ascii", errors="replace")
    if enc:
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, ValueError):
            pass
    return raw.decode("utf-8", errors="replace")


def request_options(site: dict) -> tuple[str, dict[str, str], str | None]:
    """(method, headers, body) for the site's HTTP probe."""
    method = (site.get("http_method") or "GET").upper()
    if method not in HTTP_METHODS:
        method = "GET"
    headers = {"User-Agent": USER_AGENT}
    raw = site.get("http_headers")
    if raw:
        try:
            custom = json.loads(raw)
            if isinstance(custom, dict):
                headers.update({str(k): str(v) for k, v in custom.items()})
        except ValueError:
            pass
    body = site.get("http_body") if method in _BODY_METHODS else None
    return method, headers, body


async def _read_capped(resp: aiohttp.ClientResponse, limit: int) -> bytes:
    # StreamReader.read(n) returns whatever is buffered, NOT n bytes —
    # accumulate explicitly up to the cap.
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = await resp.content.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


async def _probe_http(url: str, site: dict, r: AvailabilityResult):
    keyword = (site.get("keyword") or "").strip()
    ranges = parse_accepted_codes(site.get("accepted_codes"))
    method, headers, body = request_options(site)
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.request(method, url, headers=headers, data=body,
                                       ssl=True, allow_redirects=True) as resp:
                r.status_code = resp.status
                r.response_time_ms = int((time.monotonic() - start) * 1000)
                if not code_accepted(resp.status, ranges):
                    r.status, r.error = "error", f"HTTP {resp.status}"
                elif keyword and method != "HEAD":
                    # The page loaded, but does it contain the page — or an
                    # error text? Catches "HTTP 200 with a white screen".
                    raw = await _read_capped(resp, KEYWORD_BODY_LIMIT)
                    found = keyword.casefold() in decode_body(raw, resp.charset).casefold()
                    mode = site.get("keyword_mode") or "present"
                    if mode == "absent" and found:
                        r.status, r.keyword_failed = "error", True
                        r.error = f"на странице стоп-фраза «{keyword}»"
                    elif mode != "absent" and not found:
                        r.status, r.keyword_failed = "error", True
                        r.error = f"на странице нет фразы «{keyword}»"
    except TimeoutError:
        r.status, r.error = "error", f"Timeout ({int(TIMEOUT.total)}s)"
        r.response_time_ms = int((time.monotonic() - start) * 1000)
    except aiohttp.ClientError as e:
        r.status, r.error = "error", str(e) or type(e).__name__
        r.response_time_ms = int((time.monotonic() - start) * 1000)


async def _probe_tcp(url: str, r: AvailabilityResult):
    """tcp://host:port — is the port accepting connections?"""
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        r.status, r.error = "error", "нужен порт: tcp://host:порт"
        return
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT_SEC)
        r.response_time_ms = int((time.monotonic() - start) * 1000)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    except TimeoutError:
        r.status, r.error = "error", f"Timeout ({PROBE_TIMEOUT_SEC}s)"
        r.response_time_ms = int((time.monotonic() - start) * 1000)
    except OSError as e:
        r.status, r.error = "error", e.strerror or str(e)
        r.response_time_ms = int((time.monotonic() - start) * 1000)


_PING_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")


async def _probe_ping(url: str, r: AvailabilityResult):
    """ping://host — ICMP echo via the system ping binary (raw sockets need
    root; the cap-equipped binary is the portable way)."""
    host = urlparse(url).hostname
    if not host:
        r.status, r.error = "error", "нужен хост: ping://host"
        return
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-n", "-c", "1", host,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        r.status, r.error = "error", "ping недоступен на хосте бота — используй tcp://host:порт"
        return
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=PROBE_TIMEOUT_SEC)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
        r.status, r.error = "error", f"Timeout ({PROBE_TIMEOUT_SEC}s)"
        r.response_time_ms = int((time.monotonic() - start) * 1000)
        return
    elapsed_ms = int((time.monotonic() - start) * 1000)
    out_text = out.decode("utf-8", errors="replace")
    if proc.returncode == 0:
        m = _PING_RTT_RE.search(out_text)
        r.response_time_ms = int(float(m.group(1)) + 0.5) if m else elapsed_ms
        return
    r.status, r.response_time_ms = "error", elapsed_ms
    low = out_text.lower()
    if any(s in low for s in ("unknown host", "name or service not known",
                              "cannot resolve", "failure in name resolution")):
        r.error = "хост не резолвится (DNS)"
    elif "permission" in low or "operation not permitted" in low:
        r.error = "нет прав на ICMP — используй tcp://host:порт"
    else:
        r.error = "хост не отвечает на ping"


async def check_availability(url: str, manage: bool = True) -> AvailabilityResult:
    site = await get_site_by_url(url)
    r = AvailabilityResult(url=url, site_id=site["id"])
    scheme = urlparse(url).scheme
    try:
        if scheme == "tcp":
            await _probe_tcp(url, r)
        elif scheme == "ping":
            await _probe_ping(url, r)
        else:
            await _probe_http(url, site, r)
    except Exception as e:
        # An exotic error (IDNA encoding of a weird host, subprocess quirk)
        # must degrade to ONE failed check, not escape into the batch.
        r.status, r.error = "error", str(e) or type(e).__name__

    await save_check(r.site_id, "availability", r.status,
                     response_time_ms=r.response_time_ms,
                     status_code=r.status_code, details=r.error)
    if not manage:
        return r

    threshold = site.get("fail_threshold") or CONSECUTIVE_FAILURE_THRESHOLD
    if r.ok:
        resolved = await resolve_incident(r.site_id, "availability")
        if resolved:
            r.recovered, r.resolved_incident = True, resolved
        return r

    recent = await get_recent_check_statuses(r.site_id, "availability", limit=threshold)
    if len(recent) < threshold or any(s != "error" for s in recent):
        return r  # a single flap is normal on the public internet
    # Second opinion from external nodes before paging: reachable from
    # outside means the problem is on our side. Skipped for keyword
    # failures — our own fetch succeeded, so the content problem is real.
    if integrations.second_opinion_enabled() and not r.keyword_failed:
        r.external_ok = await second_opinion_up(url)
    if r.external_ok is True:
        logger.warning("%s: down from here but UP externally — skipping incident", url)
        return r
    prefix = "Bad content" if r.keyword_failed else "Site down"
    r.incident_id, r.incident_new = await save_incident(
        r.site_id, "availability", f"{prefix}: {r.error}", severity="critical")
    return r


async def check_all(urls: list[str], manage: bool = True) -> list[AvailabilityResult]:
    return await gather_checks((check_availability(u, manage=manage) for u in urls),
                               "availability")
