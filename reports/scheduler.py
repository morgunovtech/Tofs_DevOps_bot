import logging
from datetime import datetime, timezone

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
from db.database import (
    get_active_incidents, get_state,
    get_or_create_site, save_incident, resolve_incident,
)
from reports.formatter import (
    format_compact_status_report,
    format_availability_alert,
    format_recovery_alert,
    format_ssl_alert,
    format_domain_alert,
    format_links_report,
)

logger = logging.getLogger(__name__)


# ── Mute ─────────────────────────────────────────────────────────────────────

async def is_muted() -> bool:
    """True if the user has silenced non-critical alerts."""
    until = await get_state("mute_until")
    if not until:
        return False
    try:
        deadline = datetime.fromisoformat(until)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        # Legacy value written by the old naive-utcnow code — it was UTC.
        deadline = deadline.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) < deadline


async def send_to_admin(bot: Bot, text: str, force: bool = False):
    """Send a message to the admin chat. Honors mute unless `force=True`."""
    if not force and await is_muted():
        logger.info("Skipping alert (muted): %s", text[:60])
        return
    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to admin: {e}")


# ── Alert handlers ───────────────────────────────────────────────────────────

# In-memory tracker for "slow response" — N consecutive checks > threshold.
SLOW_RESPONSE_MS = 3000
SLOW_RESPONSE_STREAK = 3
_slow_streak: dict[str, int] = {}


async def run_availability_checks(bot: Bot):
    """Run availability checks. Alert only on state transitions."""
    urls = config.get_site_urls()
    results = await check_all(urls)

    for r in results:
        if r.get("incident_new"):
            msg = format_availability_alert(r)
            if msg:
                await send_to_admin(bot, msg, force=True)
        elif r.get("recovered"):
            await send_to_admin(bot, format_recovery_alert(r), force=True)

        # Slow-response detection: the site is up, but consistently slow.
        # Open a "performance" incident only after a streak of slow responses.
        if r.get("status") == "ok":
            ms = r.get("response_time_ms") or 0
            url = r["url"]
            if ms >= SLOW_RESPONSE_MS:
                _slow_streak[url] = _slow_streak.get(url, 0) + 1
                if _slow_streak[url] == SLOW_RESPONSE_STREAK:
                    site_id = await get_or_create_site(url)
                    _, is_new = await save_incident(
                        site_id, "performance",
                        f"Slow responses: ~{ms}ms",
                        severity="warning",
                    )
                    if is_new:
                        await send_to_admin(
                            bot,
                            f"🐢 {url} отвечает медленно — "
                            f"{ms}ms ({SLOW_RESPONSE_STREAK} проверки подряд)",
                        )
            else:
                _slow_streak[url] = 0
                # Resolve unconditionally, not only when an in-memory streak
                # exists: after a restart the streak dict is empty while a
                # "performance" incident may still be open in the DB.
                site_id = await get_or_create_site(url)
                if await resolve_incident(site_id, "performance"):
                    await send_to_admin(
                        bot,
                        f"✅ {url} — скорость восстановилась ({ms}ms)",
                    )


async def run_ssl_checks(bot: Bot):
    """SSL alert ladder: notify only when crossing a new threshold."""
    urls = config.get_site_urls()
    results = await check_all_ssl(urls)

    for r in results:
        if r.get("incident_new"):
            msg = format_ssl_alert(r)
            if msg:
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            await send_to_admin(
                bot,
                f"✅ SSL: {r['url']} — сертификат обновлён, всё в порядке",
            )


async def run_domain_checks(bot: Bot):
    """Domain alert ladder."""
    urls = config.get_site_urls()
    results = await check_all_domains(urls)

    for r in results:
        if r.get("incident_new"):
            msg = format_domain_alert(r)
            if msg:
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            await send_to_admin(
                bot,
                f"✅ Домен {r.get('domain', r['url'])} — продлён, всё в порядке",
            )


async def run_links_checks(bot: Bot):
    """Broken-links alerts: only when a new internal-broken-links incident opens."""
    urls = config.get_site_urls()
    results = await check_all_links(urls)

    for r in results:
        if r.get("incident_new"):
            msg = format_links_report(r)
            if msg:
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            await send_to_admin(
                bot,
                f"✅ Ссылки на {r['url']} — все внутренние ссылки снова работают",
            )


# ── Daily reports ────────────────────────────────────────────────────────────

async def send_morning_report(bot: Bot):
    """Compact morning status report."""
    urls = config.get_site_urls()
    availability = await check_all(urls)
    ssl_results = await check_all_ssl(urls)
    domain_results = await check_all_domains(urls)
    incidents = await get_active_incidents()

    report = format_compact_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="morning",
    )
    # Daily report is informational — it should respect mute.
    await send_to_admin(bot, report)


async def send_evening_report(bot: Bot):
    """Evening report — only if there's something interesting."""
    incidents = await get_active_incidents()
    if not incidents:
        logger.info("Evening report skipped: no active incidents.")
        return

    urls = config.get_site_urls()
    availability = await check_all(urls)
    ssl_results = await check_all_ssl(urls)
    domain_results = await check_all_domains(urls)

    report = format_compact_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="evening",
    )
    await send_to_admin(bot, report)


# ── Setup ────────────────────────────────────────────────────────────────────

def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Configure and return the APScheduler instance."""
    tz = pytz.timezone(config.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(
        run_availability_checks,
        trigger=IntervalTrigger(minutes=config.check_interval_minutes),
        args=[bot],
        id="availability_checks",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    # SSL + domain once a day — alert ladder prevents repeat noise.
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

    scheduler.add_job(
        run_links_checks,
        trigger=IntervalTrigger(hours=config.links_check_interval_hours),
        args=[bot],
        id="links_checks",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        send_morning_report,
        trigger=CronTrigger(hour=config.morning_report_hour, minute=0, timezone=tz),
        args=[bot],
        id="morning_report",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        send_evening_report,
        trigger=CronTrigger(hour=config.evening_report_hour, minute=0, timezone=tz),
        args=[bot],
        id="evening_report",
        replace_existing=True,
        max_instances=1,
    )

    return scheduler
