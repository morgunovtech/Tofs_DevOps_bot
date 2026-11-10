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
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse

import aiohttp

from config import config
from db.database import (
    get_active_incidents,
    get_last_incident,
    get_recent_checks,
    get_site_by_url,
    get_state,
    last_failed_check_at,
    relabel_recent_failures,
    reopen_incident,
    resolve_incident,
    save_incident,
    set_state,
    span_cap_sec,
)
from monitors import netcheck, region
from monitors.base import AvailabilityResult, decode_body, gather_checks, phrase_found, read_capped
from monitors.second_opinion import repeatable, second_opinion
from services import health, hosting, humanize, integrations, settings, sitestatus
from utils.net import PRIVATE_REFUSED, owner_connector, refused_private
from utils.parse import parse_accepted_codes
from utils.redact import without_query
from utils.text import parse_iso_utc, parse_sqlite_utc
from utils.urls import USER_AGENT, host_of, is_api_check

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
# tcp:// and ping:// probes get a shorter budget — a port either answers
# or it doesn't; there is no slow-render excuse.
PROBE_TIMEOUT_SEC = 10
# Consecutive failures before an incident opens (per-site override in
# «⚙️ Настройки сайта»). A single transient flap should not page the user.
CONSECUTIVE_FAILURE_THRESHOLD = 2
# A site that fails every other check never makes «N подряд»: it also counts
# as down when at least half of the last 2×N (at least 6) planned checks
# failed — and at least N of them.
FLAP_WINDOW_MIN_CHECKS = 6
# Up only after two planned successes in a row: one good answer between
# failures is not a recovery to ring about. A site checked rarely (every
# half hour or less often) is up on the first: waiting another interval
# would call it down for an hour after it is back.
RECOVERY_STREAK = 2
RECOVERY_SINGLE_FROM_MIN = 30
# An incident closed less than this ago is reopened, not opened anew: no
# second ring, no second rebuild.
REOPEN_WITHIN_MIN = 30
# The last planned statuses per site, newest last: the failure threshold
# keeps working while the database refuses to store the history.
RECENT_IN_MEMORY = 50
_recent: dict[int, deque[str]] = {}
# Sites alerted while the database refused writes: no incident row exists,
# so «new» and «снова открывается» are decided here.
_down_without_db: set[int] = set()
# Keyword search looks at the first 1 MB of the page body.
KEYWORD_BODY_LIMIT = 1024 * 1024
HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
_BODY_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Hops a check with its own headers or body follows by hand (aiohttp's own limit).
MAX_REDIRECTS = 10


def code_accepted(status: int, ranges: list[tuple[int, int]] | None) -> bool:
    if ranges is None:
        return status < 400
    return any(lo <= status <= hi for lo, hi in ranges)


def share_window(threshold: int) -> int:
    return max(FLAP_WINDOW_MIN_CHECKS, 2 * threshold)


def down_verdict(statuses: list[str], threshold: int) -> tuple[int, int] | bool:
    """statuses — the latest planned checks, newest first. False — not down;
    True — «threshold» failures in a row; (failed, window) — by the share."""
    if len(statuses) >= threshold and all(s == "error" for s in statuses[:threshold]):
        return True
    window = statuses[:share_window(threshold)]
    failed = window.count("error")
    if len(window) == share_window(threshold) and failed >= max(threshold, len(window) // 2):
        return failed, len(window)
    return False


def recovery_streak(interval_min: int) -> int:
    """Planned successes in a row that close an incident, by the site's interval."""
    return 1 if interval_min >= RECOVERY_SINGLE_FROM_MIN else RECOVERY_STREAK


def up_verdict(statuses: list[str], streak: int = RECOVERY_STREAK) -> bool:
    """`streak` planned successes in a row, newest first."""
    return len(statuses) >= streak and all(s == "ok" for s in statuses[:streak])


def _first_ok_at(recent: list[dict]) -> datetime | None:
    """When the leading run of successes began — the end of the downtime."""
    moment = None
    for check in recent:
        if check["status"] != "ok":
            break
        moment = parse_sqlite_utc(check.get("checked_at")) or moment
    return moment


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


class RedirectStopped(Exception):
    """A check with its own headers or body was redirected where they must
    not go (another origin), or round and round."""


def _stays_home(src: str, dst: str) -> bool:
    """The hop keeps the request with the same server: the same host and
    port (http:// → https:// of one host on the standard ports is the same
    server too), and never https:// → http://."""
    try:
        a, b = urlparse(src), urlparse(dst)
        if b.scheme not in _DEFAULT_PORTS or not host_of(dst) or host_of(src) != host_of(dst):
            return False
        if a.scheme == "https" and b.scheme == "http":
            return False
        pa, pb = a.port or _DEFAULT_PORTS.get(a.scheme), b.port or _DEFAULT_PORTS[b.scheme]
    except ValueError:
        return False
    return pa == pb or (a.scheme, pa, pb) == ("http", 80, 443)


def _netloc(url: str) -> str:
    """host, or host:port when the port is explicit — never the login."""
    try:
        p = urlparse(url)
        return f"{host_of(url)}:{p.port}" if p.port else host_of(url)
    except ValueError:
        return host_of(url)


async def _request_at_home(session: aiohttp.ClientSession, method: str, url: str,
                           headers: dict[str, str], body: str | None) -> aiohttp.ClientResponse:
    """The request of a check with its own headers or body (an API key,
    credentials). aiohttp would carry them to whatever host a redirect
    names (it drops only Authorization and Cookie), so the redirects are
    followed by hand and only while they stay with the same server; every
    hop goes through the session's guarded connector all the same."""
    for _ in range(MAX_REDIRECTS):
        resp = await session.request(method, url, headers=headers, data=body, ssl=True, allow_redirects=False)
        location = resp.headers.get("Location")
        if resp.status not in _REDIRECT_CODES or not location:
            return resp
        resp.release()
        target = urljoin(url, location)
        if not _stays_home(url, target):
            raise RedirectStopped(f"API перенаправляет на {_netloc(target)} — ключ туда не отправляю")
        # As a browser (and aiohttp) does: after a 303, and after a 301/302 of
        # a POST, the request continues as a GET without the body.
        if (resp.status == 303 and method != "HEAD") or (resp.status in (301, 302) and method == "POST"):
            method, body = "GET", None
        url = target
    raise RedirectStopped("слишком много перенаправлений (редирект по кругу)")


def _request(session: aiohttp.ClientSession, method: str, url: str, headers: dict[str, str],
             body: str | None, own: bool):
    """Awaitable → ClientResponse: aiohttp follows the redirects of a plain
    request; a request with its own headers or body keeps them at home."""
    if own:
        return _request_at_home(session, method, url, headers, body)
    return session.request(method, url, headers=headers, data=body, ssl=True, allow_redirects=True)


def _own_request(site: dict, body: str | None) -> bool:
    return bool(site.get("http_headers")) or body is not None


async def _probe_http(url: str, site: dict, r: AvailabilityResult):
    keyword = (site.get("keyword") or "").strip()
    ranges = parse_accepted_codes(site.get("accepted_codes"))
    method, headers, body = request_options(site)
    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, connector=owner_connector(url)) as session:
            async with await _request(session, method, url, headers, body, _own_request(site, body)) as resp:
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
                    found = phrase_found(keyword, decode_body(raw, resp.charset))
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
    except RedirectStopped as e:
        # The endpoint answered — with a redirect the key must not follow.
        r.status, r.error = "error", str(e)
        r.response_time_ms = int((time.monotonic() - start) * 1000)
    except aiohttp.ClientConnectorCertificateError as e:   # a ClientError too: must come first
        # The server is there and answered: its certificate is what every
        # visitor's browser rejects. Nothing to ask the outside about.
        r.status, r.error, r.cert_failed = "error", _error_text(e), True
        r.response_time_ms = int((time.monotonic() - start) * 1000)
    except aiohttp.ClientError as e:
        r.status = "error"
        r.error = PRIVATE_REFUSED if refused_private(e) else _error_text(e)
        r.response_time_ms = int((time.monotonic() - start) * 1000)


async def phrase_state(site: dict, phrase: str) -> bool | None:
    """Is the phrase on the page right now, as the monitor would see it?
    None when that cannot be told: HEAD (the monitor never reads a body),
    a status the site's rules reject, a network error."""
    method, headers, body = request_options(site)
    if method == "HEAD":
        return None
    url = site["url"]
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, connector=owner_connector(url)) as session:
            async with await _request(session, method, url, headers, body, _own_request(site, body)) as resp:
                if not code_accepted(resp.status, parse_accepted_codes(site.get("accepted_codes"))):
                    return None
                raw = await read_capped(resp, KEYWORD_BODY_LIMIT)
                return phrase_found(phrase, decode_body(raw, resp.charset))
    except Exception:   # a timeout or a refused connection is «cannot tell», not «no phrase»
        return None


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
_PING_NO_SOCKET = ("socket:", "cap_net_raw", "operation not permitted")
# «Down from here, up from outside»: while the episode lasts, failures are
# the bot's, and the outside is asked again no more often than this.
EXT_OK_RECHECK = timedelta(minutes=10)


async def _probe_ping(url: str, r: AvailabilityResult):
    """ping://host — ICMP echo via the system ping binary. Debian's ping has
    no setuid and no capabilities: it opens an ICMP datagram socket, which
    the kernel allows only to groups in net.ipv4.ping_group_range."""
    host = urlparse(url).hostname
    if not host:
        r.status, r.error = "error", "нужен хост: ping://host"
        return
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            # -W 5: ping gives up before our 10 s timeout and prints «0 received»;
            # «--»: the host is never read as an option.
            "ping", "-n", "-c", "1", "-W", "5", "--", host,
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
    elif proc.returncode == 2 and any(s in low for s in _PING_NO_SOCKET):
        # The bot's environment, not the host: no incident, no second opinion.
        logger.warning("%s: ping cannot open an ICMP socket: %s", url, out_text.strip()[:200])
        r.status, r.transient = "unknown", True
        r.error = ("ping на сервере бота не может открыть ICMP-сокет (нужен net.ipv4.ping_group_range) "
                   "— используй tcp://host:порт")
    elif "address family for hostname" in low:
        # iputils on an IPv6 address from a container without an IPv6 route:
        # the bot's environment again, not the host.
        logger.warning("%s: no IPv6 on the bot's server: %s", url, out_text.strip()[:200])
        r.status, r.transient = "unknown", True
        r.error = "у сервера бота нет IPv6 — до IPv6-адреса ping отсюда не дойдёт"
    else:
        r.error = "хост не отвечает на ping"


async def check_availability(url: str, manage: bool = True, site: dict | None = None) -> AvailabilityResult:
    """site — the row the tick already holds (check_all passes it); a manual
    check from the chat looks it up as before."""
    site = site or await get_site_by_url(url)
    r = AvailabilityResult(url=url, site_id=site["id"], api=is_api_check(url, site.get("http_method")))
    scheme = urlparse(url).scheme
    try:
        if scheme == "tcp":
            await _probe_tcp(url, r)
        elif scheme == "ping":
            await _probe_ping(url, r)
        else:
            await _probe_http(url, site, r)
    except ValueError as e:
        # The request could not even be built (a header with a line break, a
        # host IDNA cannot encode): our settings, not the site.
        logger.warning("%s: request settings rejected: %s", url, e)
        r.status, r.transient = "unknown", True
        r.error = f"настройки запроса не принимаются: {e}"
    except Exception as e:
        # Anything else must degrade to ONE failed check, not escape into the
        # batch — but loudly: a bug here would read as «all sites are down».
        logger.exception("Availability probe crashed for %s", url)
        r.status, r.error = "error", str(e) or type(e).__name__

    if r.ok or r.status_code is not None:
        netcheck.note_answer()                 # the network is up: a site answered
    if (not r.ok and not r.transient and r.status_code is None and not r.cert_failed
            and not await netcheck.own_network_ok()):
        # No answer at all, and the bot's own network is down: nothing is known
        # about the site. No incident, no downtime; one summary when it is back.
        logger.info("%s: %s — the bot's own network is down", url, r.error)
        await netcheck.mark_outage()
        r.status, r.transient, r.own_outage = "unknown", True, True
        r.error = "у сервера бота нет сети"

    memory = _recent.setdefault(r.site_id, deque(maxlen=RECENT_IN_MEMORY))
    if manage:
        memory.append(r.status)
    # While writes fail, do not even try: under «database is locked» each one
    # holds the only DB thread for busy_timeout (5 s). run_alive probes it.
    # Only the tick's check is planned: a «🔄 Проверить», a follow-up or a
    # snapshot writes its row for uptime, but must not count toward the threshold.
    # Remember the hosting so alerts can link to its panel even when the
    # failing response carries no headers at all.
    known_hosting = await get_state(f"hosting:{r.site_id}")
    new_hosting = r.hosting if r.hosting and r.hosting != known_hosting else None
    r.hosting = r.hosting or known_hosting
    # The history row, the snapshot and a changed hosting: one transaction
    # (one fsync per check), and one failure path for all three.
    stored = not health.db_failing() and await _write(sitestatus.update_with_check(
        r.site_id, "avail",
        fields={"status": r.status, "ms": r.response_time_ms, "error": r.error, "code": r.status_code,
                "kw": r.keyword_failed},
        check={"check_type": "availability", "status": r.status, "response_time_ms": r.response_time_ms,
               "status_code": r.status_code, "details": r.error, "scheduled": manage},
        hosting=new_hosting))
    if not manage or r.transient:
        return r

    threshold = site.get("fail_threshold") or CONSECUTIVE_FAILURE_THRESHOLD
    streak = recovery_streak(site.get("check_interval_min") or config.check_interval_minutes)
    window = max(streak, share_window(threshold))
    if stored:
        recent = await get_recent_checks(r.site_id, "availability", limit=window, scheduled_only=True)
    else:
        recent = [{"status": s, "checked_at": None} for s in reversed(memory)][:window]
    statuses = [c["status"] for c in recent]
    if r.ok:
        if await get_state(f"ext_ok:{r.site_id}"):
            await _write(set_state(f"ext_ok:{r.site_id}", None))   # the episode «up from outside» is over
        if not up_verdict(statuses, streak):
            return r                                  # one good answer between failures is not «up»
        if r.site_id in _down_without_db:            # alerted from memory: say it is back
            _down_without_db.discard(r.site_id)
            r.recovered = True
        try:
            resolved = await _close_downtime(r.site_id, _first_ok_at(recent),
                                             site.get("check_interval_min"))
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)                 # closed by a check once writes work
            resolved = None
        if resolved:
            r.recovered, r.resolved_incident = True, resolved
        return r

    ext_since = parse_iso_utc(await get_state(f"ext_ok:{r.site_id}"))
    if ext_since and datetime.now(UTC) - ext_since < EXT_OK_RECHECK:
        # Still inside «down from here, up from outside»: this failure is the
        # bot's too — no flicker between 🔴 and ⚠️, no downtime counted.
        await _outside_is_up(r, 1)
        return r
    verdict = down_verdict(statuses, threshold)
    if not verdict:
        return r  # a single flap is normal on the public internet
    if isinstance(verdict, tuple):
        r.failed_of = verdict
    # Second opinion from external nodes before paging: reachable from
    # outside means the problem is on our side. Skipped for keyword
    # failures — our own fetch succeeded, so the content problem is real —,
    # for a rejected certificate — the server answered, every browser sees
    # it —, for a request check-host cannot repeat (a POST, own headers),
    # and while the incident is already open: the verdict was taken when it
    # opened, the re-checks need not hold the tick for it.
    if (integrations.second_opinion_enabled() and not r.keyword_failed and not r.cert_failed
            and repeatable(site) and not await _incident_open(r.site_id)):
        country = region.country_for_timezone(settings.timezone())
        verdict = await second_opinion(url, country)
        r.external_ok, r.external_ups, r.external_total = verdict.up, verdict.ups, verdict.total
        if verdict.region_down:
            r.external_region = country
    if r.external_ok is True:
        logger.warning("%s: down from here but UP externally — skipping incident", url)
        await _write(set_state(f"ext_ok:{r.site_id}", datetime.now(UTC).isoformat()))
        await _outside_is_up(r, threshold)
        return r
    if ext_since:
        await _write(set_state(f"ext_ok:{r.site_id}", None))   # confirmed down this time
    prefix = "Bad content" if r.keyword_failed else "Site down"
    try:
        # Fell again minutes after it came back: the same incident continues —
        # no second ring, no second rebuild, no re-checks; the tick says «мигает».
        if await reopen_incident(r.site_id, "availability", REOPEN_WITHIN_MIN):
            r.flapping = True
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


async def _close_downtime(site_id: int, first_ok: datetime | None, interval_min: int | None) -> dict | None:
    """Close the availability incident at the moment the downtime ended.
    That is the first good answer of the recovery series, not the one that
    confirmed it. But when nobody checked for longer than the site's
    interval before that answer (the bot was down, its ticks stalled), when
    it came back is unknown: the incident ends where the site was last seen
    down — the lower bound — and the row carries unseen_until (the first
    good answer), so the recovery message says what it does not know."""
    seen_up = first_ok or datetime.now(UTC)
    last_bad = await last_failed_check_at(site_id)
    blind = last_bad is not None and (seen_up - last_bad).total_seconds() > span_cap_sec(interval_min)
    resolved = await resolve_incident(site_id, "availability", resolved_at=last_bad if blind else first_ok)
    if resolved and blind:
        resolved["unseen_until"] = seen_up.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return resolved


async def _incident_open(site_id: int) -> bool:
    """An availability incident is already open for the site — in the
    database, or in memory while the database refuses writes."""
    if site_id in _down_without_db:
        return True
    try:
        last = await get_last_incident(site_id, "availability")
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
        return False
    return bool(last) and not last["resolved"]


async def _outside_is_up(r: AvailabilityResult, relabel: int):
    """The failures of this streak say nothing about the site: out of uptime,
    the card and the public page say «cannot check», not «down»."""
    await _write(relabel_recent_failures(r.site_id, relabel))
    r.status, r.transient = "unknown", True
    r.error = f"с сервера бота не открывается ({humanize.describe_error(r.error)}), снаружи открывается"
    await _write(sitestatus.update(r.site_id, "avail", status=r.status, ms=r.response_time_ms, error=r.error,
                                   code=r.status_code))


async def _write(coro) -> bool:
    """Await one write; a database that refuses it (full disk, «database is
    locked») is noted in services.health instead of killing the check."""
    try:
        await coro
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
        return False
    return True


async def check_all(urls: list[str], manage: bool = True,
                    sites: dict[str, dict] | None = None) -> list[AvailabilityResult]:
    """sites — {url: row} the tick already read: no second lookup per site."""
    known = sites or {}

    async def check(url: str, **kwargs) -> AvailabilityResult:
        return await check_availability(url, site=known.get(url), **kwargs)

    results = await gather_checks(urls, check, "availability", manage=manage)
    if any(r.own_outage for r in results) and netcheck.recently_answered():
        # The checks ran side by side: a site answered in this very tick, so
        # the network is up and the «no network» verdicts were premature —
        # those sites are asked again, now with the answer on record.
        await netcheck.end_outage()
        redo = [r.url for r in results if r.own_outage]
        fixed = {r.url: r for r in await gather_checks(redo, check, "availability", manage=manage)}
        results = [fixed.get(r.url, r) for r in results]
    return results
