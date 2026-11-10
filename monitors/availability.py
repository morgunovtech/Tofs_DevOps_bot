"""Availability: http(s):// pages and API checks (status code against
per-site accepted codes, optional keyword/stop-phrase, custom
method/headers/body), tcp://host:port (port connect) and ping://host
(ICMP echo). For an API check (utils.urls.is_api_check) this is the only
monitor that runs."""

import asyncio
import contextlib
import json
import logging
import re
import sqlite3
import time
from collections import deque
from urllib.parse import urlparse

import aiohttp

from db.database import (
    get_active_incidents,
    get_recent_check_statuses,
    get_site_by_url,
    get_state,
    resolve_incident,
    save_check,
    save_incident,
    set_state,
)
from monitors.base import AvailabilityResult, decode_body, gather_checks, read_capped
from monitors.second_opinion import repeatable, second_opinion_up
from services import health, hosting, integrations, sitestatus
from utils.parse import parse_accepted_codes
from utils.redact import without_query
from utils.urls import USER_AGENT, is_api_check

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
# tcp:// and ping:// probes get a shorter budget — a port either answers
# or it doesn't; there is no slow-render excuse.
PROBE_TIMEOUT_SEC = 10
# Consecutive failures before an incident opens (per-site override in
# «⚙️ Настройки сайта»). A single transient flap should not page the user.
CONSECUTIVE_FAILURE_THRESHOLD = 2
# The last statuses per site, newest last: the failure threshold keeps
# working while the database refuses to store the history.
RECENT_IN_MEMORY = 50
_recent: dict[int, deque[str]] = {}
# Sites alerted while the database refused writes: no incident row exists,
# so «new» and «снова открывается» are decided here.
_down_without_db: set[int] = set()
# Keyword search looks at the first 1 MB of the page body.
KEYWORD_BODY_LIMIT = 1024 * 1024
HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def code_accepted(status: int, ranges: list[tuple[int, int]] | None) -> bool:
    if ranges is None:
        return status < 400
    return any(lo <= status <= hi for lo, hi in ranges)




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
    if body and not any(k.lower() == "content-type" for k in headers) and _is_json(body):
        # aiohttp sends a str as text/plain, and a JSON API answers that with
        # 400 «expected an object» — a false outage.
        headers["Content-Type"] = "application/json"
    return method, headers, body


def _is_json(body: str) -> bool:
    if not body.lstrip().startswith(("{", "[")):
        return False
    try:
        json.loads(body)
    except ValueError:
        return False
    return True


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
                r.hosting = hosting.detect_hosting(resp.headers)
                if not code_accepted(resp.status, ranges):
                    r.status, r.error = "error", f"HTTP {resp.status}"
                elif keyword and method != "HEAD":
                    # The page loaded, but does it contain the page — or an
                    # error text? Catches "HTTP 200 with a white screen"
                    # (and an API answering 200 with an error object).
                    raw = await read_capped(resp, KEYWORD_BODY_LIMIT)
                    found = keyword.casefold() in decode_body(raw, resp.charset).casefold()
                    mode = site.get("keyword_mode") or "present"
                    where = "в ответе" if r.api else "на странице"
                    if mode == "absent" and found:
                        r.status, r.keyword_failed = "error", True
                        r.error = f"{where} стоп-фраза «{keyword}»"
                    elif mode != "absent" and not found:
                        r.status, r.keyword_failed = "error", True
                        r.error = f"{where} нет фразы «{keyword}»"
    except TimeoutError:
        r.status, r.error = "error", f"Timeout ({int(TIMEOUT.total)}s)"
        r.response_time_ms = int((time.monotonic() - start) * 1000)
    except aiohttp.ClientError as e:
        r.status, r.error = "error", _error_text(e)
        r.response_time_ms = int((time.monotonic() - start) * 1000)


def _error_text(e: aiohttp.ClientError) -> str:
    """aiohttp puts the whole request URL, query and all, into str() of its
    response errors; r.error goes to the history, the alert and the helpers'
    copy — an API check's token must not."""
    if isinstance(e, aiohttp.TooManyRedirects):
        return "слишком много перенаправлений (редирект по кругу)"
    if isinstance(e, aiohttp.ClientResponseError):
        return f"HTTP {e.status}" if e.status else type(e).__name__
    return without_query(str(e)) or type(e).__name__


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
    r = AvailabilityResult(url=url, site_id=site["id"], api=is_api_check(url, site.get("http_method")))
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

    memory = _recent.setdefault(r.site_id, deque(maxlen=RECENT_IN_MEMORY))
    memory.append(r.status)
    # While writes fail, do not even try: under «database is locked» each one
    # holds the only DB thread for busy_timeout (5 s). run_alive probes it.
    stored = not health.db_failing() and await _write(save_check(
        r.site_id, "availability", r.status, response_time_ms=r.response_time_ms,
        status_code=r.status_code, details=r.error))
    # Remember the hosting so alerts can link to its panel even when the
    # failing response carries no headers at all.
    if r.hosting:
        if stored and await get_state(f"hosting:{r.site_id}") != r.hosting:
            await _write(set_state(f"hosting:{r.site_id}", r.hosting))
            await _write(sitestatus.set_field(r.site_id, "hosting", r.hosting))
    else:
        r.hosting = await get_state(f"hosting:{r.site_id}")
    if stored:
        await _write(sitestatus.update(r.site_id, "avail", status=r.status, ms=r.response_time_ms,
                                       error=r.error, code=r.status_code))
    if not manage:
        return r

    threshold = site.get("fail_threshold") or CONSECUTIVE_FAILURE_THRESHOLD
    if r.ok:
        if r.site_id in _down_without_db:            # alerted from memory: say it is back
            _down_without_db.discard(r.site_id)
            r.recovered = True
        try:
            resolved = await resolve_incident(r.site_id, "availability")
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)                 # closed by a check once writes work
            resolved = None
        if resolved:
            r.recovered, r.resolved_incident = True, resolved
        return r

    if stored:
        recent = await get_recent_check_statuses(r.site_id, "availability", limit=threshold)
    else:
        recent = list(memory)[-threshold:]
    if len(recent) < threshold or any(s != "error" for s in recent):
        return r  # a single flap is normal on the public internet
    # Second opinion from external nodes before paging: reachable from
    # outside means the problem is on our side. Skipped for keyword
    # failures — our own fetch succeeded, so the content problem is real —
    # and for a request check-host cannot repeat (a POST, own headers).
    if integrations.second_opinion_enabled() and not r.keyword_failed and repeatable(site):
        r.external_ok = await second_opinion_up(url)
    if r.external_ok is True:
        logger.warning("%s: down from here but UP externally — skipping incident", url)
        return r
    prefix = "Bad content" if r.keyword_failed else "Site down"
    try:
        r.incident_id, r.incident_new = await save_incident(
            r.site_id, "availability", f"{prefix}: {r.error}", severity="critical")
        if r.site_id in _down_without_db:            # said from memory already: the row catches up
            _down_without_db.discard(r.site_id)
            r.incident_new = False
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
        # The alert matters more than its row: it is new unless already said —
        # by an earlier tick from memory or by an incident opened before the failure.
        open_before = any(i["site_id"] == r.site_id and i["check_type"] == "availability"
                          for i in await get_active_incidents())
        r.incident_new = r.site_id not in _down_without_db and not open_before
        _down_without_db.add(r.site_id)
    return r


async def _write(coro) -> bool:
    """Await one write; a database that refuses it (full disk, «database is
    locked») is noted in services.health instead of killing the check."""
    try:
        await coro
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
        return False
    return True


async def check_all(urls: list[str], manage: bool = True) -> list[AvailabilityResult]:
    return await gather_checks(urls, check_availability, "availability", manage=manage)
