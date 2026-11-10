"""Broken-link crawl of one page. A link that does not open is a dead end
for the visitor wherever it points, and the owner can always fix it (a new
address, an archive copy, or remove it) — so own and other sites' links are
one list and one 🟠 problem.

What decides is how sure the answer is, not the domain:
  * definite — a 4xx (page gone), a domain that does not resolve, a bad
    certificate: a problem at once;
  * maybe temporary — a timeout, a 5xx, a refused connection: a problem
    only when the same link still fails CONFIRM_AFTER after its first
    failure. Time, not «the next crawl»: every bot start runs a crawl, and
    on 09.11.2026 six deploys in twenty minutes turned a GitHub incident
    (504 for anonymous visitors) into two confirmed «broken» links;
  * a bot block (401/403/429…, ALWAYS_OK_HOSTS) is not an answer at all.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.database import get_or_create_site, get_state, resolve_incident, save_check, save_incident, set_state
from monitors.base import LinkCheck, LinksResult, decode_body, gather_checks, read_capped
from services import sitestatus
from utils.text import parse_iso_utc
from utils.urls import USER_AGENT, host_of, same_site

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
# A maybe-temporary failure must outlive a typical outage on the far end
# before it is called broken. Shorter than the 6-hour crawl interval, so a
# scheduled crawl always confirms; longer than any burst of restarts.
CONFIRM_AFTER = timedelta(hours=3)
MAX_PAGE_BYTES = 2 * 1024 * 1024
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
}
# Codes that look like errors but mean "bot blocked / method not allowed /
# range not satisfiable" rather than "the resource is broken".
BENIGN_STATUS_CODES = {401, 403, 405, 416, 429, 503}
# Hosts known to block automated requests — assume they work.
ALWAYS_OK_HOSTS = {
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "m.facebook.com", "twitter.com", "x.com", "www.twitter.com",
    "instagram.com", "www.instagram.com", "t.me", "telegram.me",
    "youtube.com", "www.youtube.com", "youtu.be",
}
# Tracking / analytics — skip entirely.
SKIP_HOSTS = {
    "google-analytics.com", "www.google-analytics.com",
    "googletagmanager.com", "www.googletagmanager.com", "doubleclick.net",
    "mc.yandex.ru", "facebook.net", "connect.facebook.net",
}
# /cdn-cgi/ is Cloudflare's internal namespace: these intentionally 404 on
# direct requests and can't be fixed by the site owner anyway.
SKIP_PATH_PREFIXES = ("/cdn-cgi/",)


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=TIMEOUT, allow_redirects=True,
                               headers=DEFAULT_HEADERS) as resp:
            if resp.status == 200 and "text/html" in (resp.content_type or ""):
                return decode_body(await read_capped(resp, MAX_PAGE_BYTES), resp.charset)
    except Exception as e:
        logger.debug("Could not fetch %s: %s", url, e)
    return None


async def _check_link(session: aiohttp.ClientSession, url: str, base_url: str) -> LinkCheck:
    """GET with Range: bytes=0-0 (cheap but real GET behaviour), one retry
    on timeout/5xx. TLS is verified strictly for internal links (a broken
    cert on our own domain IS a problem); external hosts only need to be
    reachable."""
    host = host_of(url)
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return LinkCheck(url=url, status_code=None, ok=False, error=MALFORMED)
    if (host in SKIP_HOSTS or host in ALWAYS_OK_HOSTS
            or any(path.startswith(p) for p in SKIP_PATH_PREFIXES)):
        return LinkCheck(url=url, status_code=None, ok=True)

    headers = {**DEFAULT_HEADERS, "Range": "bytes=0-0"}
    ssl_arg = same_site(url, base_url)

    async def attempt() -> LinkCheck:
        try:
            async with session.get(url, timeout=TIMEOUT, allow_redirects=True,
                                   headers=headers, ssl=ssl_arg) as resp:
                ok = resp.status < 400 or resp.status in BENIGN_STATUS_CODES
                return LinkCheck(url=url, status_code=resp.status, ok=ok)
        except TimeoutError:
            return LinkCheck(url=url, status_code=None, ok=False, error="timeout")
        except Exception as e:
            return LinkCheck(url=url, status_code=None, ok=False, error=str(e) or type(e).__name__)

    result = await attempt()
    transient = not result.ok and (result.status_code is None or result.status_code >= 500)
    if transient:
        await asyncio.sleep(0.8)
        result = await attempt()
    return result


MALFORMED = "адрес записан с ошибкой"


def extract_links(html: str, base_url: str) -> list[str]:
    """Anchor and resource links (a/link/script/img) resolved to absolute URLs."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all(["a", "link", "script", "img"]):
        # preconnect / dns-prefetch name an origin, not a page: its root may
        # well answer 404 and is not a link anyone follows.
        if tag.name == "link" and {"preconnect", "dns-prefetch"} & set(tag.get("rel") or []):
            continue
        href = tag.get("href") or tag.get("src")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        try:
            full = urljoin(base_url, href)
            scheme = urlparse(full).scheme
        except ValueError:
            links.add(href[:300])          # «https://example.com]/post»: a dead end for a visitor too
            continue
        if scheme in ("http", "https"):
            links.add(full)
    return sorted(links)


_DNS_GONE = ("name or service not known", "nodename nor servname", "no address associated",
             "name does not resolve", "temporary failure in name")


def is_definite(c: LinkCheck) -> bool:
    """The far end answered for sure: the page or the domain is gone, or
    its certificate is bad. Timeouts and 5xx may pass by themselves."""
    if c.status_code is not None:
        return 400 <= c.status_code < 500
    if c.error == MALFORMED:
        return True
    err = (c.error or "").lower()
    return "certificate" in err or any(s in err for s in _DNS_GONE)


async def check_links(url: str, manage: bool = True) -> LinksResult:
    site_id = await get_or_create_site(url)
    r = LinksResult(url=url, site_id=site_id)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20)) as session:
        html = await _fetch_page(session, url)
        if not html:
            r.status, r.error, r.transient = "error", "Could not fetch page", True
            await save_check(site_id, "links", "error", details=r.error)
            await sitestatus.update(site_id, "links", status="error", broken=0, pending=0)
            return r
        links = extract_links(html, url)
        r.total_links = len(links)
        sem = asyncio.Semaphore(8)

        async def guarded(link: str) -> LinkCheck:
            async with sem:
                return await _check_link(session, link, url)

        failed = [c for c in await asyncio.gather(*(guarded(link) for link in links)) if not c.ok]

    # A maybe-temporary failure becomes a problem when it still fails
    # CONFIRM_AFTER after the first time; {url: first failure} is alert
    # state, kept by the scheduler only.
    key = f"links_suspect:{site_id}"
    now = datetime.now(UTC)
    suspects = _suspects(await get_state(key), now)
    flaky = [c for c in failed if not is_definite(c)]
    for c in failed:
        since = parse_iso_utc(suspects.get(c.url))
        confirmed = since is not None and now - since >= CONFIRM_AFTER
        (r.broken if is_definite(c) or confirmed else r.pending).append(c)
    if manage:
        state = {c.url: suspects.get(c.url) or now.isoformat() for c in flaky}
        await set_state(key, json.dumps(state, sort_keys=True) if state else None)

    if r.broken:
        r.status = "warning"
        preview = "\n".join(f"  - {b.url} ({b.reason})" for b in r.broken[:10])
        r.error = f"{len(r.broken)} broken link(s):\n{preview}"
    details = r.error or (f"All {r.total_links} links OK"
                          + (f" ({len(r.pending)} failed once, rechecking)" if r.pending else ""))
    await save_check(site_id, "links", r.status, details=details)
    await sitestatus.update(site_id, "links", status=r.status, broken=len(r.broken),
                            pending=len(r.pending), total=r.total_links,
                            # the card lists them — no separate «🔗 Ссылки» screen to tap
                            items=[{"url": b.url, "code": b.status_code, "error": b.error} for b in r.broken[:5]])
    if not manage:
        # Read-only, with one exception (as for search): a clean check — no
        # dead link, nothing waiting for a recheck — closes the open links
        # problem. The screen says «все открываются»; the list must agree.
        if r.status == "ok" and not r.pending:
            await resolve_incident(site_id, "links")
        return r
    if r.broken:
        r.incident_id, r.incident_new = await save_incident(
            site_id, "links", f"{len(r.broken)} broken link(s) found on {url}", severity="warning")
    else:
        closed = await resolve_incident(site_id, "links")
        r.recovered, r.resolved_incident = closed is not None, closed
    return r


def _suspects(raw: str | None, now: datetime) -> dict[str, str]:
    """{url: first failure, ISO}. The list kept before 11.2026 had no times:
    its links start the clock now rather than count as confirmed."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if isinstance(data, list):
        return {u: now.isoformat() for u in data if isinstance(u, str)}
    return {u: t for u, t in data.items() if isinstance(u, str) and isinstance(t, str)} \
        if isinstance(data, dict) else {}


async def check_all_links(urls: list[str], manage: bool = True) -> list[LinksResult]:
    return await gather_checks(urls, check_links, "links", manage=manage)
