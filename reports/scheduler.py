import logging
from datetime import datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

from config import config
from monitors.availability import check_all
from monitors.ssl_checker import check_all_ssl
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from db.database import get_active_incidents
from reports.formatter import (
    format_status_report,
    format_availability_alert,
    format_recovery_alert,
    format_ssl_alert,
    format_domain_alert,
    format_links_report,
)

logger = logging.getLogger(__name__)


async def send_to_admin(bot: Bot, text: str):
    """Send a message to the admin chat."""
    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to admin: {e}")


async def run_availability_checks(bot: Bot):
    """Run availability checks and send alerts if something is wrong."""
    urls = config.get_site_urls()
    results = await check_all(urls)

    for r in results:
        if r.get("error") and r["status"] == "error":
            msg = format_availability_alert(r)
            if msg:
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            msg = format_recovery_alert(r)
            await send_to_admin(bot, msg)


async def run_ssl_checks(bot: Bot):
    """Run SSL checks and alert on issues."""
    urls = config.get_site_urls()
    results = await check_all_ssl(urls)

    for r in results:
        if r["status"] in ("error", "critical", "warning"):
            msg = format_ssl_alert(r)
            if msg:
                await send_to_admin(bot, msg)


async def run_domain_checks(bot: Bot):
    """Run domain expiry checks and alert on issues."""
    urls = config.get_site_urls()
    results = await check_all_domains(urls)

    for r in results:
        if r["status"] in ("error", "critical", "warning") and r.get("error"):
            msg = format_domain_alert(r)
            if msg:
                await send_to_admin(bot, msg)


async def run_links_checks(bot: Bot):
    """Run broken links checks and alert."""
    urls = config.get_site_urls()
    results = await check_all_links(urls)

    for r in results:
        if r.get("broken_links"):
            msg = format_links_report(r)
            if msg:
                await send_to_admin(bot, msg)


async def send_morning_report(bot: Bot):
    """Send a comprehensive morning status report."""
    await send_to_admin(bot, "⏳ Формирую утренний отчёт...")

    urls = config.get_site_urls()
    availability = await check_all(urls)
    ssl_results = await check_all_ssl(urls)
    domain_results = await check_all_domains(urls)
    incidents = await get_active_incidents()

    report = format_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="morning",
    )
    await send_to_admin(bot, report)


async def send_evening_report(bot: Bot):
    """Send a comprehensive evening status report."""
    urls = config.get_site_urls()
    availability = await check_all(urls)
    ssl_results = await check_all_ssl(urls)
    domain_results = await check_all_domains(urls)
    incidents = await get_active_incidents()

    report = format_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="evening",
    )
    await send_to_admin(bot, report)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Configure and return the APScheduler instance."""
    tz = pytz.timezone(config.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    # Availability checks every N minutes (default: 5)
    scheduler.add_job(
        run_availability_checks,
        trigger=IntervalTrigger(minutes=config.check_interval_minutes),
        args=[bot],
        id="availability_checks",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    # SSL + domain checks once a day at 08:00
    scheduler.add_job(
        run_ssl_checks,
        trigger=CronTrigger(hour=8, minute=0, timezone=tz),
        args=[bot],
        id="ssl_checks",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_domain_checks,
        trigger=CronTrigger(hour=8, minute=5, timezone=tz),
        args=[bot],
        id="domain_checks",
        replace_existing=True,
        max_instances=1,
    )

    # Links check every N hours (default: 6)
    scheduler.add_job(
        run_links_checks,
        trigger=IntervalTrigger(hours=config.links_check_interval_hours),
        args=[bot],
        id="links_checks",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Morning report
    scheduler.add_job(
        send_morning_report,
        trigger=CronTrigger(hour=config.morning_report_hour, minute=0, timezone=tz),
        args=[bot],
        id="morning_report",
        replace_existing=True,
        max_instances=1,
    )

    # Evening report
    scheduler.add_job(
        send_evening_report,
        trigger=CronTrigger(hour=config.evening_report_hour, minute=0, timezone=tz),
        args=[bot],
        id="evening_report",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
