"""
TofsDevOps — personal DevOps in Telegram.
Entry point: запускает бота, планировщик и веб-сервер.
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
from urllib.parse import urlsplit

from utils.redact import RedactingFormatter

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def secret_values() -> list[str]:
    """What must never reach a log line, as the bot knows it right now.
    Read at the moment a line is written: until the imports below are done
    the names are unbound, the formatter swallows that and masks by patterns."""
    proxy_password = urlsplit(config.telegram_proxy).password if config.telegram_proxy else None
    return [config.bot_token, proxy_password or "", secrets.webhook_secret(), secrets.heartbeat_secret(),
            integrations.self_heartbeat_url(), integrations.crux_key(), integrations.github_token(),
            integrations.cf_api_token(), integrations.yandex_token(), *integrations.deploy_hooks().values()]


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, secret_values))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


# Before `config`: it warns about junk .env values while it is imported, and
# those lines must carry a time and a level like every other.
setup_logging()
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.exceptions import TelegramUnauthorizedError  # noqa: E402
from aiogram.types import MenuButtonDefault  # noqa: E402
from aiogram.utils.token import TokenValidationError, validate_token  # noqa: E402

from config import config  # noqa: E402
from db import database  # noqa: E402
from db.database import (  # noqa: E402
    close_db,
    count_sites_total,
    get_or_create_site,
    get_state,
    init_db,
    set_state,
)
from db.migrations import StartRefused  # noqa: E402
from handlers import router  # noqa: E402
from handlers.common import BOTTOM, MAX_SITES, LeanMemoryStorage  # noqa: E402
from handlers.sites import parse_site_input  # noqa: E402
from handlers.start import BOTTOM_SHOWN  # noqa: E402
from monitors import parse_worker  # noqa: E402
from reports.scheduler import (  # noqa: E402
    cancel_alert_worker,
    catch_up_after_restart,
    clock_stepped,
    drain_alert_worker,
    rerun_seo_after_update,
    setup_scheduler,
)
from services import (  # noqa: E402
    health,
    integrations,
    maintenance,
    notifier,
    runtime,
    secrets,
    settings,
    updates,
)
from utils.urls import proxy_label, proxy_problem, site_label  # noqa: E402
from web.webhook import start_web_server  # noqa: E402

# What a stop may take: in-flight checks and alerts finish within this, the
# rest is cancelled. docker-compose.yml gives the whole stop 20 s.
STOP_DRAIN_SEC = 8


class ConfigError(RuntimeError):
    """A setting the bot cannot start with: one sentence in the log, exit code 2."""


class SelfCheckFailed(RuntimeError):
    """The in-process watchdog found a state only a restart cures: exit code 3."""


def check_token(token: str) -> None:
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN не задан — возьми токен у @BotFather и впиши его в .env")
    try:
        validate_token(token)
    except TokenValidationError:
        raise ConfigError("TELEGRAM_BOT_TOKEN не похож на токен бота (цифры, двоеточие, буквы) — "
                          "скопируй его у @BotFather целиком") from None


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


async def whoami(bot: Bot, first_delay: float = 1.0) -> str:
    """Telegram's answer about the bot, waited for: after a host reboot the
    network or the proxy often comes up after the container, and a crash loop
    would only make it worse. Checks already run meanwhile; alerts wait in
    the outbox. bot.me() keeps the answer, so start_polling does not ask again."""
    delay = first_delay
    while True:
        try:
            return (await bot.me()).username
        except TelegramUnauthorizedError:
            raise ConfigError("Telegram не принял TELEGRAM_BOT_TOKEN: токен отозван или скопирован "
                              "не целиком — возьми новый у @BotFather") from None
        except Exception as e:
            logger.warning("Telegram is not reachable yet (%s) — next try in %.0f s", type(e).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def _step(name: str, coro) -> None:
    """A start step that must not stop the start: logged and skipped."""
    try:
        await coro
    except Exception:
        logger.exception("Start step «%s» failed — going on without it", name)


async def on_startup(bot: Bot):
    username = await whoami(bot)
    runtime.set_bot_username(username)
    logger.info("Bot started: @%s — TofsDevOps on duty 🐕", username)
    # No ☰ menu: its doors are the buttons under the input field. The
    # commands are not registered with Telegram either — typed /menu, /help,
    # /mute and /unmute still work, and the help («❓ Значки») names them.
    await _step("delete_my_commands", bot.delete_my_commands())
    await _step("set_chat_menu_button", bot.set_chat_menu_button(menu_button=MenuButtonDefault()))
    await _step("bottom buttons", _show_bottom_buttons(bot))
    # No «I'm up» ping — silence is the default. A start speaks only when
    # something changed («I got updated») or when the bot was gone long
    # enough that its silence meant nothing («Меня не было …»).
    if runtime.db_on_persistent_disk() is False:
        logger.warning("The database is not on a persistent disk — it is wiped on every deploy")
        await _step("volume warning", notifier.send(runtime.NO_VOLUME))
    await _step("update report", _report_update())
    await _step("schema copy note", _report_schema_copy())
    await _step("catch-up after restart", catch_up_after_restart())


async def _report_schema_copy():
    reason = database.migrated_without_copy()
    if reason:
        await notifier.send("🟠 Обновил схему базы без копии перед обновлением: не смог её сохранить "
                            f"({reason}). Откат на прошлую версию потребует ночной копии из data/backups. "
                            "Проверь место на диске с базой.")


async def _report_update():
    if await updates.report_startup_changes():
        rerun_seo_after_update()


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
        raise ConfigError(f"TELEGRAM_PROXY: {problem}")
    logger.info("Telegram через прокси %s", proxy_label(config.telegram_proxy))
    return AiohttpSession(proxy=config.telegram_proxy)


async def main():
    # The database holds tokens: the files the bot creates — the database,
    # its copies, logs — are for its own user only (also when run without Docker).
    os.umask(0o077)
    # SIGTERM during the start: python is PID 1 in the container, and without
    # a handler `docker stop` ends in SIGKILL. start_polling puts aiogram's
    # handlers on top later.
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):       # Windows has no such handlers
            loop.add_signal_handler(sig, task.cancel)
    check_token(config.bot_token)
    bot = Bot(token=config.bot_token, session=telegram_session(), default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        # Reports are full of URLs — without this every message grows a
        # huge site-preview card.
        link_preview_is_disabled=True))
    # Every Bot API call, getMe and getUpdates included, feeds the pulse.
    bot.session.middleware(health.TelegramWatch())
    # The FSM in memory, without a record per stranger who ever wrote (STAB-016).
    dp = Dispatcher(storage=LeanMemoryStorage())
    dp.include_router(router)

    web_runner = scheduler = None
    try:
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
        await on_startup(bot)
        logger.info("Starting polling...")
        # The session stays open after polling: running jobs still send alerts.
        # A storm of taps or strangers' messages is handled 50 at a time: the
        # rest wait in the queue, the alerts keep their share of the connection pool.
        polling = asyncio.create_task(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(),
                                                       close_bot_session=False, tasks_concurrency_limit=50))
        # Outside APScheduler on purpose: a dead scheduler cannot stop it.
        guard = asyncio.create_task(health.watchdog(on_clock_step=clock_stepped))
        done, _ = await asyncio.wait({polling, guard}, return_when=asyncio.FIRST_COMPLETED)
        if guard in done:
            reason = guard.result()
            await dp.stop_polling()
            with contextlib.suppress(Exception):
                await polling
            raise SelfCheckFailed(reason)
        guard.cancel()
        await polling                      # a polling crash surfaces here
    finally:
        logger.info("Shutting down...")
        await stop(scheduler, web_runner, bot)


async def stop(scheduler, web_runner, bot) -> None:
    """No new jobs; the web server closed; what is running (a check, an alert
    half-sent, a button being answered) gets STOP_DRAIN_SEC to finish; then
    everything is closed — the database last, whatever failed before it."""
    try:
        if scheduler is not None and scheduler.running:
            scheduler.pause()
        try:
            if web_runner is not None:
                await web_runner.cleanup()
        finally:
            # Alerts queued by the last tick go out first; then the worker, which
            # waits for work forever, is cancelled so it does not eat the drain time.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(drain_alert_worker(), timeout=5)
            cancel_alert_worker()
            others = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
            if others:
                _, pending = await asyncio.wait(others, timeout=STOP_DRAIN_SEC)
                if pending:
                    logger.warning("Stopping with %d task(s) still running — they are cancelled", len(pending))
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            # The parse child would exit by itself on EOF; not waiting for
            # that keeps the stop from leaving a stray process behind.
            await parse_worker.close()
    finally:
        try:
            await bot.session.close()
        finally:
            await close_db()


def run() -> int:
    """The exit code: 0 — stopped, 2 — a setting or the database must be put
    right by a person (said in one sentence), 3 — the self-check says only a
    restart cures it (the container's restart policy does), 1 — anything else
    (with its traceback)."""
    try:
        asyncio.run(main())
    except (ConfigError, StartRefused) as e:
        logger.critical("%s", e)
        return 2
    except SelfCheckFailed as e:
        logger.error("Self-check: %s — exiting so the container restarts the bot", e)
        return 3
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0
    except BaseException:
        logger.exception("The bot stopped on an error")
        return 1
    return 0


if __name__ == "__main__":
    code = run()
    logging.shutdown()
    if code:
        # A failed start must end the process even if a non-daemon thread
        # (aiosqlite's) is left behind: Docker restarts what exits.
        os._exit(code)
    sys.exit(0)
