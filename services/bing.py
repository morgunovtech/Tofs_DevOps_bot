"""Bing Webmaster Tools — the technical side only: is the site verified, is
Bing indexing it, what its crawler stumbles on, do the sitemaps read; and,
on Sundays, clicks and impressions as a breakage signal next to Google's.
Bing's index feeds ChatGPT's and Copilot's search.

Setup: Bing Webmaster → Settings → API Access → Generate API Key (one key
per user; it sees every site of that account), then «⚙️ Настройки → 🔌
Подключения → 🔎 Bing» or BING_WEBMASTER_KEY. Read-only: only the GET
methods below are called.

API (learn.microsoft.com, IWebmasterApi): https://ssl.bing.com/webmaster/
api.svc/json/<Method>?apikey=…, the answer wrapped in «d», dates as
«/Date(1315349995266-0700)/». What the reference does not define is said
so: there is no «indexed» flag — CrawlStats.InIndex is the closest, so the
bot says «страниц в индексе Bing: N», never «главная в индексе». The
error format on the wire is not documented: 401/403, or an ErrorCode that
names the key, is a refused key; anything else is «Bing did not answer».
Same contract as Search Console: no verdict, no alarm.
"""

import logging
import re
from datetime import UTC, date, datetime, timedelta

import aiohttp

from services import integrations
from services.integrations import Probe
from utils.i18n import t
from utils.text import err_text
from utils.urls import host_of

logger = logging.getLogger(__name__)

BASE = "https://ssl.bing.com/webmaster/api.svc/json"   # module-level: tests point it at a local server
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=15)
_DATE = re.compile(r"/Date\((-?\d+)")


class KeyRejected(Exception):
    """Bing turned the key down."""


def available() -> bool:
    return bool(integrations.bing_key())


def parse_date(raw) -> datetime | None:
    """«/Date(1315349995266-0700)/» → aware UTC datetime (the number is
    milliseconds since the epoch; the offset only says where it was written)."""
    m = _DATE.match(str(raw or ""))
    return datetime.fromtimestamp(int(m.group(1)) / 1000, UTC) if m else None


async def _call(session: aiohttp.ClientSession, method: str, key: str | None = None, **params):
    """The «d» of a GET; None when Bing did not answer. Raises KeyRejected."""
    params = {**params, "apikey": key or integrations.bing_key()}
    async with session.get(f"{BASE}/{method}", params=params,
                           headers={"Content-Type": "application/json; charset=utf-8"}) as resp:
        try:
            body = await resp.json(content_type=None)
        except ValueError:
            body = None
        code = str((body or {}).get("ErrorCode") or "") if isinstance(body, dict) else ""
        if resp.status in (401, 403) or "key" in code.lower() or "auth" in code.lower():
            raise KeyRejected(t("ключ не принят ({why})").format(why=code or f"HTTP {resp.status}"))
        if resp.status != 200 or not isinstance(body, dict) or "d" not in body:
            logger.warning("Bing %s: HTTP %s %s", method, resp.status, code)
            return None
        return body["d"]


async def check_key(key: str | None = None) -> tuple[Probe, int | None]:
    """(answer, sites in the account) — the wizard's live check."""
    try:
        async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT) as session:
            sites = await _call(session, "GetUserSites", key=key)
    except KeyRejected:
        return Probe.REJECTED, None
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Bing key check failed: %s", err_text(e))
        return Probe.UNREACHABLE, None
    if not isinstance(sites, list):
        return Probe.UNREACHABLE, None
    return Probe.OK, len(sites)


def _latest(rows, field: str) -> int | None:
    dated = [(parse_date(r.get("Date")), r.get(field)) for r in rows or [] if isinstance(r, dict)]
    dated = [(d, v) for d, v in dated if d is not None and isinstance(v, int)]
    return max(dated)[1] if dated else None


def week_totals(rows, today: date | None = None) -> tuple[dict, dict] | None:
    """(this week, the week before): {clicks, impressions} over the last 7
    full days of RankAndTrafficStats against the 7 before; None when Bing
    gave no days at all."""
    days = {}
    for r in rows or []:
        d = parse_date(r.get("Date")) if isinstance(r, dict) else None
        if d is not None:
            days[d.date()] = (int(r.get("Clicks") or 0), int(r.get("Impressions") or 0))
    if not days:
        return None
    end = (today or datetime.now(UTC).date()) - timedelta(days=1)

    def total(last: date) -> dict:
        span = [days.get(last - timedelta(days=i), (0, 0)) for i in range(7)]
        return {"clicks": sum(c for c, _ in span), "impressions": sum(i for _, i in span)}
    return total(end), total(end - timedelta(days=7))


async def site_states(urls: list[str]) -> dict[str, dict] | None:
    """{host: state} for the watched hosts that are in the Bing account:
    {verified, in_index, issues, bad_feeds}. None when Bing could not be
    asked (the key, the network) — then nothing is concluded."""
    if not available():
        return None
    wanted = {host_of(u) for u in urls}
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            sites = await _call(session, "GetUserSites")
            if not isinstance(sites, list):
                await integrations.record_result("bing", False, t("Bing не ответил"))
                return None
            out: dict[str, dict] = {}
            for s in sites:
                site_url = str(s.get("Url") or "") if isinstance(s, dict) else ""
                host = host_of(site_url)
                if host not in wanted or host in out:
                    continue
                state = {"site_url": site_url, "verified": bool(s.get("IsVerified"))}
                if state["verified"]:
                    stats = await _call(session, "GetCrawlStats", siteUrl=site_url)
                    issues = await _call(session, "GetCrawlIssues", siteUrl=site_url)
                    feeds = await _call(session, "GetFeeds", siteUrl=site_url)
                    state["in_index"] = _latest(stats, "InIndex") if isinstance(stats, list) else None
                    state["issues"] = len(issues) if isinstance(issues, list) else None
                    state["bad_feeds"] = ([str(f.get("Url") or "") for f in feeds if isinstance(f, dict)
                                           and str(f.get("Status") or "").lower() not in ("success", "")]
                                          if isinstance(feeds, list) else None)
                out[host] = state
        await integrations.record_result("bing", True)
        return out
    except KeyRejected as e:
        await integrations.record_result("bing", False, str(e), refused=True)
        return None
    except (aiohttp.ClientError, TimeoutError) as e:
        logger.warning("Bing Webmaster failed: %s", err_text(e))
        await integrations.record_result("bing", False, t("Bing не ответил ({why})").format(why=err_text(e)))
        return None


async def user_sites() -> dict[str, str] | None:
    """{host: the site's address in Bing} of the verified sites; None when
    Bing could not be asked."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            sites = await _call(session, "GetUserSites")
    except (KeyRejected, aiohttp.ClientError, TimeoutError) as e:
        logger.info("Bing sites: %s", err_text(e))
        return None
    if not isinstance(sites, list):
        return None
    return {host_of(str(s.get("Url") or "")): str(s.get("Url") or "") for s in sites
            if isinstance(s, dict) and s.get("IsVerified") and s.get("Url")}


async def traffic(site_url: str) -> tuple[dict, dict] | None:
    """The week's clicks and impressions against the week before (week_totals)."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            rows = await _call(session, "GetRankAndTrafficStats", siteUrl=site_url)
    except (KeyRejected, aiohttp.ClientError, TimeoutError) as e:
        logger.info("Bing traffic of %s: %s", site_url, err_text(e))
        return None
    return week_totals(rows) if isinstance(rows, list) else None
