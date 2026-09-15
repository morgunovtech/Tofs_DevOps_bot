"""
TofsDevOps — personal DevOps in Telegram.
Entry point: запускает бота, планировщик и веб-сервер.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands

from config import config
from db.database import close_db, count_sites_total, get_or_create_site, init_db
from handlers import router
from reports.scheduler import setup_scheduler
from services import integrations, maintenance, notifier, runtime, secrets, settings, updates
from web.webhook import start_web_server

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="status", description="Быстрый статус сайтов"),
    BotCommand(command="sites", description="Список сайтов"),
    BotCommand(command="mute", description="Заглушить алерты (напр. /mute 8h)"),
    BotCommand(command="unmute", description="Снять заглушку"),
    BotCommand(command="help", description="Справка"),
]


async def on_startup(bot: Bot):
    me = await bot.get_me()
    runtime.set_bot_username(me.username)
    logger.info("Bot started: @%s — TofsDevOps on duty 🐕", me.username)
    await bot.set_my_commands(COMMANDS)
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    # Intentionally no startup ping — silence is the default. The only
    # startup message is "I got updated", and only when something changed.
    await updates.report_startup_changes()


async def main():
    if not config.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(
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
        for url in config.seed_site_urls():
            await get_or_create_site(url)
            logger.info("  Registered: %s", url)

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
