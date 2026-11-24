"""The Cloudflare API: one client for every part of the bot that asks it —
the cache purge (services.actions), the account's audit log
(services.cf_audit), the Pages deployments behind a short outage
(services.causes).

The token and the Zone ID come from «⚙️ Настройки → 🔌 Подключения →
☁️ Cloudflare» or CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID. The account is
the zone's (GET /zones/{id} → account.id), learnt on first use. Every read
needs its own permission on the token: a refusal names the permission that
is missing (PERMISSIONS), so the owner adds exactly that — the token stays
read-only apart from the purge.
"""

import logging
import re
from datetime import datetime

import aiohttp

from services import integrations
from utils.i18n import t

logger = logging.getLogger(__name__)

API = "https://api.cloudflare.com/client/v4"   # a constant so tests can point it at a local server
TIMEOUT = aiohttp.ClientTimeout(total=30)
ZONE_ID = re.compile(r"[0-9a-f]{32}")

# What each read needs, as the token editor names it (dash.cloudflare.com →
# My Profile → API Tokens → Edit). The audit log v2 accepts «Audit Logs Read»
# or «Account Settings Read»; the second one is in every account's list.
PERMISSIONS = {
    "zone": "Zone → Zone → Read",
    "audit": "Account → Account Settings → Read",
    "pages": "Account → Cloudflare Pages → Read",
}

_account: dict[str, str] = {}   # zone id → account id, learnt once per run


class CloudflareError(Exception):
    """Cloudflare could not be asked; the message is a short human reason.
    refused — the token was turned down (401/403): the permission is missing
    or the token is wrong; a network failure or a 5xx is not refused."""

    def __init__(self, message: str, refused: bool = False):
        super().__init__(message)
        self.refused = refused


def configured() -> bool:
    """A token and a well-formed Zone ID: everything here needs both."""
    return bool(integrations.cf_api_token()) and bool(ZONE_ID.fullmatch(integrations.cf_zone_id()))


def headers(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or integrations.cf_api_token()}"}


async def envelope(resp: aiohttp.ClientResponse) -> dict:
    """The v4 envelope, or {} when the answer is not JSON (an HTML error page)."""
    try:
        body = await resp.json(content_type=None)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def error_text(body: dict, status: int) -> str:
    errors = body.get("errors") or []
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    return str(first.get("message") or f"HTTP {status}")


async def get(session: aiohttp.ClientSession, path: str, need: str, **params) -> tuple[object, dict]:
    """(result, result_info) of GET {API}{path}. need — the PERMISSIONS key
    the call depends on: a refusal says which permission to add."""
    try:
        async with session.get(f"{API}{path}", params={k: v for k, v in params.items() if v is not None},
                               headers=headers()) as resp:
            body, status = await envelope(resp), resp.status
    except (TimeoutError, aiohttp.ClientError) as e:
        logger.warning("Cloudflare %s: %s", path.split("/")[1], type(e).__name__)
        raise CloudflareError(t("Cloudflare не ответил")) from e
    if status in (401, 403):
        logger.warning("Cloudflare %s: HTTP %s (%s)", need, status, error_text(body, status))
        raise CloudflareError(t("токену не хватает права {permission}").format(permission=PERMISSIONS[need]),
                              refused=True)
    if status != 200 or not body.get("success"):
        raise CloudflareError(t("Cloudflare ответил ошибкой: {why}").format(why=error_text(body, status)))
    info = body.get("result_info")
    return body.get("result"), info if isinstance(info, dict) else {}


async def account_id(session: aiohttp.ClientSession) -> str:
    """The account of the configured zone: the audit log and Pages live there."""
    zone = integrations.cf_zone_id()
    if zone not in _account:
        result, _ = await get(session, f"/zones/{zone}", "zone")
        acc = str(((result or {}).get("account") or {}).get("id") or "") if isinstance(result, dict) else ""
        if not acc:
            raise CloudflareError(t("Cloudflare не назвал аккаунт зоны"))
        _account[zone] = acc
    return _account[zone]


def forget():
    """Another zone or token: the account is learnt again."""
    _account.clear()


def parse_time(raw) -> datetime | None:
    """Cloudflare's RFC 3339 («2026-11-24T14:02:11.123456Z») → aware datetime."""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Pages ────────────────────────────────────────────────────────────────────

async def pages_projects(session: aiohttp.ClientSession) -> list[dict]:
    """The account's Pages projects: name, the *.pages.dev subdomain and the
    custom domains (fields as in the API reference: name, subdomain, domains)."""
    acc = await account_id(session)
    out: list[dict] = []
    for page in range(1, 6):
        result, info = await get(session, f"/accounts/{acc}/pages/projects", "pages", page=page, per_page=10)
        items = [p for p in result or [] if isinstance(p, dict) and p.get("name")]
        out += items
        total = int(info.get("total_pages") or 1) if str(info.get("total_pages") or "1").isdigit() else 1
        if not items or page >= total:
            break
    return out


def project_hosts(project: dict) -> set[str]:
    hosts = {str(d).lower() for d in project.get("domains") or [] if isinstance(d, str)}
    if isinstance(project.get("subdomain"), str):
        hosts.add(project["subdomain"].lower())
    return hosts


async def deployments(session: aiohttp.ClientSession, project: str) -> list[dict]:
    """The latest production deployments of a project (one page, newest
    first): id, short_id, created_on, latest_stage, deployment_trigger."""
    acc = await account_id(session)
    result, _ = await get(session, f"/accounts/{acc}/pages/projects/{project}/deployments", "pages",
                          env="production", per_page=10)
    return [d for d in result or [] if isinstance(d, dict)]
