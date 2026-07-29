"""
Deep 5xx probe: sample pages from the site's sitemap.xml and alert when
several of them error out.

Cloudflare Pages gives no nginx logs, so this is the practical way to catch
"the homepage is fine but half the site returns errors". Sites without a
sitemap are skipped silently.
"""

import asyncio
import logging
import re
from urllib.parse import urlparse

import aiohttp

from config import config
from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
)

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 DevOpsBot/1.0"
)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def _pick_sample(urls: list[str], n: int) -> list[str]:
    """Up to n URLs spread evenly across the list (deterministic)."""
    if len(urls) <= n:
        return urls
    step = len(urls) / n
    return [urls[int(i * step)] for i in range(n)]


async def _fetch_text(session: aiohttp.ClientSession, url: str) -> str | None:
    try:
        async with session.get(url, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        logger.debug(f"deep_checker: could not fetch {url}: {e}")
    return None


async def _sitemap_urls(session: aiohttp.ClientSession, base_url: str) -> list[str]:
    text = await _fetch_text(session, base_url.rstrip("/") + "/sitemap.xml")
    if not text:
        return []
    locs = _LOC_RE.findall(text)
    # A sitemap index points at nested sitemaps — follow one level.
    if locs and all(l.rstrip("/").endswith(".xml") for l in locs[:3]):
        nested: list[str] = []
        for sm in locs[:5]:
            sub = await _fetch_text(session, sm)
            if sub:
                nested.extend(_LOC_RE.findall(sub))
        locs = nested
    base_host = (urlparse(base_url).hostname or "").lower()
    return [l for l in locs if (urlparse(l).hostname or "").lower() == base_host]


async def check_deep(url: str) -> dict:
    """Probe sampled sitemap pages for 5xx responses."""
    site_id = await get_or_create_site(url)
    result = {
        "url": url,
        "status": "ok",
        "sampled": 0,
        "errors": [],          # [{url, status_code}]
        "skipped": False,
        "incident_new": False,
        "recovered": False,
    }

    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT},
        connector=aiohttp.TCPConnector(limit=5),
    ) as session:
        pages = await _sitemap_urls(session, url)
        if not pages:
            result["skipped"] = True
            return result

        sample = _pick_sample(pages, config.deep_check_sample)
        result["sampled"] = len(sample)

        sem = asyncio.Semaphore(5)

        async def probe(page: str) -> dict | None:
            async with sem:
                try:
                    async with session.get(
                        page, timeout=TIMEOUT,
                        headers={"Range": "bytes=0-256"},
                    ) as resp:
                        if resp.status >= 500:
                            return {"url": page, "status_code": resp.status}
                except Exception:
                    pass  # network flakes are the availability checker's job
                return None

        probes = await asyncio.gather(*[probe(p) for p in sample])
        result["errors"] = [p for p in probes if p]

    # One 5xx out of ten can be a fluke; several mean the site is broken.
    threshold = 1 if result["sampled"] <= 3 else 2
    if len(result["errors"]) >= threshold:
        result["status"] = "error"
        preview = ", ".join(
            f"{e['url']} ({e['status_code']})" for e in result["errors"][:5]
        )
        await save_check(site_id, "deep", "error", details=preview)
        _, is_new = await save_incident(
            site_id, "deep",
            f"5xx на {len(result['errors'])} из {result['sampled']} проверенных страниц",
            severity="warning",
        )
        result["incident_new"] = is_new
    else:
        await save_check(
            site_id, "deep", "ok",
            details=f"{result['sampled']} pages sampled, no 5xx",
        )
        if await resolve_incident(site_id, "deep"):
            result["recovered"] = True

    return result


async def check_all_deep(urls: list[str]) -> list[dict]:
    return await asyncio.gather(*[check_deep(u) for u in urls])
