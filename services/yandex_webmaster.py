"""
Yandex.Webmaster API v4 client — indexing status and site problems.

Setup: create an OAuth token with the `webmaster:read` scope at
https://oauth.yandex.ru (create an app → get the token) and put it in
.env as YANDEX_WEBMASTER_TOKEN. The token sees every host verified for
that Yandex account, so one token covers all sites.
"""

import logging
from urllib.parse import urlparse

import aiohttp

from config import config

logger = logging.getLogger(__name__)

# Module-level so tests can point it at a fake server.
BASE = "https://api.webmaster.yandex.net/v4"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Problem severities worth alerting about (docs: FATAL > CRITICAL >
# POSSIBLE_PROBLEM > RECOMMENDATION).
ALERT_SEVERITIES = {"FATAL", "CRITICAL"}


def available() -> bool:
    return bool(config.yandex_webmaster_token)


def _headers() -> dict:
    return {"Authorization": f"OAuth {config.yandex_webmaster_token}"}


async def _get(session: aiohttp.ClientSession, path: str) -> dict | None:
    async with session.get(BASE + path, headers=_headers()) as resp:
        data = await resp.json()
        if resp.status != 200:
            logger.warning(f"Yandex.Webmaster {path} HTTP {resp.status}: {data}")
            return None
        return data


async def get_summaries() -> dict[str, dict] | None:
    """{host (e.g. 'app.example.com'): summary} for every verified host.

    summary: {sqi, searchable_pages, excluded_pages, problems: {type: severity}}
    """
    if not available():
        return None
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            user = await _get(session, "/user")
            if not user:
                return None
            uid = user.get("user_id")
            hosts_data = await _get(session, f"/user/{uid}/hosts")
            if not hosts_data:
                return None

            out: dict[str, dict] = {}
            for h in hosts_data.get("hosts", []):
                if not h.get("verified"):
                    continue
                host_id = h["host_id"]
                host = (urlparse(h.get("ascii_host_url", "")).hostname
                        or host_id)
                summary = await _get(session, f"/user/{uid}/hosts/{host_id}/summary")
                if summary is None:
                    continue
                problems = summary.get("site_problems") or {}
                # API v4 shape: {"FATAL": 1, "POSSIBLE_PROBLEM": 3, ...} —
                # KEYS are severities, VALUES are counts. Filtering by value
                # (the old bug) meant alerting could never fire.
                out[host] = {
                    "sqi": summary.get("sqi"),
                    "searchable_pages": summary.get("searchable_pages_count"),
                    "excluded_pages": summary.get("excluded_pages_count"),
                    "problems": problems,
                    "alert_problems": {
                        k: v for k, v in problems.items()
                        if str(k).upper() in ALERT_SEVERITIES and v
                    },
                }
            return out
    except Exception as e:
        logger.warning(f"Yandex.Webmaster failed: {e}")
        return None
