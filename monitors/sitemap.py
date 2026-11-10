"""sitemap.xml helpers shared by the deep 5xx probe and the SEO audit."""

import html
import logging
import re

import aiohttp

from monitors.base import decode_body, read_capped
from utils.urls import USER_AGENT, bare_host, host_of

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_SITEMAP_BYTES = 5 * 1024 * 1024
# Linear on purpose: one branch per <loc>, no two quantifiers over the same
# characters — a 10 KB sitemap with an unclosed <loc> used to hold the event
# loop for minutes (catastrophic backtracking; re keeps the GIL throughout).
_LOC_RE = re.compile(r"<loc>(?:\s*<!\[CDATA\[([^\]<]*)\]\]>\s*|([^<]*))</loc>", re.IGNORECASE)


def pick_sample(urls: list[str], n: int) -> list[str]:
    """Up to n URLs spread evenly across the list (deterministic)."""
    if n <= 0:
        return []
    if len(urls) <= n:
        return urls
    step = len(urls) / n
    return [urls[int(i * step)] for i in range(n)]


async def fetch_text(session: aiohttp.ClientSession, url: str,
                     limit: int = MAX_SITEMAP_BYTES) -> tuple[int | None, str | None]:
    """(status, body text of a 200 response capped at `limit` bytes) —
    status None on a network error, text None unless 200."""
    try:
        async with session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                return resp.status, None
            return resp.status, decode_body(await read_capped(resp, limit), resp.charset)
    except Exception as e:
        logger.debug("sitemap: could not fetch %s: %s", url, e)
    return None, None


def _locs(xml: str) -> list[str]:
    """<loc> values in document order. Plain values are XML-escaped (&amp;
    must become & before probing); CDATA is taken as is."""
    out = []
    for cdata, plain in _LOC_RE.findall(xml):
        loc = cdata.strip() if cdata else html.unescape(plain.strip())
        if loc:
            out.append(loc)
    return out


async def sitemap_urls(session: aiohttp.ClientSession, base_url: str) -> list[str] | None:
    """Page URLs from /sitemap.xml (one level of sitemap index followed),
    limited to the site's own host (www or not).

    [] when the site says there is none (404/410, or no pages of its own);
    None when it could not be read — a timeout, a 5xx or a block says
    nothing about the sitemap and must not become «нет карты сайта» or
    close a deep-probe incident."""
    status, text = await fetch_text(session, base_url.rstrip("/") + "/sitemap.xml")
    if status in (404, 410):
        return []
    if text is None:
        return None
    locs = _locs(text)
    # An index is recognised by its root element, not by «.xml» in the
    # entries: Shopify's are «sitemap_products_1.xml?from=1&to=99».
    if re.search(r"<sitemapindex[\s>]", text, re.I):
        nested: list[str] = []
        failed = 0
        for sm in locs[:5]:
            _, sub = await fetch_text(session, sm)
            if sub is None:
                failed += 1
            else:
                nested.extend(_locs(sub))
        if not nested and failed:
            return None
        locs = nested
    base_host = bare_host(host_of(base_url))
    return [loc for loc in locs if bare_host(host_of(loc)) == base_host]
