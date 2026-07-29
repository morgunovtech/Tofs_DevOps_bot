import asyncio
import time
import logging

import aiohttp

from config import config
from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
    get_recent_check_statuses,
)
from monitors.second_opinion import second_opinion_up

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)

# Number of consecutive failures required before we open an incident.
# A single transient flap should not page the user.
CONSECUTIVE_FAILURE_THRESHOLD = 2

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 DevOpsBot/1.0"
)


async def check_availability(url: str) -> dict:
    """Check if a site is available. Returns status dict."""
    site_id = await get_or_create_site(url)
    result = {
        "url": url,
        "site_id": site_id,
        "status": "ok",
        "status_code": None,
        "response_time_ms": None,
        "error": None,
        "incident_new": False,
        "incident_id": None,
        "recovered": False,
        "resolved_incident": None,
        # Second-opinion verdict: True = up externally, False = confirmed
        # down, None = not checked / verdict service unreachable.
        "external_ok": None,
    }

    start = time.monotonic()
    try:
        async with aiohttp.ClientSession(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT},
        ) as session:
            async with session.get(url, ssl=True, allow_redirects=True) as resp:
                result["status_code"] = resp.status
                result["response_time_ms"] = int((time.monotonic() - start) * 1000)

                if resp.status >= 400:
                    result["status"] = "error"
                    result["error"] = f"HTTP {resp.status}"
    except aiohttp.ClientError as e:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = str(e)
    except asyncio.TimeoutError:
        result["status"] = "error"
        result["response_time_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = "Timeout (15s)"
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)

    await save_check(
        site_id=site_id,
        check_type="availability",
        status=result["status"],
        response_time_ms=result["response_time_ms"],
        status_code=result["status_code"],
        details=result["error"],
    )

    if result["status"] == "error":
        # Only open an incident after N consecutive failures — single flaps
        # are normal on the public internet.
        recent = await get_recent_check_statuses(
            site_id, "availability", limit=CONSECUTIVE_FAILURE_THRESHOLD,
        )
        if len(recent) >= CONSECUTIVE_FAILURE_THRESHOLD and all(
            s == "error" for s in recent
        ):
            # Second opinion from external nodes before paging: if the site
            # is reachable from outside, the problem is on our side of the
            # network — don't open a "site down" incident for it.
            if config.second_opinion:
                result["external_ok"] = await second_opinion_up(url)
            if result["external_ok"] is True:
                logger.warning(
                    f"{url}: down from here but UP externally — skipping incident"
                )
            else:
                incident_id, is_new = await save_incident(
                    site_id, "availability",
                    f"Site down: {result['error']}",
                    severity="critical",
                )
                result["incident_new"] = is_new
                result["incident_id"] = incident_id
    else:
        resolved = await resolve_incident(site_id, "availability")
        if resolved:
            result["recovered"] = True
            result["resolved_incident"] = resolved

    return result


async def check_all(urls: list[str]) -> list[dict]:
    """Check availability for all URLs concurrently."""
    tasks = [check_availability(url) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
