"""Broken-link crawl of one page: internal broken links are actionable
(incident), external unreachable ones are informational."""

import asyncio
import logging
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.database import get_or_create_site, resolve_incident, save_check, save_incident
from monitors.base import LinkCheck, LinksResult, gather_checks
from utils.urls import USER_AGENT, host_of, same_site

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
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
                raw = await resp.content.read(MAX_PAGE_BYTES)
                return raw.decode(resp.charset or "utf-8", errors="replace")
    except Exception as e:
        logger.debug("Could not fetch %s: %s", url, e)
    return None


async def _check_link(session: aiohttp.ClientSession, url: str, base_url: str) -> LinkCheck:
    """GET with Range: bytes=0-0 (cheap but real GET behaviour), one retry
    on timeout/5xx. TLS is verified strictly for internal links (a broken
    cert on our own domain IS a problem); external hosts only need to be
    reachable."""
    host = host_of(url)
    path = urlparse(url).path or ""
    if (host in SKIP_HOSTS or host in ALWAYS_OK_HOSTS
            or any(path.startswith(p) for p in SKIP_PATH_PREFIXES)):
        return LinkCheck(url=url, status_code=None, ok=True, skipped=True)

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


def extract_links(html: str, base_url: str) -> list[str]:
    """Anchor and resource links (a/link/script/img) resolved to absolute URLs."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for tag in soup.find_all(["a", "link", "script", "img"]):
        href = tag.get("href") or tag.get("src")
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).scheme in ("http", "https"):
            links.add(full)
    return sorted(links)


async def check_links(url: str, manage: bool = True) -> LinksResult:
    site_id = await get_or_create_site(url)
    r = LinksResult(url=url, site_id=site_id)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20)) as session:
        html = await _fetch_page(session, url)
        if not html:
            r.status, r.error, r.transient = "error", "Could not fetch page", True
            await save_check(site_id, "links", "error", details=r.error)
            return r
        links = extract_links(html, url)
        r.total_links = len(links)
        sem = asyncio.Semaphore(8)

        async def guarded(link: str) -> LinkCheck:
            async with sem:
                return await _check_link(session, link, url)

        for c in await asyncio.gather(*(guarded(link) for link in links)):
            if c.ok:
                continue
            (r.broken_internal if same_site(c.url, url) else r.broken_external).append(c)

    if r.broken_internal:
        r.status = "warning"
        preview = "\n".join(f"  - {b.url} ({b.reason})" for b in r.broken_internal[:10])
        r.error = (f"{len(r.broken_internal)} broken internal link(s)"
                   + (f" (+{len(r.broken_external)} external unreachable)"
                      if r.broken_external else "") + f":\n{preview}")
    details = r.error or (f"All {r.total_links} links OK"
                          + (f" ({len(r.broken_external)} external unreachable, ignored)"
                             if r.broken_external else ""))
    await save_check(site_id, "links", r.status, details=details)
    if not manage:
        return r
    if r.broken_internal:
        _, r.incident_new = await save_incident(
            site_id, "links", f"{len(r.broken_internal)} broken link(s) found on {url}",
            severity="warning")
    elif await resolve_incident(site_id, "links"):
        r.recovered = True
    return r


async def check_all_links(urls: list[str], manage: bool = True) -> list[LinksResult]:
    return await gather_checks((check_links(u, manage=manage) for u in urls), "links")
