"""Daily verdict on real visitors' speed (services.rum): asks Google's
Chrome UX Report for each site's origin when a key is set, then takes the
owner's own measurement when it has enough page views, else Chrome's.
Slow by Google's yardstick (p75 in the red zone) is a quiet 🟠 «visitors»
incident; back to not-slow closes it."""

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from db.database import get_or_create_site
from monitors.base import CheckResult
from monitors.ladder import apply_incident
from services import crux, rum, sitestatus

logger = logging.getLogger(__name__)


@dataclass
class VisitorsResult(CheckResult):
    source: str | None = None
    values: dict = field(default_factory=dict)


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def message(slow: list[str]) -> str:
    """«у посетителей страница появляется за 5.2 с»."""
    return "у посетителей " + "; ".join(slow)


async def check_visitors(url: str, chrome: dict | None, manage: bool = True) -> VisitorsResult:
    site_id = await get_or_create_site(url)
    source, values = await rum.best_source(site_id, chrome)
    r = VisitorsResult(url=url, site_id=site_id, source=source, values=values)
    slow = rum.slow_parts(values) if source else []
    if slow:
        r.status, r.error = "warning", message(slow)
    # No data (the script was removed, Google lost the site): nothing is known — an open incident stays.
    await apply_incident(r, "visitors", settled=source is not None, open_new=manage)
    return r


async def check_all_visitors(urls: list[str]) -> list[VisitorsResult]:
    out = []
    asked: dict[str, dict | None] = {}
    for url in urls:
        try:
            site_id = await get_or_create_site(url)
            chrome = (await sitestatus.get(site_id)).get("crux")
            if crux.available():
                origin = _origin(url)
                if origin not in asked:
                    asked[origin] = await crux.query(origin)
                if asked[origin] is not None:     # None: the API was not reachable — keep yesterday's
                    chrome = asked[origin] or {"none": True}
                    await sitestatus.update(site_id, "crux", **chrome)
            out.append(await check_visitors(url, chrome))
        except Exception as e:   # one site must not stop the others
            logger.error("Visitor speed for %s failed: %r", url, e)
    return out
