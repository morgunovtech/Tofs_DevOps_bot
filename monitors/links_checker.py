import asyncio
import logging
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
)

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 DevOpsBot/1.0"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
}

# Codes that look like errors but in practice mean "bot blocked / method not allowed"
# rather than "the resource is broken". We treat them as OK.
BENIGN_STATUS_CODES = {401, 403, 405, 429, 503}

# Hosts known to aggressively block non-browser HEAD/automated requests.
# We don't even try to verify these — assume they work.
ALWAYS_OK_HOSTS = {
    "linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "twitter.com", "x.com", "www.twitter.com",
    "instagram.com", "www.instagram.com",
    "t.me", "telegram.me",
    "youtube.com", "www.youtube.com", "youtu.be",
}

# Tracking / analytics — skip checking entirely
SKIP_HOSTS = {
    "google-analytics.com", "www.google-analytics.com",
    "googletagmanager.com", "www.googletagmanager.com",
    "doubleclick.net",
    "yandex.ru/metrika", "mc.yandex.ru",
    "facebook.net", "connect.facebook.net",
}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_internal(link: str, base_url: str) -> bool:
    """Same registrable domain as the base site."""
    base_host = _host(base_url)
    link_host = _host(link)
    if not base_host or not link_host:
        return False
    base_root = ".".join(base_host.split(".")[-2:])
    link_root = ".".join(link_host.split(".")[-2:])
    return base_root == link_root


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(
            url, timeout=TIMEOUT, allow_redirects=True, headers=DEFAULT_HEADERS,
        ) as resp:
            if resp.status == 200 and "text/html" in (resp.content_type or ""):
                return await resp.text()
    except Exception as e:
        logger.debug(f"Could not fetch {url}: {e}")
    return None


async def _check_link(session: aiohttp.ClientSession, url: str) -> dict:
    """Check if a single link is accessible.

    Strategy:
      1. Skip known always-OK / tracker hosts.
      2. Try GET with Range: bytes=0-0 (cheap, but real GET behaviour).
      3. On timeout/5xx, retry once with a small backoff.
      4. 4xx codes that mean "bot blocked" (401/403/405/429/503) are not failures.
    """
    host = _host(url)
    if host in SKIP_HOSTS:
        return {"url": url, "status_code": None, "ok": True, "skipped": True}
    if host in ALWAYS_OK_HOSTS:
        return {"url": url, "status_code": None, "ok": True, "skipped": True}

    headers = {**DEFAULT_HEADERS, "Range": "bytes=0-0"}

    async def _attempt() -> dict:
        try:
            async with session.get(
                url, timeout=TIMEOUT, allow_redirects=True, headers=headers,
            ) as resp:
                status = resp.status
                ok = status < 400 or status in BENIGN_STATUS_CODES
                return {"url": url, "status_code": status, "ok": ok}
        except asyncio.TimeoutError:
            return {"url": url, "status_code": None, "ok": False, "error": "timeout"}
        except aiohttp.ClientError as e:
            return {"url": url, "status_code": None, "ok": False, "error": str(e)}
        except Exception as e:
            return {"url": url, "status_code": None, "ok": False, "error": str(e)}

    result = await _attempt()

    # Retry once for transient failures (timeout / 5xx that isn't benign)
    needs_retry = (
        not result["ok"]
        and (result.get("status_code") is None  # network error / timeout
             or (result.get("status_code", 0) >= 500
                 and result["status_code"] not in BENIGN_STATUS_CODES))
    )
    if needs_retry:
        await asyncio.sleep(0.8)
        result = await _attempt()

    return result


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract anchor and resource links from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for tag in soup.find_all(["a", "link", "script", "img"]):
        href = tag.get("href") or tag.get("src")
        if not href:
            continue
        if href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme in ("http", "https"):
            links.add(full_url)

    return list(links)


async def check_links(url: str) -> dict:
    """Check all links on a page.

    Returns:
        {
          url, status, total_links,
          broken_internal: [...],     # broken links on YOUR domain — actionable
          broken_external: [...],     # broken third-party links — informational
          error,
        }
    """
    site_id = await get_or_create_site(url)

    result = {
        "url": url,
        "status": "ok",
        "total_links": 0,
        "broken_internal": [],
        "broken_external": [],
        "broken_links": [],  # backwards-compat: combined view
        "error": None,
        "incident_new": False,
        "recovered": False,
    }

    connector = aiohttp.TCPConnector(limit=20, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        html = await _fetch_page(session, url)
        if not html:
            result["status"] = "error"
            result["error"] = "Could not fetch page"
            await save_check(site_id, "links", "error", details=result["error"])
            return result

        links = _extract_links(html, url)
        result["total_links"] = len(links)

        sem = asyncio.Semaphore(8)

        async def check_with_sem(link):
            async with sem:
                return await _check_link(session, link)

        checks = await asyncio.gather(*[check_with_sem(l) for l in links])

        broken_internal: list[dict] = []
        broken_external: list[dict] = []
        for c in checks:
            if c["ok"]:
                continue
            if _is_internal(c["url"], url):
                broken_internal.append(c)
            else:
                broken_external.append(c)

        result["broken_internal"] = broken_internal
        result["broken_external"] = broken_external
        result["broken_links"] = broken_internal + broken_external

        if broken_internal:
            result["status"] = "warning"
            preview = "\n".join(
                f"  - {b['url']} ({b.get('status_code') or b.get('error', 'N/A')})"
                for b in broken_internal[:10]
            )
            result["error"] = (
                f"{len(broken_internal)} broken internal link(s)"
                + (f" (+{len(broken_external)} external unreachable)"
                   if broken_external else "")
                + f":\n{preview}"
            )

    details = result["error"] or (
        f"All {result['total_links']} links OK"
        + (f" ({len(result['broken_external'])} external unreachable, ignored)"
           if result["broken_external"] else "")
    )
    await save_check(site_id, "links", result["status"], details=details)

    # Only treat broken INTERNAL links as an incident — external is just noise.
    if broken_internal:
        _, is_new = await save_incident(
            site_id, "links",
            f"{len(broken_internal)} broken link(s) found on {url}",
            severity="warning",
        )
        result["incident_new"] = is_new
    else:
        if await resolve_incident(site_id, "links"):
            result["recovered"] = True

    return result


async def check_all_links(urls: list[str]) -> list[dict]:
    tasks = [check_links(url) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
