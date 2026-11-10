"""
TofsDevOps — personal DevOps in Telegram.
Entry point: запускает бота, планировщик и веб-сервер.
"""

import asyncio
import logging
import os
from urllib.parse import urlsplit

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonDefault

from config import config
from db.database import close_db, count_sites_total, get_or_create_site, get_state, init_db, set_state
from handlers import router
from handlers.common import BOTTOM, MAX_SITES
from handlers.sites import parse_site_input
from handlers.start import BOTTOM_SHOWN
from reports.scheduler import catch_up_after_restart, rerun_seo_after_update, setup_scheduler
from services import integrations, maintenance, notifier, runtime, secrets, settings, updates
from utils.redact import RedactingFormatter
from utils.urls import proxy_label, proxy_problem, site_label
from web.webhook import start_web_server

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def secret_values() -> list[str]:
    """What must never reach a log line, as the bot knows it right now."""
    proxy_password = urlsplit(config.telegram_proxy).password if config.telegram_proxy else None
    return [config.bot_token, proxy_password or "", secrets.webhook_secret(), secrets.heartbeat_secret(),
            integrations.self_heartbeat_url(), integrations.crux_key(), integrations.github_token(),
            integrations.cf_api_token(), integrations.yandex_token(), *integrations.deploy_hooks().values()]


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, secret_values))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


setup_logging()
logger = logging.getLogger(__name__)



async def seed_sites(raw_urls: list[str]) -> list[str]:
    """env SITES → the empty database, through the same check and the same
    limit as a site added from the chat: one typo («tcp://db:5432x») must
    not reach the DB, where it would break every screen that lists sites."""
    added: list[str] = []
    for raw in raw_urls:
        url, problem = parse_site_input(raw)
        if problem or not url:
            logger.warning("SITES: skipping %s — %s", site_label(raw), problem)
            continue
        if url in added:
            continue
        if len(added) >= MAX_SITES:
            logger.warning("SITES: more than %d sites — skipping %s", MAX_SITES, site_label(url))
            continue
        await get_or_create_site(url)
        added.append(url)
        logger.info("  Registered: %s", site_label(url))
    return added


async def on_startup(bot: Bot):
    me = await bot.get_me()
    runtime.set_bot_username(me.username)
    logger.info("Bot started: @%s — TofsDevOps on duty 🐕", me.username)
    # No ☰ menu: its doors are the buttons under the input field. Typed
    # /menu, /help, /mute and /unmute still work (the help names them).
    await bot.delete_my_commands()
    await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
    await _show_bottom_buttons(bot)
    # No «I'm up» ping — silence is the default. A start speaks only when
    # something changed («I got updated») or when the bot was gone long
    # enough that its silence meant nothing («Меня не было …»).
    if runtime.db_on_persistent_disk() is False:
        logger.warning("The database is not on a persistent disk — it is wiped on every deploy")
        await notifier.send(runtime.NO_VOLUME)
    if await updates.report_startup_changes():
        rerun_seo_after_update()
    await catch_up_after_restart()


async def _show_bottom_buttons(bot: Bot):
    """The ☰ menu is gone and its doors are buttons under the input field.
    Telegram shows a keyboard only once a message brings it — say so once."""
    chat = runtime.admin_chat_id()
    if not chat or await get_state(BOTTOM_SHOWN):
        return
    try:
        await bot.send_message(chat, "Главные кнопки теперь всегда под полем ввода: 🏠 Главная, 📖 Что я умею "
                                     "и ❓ Значки. Меню ☰ убрал.", reply_markup=BOTTOM)
        await set_state(BOTTOM_SHOWN, "1")
    except Exception as e:
        logger.warning("Could not show the bottom buttons: %s", e)


def telegram_session() -> AiohttpSession | None:
    """With TELEGRAM_PROXY every call to Telegram — messages, alerts, files —
    goes through it; aiogram resolves api.telegram.org on the proxy side too.
    A malformed value stops the start with a sentence, not a traceback: a bot
    that cannot reach Telegram has no other way to say anything."""
    if not config.telegram_proxy:
        return None
    problem = proxy_problem(config.telegram_proxy)
    if problem:
        raise RuntimeError(f"TELEGRAM_PROXY: {problem}")
    logger.info("Telegram через прокси %s", proxy_label(config.telegram_proxy))
    return AiohttpSession(proxy=config.telegram_proxy)


async def main():
    # The database holds tokens: the files the bot creates — the database,
    # its copies, logs — are for its own user only (also when run without Docker).
    os.umask(0o077)
    if not config.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    bot = Bot(token=config.bot_token, session=telegram_session(), default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        # Reports are full of URLs — without this every message grows a
        # huge site-preview card.
        link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(router)

    # DB and services must be ready before the web server or any scheduled
    # job can touch them.
    logger.info("Initialising database...")
    await init_db()
    await secrets.load()
    await settings.load()
    await maintenance.load()
    await integrations.load()
    await runtime.load()
    notifier.configure(bot)

    # The DB is the source of truth for sites; env SITES only seeds an
    # empty database on the very first run.
    if await count_sites_total() == 0 and config.seed_site_urls():
        logger.info("First run: seeding sites from .env...")
        await seed_sites(config.seed_site_urls())

    web_runner = await start_web_server()
    scheduler = setup_scheduler()
    scheduler.start()
    logger.info("Scheduler started.")
    try:
        await on_startup(bot)
        logger.info("Starting polling...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        await web_runner.cleanup()
        await bot.session.close()
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
