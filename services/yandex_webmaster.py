"""
Yandex.Webmaster API v4 client — indexing status and site problems.

Setup: create an OAuth token with the `webmaster:read` scope at
https://oauth.yandex.ru (create an app → get the token) and put it in
.env as YANDEX_WEBMASTER_TOKEN. The token sees every host verified for
that Yandex account, so one token covers all sites.
"""

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp

from services import integrations
from utils.text import err_text

logger = logging.getLogger(__name__)

# Module-level so tests can point it at a fake server.
BASE = "https://api.webmaster.yandex.net/v4"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
PARALLEL = 4            # host summaries asked at once
TOTAL_BUDGET_SEC = 60   # the whole walk over the hosts

# Problem severities worth alerting about (docs: FATAL > CRITICAL >
# POSSIBLE_PROBLEM > RECOMMENDATION).
ALERT_SEVERITIES = {"FATAL", "CRITICAL"}


class TokenRejected(Exception):
    """Yandex answered 401/403: the token expired, was revoked or lacks the scope."""


def available() -> bool:
    return bool(integrations.yandex_token())


def _headers() -> dict:
    return {"Authorization": f"OAuth {integrations.yandex_token()}"}


async def _get(session: aiohttp.ClientSession, path: str) -> dict | None:
    async with session.get(BASE + path, headers=_headers()) as resp:
        if resp.status in (401, 403):
            raise TokenRejected(f"токен не принят (HTTP {resp.status})")
        if resp.status != 200:
            # A 5xx page is HTML: reading it as JSON would abort every host.
            logger.warning("Yandex.Webmaster %s HTTP %s: %s", path, resp.status, (await resp.text())[:200])
            return None
        return await resp.json()


def _summary(summary: dict) -> dict:
    """One host's summary as the bot keeps it."""
    problems = summary.get("site_problems") or {}
    # API v4 shape: {"FATAL": 1, "POSSIBLE_PROBLEM": 3, ...} —
    # KEYS are severities, VALUES are counts. Filtering by value
    # (the old bug) meant alerting could never fire.
    return {
        "sqi": summary.get("sqi"),
        "searchable_pages": summary.get("searchable_pages_count"),
        "excluded_pages": summary.get("excluded_pages_count"),
        "problems": problems,
        "alert_problems": {
            k: v for k, v in problems.items()
            if str(k).upper() in ALERT_SEVERITIES and v
        },
    }


def _main_hosts(hosts: list[dict]) -> dict[str, dict]:
    """{hostname: host record} of the verified main hosts: a mirror
    (http://example.com of https://example.com, a www. twin) is the same
    site and must not overwrite the main host's numbers and problems; when
    Yandex does not say which is the mirror, https wins over http."""
    out: dict[str, dict] = {}
    for h in hosts:
        if not isinstance(h, dict) or not h.get("verified") or h.get("main_mirror"):
            continue
        host_id = str(h.get("host_id") or "")
        host = urlparse(h.get("ascii_host_url", "")).hostname or host_id
        if host in out and host_id.startswith("http:"):
            continue
        out[host] = h
    return out


async def get_summaries(only: set[str] | None = None) -> dict[str, dict] | None:
    """{host (e.g. 'app.example.com'): summary} for every verified main
    host — or for the hosts in `only`. None when Webmaster itself could not
    be asked; a host whose summary failed is left out, not the others.

    summary: {sqi, searchable_pages, excluded_pages, problems: {type: severity}}
    """
    if not available():
        return None
    try:
        async with asyncio.timeout(TOTAL_BUDGET_SEC), aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            user = await _get(session, "/user")
            if not user:
                await integrations.record_result("yandex", False, "Вебмастер не ответил")
                return None
            uid = user.get("user_id")
            hosts_data = await _get(session, f"/user/{uid}/hosts")
            if not hosts_data:
                await integrations.record_result("yandex", False, "Вебмастер не ответил")
                return None
            wanted = _main_hosts(hosts_data.get("hosts") or [])
            if only is not None:
                wanted = {host: h for host, h in wanted.items() if host in only}
            sem = asyncio.Semaphore(PARALLEL)

            async def one(h: dict) -> dict | None:
                async with sem:
                    summary = await _get(session, f"/user/{uid}/hosts/{h['host_id']}/summary")
                return _summary(summary) if isinstance(summary, dict) else None

            out: dict[str, dict] = {}
            answers = await asyncio.gather(*(one(h) for h in wanted.values()), return_exceptions=True)
            for host, summary in zip(wanted, answers, strict=True):
                if isinstance(summary, TokenRejected):
                    raise summary
                if isinstance(summary, (aiohttp.ClientError, TimeoutError, ValueError)):
                    # HTML with a 200, a reset, a timeout — this host only.
                    logger.warning("Yandex.Webmaster summary of %s failed: %s", host, err_text(summary))
                    continue
                if isinstance(summary, BaseException):
                    raise summary
                if summary is not None:
                    out[host] = summary
        await integrations.record_result("yandex", True)
        return out
    except TokenRejected as e:
        logger.warning("Yandex.Webmaster: %s", err_text(e))
        await integrations.record_result("yandex", False, str(e), refused=True)
        return None
    except Exception as e:
        logger.warning("Yandex.Webmaster failed: %s", err_text(e))
        await integrations.record_result("yandex", False, f"Вебмастер не ответил ({err_text(e)})")
        return None
