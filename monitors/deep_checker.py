"""Deep 5xx probe: sample pages from sitemap.xml and alert when several of
them error out — "the homepage is fine but half the site returns errors".
Sites without a sitemap are skipped silently."""

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp

from config import config
from db.database import (
    get_or_create_site,
    get_recent_check_statuses,
    resolve_incident,
    save_check,
    save_incident,
)
from monitors.base import DeepResult, gather_checks
from monitors.sitemap import TIMEOUT, pick_sample, sitemap_urls
from services import sitestatus
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)

# The «code» of a page that did not answer in time (a timeout, a dropped
# connection): kept next to the 5xx codes in the snapshot and the incident.
NO_ANSWER = 0


def _path(page: str, site_url: str) -> str:
    """«/privacy» for a page of the site itself, the whole address otherwise."""
    p, home = urlparse(page), urlparse(site_url)
    if p.netloc.lower() != home.netloc.lower():
        return page
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


async def check_deep(url: str) -> DeepResult:
    site_id = await get_or_create_site(url)
    r = DeepResult(url=url, site_id=site_id)
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT},
                                     connector=aiohttp.TCPConnector(limit=5)) as session:
        pages = await sitemap_urls(session, url)
        if pages is None:
            return r   # sitemap unreadable right now: no verdict either way
        if not pages:
            # A previously opened deep incident must not stay open forever
            # just because the sitemap is gone (the site said 404) — but
            # nothing was checked, so this is not «✅ все страницы снова
            # отвечают»: the incident closes quietly, the card forgets the pages.
            await resolve_incident(site_id, "deep")
            await sitestatus.update(site_id, "deep", sampled=0, errors=0, pages=[])
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
                except (TimeoutError, aiohttp.ClientConnectionError):
                    return page, NO_ANSWER     # a page that hangs is not a healthy one
                except Exception:
                    pass  # anything else is the availability checker's job
                return None

        r.errors = [p for p in await asyncio.gather(*(probe(p) for p in sample)) if p]

    if r.errors and all(code == NO_ANSWER for _, code in r.errors) and len(r.errors) == r.sampled:
        return r   # nobody answered: the bot's network or the whole site — the availability monitor's call

    # The card says how the rest of the site is, not only the home page —
    # each page with its own mark.
    failed = dict(r.errors)
    await sitestatus.update(site_id, "deep", sampled=r.sampled, errors=len(r.errors),
                            pages=[[_path(page, url), failed.get(page)] for page in sample])
    # One 5xx out of ten can be a fluke; several mean the site is broken.
    threshold = 1 if r.sampled <= 3 else 2
    if len(r.errors) >= threshold:
        r.status = "error"
        silent = any(code == NO_ANSWER for _, code in r.errors)
        r.error = (f"5xx {'или нет ответа ' if silent else ''}на {len(r.errors)} "
                   f"из {r.sampled} проверенных страниц")
        preview = ", ".join(f"{u} ({code})" for u, code in r.errors[:5])
        previous = await get_recent_check_statuses(site_id, "deep", 1)
        await save_check(site_id, "deep", "error", details=preview)
        # Anti-flapping: an hour of 5xx can be a deploy in progress — the
        # incident opens on the second run in a row.
        if previous[:1] == ["error"]:
            r.incident_id, r.incident_new = await save_incident(site_id, "deep", r.error, severity="warning")
    else:
        n = len(r.errors)
        await save_check(site_id, "deep", "ok", details=(
            f"{r.sampled} pages sampled, " + (f"{n} 5xx (below threshold)" if n else "no 5xx")))
        closed = await resolve_incident(site_id, "deep")
        r.recovered, r.resolved_incident = closed is not None, closed
    return r


async def check_all_deep(urls: list[str]) -> list[DeepResult]:
    return await gather_checks(urls, check_deep, "deep")
