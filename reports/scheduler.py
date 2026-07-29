import logging
import os
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot
from aiogram.types import FSInputFile
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz

from config import config
from monitors.availability import check_all
from monitors.ssl_checker import check_all_ssl
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.dns_checker import check_all_dns
from monitors.deep_checker import check_all_deep
from monitors.host_checker import check_disk, watch_containers
from monitors.seo_checker import check_all_seo
from services import gsc, yandex_webmaster
from services.actions import (
    alert_actions_keyboard, deploy_hook_for, trigger_redeploy,
)
from services import settings
from db.database import (
    get_active_incidents, get_state, set_state,
    get_or_create_site, save_incident, resolve_incident,
    get_heartbeats, get_all_sites, get_uptime_stats, get_last_check,
    get_active_site_urls,
    queue_notification, peek_notifications, delete_notifications,
    rollup_old_checks, backup_db, set_dns_state,
)
from reports.formatter import (
    format_compact_status_report,
    format_availability_alert,
    format_recovery_alert,
    format_ssl_alert,
    format_domain_alert,
    format_links_report,
    format_dns_change,
    format_seo_alert,
    incident_duration_line,
    _parse_sqlite_utc,
    _short_host,
    _esc,
    plural,
)
from reports.weekly import build_weekly_report

logger = logging.getLogger(__name__)

# Set when the scheduler starts; used as the grace reference for heartbeat
# jobs that have never pinged.
_started_at: datetime | None = None

TG_MESSAGE_LIMIT = 4096


def _clip(text: str, limit: int = TG_MESSAGE_LIMIT - 100) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (обрезано)"


# ── Mute / quiet hours ───────────────────────────────────────────────────────

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


def in_quiet_hours(hour: int | None = None) -> bool:
    """True during configured quiet hours (local time). Non-critical alerts
    are queued instead of sent, and flushed as a digest in the morning."""
    qh = settings.quiet_hours()
    if not qh:
        return False
    if hour is None:
        hour = datetime.now(pytz.timezone(config.timezone)).hour
    start, end = qh
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


async def send_to_admin(bot: Bot, text: str, force: bool = False,
                        reply_markup=None, quiet_ok: bool = False) -> bool:
    """Send a message to the admin chat.

    force=True — critical: bypasses mute and quiet hours, rings.
    Otherwise the message is DEFERRED (never dropped): while muted or during
    quiet hours it goes to the pending queue and arrives later as a digest.
    quiet_ok=True lets a message through quiet hours (the morning report IS
    the digest) while still deferring under an explicit mute.

    Returns True when the message was delivered or queued — callers that set
    one-shot "already alerted" flags must only do so on True.
    """
    if not config.admin_chat_id:
        # Nobody has claimed the bot via /start yet — nowhere to deliver.
        logger.warning("No admin yet, dropping message: %s", text[:60])
        return False
    if not force:
        if await is_muted():
            await queue_notification(text)
            logger.info("Queued alert (muted): %s", text[:60])
            return True
        if in_quiet_hours() and not quiet_ok:
            await queue_notification(text)
            logger.info("Queued alert (quiet hours): %s", text[:60])
            return True
    try:
        # The philosophy, made literal: informational messages arrive
        # silently and wait to be read; only critical ones make a sound.
        await bot.send_message(chat_id=config.admin_chat_id, text=text,
                               reply_markup=reply_markup,
                               disable_notification=not force)
        return True
    except Exception as e:
        logger.error(f"Failed to send message to admin: {e}")
        return False


async def flush_quiet_queue(bot: Bot):
    """Deliver notifications deferred by quiet hours or mute as one digest.
    Rows are deleted only AFTER a successful send — a Telegram hiccup must
    not cost the user their queued alerts."""
    if in_quiet_hours() or await is_muted() or not config.admin_chat_id:
        return
    rows = await peek_notifications()
    if not rows:
        return
    digest = "🌙 Накопилось, пока было тихо:\n\n" + "\n\n".join(
        f"— {r['text']}" for r in rows
    )
    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=_clip(digest),
                               disable_notification=True)
    except Exception as e:
        logger.error(f"Failed to flush quiet queue: {e}")
        return
    await delete_notifications([r["id"] for r in rows])


# ── Alert handlers ───────────────────────────────────────────────────────────

# In-memory tracker for "slow response" — N consecutive checks > threshold.
SLOW_RESPONSE_MS = 3000
SLOW_RESPONSE_STREAK = 3
_slow_streak: dict[str, int] = {}


async def run_availability_checks(bot: Bot):
    """Run availability checks. Alert only on state transitions."""
    urls = await get_active_site_urls()
    results = await check_all(urls)

    for r in results:
        # Maintenance pause: checks, incidents AND slow-streak bookkeeping
        # continue below — only the outgoing messages are muted, so state
        # doesn't drift while a site is paused.
        paused = bool(r.get("site_id")) and await settings.is_paused(r["site_id"])
        if paused:
            pass
        elif r.get("incident_new"):
            msg = format_availability_alert(r)
            if msg:
                if r.get("external_ok") is False:
                    msg += "\n🌐 Подтверждено извне: сайт недоступен и со второй точки"
                elif config.second_opinion:
                    msg += "\n❓ Перепроверить со второй точки не удалось"
                # Auto-remediation for Cloudflare Pages: fire the deploy hook
                # once per new incident, report what happened in the alert.
                if config.auto_redeploy and deploy_hook_for(r["url"]):
                    note = await trigger_redeploy(r["url"])
                    msg += f"\n\n🤖 Автодействие: {note}"
                await send_to_admin(bot, msg, force=True,
                                    reply_markup=await alert_actions_keyboard(r["url"]))
        elif r.get("external_ok") is True:
            # Down from the bot's network but fine externally — likely a
            # local network problem; mention it at most once per 6 hours.
            key = f"extok:{r['url']}"
            last = await get_state(key)
            stale = True
            if last:
                try:
                    stale = (datetime.now(timezone.utc)
                             - datetime.fromisoformat(last)).total_seconds() > 6 * 3600
                except ValueError:
                    pass
            if stale:
                sent = await send_to_admin(
                    bot,
                    f"🤔 {r['url']} не открывается с сервера бота, но извне "
                    f"доступен. Похоже на сетевую проблему на моей стороне — "
                    f"инцидент не открываю.",
                )
                if sent:
                    await set_state(key, datetime.now(timezone.utc).isoformat())
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
                    if is_new and not paused:
                        await send_to_admin(
                            bot,
                            f"🐢 {url} отвечает медленно — {ms}ms "
                            f"({SLOW_RESPONSE_STREAK} "
                            f"{plural(SLOW_RESPONSE_STREAK, 'проверка', 'проверки', 'проверок')} подряд)",
                        )
            else:
                _slow_streak[url] = 0
                # Resolve unconditionally, not only when an in-memory streak
                # exists: after a restart the streak dict is empty while a
                # "performance" incident may still be open in the DB.
                site_id = await get_or_create_site(url)
                if await resolve_incident(site_id, "performance") and not paused:
                    await send_to_admin(
                        bot,
                        f"✅ {url} — скорость восстановилась ({ms}ms)",
                    )


async def run_ssl_checks(bot: Bot):
    """SSL alert ladder: notify only when crossing a new threshold."""
    urls = await get_active_site_urls()
    results = await check_all_ssl(urls)

    for r in results:
        if r.get("incident_new"):
            msg = format_ssl_alert(r)
            if msg:
                if r.get("renewal_note"):
                    msg += f"\n🔁 {r['renewal_note']}"
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            await send_to_admin(
                bot,
                f"✅ SSL: {r['url']} — сертификат обновлён, всё в порядке",
            )
        elif r.get("renewed"):
            # Renewal happened silently before any alert threshold — good news,
            # confirms auto-renewal works.
            await send_to_admin(
                bot,
                f"🔁 SSL: {r['url']} — сертификат автоматически обновлён "
                f"({r['ssl_info']['days_left']} дн. запаса)",
            )


async def run_domain_checks(bot: Bot):
    """Domain alert ladder."""
    urls = await get_active_site_urls()
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
    urls = await get_active_site_urls()
    results = await check_all_links(urls)

    for r in results:
        site_id = await get_or_create_site(r["url"])
        if await settings.is_paused(site_id):
            continue
        if r.get("incident_new"):
            msg = format_links_report(r)
            if msg:
                await send_to_admin(bot, msg)
        elif r.get("recovered"):
            await send_to_admin(
                bot,
                f"✅ Ссылки на {r['url']} — все внутренние ссылки снова работают",
            )


async def run_dns_checks(bot: Bot):
    """Alert when DNS answers change; NS changes are critical.

    The new baseline is committed only after the alert is delivered or
    queued — otherwise a failed send would swallow the change forever."""
    changes = await check_all_dns(await get_active_site_urls())
    for change in changes:
        sent = await send_to_admin(bot, format_dns_change(change),
                                   force=change.get("critical", False))
        if sent:
            await set_dns_state(change["host"], change["rtype"], change["new"])


async def run_deep_checks(bot: Bot):
    """Sitemap 5xx probe: pages beyond the homepage."""
    results = await check_all_deep(await get_active_site_urls())
    for r in results:
        site_id = await get_or_create_site(r["url"])
        if await settings.is_paused(site_id):
            continue
        if r.get("incident_new"):
            errors = "\n".join(
                f"  • {_esc(e['url'])} — HTTP {e['status_code']}"
                for e in r["errors"][:5]
            )
            await send_to_admin(
                bot,
                f"🕳 {_esc(r['url'])}: 5xx на {len(r['errors'])} из "
                f"{r['sampled']} проверенных страниц:\n{errors}",
                reply_markup=await alert_actions_keyboard(r["url"]),
            )
        elif r.get("recovered"):
            await send_to_admin(
                bot, f"✅ {r['url']} — страницы из sitemap снова отвечают без 5xx",
            )


async def run_seo_checks(bot: Bot):
    """Daily SEO/GEO audit: alert on state change, critical bypasses mute
    (noindex or a search-bot block means the site is disappearing from
    indexes right now)."""
    results = await check_all_seo(await get_active_site_urls())
    for r in results:
        if r.get("incident_new"):
            msg = format_seo_alert(r)
            if msg:
                has_critical = any(
                    p["severity"] == "critical" for p in r["problems"])
                await send_to_admin(bot, _clip(msg), force=has_critical)
        elif r.get("recovered"):
            await send_to_admin(
                bot, f"✅ SEO: {r['url']} — все проблемы устранены",
            )


async def run_index_checks(bot: Bot):
    """Daily indexing watch via GSC / Yandex.Webmaster (when tokens are set).

    Silent while everything is fine; alerts only when the state changes:
    the homepage drops out of Google's index, or Yandex reports new
    FATAL/CRITICAL site problems.
    """
    # Google: is each homepage still in the index?
    if gsc.available():
        for url in await get_active_site_urls():
            info = await gsc.inspect_url(url.rstrip("/") + "/")
            if not info:
                continue
            key = f"gsc_idx:{_short_host(url)}"
            prev = await get_state(key)
            current = f"{info['verdict']}|{info['coverage']}"
            if prev == current:
                continue
            prev_verdict = (prev or "").split("|", 1)[0]
            sent = True
            if info["verdict"] == "PASS":
                # Recovery only on a real verdict transition — a coverage
                # string change while staying PASS is not "снова в индексе".
                if prev and prev_verdict != "PASS":
                    sent = await send_to_admin(
                        bot, f"✅ Google: {url} снова в индексе "
                             f"({_esc(info['coverage'])})")
            elif prev_verdict != info["verdict"]:
                sent = await send_to_admin(
                    bot,
                    f"🔴 Google: {url} НЕ в индексе!\n"
                    f"Статус: {_esc(info['coverage'])}\n"
                    f"Проверь в Search Console.",
                    force=True,
                )
            if sent:
                await set_state(key, current)

    # Yandex: new FATAL/CRITICAL site problems?
    if yandex_webmaster.available():
        summaries = await yandex_webmaster.get_summaries()
        for host, s in (summaries or {}).items():
            key = f"yx_problems:{host}"
            prev = await get_state(key) or ""
            current = ",".join(sorted(s["alert_problems"]))
            if current == prev:
                continue
            sent = True
            if current:
                plist = "\n".join(
                    f"  • {_esc(k)}: {_esc(v)}"
                    for k, v in s["alert_problems"].items())
                sent = await send_to_admin(
                    bot,
                    f"🔴 Яндекс.Вебмастер: проблемы на {_esc(host)}:\n{plist}",
                    force="FATAL" in current.upper(),
                )
            elif prev:
                sent = await send_to_admin(
                    bot, f"✅ Яндекс.Вебмастер: {_esc(host)} — проблемы устранены")
            if sent:
                await set_state(key, current)


# ── Dead-man switch ──────────────────────────────────────────────────────────

def _fmt_ago(minutes: float) -> str:
    if minutes < 60:
        return f"{round(minutes)} мин"
    if minutes < 48 * 60:
        return f"{round(minutes / 60)} ч"
    return f"{round(minutes / 1440)} дн"


async def run_heartbeat_watch(bot: Bot):
    """Alert when an expected job (backup, cron) hasn't pinged in time."""
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return
    beats = await get_heartbeats()
    now = datetime.now(timezone.utc)

    for job, interval_min in jobs.items():
        row = beats.get(job)
        overdue = False
        detail = ""
        if row and row.get("last_ping"):
            last = _parse_sqlite_utc(row["last_ping"])
            if last:
                silence_min = (now - last).total_seconds() / 60
                # 25% slack so a slightly late cron doesn't page.
                overdue = silence_min > interval_min * 1.25
                detail = f"последний сигнал: {_fmt_ago(silence_min)} назад"
        else:
            # Never pinged at all — give one full interval from bot start
            # before deciding the job is dead.
            if _started_at and (now - _started_at).total_seconds() / 60 > interval_min:
                overdue = True
                detail = "ни одного сигнала с момента запуска бота"

        alerted = await get_state(f"hb_alerted:{job}")
        if overdue and not alerted:
            sent = await send_to_admin(
                bot,
                f"💔 Heartbeat «{_esc(job)}» молчит ({detail}; "
                f"ожидание: каждые {_fmt_ago(interval_min)}).\n"
                f"Проверь, отработал ли он.",
            )
            if sent:
                await set_state(f"hb_alerted:{job}", now.isoformat())
        elif not overdue and alerted:
            # Ping endpoint announces recovery; this just clears a stale flag
            # (e.g. config interval was increased).
            await set_state(f"hb_alerted:{job}", None)


# ── Bot-host monitoring ──────────────────────────────────────────────────────

async def run_host_checks(bot: Bot):
    """Disk usage on the bot's host; auto-cleanup via docker prune."""
    result = await check_disk()
    if result["over_threshold"]:
        already = await get_state("disk_alerted")
        cleaned = result["cleaned_bytes"]
        if cleaned:
            gb = cleaned / 1e9
            msg = (f"💾 Диск был заполнен на {result['pct']}% — почистил docker "
                   f"(−{gb:.1f} GB), сейчас {result['pct_after']}%")
            if result["pct_after"] < config.disk_alert_pct:
                await set_state("disk_alerted", None)
                await send_to_admin(bot, msg)
                return
        if not already:
            sent = await send_to_admin(
                bot,
                f"💾 Диск на сервере бота заполнен на {result['pct']}% "
                f"({result['used_gb']}/{result['total_gb']} GB) — надо разобраться",
                force=result["pct"] >= 95,
            )
            if sent:
                await set_state("disk_alerted", "1")
    else:
        if await get_state("disk_alerted"):
            await set_state("disk_alerted", None)
            await send_to_admin(
                bot, f"💾 Диск в норме: {result['pct']}%",
            )


async def run_container_watch(bot: Bot):
    """Auto-restart configured containers that died or went unhealthy."""
    events = await watch_containers()
    for e in events:
        if e["restarted"] and e["ok_after"]:
            await send_to_admin(
                bot,
                f"🔄 Контейнер «{e['name']}» был {e['problem']} — "
                f"перезапустил, работает.",
            )
        else:
            await send_to_admin(
                bot,
                f"🔴 Контейнер «{e['name']}» {e['problem']}, "
                + ("перезапустил, но он всё ещё нездоров."
                   if e["restarted"] else "перезапустить не удалось.")
                + " Нужно смотреть руками.",
                force=True,
            )


# ── Escalation ───────────────────────────────────────────────────────────────

async def run_escalation_watch(bot: Bot):
    """Re-alert about an unresolved DOWN site every N minutes — bypasses
    mute: a dead site must not be forgettable.

    Deliberately limited to availability incidents: an expired SSL or a
    Yandex FATAL is critical too, but force-paging every 30 minutes about
    something that takes hours to fix would train the user to ignore
    alerts. Those fire once via their own monitors and stay visible in
    «⚠️ Инциденты»."""
    repeat_min = config.escalation_repeat_min
    if not repeat_min:
        return
    now = datetime.now(timezone.utc)
    for inc in await get_active_incidents():
        if inc["severity"] != "critical" or inc["check_type"] != "availability":
            continue
        if await settings.is_paused(inc["site_id"]):
            continue
        created = _parse_sqlite_utc(inc["created_at"])
        if not created:
            continue
        age_min = (now - created).total_seconds() / 60
        if age_min < repeat_min:
            continue
        last = await get_state(f"escalated:{inc['id']}")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds() / 60 < repeat_min:
                    continue
            except ValueError:
                pass
        await set_state(f"escalated:{inc['id']}", now.isoformat())
        await send_to_admin(
            bot,
            f"⏰ ВСЁ ЕЩЁ НЕ РЕШЕНО (уже {_fmt_ago(age_min)})\n"
            f"{_esc(_short_host(inc['url']))} [{_esc(inc['check_type'])}]: "
            f"{_esc(inc['message'])}",
            force=True,
            reply_markup=await alert_actions_keyboard(inc["url"]),
        )


# ── Self-maintenance ─────────────────────────────────────────────────────────

async def run_self_heartbeat(bot: Bot):
    """Ping the external watchdog (healthchecks.io etc.) — who watches the
    watchman. If the bot dies, the external service alerts instead."""
    if not config.self_heartbeat_url:
        return
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as s:
            await s.get(config.self_heartbeat_url)
    except Exception as e:
        logger.warning(f"Self-heartbeat ping failed: {e}")


async def run_retention(bot: Bot):
    """Roll old raw checks up into daily aggregates so the DB stays small."""
    try:
        aggregated, _ = await rollup_old_checks(config.retention_days)
        if aggregated:
            logger.info(f"Retention: rolled up {aggregated} old check rows")
    except Exception as e:
        logger.error(f"Retention job failed: {e}")


def _backups_dir() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "backups")


async def run_db_backup(bot: Bot):
    """Nightly local DB backup with rotation."""
    try:
        os.makedirs(_backups_dir(), exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = os.path.join(_backups_dir(), f"bot-{stamp}.db")
        await backup_db(path)
        # Rotate: keep the newest N.
        files = sorted(
            f for f in os.listdir(_backups_dir())
            if f.startswith("bot-") and f.endswith(".db")
        )
        keep = max(1, config.db_backup_keep)
        for stale in files[:-keep]:
            os.remove(os.path.join(_backups_dir(), stale))
        logger.info(f"DB backup written: {path}")
    except Exception as e:
        logger.error(f"DB backup failed: {e}")
        await send_to_admin(bot, f"⚠️ Не удалось сделать бэкап базы бота: {e}")


async def send_db_backup_to_telegram(bot: Bot):
    """Weekly: push the freshest backup into the admin chat — Telegram keeps
    the file, which makes it an off-host copy with zero extra infrastructure."""
    try:
        files = sorted(
            f for f in os.listdir(_backups_dir())
            if f.startswith("bot-") and f.endswith(".db")
        ) if os.path.isdir(_backups_dir()) else []
        if not files:
            await run_db_backup(bot)
            files = sorted(
                f for f in os.listdir(_backups_dir())
                if f.startswith("bot-") and f.endswith(".db")
            )
        if not files:
            return
        latest = os.path.join(_backups_dir(), files[-1])
        await bot.send_document(
            chat_id=config.admin_chat_id,
            document=FSInputFile(latest),
            caption="🗄 Еженедельная копия базы бота",
            disable_notification=True,
        )
    except Exception as e:
        logger.error(f"Sending DB backup to Telegram failed: {e}")


# ── Daily reports ────────────────────────────────────────────────────────────

async def _morning_extras(ssl_results: list[dict],
                          domain_results: list[dict]) -> list[str]:
    """Extra one-liners for the morning digest: weekly uptime, upcoming
    expirations, heartbeat and disk status."""
    extras: list[str] = []

    # Uptime over the last 7 days, one compact line.
    sites = await get_all_sites()
    chips = []
    for s in sites:
        stats = await get_uptime_stats(s["id"], hours=168)
        if stats["total_checks"]:
            chips.append(f"{_short_host(s['url'])} {stats['uptime_pct']}%")
    if chips:
        extras.append("📈 Uptime 7д: " + " · ".join(chips))

    # Upcoming expirations worth knowing about ahead of time.
    expiring = []
    for r in ssl_results or []:
        info = r.get("ssl_info")
        if info and info["days_left"] <= 14:
            expiring.append(f"SSL {_short_host(r['url'])} — {info['days_left']}д")
    for r in domain_results or []:
        info = r.get("domain_info")
        if info and info.get("days_left") is not None and info["days_left"] <= 60:
            expiring.append(f"домен {r.get('domain', '')} — {info['days_left']}д")
    if expiring:
        extras.append("🔜 Истекает скоро: " + "; ".join(expiring))

    # Dead-man switch status.
    if settings.heartbeat_jobs():
        beats = await get_heartbeats()
        now = datetime.now(timezone.utc)
        hb_chips = []
        for job, interval_min in settings.heartbeat_jobs().items():
            row = beats.get(job)
            last = _parse_sqlite_utc(row["last_ping"]) if row and row.get("last_ping") else None
            if last:
                ago_min = (now - last).total_seconds() / 60
                icon = "✅" if ago_min <= interval_min * 1.25 else "💔"
                hb_chips.append(f"{job} {icon} {_fmt_ago(ago_min)} назад")
            else:
                hb_chips.append(f"{job} ❓ нет сигналов")
        extras.append("💓 " + " · ".join(hb_chips))

    # SEO/GEO status from the last daily audit (runs at 07:30, before this).
    seo_chips = []
    seo_ok = True
    for s in sites:
        last = await get_last_check(s["id"], "seo")
        if not last:
            continue
        if last["status"] == "ok":
            seo_chips.append(f"{_short_host(s['url'])} ✅")
        else:
            seo_ok = False
            icon = "🔴" if last["status"] == "critical" else "⚠️"
            seo_chips.append(f"{_short_host(s['url'])} {icon}")
    if seo_chips:
        extras.append("🔍 SEO: " + ("✅ все сайты" if seo_ok
                                    else " · ".join(seo_chips)))

    # Disk on the bot host.
    try:
        disk = await check_disk(auto_cleanup=False)
        icon = "✅" if not disk["over_threshold"] else "⚠️"
        extras.append(
            f"💾 Диск: {icon} {disk['pct']}% "
            f"({disk['used_gb']}/{disk['total_gb']} GB)"
        )
    except Exception as e:
        logger.warning(f"Disk stat for morning report failed: {e}")

    return extras


async def send_morning_report(bot: Bot):
    """Compact morning status report — the daily 10-second health digest."""
    urls = await get_active_site_urls()
    # manage=False: a report is a read-only observer — it must never consume
    # incident transitions that belong to the scheduled monitors.
    availability = await check_all(urls, manage=False)
    ssl_results = await check_all_ssl(urls, manage=False)
    domain_results = await check_all_domains(urls, manage=False)
    incidents = await get_active_incidents()

    report = format_compact_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="morning",
        extras=await _morning_extras(ssl_results, domain_results),
    )
    # The morning report IS the daily digest — it must arrive even when its
    # hour falls inside quiet hours (still deferred by an explicit mute).
    await send_to_admin(bot, _clip(report), quiet_ok=True)


async def send_evening_report(bot: Bot):
    """Evening report — only if there's something interesting."""
    incidents = await get_active_incidents()
    if not incidents:
        logger.info("Evening report skipped: no active incidents.")
        return
    if settings.evening_hour() is None:
        logger.info("Evening report disabled in settings.")
        return

    urls = await get_active_site_urls()
    availability = await check_all(urls, manage=False)
    ssl_results = await check_all_ssl(urls, manage=False)
    domain_results = await check_all_domains(urls, manage=False)

    report = format_compact_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="evening",
    )
    await send_to_admin(bot, _clip(report))


async def send_weekly_report(bot: Bot):
    """Sunday: weekly digest + response-time chart + off-host DB copy."""
    try:
        text, chart = await build_weekly_report()
        await send_to_admin(bot, _clip(text))
        if chart:
            try:
                await bot.send_photo(
                    chat_id=config.admin_chat_id,
                    photo=FSInputFile(chart),
                    disable_notification=True,
                )
            except Exception as e:
                logger.error(f"Sending weekly chart failed: {e}")
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")
    await send_db_backup_to_telegram(bot)


# ── Setup ────────────────────────────────────────────────────────────────────

_scheduler: AsyncIOScheduler | None = None


def reschedule_report_jobs():
    """Apply UI-changed report hours to the running scheduler on the fly."""
    if _scheduler is None:
        return
    tz = pytz.timezone(config.timezone)
    _scheduler.reschedule_job(
        "morning_report",
        trigger=CronTrigger(hour=settings.morning_hour(), minute=0, timezone=tz))
    evening = settings.evening_hour()
    if evening is not None:
        _scheduler.reschedule_job(
            "evening_report",
            trigger=CronTrigger(hour=evening, minute=0, timezone=tz))


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Configure and return the APScheduler instance."""
    global _started_at, _scheduler
    _started_at = datetime.now(timezone.utc)

    tz = pytz.timezone(config.timezone)
    scheduler = AsyncIOScheduler(timezone=tz)
    _scheduler = scheduler

    def job(fn, trigger, job_id, **kwargs):
        scheduler.add_job(
            fn, trigger=trigger, args=[bot], id=job_id,
            replace_existing=True, max_instances=1, **kwargs,
        )

    job(run_availability_checks,
        IntervalTrigger(minutes=config.check_interval_minutes),
        "availability_checks", misfire_grace_time=60)

    # SSL + domain once a day — alert ladder prevents repeat noise.
    job(run_ssl_checks, CronTrigger(hour=8, minute=0, timezone=tz), "ssl_checks")
    job(run_domain_checks, CronTrigger(hour=8, minute=5, timezone=tz), "domain_checks")

    # SEO/GEO audit daily at 07:30 — before the morning report, so the
    # digest shows fresh results.
    job(run_seo_checks, CronTrigger(hour=7, minute=30, timezone=tz), "seo_checks",
        misfire_grace_time=600)
    # Index status (GSC / Yandex.Webmaster) daily at 07:45.
    job(run_index_checks, CronTrigger(hour=7, minute=45, timezone=tz),
        "index_checks", misfire_grace_time=600)

    job(run_links_checks,
        IntervalTrigger(hours=config.links_check_interval_hours),
        "links_checks", misfire_grace_time=300)

    # DNS + deep 5xx probe hourly (offset so they don't pile up).
    job(run_dns_checks, CronTrigger(minute=20, timezone=tz), "dns_checks",
        misfire_grace_time=300)
    job(run_deep_checks, CronTrigger(minute=40, timezone=tz), "deep_checks",
        misfire_grace_time=300)

    # Watchers.
    job(run_heartbeat_watch, IntervalTrigger(minutes=10), "heartbeat_watch")
    job(run_host_checks, IntervalTrigger(minutes=30), "host_checks")
    job(run_container_watch, IntervalTrigger(minutes=10), "container_watch")
    job(run_escalation_watch, IntervalTrigger(minutes=5), "escalation_watch")
    job(run_self_heartbeat, IntervalTrigger(minutes=5), "self_heartbeat")
    job(flush_quiet_queue, IntervalTrigger(minutes=10), "quiet_flush")

    # Nightly maintenance.
    job(run_retention, CronTrigger(hour=3, minute=30, timezone=tz), "retention")
    job(run_db_backup, CronTrigger(hour=3, minute=45, timezone=tz), "db_backup")

    # Reports.
    job(send_morning_report,
        CronTrigger(hour=settings.morning_hour(), minute=0, timezone=tz),
        "morning_report")
    job(send_evening_report,
        CronTrigger(hour=settings.evening_hour() or config.evening_report_hour,
                    minute=0, timezone=tz),
        "evening_report")
    job(send_weekly_report,
        CronTrigger(day_of_week="sun", hour=config.weekly_report_hour,
                    minute=0, timezone=tz),
        "weekly_report")

    return scheduler
