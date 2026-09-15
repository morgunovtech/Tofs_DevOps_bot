"""Deep 5xx probe: sample pages from sitemap.xml and alert when several of
them error out — "the homepage is fine but half the site returns errors".
Sites without a sitemap are skipped silently."""

import asyncio
import logging

import aiohttp

from config import config
from db.database import get_or_create_site, resolve_incident, save_check, save_incident
from monitors.base import DeepResult, gather_checks
from monitors.sitemap import TIMEOUT, pick_sample, sitemap_urls
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)


async def check_deep(url: str) -> DeepResult:
    site_id = await get_or_create_site(url)
    r = DeepResult(url=url, site_id=site_id)
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT},
                                     connector=aiohttp.TCPConnector(limit=5)) as session:
        pages = await sitemap_urls(session, url)
        if not pages:
            r.skipped = True
            # A previously opened deep incident must not stay open forever
            # just because the sitemap disappeared.
            if await resolve_incident(site_id, "deep"):
                r.recovered = True
            return r
        sample = pick_sample(pages, config.deep_check_sample)
        r.sampled = len(sample)
        sem = asyncio.Semaphore(5)

        async def probe(page: str) -> tuple[str, int] | None:
            async with sem:
                try:
                    async with session.get(page, timeout=TIMEOUT,
                                           headers={"Range": "bytes=0-256"}) as resp:
                        if resp.status >= 500:
                            return page, resp.status
                except Exception:
                    pass  # network flakes are the availability checker's job
                return None

        r.errors = [p for p in await asyncio.gather(*(probe(p) for p in sample)) if p]

    # One 5xx out of ten can be a fluke; several mean the site is broken.
    threshold = 1 if r.sampled <= 3 else 2
    if len(r.errors) >= threshold:
        r.status = "error"
        r.error = f"5xx на {len(r.errors)} из {r.sampled} проверенных страниц"
        preview = ", ".join(f"{u} ({code})" for u, code in r.errors[:5])
        await save_check(site_id, "deep", "error", details=preview)
        _, r.incident_new = await save_incident(site_id, "deep", r.error, severity="warning")
    else:
        n = len(r.errors)
        await save_check(site_id, "deep", "ok", details=(
            f"{r.sampled} pages sampled, " + (f"{n} 5xx (below threshold)" if n else "no 5xx")))
        if await resolve_incident(site_id, "deep"):
            r.recovered = True
    return r


async def check_all_deep(urls: list[str]) -> list[DeepResult]:
    return await gather_checks((check_deep(u) for u in urls), "deep")
