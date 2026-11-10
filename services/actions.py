"""One-tap remediation actions for Cloudflare Pages sites + the alert keyboard.

The sites are static (Cloudflare Pages), so there is nothing to "restart" —
the meaningful actions are: re-run the check, trigger a Pages deploy hook
(rebuild + redeploy), and purge the Cloudflare cache.
"""

import logging
import re
from typing import NamedTuple

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import hosting as hosting_svc
from services import humanize, integrations, maintenance, seo_fixes
from utils.text import esc
from utils.urls import host_of, is_http_url

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
CF_API = "https://api.cloudflare.com/client/v4"   # a constant so tests can point it at a local server
_ZONE_ID = re.compile(r"[0-9a-f]{32}")


def deploy_hook_for(url: str) -> str | None:
    # Hooks are keyed by hostname, but only WEB sites can be redeployed —
    # a tcp://example.com:25 incident must not fire example.com's hook.
    if not is_http_url(url):
        return None
    return integrations.deploy_hooks().get(host_of(url))


def in_cf_zone(url: str, hosting: str | None = None) -> bool:
    """Can «🧹» help this site: the host is in the configured zone (its name is
    learnt on the first purge); until the name is known — when the site is
    served by Cloudflare."""
    if not is_http_url(url) or not integrations.cf_purge_configured():
        return False
    zone, host = integrations.cf_zone_name(), host_of(url)
    if zone:
        return host == zone or host.endswith("." + zone)
    return hosting == "cloudflare"


def alert_actions_keyboard(url: str, site_id: int, incident_id: int | None = None,
                           hosting: str | None = None, api: bool = False) -> InlineKeyboardMarkup:
    """Action buttons attached to availability alerts. The site is addressed
    by its DB id — stable across list edits and well under the 64-byte
    callback_data limit. «:a» on a button marks it as living on an alert:
    its screen answers below instead of replacing the alert text. An API
    check has no page to photograph."""
    http = is_http_url(url)
    rows = [[InlineKeyboardButton(text=f"🔧 Я чиню, {maintenance.fix_length()} тишины", callback_data=f"act:fix:{site_id}"),
             InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data=f"act:recheck:{site_id}")]]
    second = []
    if http and not api:
        second.append(InlineKeyboardButton(text="📸 Как выглядит сайт", callback_data=f"act:shot:{site_id}"))
    if incident_id:
        second.append(InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{incident_id}:a"))
    if second:
        rows.append(second)
    extra = []
    if http and deploy_hook_for(url):
        extra.append(InlineKeyboardButton(text="🚀 Пересобрать сайт", callback_data=f"act:redeploy:{site_id}"))
    if in_cf_zone(url, hosting):
        extra.append(InlineKeyboardButton(text="🧹 Сбросить кэш", callback_data=f"act:purge:{site_id}"))
    if extra:
        rows.append(extra)
    if http and deploy_hook_for(url) and integrations.auto_enabled("redeploy"):
        # The bot will rebuild this site by itself (services.automation): one tap to stop that
        # for a while — the owner may be rolling back by hand. The panel link stays last.
        rows.append([InlineKeyboardButton(text="⏸ Не чинить самому 2 ч", callback_data="auto:pause:2:a")])
    panel = hosting_svc.panel_url(hosting)
    if panel:
        rows.append([InlineKeyboardButton(text=f"🔗 Открыть панель {hosting_svc.label(hosting)}", url=panel)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_keyboard(group_id: int) -> InlineKeyboardMarkup:
    """Buttons of a message about several sites at once (reports.scheduler,
    GROUP_FROM). The per-site buttons are a tap away: the list answers
    below, the alert stays intact. group_id — the first incident of the
    group; its sites live in bot_state (alert_group:<id>)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Показать по сайтам", callback_data=f"grp:list:{group_id}")],
        [InlineKeyboardButton(text=f"🔧 Я чиню все — {maintenance.fix_length()} тишины",
                              callback_data=f"grp:fix:{group_id}")]])


def deep_alert_keyboard(url: str, site_id: int, incident_id: int | None) -> InlineKeyboardMarkup | None:
    """«5xx на страницах»: the home page opens, so «🔧 Я чиню» (silences the
    whole site), «🔍 Проверить сейчас» (checks the home page only) and «📸»
    (photographs the home page) would answer a different question."""
    redeploy = ([InlineKeyboardButton(text="🚀 Пересобрать сайт", callback_data=f"act:redeploy:{site_id}")]
                if deploy_hook_for(url) else [])
    return explain_keyboard(incident_id, redeploy)


def explain_keyboard(incident_id: int | None, *extra_rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    """«ℹ️ Что делать» for an incident, plus any extra rows (links etc.)."""
    rows = [[InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{incident_id}:a")]] if incident_id else []
    rows += [row for row in extra_rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def seo_fix_keyboard(site_id: int, codes: list[str], *, numbered: bool = False, on_alert: bool = False,
                     extra_rows: tuple[list[InlineKeyboardButton], ...] = ()) -> InlineKeyboardMarkup:
    """One «💡 …» button per kind of search/AI finding → its steps screen
    (answered below the message when the buttons sit on an alert)."""
    tail = ":a" if on_alert else ""
    rows = []
    for i, code in enumerate(codes, 1):
        label = seo_fixes.button_label(code)
        rows.append([InlineKeyboardButton(text=f"{i}. {label}" if numbered else label,
                                          callback_data=f"seo_fix:{site_id}:{code}{tail}")])
    rows += [row for row in extra_rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def heartbeat_keyboard(jobs: list[str], seen: frozenset[str] | set[str] = frozenset()) -> InlineKeyboardMarkup | None:
    """Per silent task, the two ways out in one tap: its card (answered
    below) — «🔍 Что с …» for a task that has been in touch (seen), «🔗
    Подключить» for one that never was — and «🗑 Удалить» when there is no
    such task."""
    rows = []
    for job in jobs[:3]:
        if len(f"hb_how:{job}".encode()) > 64:   # an .env name may be anything
            continue
        rows.append([InlineKeyboardButton(text=f"🔍 Что с {job}" if job in seen else f"🔗 Подключить {job}",
                                          callback_data=f"hb_how:{job}"),
                     InlineKeyboardButton(text="🗑 Удалить", callback_data=f"hb_rm:{job}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def domain_keyboard(registrar: str | None, incident_id: int | None = None) -> InlineKeyboardMarkup | None:
    """«ℹ️ Что делать» and a «Продлить у регистратора» link when known."""
    url = humanize.registrar_url(registrar)
    link_row = [InlineKeyboardButton(text=f"🔗 Открыть панель {registrar}", url=url)] if url else []
    return explain_keyboard(incident_id, link_row)


class RedeployResult(NamedTuple):
    ok: bool
    text: str    # one sentence, no icon: the caller adds 🚀 / ❌ or «🤖 …»


async def trigger_redeploy(url: str) -> RedeployResult:
    """Fire the Cloudflare Pages deploy hook. The text says what happened and,
    on a failure, what to do about it. Never the exception text or the hook
    address: the address is the secret."""
    hook = deploy_hook_for(url)
    if not hook:
        return RedeployResult(False, "Пересборка не настроена для этого сайта "
                                     "(⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)")
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(hook) as resp:
                status = resp.status
    except aiohttp.InvalidURL:
        logger.warning("Deploy hook for %s is not a valid URL", host_of(url))
        return RedeployResult(False, "Адрес deploy hook испорчен — задай его заново "
                                     "(⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)")
    except aiohttp.ClientConnectorError:
        logger.warning("Deploy hook for %s: cannot connect", host_of(url))
        return RedeployResult(False, "Не удалось связаться с Cloudflare — нажми «🚀 Пересобрать сайт» чуть позже")
    except TimeoutError:
        logger.warning("Deploy hook for %s: timed out", host_of(url))
        return RedeployResult(False, f"Cloudflare не ответил за {int(_TIMEOUT.total)} с — "
                                     f"нажми «🚀 Пересобрать сайт» чуть позже")
    except Exception as e:
        logger.warning("Deploy hook for %s failed: %s", host_of(url), type(e).__name__)
        return RedeployResult(False, "Не удалось запустить пересборку — подробности в логе бота")
    if status in (200, 201):
        return RedeployResult(True, "Запустил пересборку сайта — Cloudflare Pages соберёт его за 1–2 минуты")
    if status in (404, 410):
        return RedeployResult(False, f"Cloudflare не знает этот хук пересборки (ошибка {status}) — похоже, его "
                                     f"удалили. Создай новый в Cloudflare Pages и вставь его: ⚙️ Настройки → "
                                     f"🔌 Подключения → ☁️ Cloudflare")
    if status >= 500:
        return RedeployResult(False, f"Cloudflare сейчас не принимает команды (ошибка {status}) — "
                                     f"нажми «🚀 Пересобрать сайт» чуть позже")
    return RedeployResult(False, f"Cloudflare не принял команду пересборки (ошибка {status})")


async def purge_cf_cache(url: str) -> str:
    """Purge the Cloudflare cache of this site's host only — the rest of the
    zone (www, api, other subdomains) keeps its cache. Purge by hostname is
    documented for every plan since 2025, but that was not verified against
    a Free zone: if Cloudflare refuses, its own words are shown, and a
    full purge stays one click away in the Cloudflare panel."""
    if not integrations.cf_purge_configured():
        return "❌ Cloudflare API token / zone id не настроены (⚙️ Настройки → 🔌 Подключения → Cloudflare)"
    zone_id = integrations.cf_zone_id()
    if not _ZONE_ID.fullmatch(zone_id):
        return "❌ Zone ID Cloudflare испорчен — задай его заново (⚙️ Настройки → 🔌 Подключения → Cloudflare)"
    zone = f"{CF_API}/zones/{zone_id}"
    auth = {"Authorization": f"Bearer {integrations.cf_api_token()}"}
    host = host_of(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(zone, headers=auth) as resp:
                body, status = await _cf_json(resp), resp.status
            if not body.get("success"):
                return (f"❌ Cloudflare не дал прочитать зону: {esc(_cf_error(body, status))}. "
                        f"Проверь токен (права на зону) и Zone ID")
            name = str((body.get("result") or {}).get("name") or "").lower()
            if name and name != integrations.cf_zone_name():
                await integrations.set_value("cf_zone_name", name)
            # The configured zone is one domain: never purge it for a site
            # that lives elsewhere (another zone, Vercel, …).
            if not name or not (host == name or host.endswith("." + name)):
                return f"❌ {esc(host)} не в зоне Cloudflare {esc(name or '?')} — кэш не трогаю"
            async with s.post(zone + "/purge_cache", json={"hosts": [host]}, headers=auth) as resp:
                body, status = await _cf_json(resp), resp.status
            if body.get("success"):
                return f"🧹 Кэш Cloudflare для {esc(host)} сброшен — посетители увидят свежую версию сайта"
            return "❌ Cloudflare: " + esc(_cf_error(body, status))
    except Exception as e:
        logger.warning("CF cache purge failed: %s", type(e).__name__)
        return f"❌ Не удалось сбросить кэш: {esc(type(e).__name__)} — попробуй через минуту"


async def _cf_json(resp: aiohttp.ClientResponse) -> dict:
    """The v4 envelope, or {} when the answer is not JSON (an HTML error page)."""
    try:
        body = await resp.json(content_type=None)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _cf_error(body: dict, status: int) -> str:
    errors = body.get("errors") or []
    first = errors[0] if errors and isinstance(errors[0], dict) else {}
    return str(first.get("message") or f"HTTP {status}")
