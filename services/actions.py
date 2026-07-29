"""
One-tap remediation actions for Cloudflare Pages sites + the alert keyboard.

The sites are static (Cloudflare Pages), so there is nothing to "restart" —
the meaningful actions are: re-run the check, trigger a Pages deploy hook
(rebuild + redeploy), and purge the Cloudflare cache.
"""

import logging
from urllib.parse import urlparse

import aiohttp
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config
from db.database import get_or_create_site

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def deploy_hook_for(url: str) -> str | None:
    return config.deploy_hooks.get(_host(url))


async def alert_actions_keyboard(url: str) -> InlineKeyboardMarkup:
    """Action buttons attached to availability alerts. Site is addressed by
    its DB id — stable across site list edits and short enough for the
    64-byte callback_data limit."""
    site_id = await get_or_create_site(url)
    rows = [[
        InlineKeyboardButton(text="🔍 Перепроверить", callback_data=f"act:recheck:{site_id}"),
        InlineKeyboardButton(text="📸 Скрин", callback_data=f"act:shot:{site_id}"),
    ]]
    extra = []
    if deploy_hook_for(url):
        extra.append(InlineKeyboardButton(text="🚀 Передеплой", callback_data=f"act:redeploy:{site_id}"))
    if config.cf_api_token and config.cf_zone_id:
        extra.append(InlineKeyboardButton(text="🧹 Сброс кэша CF", callback_data=f"act:purge:{site_id}"))
    if extra:
        rows.append(extra)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def trigger_redeploy(url: str) -> str:
    """Fire the Cloudflare Pages deploy hook for the site. Returns a
    human-readable result line."""
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
        logger.warning(f"Deploy hook for {url} failed: {e}")
        return f"❌ Не удалось дёрнуть deploy hook: {e}"


async def purge_cf_cache(url: str) -> str:
    """Purge the whole Cloudflare zone cache (per-host purge needs an
    Enterprise plan; purge_everything works everywhere)."""
    if not (config.cf_api_token and config.cf_zone_id):
        return "❌ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID не настроены"
    api = f"https://api.cloudflare.com/client/v4/zones/{config.cf_zone_id}/purge_cache"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(
                api,
                json={"purge_everything": True},
                headers={"Authorization": f"Bearer {config.cf_api_token}"},
            ) as resp:
                data = await resp.json()
                if data.get("success"):
                    return "🧹 Кэш Cloudflare сброшен по всей зоне"
                errs = data.get("errors") or [{"message": f"HTTP {resp.status}"}]
                return f"❌ Cloudflare: {errs[0].get('message', 'ошибка')}"
    except Exception as e:
        logger.warning(f"CF cache purge failed: {e}")
        return f"❌ Не удалось сбросить кэш: {e}"
