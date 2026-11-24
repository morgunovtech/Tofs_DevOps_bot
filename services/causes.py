"""The likely cause of a short outage: what happened on Cloudflare in the
WINDOW before the site went down (and up to AFTER past the first failed
check: the check comes up to an interval late) — a Pages deployment of the project
that serves the host («деплой Pages 7991da4 в 14:02»), or a change in the
zone from the audit log (services.cf_audit, «в 14:01 в Cloudflare: Update
DNS record»). Said in the alert and in «✅ снова открывается».

Only with Cloudflare connected; the deployments need «Account → Cloudflare
Pages → Read», the changes «Account → Account Settings → Read». Nothing found,
nothing asked in BUDGET, Cloudflare refused: no line — the alert never
waits longer than that and never guesses.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

import aiohttp

from services import cf_audit, cloudflare, integrations
from services.cloudflare import CloudflareError
from utils.clock import to_local
from utils.i18n import t
from utils.text import esc
from utils.urls import host_of, is_http_url

logger = logging.getLogger(__name__)

WINDOW = timedelta(minutes=10)
# Later than this past the first failed check it is not the cause — it is
# the fix (the bot's own redeploy, the owner's rollback).
AFTER = timedelta(minutes=1)
BUDGET = 8                        # seconds: the alert waits no longer
PROJECTS_TTL = 3600               # the list of Pages projects changes rarely
_projects: tuple[float, list[dict]] | None = None


async def _project_for(session: aiohttp.ClientSession, host: str) -> str | None:
    global _projects
    if _projects is None or time.monotonic() - _projects[0] > PROJECTS_TTL:
        _projects = (time.monotonic(), await cloudflare.pages_projects(session))
    return next((p["name"] for p in _projects[1] if host in cloudflare.project_hosts(p)), None)


def _commit(d: dict) -> str:
    meta = ((d.get("deployment_trigger") or {}).get("metadata") or {}) if isinstance(d.get("deployment_trigger"), dict) else {}
    commit = meta.get("commit_hash") if isinstance(meta, dict) else None
    return str(commit)[:7] if isinstance(commit, str) and commit else str(d.get("short_id") or "")[:8]


async def _deployment(host: str, start: datetime) -> str | None:
    async with aiohttp.ClientSession(timeout=cloudflare.TIMEOUT) as session:
        project = await _project_for(session, host)
        if not project:
            return None
        found = []
        for d in await cloudflare.deployments(session, project):
            at = cloudflare.parse_time(d.get("created_on"))
            if at and start - WINDOW <= at <= start + AFTER:
                found.append((abs(at - start), at, _commit(d)))
    if not found:
        return None
    _, at, commit = min(found)
    return t("деплой Cloudflare Pages {commit} в {time}").format(commit=esc(commit), time=to_local(at).strftime("%H:%M"))


async def _change(host: str, start: datetime) -> str | None:
    zone = integrations.cf_zone_name()
    if zone and not (host == zone or host.endswith("." + zone)):
        return None
    near = await cf_audit.changes_between(start - WINDOW, start + AFTER, zone or None)
    if not near:
        return None
    at = cloudflare.parse_time(near[-1].get("at"))
    more = t(" и ещё {n}").format(n=len(near) - 1) if len(near) > 1 else ""
    return t("в {time} в Cloudflare: {what}{more}").format(
        time=to_local(at).strftime("%H:%M") if at else "?", what=esc(str(near[-1].get("what") or "")), more=more)


async def near(url: str, start: datetime | None) -> str | None:
    """«Рядом по времени: деплой Cloudflare Pages 7991da4 в 14:02.» — or None.
    The deployments and the newest page of the audit log are asked within
    one BUDGET; each answers or not on its own (a token may read one and
    not the other)."""
    if start is None or not is_http_url(url) or not cloudflare.configured():
        return None
    host = host_of(url)
    deadline = asyncio.get_running_loop().time() + BUDGET
    found: list[str] = []
    for what, ask in (("Pages deployments", lambda: _deployment(host, start)),
                      ("audit log", _fresh_changes)):
        try:
            async with asyncio.timeout_at(deadline):
                answer = await ask()
        except CloudflareError as e:
            logger.info("Cause of the outage of %s: %s — %s", host, what, e)
            continue
        except TimeoutError:
            logger.info("Cause of the outage of %s: %s did not answer in time", host, what)
            continue
        except Exception:
            logger.exception("Cause of the outage of %s: %s failed", host, what)
            continue
        if answer:
            found.append(answer)
    try:
        change = await _change(host, start)
    except Exception:
        logger.exception("Cloudflare changes near the outage of %s not read", host)
        change = None
    if change:
        found.append(change)
    if not found:
        return None
    return t("Рядом по времени: {found}.").format(found="; ".join(found))


async def _fresh_changes() -> None:
    """The newest changes, not the read of up to 15 minutes ago."""
    await cf_audit.poll()
