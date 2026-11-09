"""Google's Chrome UX Report: how fast a site is for its real Chrome
visitors over the last 28 days, at the 75th percentile — the numbers
Search Console shows. Needs a free Google API key (🔌 Подключения); a site
with too few Chrome visitors has no record (404), which is not an error.
"""

import logging

import aiohttp

from services import integrations

logger = logging.getLogger(__name__)

API = "https://chromeuxreport.googleapis.com/v1/records:queryRecord"
TIMEOUT = aiohttp.ClientTimeout(total=20)
_METRICS = {"largest_contentful_paint": "lcp", "interaction_to_next_paint": "inp",
            "cumulative_layout_shift": "cls", "experimental_time_to_first_byte": "ttfb"}


def available() -> bool:
    return bool(integrations.crux_key())


def parse(record: dict) -> dict:
    """{"lcp", "inp", "cls", "ttfb"} p75 values out of a CrUX record."""
    out: dict = {key: None for key in _METRICS.values()}
    for name, key in _METRICS.items():
        p75 = ((record.get("metrics") or {}).get(name) or {}).get("percentiles", {}).get("p75")
        try:
            out[key] = float(p75) if key == "cls" else int(float(p75))
        except (TypeError, ValueError):
            pass
    return out


async def query(origin: str, key: str | None = None) -> dict | None:
    """p75 values for the origin; {} when Google has too little data for it;
    None when the API could not be asked (no key, rejected key, network)."""
    key = key or integrations.crux_key()
    if not key:
        return None
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.post(API, params={"key": key}, json={"origin": origin}) as resp:
                if resp.status == 404:
                    return {}
                if resp.status != 200:
                    logger.warning("CrUX for %s: HTTP %s", origin, resp.status)
                    return None
                return parse((await resp.json()).get("record") or {})
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.warning("CrUX for %s failed: %s", origin, e)
        return None


async def key_works(key: str) -> bool:
    """A live question about a site Google surely has data for."""
    return bool(await query("https://www.google.com", key))
