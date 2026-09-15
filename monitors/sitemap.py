"""sitemap.xml helpers shared by the deep 5xx probe and the SEO audit."""

import html
import logging
import re

import aiohttp

from utils.urls import host_of

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_SITEMAP_BYTES = 5 * 1024 * 1024
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def pick_sample(urls: list[str], n: int) -> list[str]:
    """Up to n URLs spread evenly across the list (deterministic)."""
    if n <= 0:
        return []
    if len(urls) <= n:
        return urls
    step = len(urls) / n
    return [urls[int(i * step)] for i in range(n)]


async def fetch_text(session: aiohttp.ClientSession, url: str,
                     limit: int = MAX_SITEMAP_BYTES) -> str | None:
    """Body text of a 200 response, capped at `limit` bytes; None otherwise."""
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            raw = await resp.content.read(limit)
            return raw.decode(resp.charset or "utf-8", errors="replace")
    except Exception as e:
        logger.debug("sitemap: could not fetch %s: %s", url, e)
    return None


def _locs(xml: str) -> list[str]:
    # <loc> values are XML-escaped: &amp; must become & before probing.
    return [html.unescape(loc) for loc in _LOC_RE.findall(xml)]


async def sitemap_urls(session: aiohttp.ClientSession, base_url: str) -> list[str]:
    """Page URLs from /sitemap.xml (one level of sitemap index followed),
    limited to the site's own host."""
    text = await fetch_text(session, base_url.rstrip("/") + "/sitemap.xml")
    if not text:
        return []
    locs = _locs(text)
    if locs and all(loc.rstrip("/").endswith(".xml") for loc in locs[:3]):
        nested: list[str] = []
        for sm in locs[:5]:
            sub = await fetch_text(session, sm)
            if sub:
                nested.extend(_locs(sub))
        locs = nested
    base_host = host_of(base_url)
    return [loc for loc in locs if host_of(loc) == base_host]
