"""Daily verdict on real visitors' speed from Google's Chrome UX Report
(services.crux), when a key is set. Slow by Google's yardstick (p75 in the
red zone) is a quiet 🟠 «visitors» incident; back to not-slow closes it."""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from db.database import get_or_create_site, resolve_incident
from monitors.base import CheckResult
from monitors.ladder import apply_incident
from services import crux, sitestatus

logger = logging.getLogger(__name__)


@dataclass
class VisitorsResult(CheckResult):
    values: dict = field(default_factory=dict)


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def message(slow: list[str]) -> str:
    """«у посетителей страница появляется за 5.2 с»."""
    return "у посетителей " + "; ".join(slow)


async def check_visitors(url: str, chrome: dict | None, fresh: bool = True) -> VisitorsResult:
    """fresh=False: Google did not answer today and `chrome` is yesterday's
    snapshot — shown on the card, but no incident opens or closes on it."""
    site_id = await get_or_create_site(url)
    known = crux.has_data(chrome) and fresh
    r = VisitorsResult(url=url, site_id=site_id, values=chrome if known else {})
    slow = crux.slow_parts(r.values)
    if slow:
        r.status, r.error = "warning", message(slow)
    # No data (Google lost the site, no answer today): nothing is known — an open incident stays.
    await apply_incident(r, "visitors", settled=known)
    return r


async def check_all_visitors(urls: list[str]) -> list[VisitorsResult]:
    if not crux.available():
        # The owner switched it off: a finding of a switched-off check is
        # not open any more, and there is nobody to tell.
        for url in urls:
            await resolve_incident(await get_or_create_site(url), "visitors")
        return []
    out = []
    asked: dict[str, dict | None] = {}
    for url in urls:
        try:
            site_id = await get_or_create_site(url)
            chrome = (await sitestatus.get(site_id)).get("crux")
            origin = _origin(url)
            if origin not in asked:
                try:
                    asked[origin] = await crux.query(origin)
                except crux.KeyRejected as e:
                    # Every other site would get the same answer; the health job says it.
                    logger.warning("Visitor speed: Google rejected the key (%s) — the run stops", e)
                    return out
            fresh = asked[origin] is not None     # None: the API was not reachable — keep yesterday's
            if fresh:
                chrome = asked[origin] or {"none": True}
                await sitestatus.update(site_id, "crux", **chrome)
            out.append(await check_visitors(url, chrome, fresh=fresh))
        except Exception as e:   # one site must not stop the others
            logger.error("Visitor speed for %s failed: %r", url, e)
    return out
