import asyncio
import logging
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.database import get_or_create_site, save_check, save_incident, resolve_incident

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=TIMEOUT, allow_redirects=True) as resp:
            if resp.status == 200 and "text/html" in resp.content_type:
                return await resp.text()
    except Exception:
        pass
    return None


async def _check_link(session: aiohttp.ClientSession, url: str) -> dict:
    """Check if a single link is accessible."""
    try:
        async with session.head(url, timeout=TIMEOUT, allow_redirects=True) as resp:
            return {"url": url, "status_code": resp.status, "ok": resp.status < 400}
    except Exception as e:
        return {"url": url, "status_code": None, "ok": False, "error": str(e)}


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract all href and src links from HTML."""
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
    """Check all links on a page."""
    site_id = await get_or_create_site(url)

    result = {
        "url": url,
        "status": "ok",
        "total_links": 0,
        "broken_links": [],
        "error": None,
    }

    async with aiohttp.ClientSession() as session:
        html = await _fetch_page(session, url)
        if not html:
            result["status"] = "error"
            result["error"] = "Could not fetch page"
            await save_check(site_id, "links", "error", details=result["error"])
            return result

        links = _extract_links(html, url)
        result["total_links"] = len(links)

        # Check links with concurrency limit
        sem = asyncio.Semaphore(10)

        async def check_with_sem(link):
            async with sem:
                return await _check_link(session, link)

        checks = await asyncio.gather(*[check_with_sem(l) for l in links])
        broken = [c for c in checks if not c["ok"]]
        result["broken_links"] = broken

        if broken:
            result["status"] = "warning"
            broken_urls = "\n".join(f"  - {b['url']} ({b.get('status_code', 'N/A')})" for b in broken[:10])
            result["error"] = f"{len(broken)} broken link(s):\n{broken_urls}"

    details = result["error"] or f"All {result['total_links']} links OK"
    await save_check(site_id, "links", result["status"], details=details)

    if broken:
        await save_incident(
            site_id, "links",
            f"{len(broken)} broken link(s) found on {url}",
            severity="warning"
        )
    else:
        await resolve_incident(site_id, "links")

    return result


async def check_all_links(urls: list[str]) -> list[dict]:
    tasks = [check_links(url) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
