"""One-tap remediation actions for Cloudflare Pages sites + the alert keyboard.

The sites are static (Cloudflare Pages), so there is nothing to "restart" —
the meaningful actions are: re-run the check, trigger a Pages deploy hook
(rebuild + redeploy), and purge the Cloudflare cache.
"""

import logging

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import hosting as hosting_svc
from services import humanize, integrations, seo_fixes, settings
from utils.text import esc
from utils.urls import host_of, is_http_url

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def deploy_hook_for(url: str) -> str | None:
    # Hooks are keyed by hostname, but only WEB sites can be redeployed —
    # a tcp://example.com:25 incident must not fire example.com's hook.
    if not is_http_url(url):
        return None
    return integrations.deploy_hooks().get(host_of(url))


def alert_actions_keyboard(url: str, site_id: int, incident_id: int | None = None,
                           hosting: str | None = None, api: bool = False) -> InlineKeyboardMarkup:
    """Action buttons attached to availability alerts. The site is addressed
    by its DB id — stable across list edits and well under the 64-byte
    callback_data limit. «:a» on a button marks it as living on an alert:
    its screen answers below instead of replacing the alert text. An API
    check has no page to photograph."""
    http = is_http_url(url)
    rows = [[InlineKeyboardButton(text="🔧 Я чиню, час тишины", callback_data=f"act:fix:{site_id}"),
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
    if http and integrations.cf_purge_configured():
        extra.append(InlineKeyboardButton(text="🧹 Сбросить кэш", callback_data=f"act:purge:{site_id}"))
    if extra:
        rows.append(extra)
    panel = hosting_svc.panel_url(hosting)
    if panel:
        rows.append([InlineKeyboardButton(text=f"🔗 Открыть панель {hosting_svc.label(hosting)}", url=panel)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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


def heartbeat_keyboard(jobs: list[str]) -> InlineKeyboardMarkup | None:
    """Per silent task, the two ways out in one tap: «🔗 Подключить» (the
    line for its schedule, answered below) and «🗑 Удалить» when there is no
    such task. An .env task can only be removed from .env — no button."""
    rows = []
    for job in jobs[:3]:
        if len(f"hb_how:{job}".encode()) > 64:   # an .env name may be anything
            continue
        row = [InlineKeyboardButton(text=f"🔗 Подключить {job}", callback_data=f"hb_how:{job}")]
        if job in settings.ui_heartbeat_jobs():
            row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"hb_rm:{job}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def domain_keyboard(registrar: str | None, incident_id: int | None = None) -> InlineKeyboardMarkup | None:
    """«ℹ️ Что делать» and a «Продлить у регистратора» link when known."""
    url = humanize.registrar_url(registrar)
    link_row = [InlineKeyboardButton(text=f"🔗 Открыть панель {registrar}", url=url)] if url else []
    return explain_keyboard(incident_id, link_row)


async def trigger_redeploy(url: str) -> str:
    """Fire the Cloudflare Pages deploy hook. Returns a human-readable line."""
    hook = deploy_hook_for(url)
    if not hook:
        return "❌ Пересборка не настроена для этого сайта (🩺 Диагностика → Cloudflare)"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(hook) as resp:
                if resp.status in (200, 201):
                    return "🚀 Запустил пересборку сайта — Cloudflare Pages соберёт его за 1–2 минуты"
                return f"❌ Cloudflare не принял команду пересборки (ошибка {resp.status})"
    except Exception as e:
        logger.warning("Deploy hook for %s failed: %s", url, e)
        return f"❌ Не удалось запустить пересборку: {esc(str(e) or type(e).__name__)}"


async def purge_cf_cache(url: str) -> str:
    """Purge the whole Cloudflare zone cache (per-host purge needs an
    Enterprise plan; purge_everything works everywhere)."""
    if not integrations.cf_purge_configured():
        return "❌ Cloudflare API token / zone id не настроены (🩺 Диагностика → Cloudflare)"
    zone = f"https://api.cloudflare.com/client/v4/zones/{integrations.cf_zone_id()}"
    auth = {"Authorization": f"Bearer {integrations.cf_api_token()}"}
    host = host_of(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            # The configured zone is one domain: never purge it for a site
            # that lives elsewhere (another zone, Vercel, …).
            async with s.get(zone, headers=auth) as resp:
                name = str(((await resp.json()).get("result") or {}).get("name") or "").lower()
            if not name or not (host == name or host.endswith("." + name)):
                return f"❌ {esc(host)} не в зоне Cloudflare {esc(name or '?')} — кэш не трогаю"
            async with s.post(zone + "/purge_cache", json={"purge_everything": True}, headers=auth) as resp:
                data = await resp.json()
                if data.get("success"):
                    return "🧹 Кэш Cloudflare сброшен — посетители увидят свежую версию сайта"
                errs = data.get("errors") or [{"message": f"HTTP {resp.status}"}]
                return "❌ Cloudflare: " + esc(errs[0].get("message", "ошибка"))
    except Exception as e:
        logger.warning("CF cache purge failed: %s", e)
        return f"❌ Не удалось сбросить кэш: {esc(str(e) or type(e).__name__)}"
