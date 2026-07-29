"""
DevOps Monitoring Telegram Bot
Entry point: запускает бота, планировщик и webhook-сервер.
"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands

from config import config
from db.database import init_db, get_or_create_site, close_db, \
    count_sites_total, get_state
from handlers.commands import router as commands_router
from reports.scheduler import setup_scheduler
from services import settings
from web.webhook import start_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    """Telegram-side startup actions (DB is already initialised in main)."""
    me = await bot.get_me()
    logger.info(f"Bot started: @{me.username}")

    # Register commands so they show up in Telegram's "/" popup.
    await bot.set_my_commands([
        BotCommand(command="menu",   description="Главное меню"),
        BotCommand(command="status", description="Быстрый статус сайтов"),
        BotCommand(command="sites",  description="Список сайтов"),
        BotCommand(command="mute",   description="Заглушить алерты (напр. /mute 8h)"),
        BotCommand(command="unmute", description="Снять заглушку"),
        BotCommand(command="help",   description="О боте"),
    ])
    # Make the chat's menu button open that command list directly.
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    # Intentionally no startup ping — silence is the default.


async def main():
    if not config.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
            # Reports are full of URLs — without this every message grows a
            # huge site-preview card.
            link_preview_is_disabled=True,
        ),
    )
    dp = Dispatcher()
    dp.include_router(commands_router)

    # DB must be ready before the webhook server or any scheduled job can
    # touch it — a feedback POST or an early cron firing would hit missing
    # tables otherwise.
    logger.info("Initialising database...")
    await init_db()
    await settings.load()

    # Admin can be claimed via /start (first user wins) — restore it from
    # the DB when the env var is empty.
    if not config.admin_chat_id:
        saved = await get_state("admin_chat_id")
        if saved:
            config.admin_chat_id = saved
            config.admin_user_id = await get_state("admin_user_id") or saved
            logger.info("Admin restored from DB.")
        else:
            logger.warning(
                "No admin configured — the first user to /start becomes admin.")

    # The DB is the source of truth for sites; env SITES only seeds an
    # empty database on the very first run (so UI removals survive restarts).
    if await count_sites_total() == 0 and config.get_site_urls():
        logger.info("First run: seeding sites from .env...")
        for url in config.get_site_urls():
            await get_or_create_site(url)
            logger.info(f"  Registered: {url}")

    # Start webhook server (for receiving feedback from sites)
    web_runner = await start_web_server(bot)

    # Set up and start the scheduler
    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started.")

    # Startup actions
    await on_startup(bot)

    try:
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
