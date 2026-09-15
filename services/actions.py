"""One-tap remediation actions for Cloudflare Pages sites + the alert keyboard.

The sites are static (Cloudflare Pages), so there is nothing to "restart" —
the meaningful actions are: re-run the check, trigger a Pages deploy hook
(rebuild + redeploy), and purge the Cloudflare cache.
"""

import logging

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from utils.text import esc
from utils.urls import host_of, is_http_url

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def deploy_hook_for(url: str) -> str | None:
    # Hooks are keyed by hostname, but only WEB sites can be redeployed —
    # a tcp://example.com:25 incident must not fire example.com's hook.
    if not is_http_url(url):
        return None
    return config.deploy_hooks.get(host_of(url))


def alert_actions_keyboard(url: str, site_id: int,
                           incident_id: int | None = None) -> InlineKeyboardMarkup:
    """Action buttons attached to availability alerts. The site is addressed
    by its DB id — stable across list edits and well under the 64-byte
    callback_data limit. With an incident_id an «👀 не напоминать» row lets
    the user snooze escalation without closing the incident."""
    http = is_http_url(url)
    first_row = [InlineKeyboardButton(text="🔍 Перепроверить",
                                      callback_data=f"act:recheck:{site_id}")]
    if http:
        first_row.append(InlineKeyboardButton(text="📸 Скрин",
                                              callback_data=f"act:shot:{site_id}"))
    rows = [first_row]
    extra = []
    if http and deploy_hook_for(url):
        extra.append(InlineKeyboardButton(text="🚀 Передеплой",
                                          callback_data=f"act:redeploy:{site_id}"))
    if http and config.cf_api_token and config.cf_zone_id:
        extra.append(InlineKeyboardButton(text="🧹 Сброс кэша CF",
                                          callback_data=f"act:purge:{site_id}"))
    if extra:
        rows.append(extra)
    if incident_id:
        rows.append([InlineKeyboardButton(text="👀 Видел, не напоминать 2 ч",
                                          callback_data=f"ack:{incident_id}:120")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def trigger_redeploy(url: str) -> str:
    """Fire the Cloudflare Pages deploy hook. Returns a human-readable line."""
    hook = deploy_hook_for(url)
    if not hook:
        return "❌ Deploy hook не настроен для этого сайта (DEPLOY_HOOKS в .env)"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(hook) as resp:
                if resp.status in (200, 201):
                    return "🚀 Передеплой запущен — Cloudflare Pages собирает сайт (~1-2 мин)"
                return f"❌ Deploy hook ответил HTTP {resp.status}"
    except Exception as e:
        logger.warning("Deploy hook for %s failed: %s", url, e)
        return f"❌ Не удалось дёрнуть deploy hook: {esc(e)}"


async def purge_cf_cache(url: str) -> str:
    """Purge the whole Cloudflare zone cache (per-host purge needs an
    Enterprise plan; purge_everything works everywhere)."""
    if not (config.cf_api_token and config.cf_zone_id):
        return "❌ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID не настроены"
    api = f"https://api.cloudflare.com/client/v4/zones/{config.cf_zone_id}/purge_cache"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(api, json={"purge_everything": True},
                              headers={"Authorization": f"Bearer {config.cf_api_token}"}) as resp:
                data = await resp.json()
                if data.get("success"):
                    return "🧹 Кэш Cloudflare сброшен по всей зоне"
                errs = data.get("errors") or [{"message": f"HTTP {resp.status}"}]
                return "❌ Cloudflare: " + esc(errs[0].get("message", "ошибка"))
    except Exception as e:
        logger.warning("CF cache purge failed: %s", e)
        return f"❌ Не удалось сбросить кэш: {esc(e)}"
