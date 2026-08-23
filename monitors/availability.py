import asyncio
import contextlib
import re
import time
import logging
from urllib.parse import urlparse

import aiohttp

from config import config
from db.database import (
    get_site_by_url, save_check, save_incident, resolve_incident,
    get_recent_check_statuses,
)
from monitors.second_opinion import second_opinion_up

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
# tcp:// and ping:// probes get a shorter budget — a port either answers
# or it doesn't; there is no slow-render excuse.
PROBE_TIMEOUT_SEC = 10

# Default number of consecutive failures required before we open an incident
# (per-site override: «⚙️ Настройки сайта» → порог фейлов).
# A single transient flap should not page the user.
CONSECUTIVE_FAILURE_THRESHOLD = 2

# Keyword search looks at the first 1 MB of the page body — enough for any
# real HTML document without letting a runaway download eat memory.
KEYWORD_BODY_LIMIT = 1024 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 DevOpsBot/1.0"
)


def parse_accepted_codes(spec: str | None) -> list[tuple[int, int]] | None:
    """Parse "200-399" / "200-299,401,403" into [(lo, hi), …].

    None (or a malformed spec) means "use the default rule": any status
    below 400 is OK. Malformed specs fall back rather than crash a check —
    the UI validates on input, so this is belt-and-braces.
    """
    if not spec:
        return None
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                return None
            if not (100 <= lo <= hi <= 599):
                return None
            out.append((lo, hi))
        else:
            try:
                code = int(part)
            except ValueError:
                return None
            if not 100 <= code <= 599:
                return None
            out.append((code, code))
    return out or None


def code_accepted(status: int, ranges: list[tuple[int, int]] | None) -> bool:
    if ranges is None:
        return status < 400
    return any(lo <= status <= hi for lo, hi in ranges)


_META_CHARSET_RE = re.compile(rb'charset=["\']?([a-zA-Z0-9_-]{2,20})')


def _decode_body(raw: bytes, header_charset: str | None) -> str:
    """Decode a page body for keyword search. Header charset first; pages
    that declare encoding only in <meta charset=…> (classic windows-1251
    sites) are sniffed from the first bytes; utf-8 as the last resort."""
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


async def _probe_http(url: str, site: dict, result: dict):
    """Regular HTTP(S) check: status code (against per-site accepted codes)
    plus optional keyword / stop-phrase content check."""
    keyword = (site.get("keyword") or "").strip()
    ranges = parse_accepted_codes(site.get("accepted_codes"))
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
        ) as session:
            async with session.get(url, ssl=True, allow_redirects=True) as resp:
                result["status_code"] = resp.status
                result["response_time_ms"] = int((time.monotonic() - start) * 1000)

                if not code_accepted(resp.status, ranges):
                    result["status"] = "error"
                    result["error"] = f"HTTP {resp.status}"
                elif keyword:
                    # Content check: the page loaded, but does it actually
                    # contain the page — or an error text? Catches the
                    # classic "HTTP 200 with a white screen" blind spot.
                    # StreamReader.read(n) returns whatever is buffered, NOT
                    # n bytes — accumulate explicitly up to the 1 MB cap, or
                    # a keyword past the first TCP chunk causes false alarms.
                    chunks: list[bytes] = []
                    remaining = KEYWORD_BODY_LIMIT
                    while remaining > 0:
                        chunk = await resp.content.read(remaining)
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    raw = b"".join(chunks)
                    body = _decode_body(raw, resp.charset)
                    found = keyword.casefold() in body.casefold()
                    mode = site.get("keyword_mode") or "present"
                    if mode == "absent" and found:
                        result["status"] = "error"
                        result["error"] = f"на странице стоп-фраза «{keyword}»"
                        result["keyword_failed"] = True
                    elif mode != "absent" and not found:
                        result["status"] = "error"
                        result["error"] = f"на странице нет фразы «{keyword}»"
                        result["keyword_failed"] = True
    except aiohttp.ClientError as e:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = str(e)
    except asyncio.TimeoutError:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = "Timeout (15s)"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)


async def _probe_tcp(url: str, result: dict):
    """tcp://host:port — is the port accepting connections? Covers non-HTTP
    services: mail, SSH, databases, game servers."""
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        result["status"] = "error"
        result["error"] = "нужен порт: tcp://host:порт"
        return
    start = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT_SEC)
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    except asyncio.TimeoutError:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = f"Timeout ({PROBE_TIMEOUT_SEC}s)"
    except OSError as e:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = e.strerror or str(e)


_PING_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")


async def _probe_ping(url: str, result: dict):
    """ping://host — ICMP echo via the system ping binary (raw sockets need
    root; the setuid/cap-equipped binary is the portable way)."""
    host = urlparse(url).hostname
    if not host:
        result["status"] = "error"
        result["error"] = "нужен хост: ping://host"
        return
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-n", "-c", "1", host,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        result["status"] = "error"
        result["error"] = ("ping недоступен на хосте бота — "
                           "используй tcp://host:порт")
        return
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(), timeout=PROBE_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = f"Timeout ({PROBE_TIMEOUT_SEC}s)"
        return
    elapsed_ms = int((time.monotonic() - start) * 1000)
    out_text = out.decode("utf-8", errors="replace")
    if proc.returncode == 0:
        m = _PING_RTT_RE.search(out_text)
        result["response_time_ms"] = (
            int(float(m.group(1)) + 0.5) if m else elapsed_ms)
    else:
        result["status"] = "error"
        result["response_time_ms"] = elapsed_ms
        low = out_text.lower()
        if ("unknown host" in low or "name or service not known" in low
                or "cannot resolve" in low or "failure in name resolution" in low):
            result["error"] = "хост не резолвится (DNS)"
        elif "permission" in low or "operation not permitted" in low:
            result["error"] = "нет прав на ICMP — используй tcp://host:порт"
        else:
            result["error"] = "хост не отвечает на ping"


async def check_availability(url: str, manage: bool = True) -> dict:
    """Check if a site is available. Returns status dict.

    Handles three monitor kinds by URL scheme: http(s):// pages (status code
    + optional keyword), tcp://host:port (port connect) and ping://host
    (ICMP echo).

    manage=False — read-only mode for interactive checks and reports:
    the check row is still saved (feeds uptime/sparklines), but incidents
    and state transitions are NOT touched, so a menu tap can never consume
    an alert that the scheduled monitor should fire.
    """
    site = await get_site_by_url(url)
    site_id = site["id"]
    result = {
        "url": url,
        "site_id": site_id,
        "status": "ok",
        "status_code": None,
        "response_time_ms": None,
        "error": None,
        "incident_new": False,
        "incident_id": None,
        "recovered": False,
        "resolved_incident": None,
        # Second-opinion verdict: True = up externally, False = confirmed
        # down, None = not checked / verdict service unreachable.
        "external_ok": None,
        # True when HTTP itself succeeded but the keyword check failed —
        # the second opinion (a reachability check) is meaningless then.
        "keyword_failed": False,
    }

    scheme = urlparse(url).scheme
    try:
        if scheme == "tcp":
            await _probe_tcp(url, result)
        elif scheme == "ping":
            await _probe_ping(url, result)
        else:
            await _probe_http(url, site, result)
    except Exception as e:
        # Belt-and-braces: an exotic error (IDNA encoding of a weird host,
        # subprocess quirk) must degrade to ONE failed check, not escape
        # into check_all's gather and kill alert processing for the whole
        # batch of sites.
        result["status"] = "error"
        result["error"] = str(e) or type(e).__name__

    await save_check(
        site_id=site_id,
        check_type="availability",
        status=result["status"],
        response_time_ms=result["response_time_ms"],
        status_code=result["status_code"],
        details=result["error"],
    )

    if not manage:
        return result

    threshold = site.get("fail_threshold") or CONSECUTIVE_FAILURE_THRESHOLD
    if result["status"] == "error":
        # Only open an incident after N consecutive failures — single flaps
        # are normal on the public internet.
        recent = await get_recent_check_statuses(
            site_id, "availability", limit=threshold,
        )
        if len(recent) >= threshold and all(s == "error" for s in recent):
            # Second opinion from external nodes before paging: if the site
            # is reachable from outside, the problem is on our side of the
            # network — don't open a "site down" incident for it. Skipped
            # for keyword failures: our own fetch succeeded, so the network
            # is fine and the content problem is real.
            if config.second_opinion and not result["keyword_failed"]:
                result["external_ok"] = await second_opinion_up(url)
            if result["external_ok"] is True:
                logger.warning(
                    f"{url}: down from here but UP externally — skipping incident"
                )
            else:
                prefix = ("Bad content" if result["keyword_failed"]
                          else "Site down")
                incident_id, is_new = await save_incident(
                    site_id, "availability",
                    f"{prefix}: {result['error']}",
                    severity="critical",
                )
                result["incident_new"] = is_new
                result["incident_id"] = incident_id
    else:
        resolved = await resolve_incident(site_id, "availability")
        if resolved:
            result["recovered"] = True
            result["resolved_incident"] = resolved

    return result


async def check_all(urls: list[str], manage: bool = True) -> list[dict]:
    """Check availability for all URLs concurrently."""
    tasks = [check_availability(url, manage=manage) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
