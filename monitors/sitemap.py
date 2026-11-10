"""sitemap.xml helpers shared by the deep 5xx probe and the SEO audit."""

import html
import logging
import re
import zlib
from dataclasses import dataclass, field

import aiohttp

from monitors.base import decode_body, read_capped
from utils.urls import USER_AGENT, bare_host, host_of, same_site

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_SITEMAP_BYTES = 5 * 1024 * 1024
# Linear on purpose: one branch per <loc>, no two quantifiers over the same
# characters — a 10 KB sitemap with an unclosed <loc> used to hold the event
# loop for minutes (catastrophic backtracking; re keeps the GIL throughout).
_LOC_RE = re.compile(r"<loc>(?:\s*<!\[CDATA\[([^\]<]*)\]\]>\s*|([^<]*))</loc>", re.IGNORECASE)
_ROOT_RE = re.compile(r"<(?:urlset|sitemapindex)[\s>]", re.IGNORECASE)
_INDEX_RE = re.compile(r"<sitemapindex[\s>]", re.IGNORECASE)


@dataclass
class SitemapScan:
    """pages: the site's own pages, None when unread; missing: the site said
    404/410, or answered 200 with something that is not a sitemap (an SPA
    shell, an empty <urlset/>); foreign_hosts: a map exists, but every <loc>
    leads to another host (an old domain)."""
    pages: list[str] | None = None
    missing: bool = False
    foreign_hosts: list[str] = field(default_factory=list)


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
            raw = await read_capped(resp, limit)
            if raw[:2] == b"\x1f\x8b":           # sitemap.xml.gz served without Content-Encoding
                raw = _gunzip(raw, limit)
            return resp.status, decode_body(raw, resp.charset)
    except Exception as e:
        logger.debug("sitemap: could not fetch %s: %s", url, e)
    return None, None


def _gunzip(raw: bytes, limit: int) -> bytes:
    """At most `limit` bytes out, whatever the file claims — a gzip bomb
    stays a few megabytes; a broken archive is an empty map."""
    try:
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw, limit)
    except zlib.error:
        return b""


def _locs(xml: str) -> list[str]:
    """<loc> values in document order. Plain values are XML-escaped (&amp;
    must become & before probing); CDATA is taken as is."""
    out = []
    for cdata, plain in _LOC_RE.findall(xml):
        loc = cdata.strip() if cdata else html.unescape(plain.strip())
        if loc:
            out.append(loc)
    return out


async def scan_sitemap(session: aiohttp.ClientSession, base_url: str) -> SitemapScan:
    """What /sitemap.xml says about the site (one level of sitemap index
    followed). missing — the site's own answer: a 404/410, or a 200 that is
    not a sitemap (an SPA answering every address with index.html, an empty
    <urlset/>). pages None — could not read: a timeout, a 5xx or a block
    says nothing about the sitemap and must not become «нет карты сайта» or
    close a deep-probe incident. foreign_hosts — a map whose every address
    is on another host: an old domain after a move."""
    status, text = await fetch_text(session, base_url.rstrip("/") + "/sitemap.xml")
    if status in (404, 410):
        return SitemapScan(pages=[], missing=True)
    if text is None:
        return SitemapScan()
    if not _ROOT_RE.search(text):
        return SitemapScan(pages=[], missing=True)
    locs = _locs(text)
    # An index is recognised by its root element, not by «.xml» in the
    # entries: Shopify's are «sitemap_products_1.xml?from=1&to=99».
    if _INDEX_RE.search(text):
        nested: list[str] = []
        failed = 0
        # Nested maps of the site's own domain only (cdn.example.com is fine,
        # another domain is not): the pages of a foreign map are dropped below anyway.
        for sm in [u for u in locs if same_site(u, base_url)][:5]:
            _, sub = await fetch_text(session, sm)
            if sub is None:
                failed += 1
            else:
                nested.extend(_locs(sub))
        if not nested and failed:
            return SitemapScan()
        locs = nested
    if not locs:
        return SitemapScan(pages=[], missing=True)
    base_host = bare_host(host_of(base_url))
    own = [loc for loc in locs if bare_host(host_of(loc)) == base_host]
    foreign = [] if own else sorted({h for h in (host_of(loc) for loc in locs) if h})
    return SitemapScan(pages=own, foreign_hosts=foreign)


async def sitemap_urls(session: aiohttp.ClientSession, base_url: str) -> list[str] | None:
    """Page URLs from /sitemap.xml limited to the site's own host (www or
    not): [] when the site says there is none, None when it could not be
    read — what the deep probe needs to know."""
    return (await scan_sitemap(session, base_url)).pages
