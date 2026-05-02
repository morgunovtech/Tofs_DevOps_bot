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

from config import config
from db.database import init_db, get_or_create_site
from handlers.commands import router as commands_router
from reports.scheduler import setup_scheduler
from web.webhook import start_web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def on_startup(bot: Bot):
    """Actions to perform on startup."""
    logger.info("Initialising database...")
    await init_db()

    logger.info("Registering configured sites...")
    for url in config.get_site_urls():
        await get_or_create_site(url)
        logger.info(f"  Registered: {url}")

    me = await bot.get_me()
    logger.info(f"Bot started: @{me.username}")
    # Intentionally no startup ping — silence is the default.
    # Use /menu to interact, /status to verify the bot is alive.


async def main():
    if not config.bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")
    if not config.admin_chat_id:
        raise RuntimeError("TELEGRAM_ADMIN_CHAT_ID is not set in .env")

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(commands_router)

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


if __name__ == "__main__":
    asyncio.run(main())
