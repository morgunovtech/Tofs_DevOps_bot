"""APScheduler jobs: monitors, watchers, reports and self-maintenance.
Every outgoing message goes through services.notifier, which owns the
mute / quiet-hours / pause rules."""

import logging
import os
from datetime import UTC, datetime, timedelta

import aiohttp
from aiogram.types import FSInputFile, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import config
from db.database import (
    backup_db,
    get_active_http_site_urls,
    get_active_incidents,
    get_active_site_urls,
    get_all_sites,
    get_heartbeats,
    get_last_check,
    get_state,
    get_uptime_over_days,
    resolve_incident,
    rollup_old_checks,
    save_incident,
    set_dns_state,
    set_state,
)
from monitors.availability import check_all, check_availability
from monitors.base import CheckResult
from monitors.deep_checker import check_all_deep
from monitors.dns_checker import check_all_dns
from monitors.domain_checker import check_all_domains
from monitors.host_checker import check_disk, watch_containers
from monitors.links_checker import check_all_links
from monitors.seo_checker import check_all_seo
from monitors.ssl_checker import check_all_ssl
from reports.formatter import (
    format_availability_alert,
    format_compact_status_report,
    format_deep_alert,
    format_dns_change,
    format_domain_alert,
    format_links_report,
    format_recovery_alert,
    format_seo_alert,
    format_ssl_alert,
    incident_line,
)
from reports.weekly import build_weekly_report
from services import gsc, humanize, integrations, notifier, recommend, settings, updates, yandex_webmaster
from services.actions import alert_actions_keyboard, deploy_hook_for, domain_keyboard, trigger_redeploy
from services.notifier import Priority
from utils.clock import local_tz
from utils.text import SAFE_LIMIT, clip, esc, fmt_duration, parse_iso_utc, parse_sqlite_utc, plural
from utils.urls import short_host, site_label

logger = logging.getLogger(__name__)

# Set when the scheduler starts; grace reference for heartbeat jobs that
# have never pinged.
_started_at: datetime | None = None
_scheduler: AsyncIOScheduler | None = None

SLOW_RESPONSE_STREAK = 3
_slow_streak: dict[int, int] = {}
# Per-site schedule (site_id → next due, UTC). The job ticks every minute
# and checks only the sites whose time has come. In-memory on purpose:
# after a restart everything is due immediately — a "check everything on
# boot" sweep.
_next_avail_check: dict[int, datetime] = {}


def _tech_line(text: str | None) -> str:
    return f"\n<i>{esc(text)}</i>" if text else ""


def reset_site_schedule(site_id: int):
    """Forget the site's next-due time — the next tick re-checks it."""
    _next_avail_check.pop(site_id, None)


# ── Availability ─────────────────────────────────────────────────────────────

async def run_availability_checks():
    now = datetime.now(UTC)
    sites = await get_all_sites()
    by_url = {s["url"]: s for s in sites}
    for sid in [sid for sid in _next_avail_check if sid not in {s["id"] for s in sites}]:
        del _next_avail_check[sid]
    # 30s tolerance: fire times sit on a minute grid while next_due carries
    # dispatch jitter — a strict comparison would stretch every interval.
    due_cutoff = now + timedelta(seconds=30)
    due = []
    for s in sites:
        if due_cutoff >= _next_avail_check.get(s["id"], now):
            interval = s.get("check_interval_min") or config.check_interval_minutes
            _next_avail_check[s["id"]] = now + timedelta(minutes=interval)
            due.append(s["url"])
    if not due:
        return
    for r in await check_all(due):
        site = by_url.get(r.url, {})
        if r.incident_new:
            msg = format_availability_alert(r, integrations.second_opinion_enabled())
            if config.auto_redeploy and deploy_hook_for(r.url):
                msg += f"\n\n🤖 Уже сделал сам: {await trigger_redeploy(r.url)}"
            kb = alert_actions_keyboard(r.url, r.site_id, r.incident_id, hosting=r.hosting)
            sent = await notifier.send(msg, Priority.CRITICAL, site_id=r.site_id, reply_markup=kb)
            if isinstance(sent, Message):
                _schedule_followups(r.url, r.site_id, sent.message_id, msg, kb)
        elif r.external_ok is True:
            await _note_local_network_problem(r.url, r.site_id)
        elif r.recovered:
            await notifier.send(format_recovery_alert(r), Priority.CRITICAL, site_id=r.site_id)
        if r.ok:
            await _track_slow(r, site.get("slow_ms") or config.slow_response_ms)


FOLLOWUP_MINUTES = (2, 5)


def _schedule_followups(url: str, site_id: int, message_id: int, text: str, kb):
    """Re-check 2 and 5 minutes after an alert and append the verdict to the
    same message — the person should not have to press «Проверить»."""
    if _scheduler is None:
        return
    for minutes in FOLLOWUP_MINUTES:
        _scheduler.add_job(_followup, "date",
                           run_date=datetime.now(UTC) + timedelta(minutes=minutes),
                           args=[url, site_id, message_id, text, kb, minutes],
                           id=f"followup:{message_id}:{minutes}", replace_existing=True)


async def _followup(url: str, site_id: int, message_id: int, text: str, kb, minutes: int):
    r = await check_availability(url, manage=False)
    if r.ok:
        line = f"\n\n🔁 Через {minutes} мин: уже открывается. Подтвержу отдельным сообщением."
        for other in FOLLOWUP_MINUTES:
            if other > minutes and _scheduler is not None:
                job = _scheduler.get_job(f"followup:{message_id}:{other}")
                if job:
                    job.remove()
    else:
        line = f"\n\n🔁 Через {minutes} мин: всё ещё не открывается ({esc(humanize.describe_error(r.error))})."
    await notifier.edit(message_id, clip(text + line), reply_markup=kb)


async def _note_local_network_problem(url: str, site_id: int):
    """Down from the bot's network but fine externally — mention it at most
    once per 6 hours."""
    key = f"extok:{site_id}"
    last = parse_iso_utc(await get_state(key))
    if last and (datetime.now(UTC) - last) < timedelta(hours=6):
        return
    sent = await notifier.send(
        f"🤔 {esc(site_label(url))} не открылся с моего сервера, но у всех остальных открывается. "
        f"Похоже, сеть шалит на моей стороне — тревогу не поднимаю, продолжаю проверять.",
        Priority.NORMAL, site_id=site_id)
    if sent:
        await set_state(key, datetime.now(UTC).isoformat())


async def _track_slow(r, slow_ms: int):
    """The site is up, but consistently slow → a 'performance' incident
    after a streak; resolved (unconditionally) on the first fast reply."""
    ms = r.response_time_ms or 0
    if ms >= slow_ms:
        _slow_streak[r.site_id] = _slow_streak.get(r.site_id, 0) + 1
        if _slow_streak[r.site_id] == SLOW_RESPONSE_STREAK:
            _, is_new = await save_incident(r.site_id, "performance",
                                            f"Slow responses: ~{ms}ms", severity="warning")
            if is_new:
                expl = humanize.EXPLANATIONS["slow"]
                await notifier.send(
                    f"⚠️ {esc(site_label(r.url))} открывается медленно: {humanize.fmt_seconds(ms)} "
                    f"вместо обычных долей секунды, {SLOW_RESPONSE_STREAK} "
                    f"{plural(SLOW_RESPONSE_STREAK, 'проверка', 'проверки', 'проверок')} подряд.\n"
                    f"{expl.meaning}\n\nЧто это обычно значит: {esc(expl.cause)}.\n\n"
                    f"{esc(humanize.steps_block(expl))}", Priority.NORMAL, site_id=r.site_id)
        return
    _slow_streak[r.site_id] = 0
    if await resolve_incident(r.site_id, "performance"):
        await notifier.send(f"✅ {esc(site_label(r.url))} снова открывается быстро ({humanize.fmt_seconds(ms)}). "
                            f"Ничего делать не нужно.", Priority.NORMAL, site_id=r.site_id)


# ── Generic "alert on transition" for the daily/hourly monitors ──────────────

async def _notify_transitions(results: list[CheckResult], alert, recovery,
                              priority=lambda r: Priority.NORMAL, keyboard=None):
    for r in results:
        if r.incident_new:
            msg = alert(r)
            if msg:
                await notifier.send(clip(msg), priority(r), site_id=r.site_id,
                                    reply_markup=keyboard(r) if keyboard else None)
        elif r.recovered:
            await notifier.send(recovery(r), Priority.NORMAL, site_id=r.site_id)


async def run_ssl_checks():
    results = await check_all_ssl(await get_active_http_site_urls())
    await _notify_transitions(
        results, format_ssl_alert,
        lambda r: f"✅ Сертификат {esc(site_label(r.url))} обновлён, всё в порядке. Ничего делать не нужно.")
    for r in results:
        if r.renewed and not r.incident_new and not r.recovered:
            # Renewal before any alert threshold — confirms auto-renewal works.
            await notifier.send(
                f"✅ Сертификат {esc(site_label(r.url))} продлился сам, как и должен "
                f"(действует ещё {r.ssl_info.days_left} дн.). Ничего делать не нужно.",
                Priority.NORMAL, site_id=r.site_id)


async def run_domain_checks():
    await _notify_transitions(
        await check_all_domains(await get_active_http_site_urls()), format_domain_alert,
        lambda r: f"✅ Домен {esc(r.domain or r.url)} продлён, всё в порядке. Ничего делать не нужно.",
        keyboard=lambda r: domain_keyboard(r.domain_info.registrar if r.domain_info else None))


async def run_links_checks():
    await _notify_transitions(
        await check_all_links(await get_active_http_site_urls()), format_links_report,
        lambda r: f"✅ На {esc(site_label(r.url))} все ссылки снова работают.")


async def run_deep_checks():
    await _notify_transitions(
        await check_all_deep(await get_active_http_site_urls()), format_deep_alert,
        lambda r: f"✅ На {esc(site_label(r.url))} все страницы снова отвечают без ошибок.",
        keyboard=lambda r: alert_actions_keyboard(r.url, r.site_id))


SEO_GRACE_HOURS = 24


async def _recently_added() -> set[str]:
    """URLs of sites added less than SEO_GRACE_HOURS ago."""
    cutoff = datetime.now(UTC) - timedelta(hours=SEO_GRACE_HOURS)
    out = set()
    for s in await get_all_sites():
        added = parse_sqlite_utc(s.get("added_at"))
        if added and added > cutoff:
            out.add(s["url"])
    return out


async def run_seo_checks():
    """Daily SEO/GEO audit. Only critical findings (noindex, search bots
    blocked) become alerts — the site is disappearing from search right
    now, so they bypass mute. Improvements wait for the weekly report.
    A site added today is audited quietly: its first findings arrive in
    the next morning digest, not as ten separate alerts."""
    urls = await get_active_http_site_urls()
    fresh = await _recently_added()
    if fresh:
        await check_all_seo([u for u in urls if u in fresh], manage=False)
    await _notify_transitions(
        await check_all_seo([u for u in urls if u not in fresh]), format_seo_alert,
        lambda r: f"✅ {esc(site_label(r.url))} снова открыт для поисковиков. Ничего делать не нужно.",
        priority=lambda r: Priority.CRITICAL)


async def run_dns_checks():
    """The new baseline is committed only after the alert is delivered or
    queued — otherwise a failed send would swallow the change forever."""
    for change in await check_all_dns(await get_active_http_site_urls()):
        sent = await notifier.send(format_dns_change(change),
                                   Priority.CRITICAL if change["critical"] else Priority.NORMAL)
        if sent:
            await set_dns_state(change["host"], change["rtype"], change["new"])


async def run_index_checks():
    """Daily indexing watch via GSC / Yandex.Webmaster (when tokens are set).
    Alerts only on state change."""
    if gsc.available():
        for url in await get_active_http_site_urls():
            info = await gsc.inspect_url(url.rstrip("/") + "/")
            if not info:
                continue
            key = f"gsc_idx:{short_host(url)}"
            prev = await get_state(key)
            current = f"{info['verdict']}|{info['coverage']}"
            if prev == current:
                continue
            prev_verdict = (prev or "").split("|", 1)[0]
            sent = True
            if info["verdict"] == "PASS":
                if prev and prev_verdict != "PASS":
                    sent = await notifier.send(
                        f"✅ Google снова показывает {esc(site_label(url))} в поиске. Ничего делать не нужно."
                        + _tech_line(info["coverage"]))
            elif prev_verdict != info["verdict"]:
                sent = await notifier.send(
                    f"🔴 Google убрал {esc(site_label(url))} из поиска\n"
                    f"Люди больше не найдут сайт через Google.\n\n"
                    f"Что делать:\n• Открыть Google Search Console → Проверка URL для главной: там будет "
                    f"написана причина.\n• Чаще всего это noindex на странице или запрет в robots.txt — "
                    f"проверь «🔍 Поиск и ИИ» в боте." + _tech_line(info["coverage"]), Priority.CRITICAL)
            if sent:
                await set_state(key, current)
    if yandex_webmaster.available():
        for host, s in (await yandex_webmaster.get_summaries() or {}).items():
            key = f"yx_problems:{host}"
            prev = await get_state(key) or ""
            current = ",".join(sorted(s["alert_problems"]))
            if current == prev:
                continue
            sent = True
            if current:
                plist = "\n".join(f"  • {esc(k)}: {esc(v)}" for k, v in s["alert_problems"].items())
                sent = await notifier.send(
                    f"🔴 Яндекс видит проблемы на {esc(host)}:\n{plist}\n\n"
                    f"Что делать: открыть Яндекс.Вебмастер → Диагностика сайта, там каждая проблема "
                    f"с инструкцией.",
                    Priority.CRITICAL if "FATAL" in current.upper() else Priority.NORMAL)
            elif prev:
                sent = await notifier.send(f"✅ Яндекс больше не видит проблем на {esc(host)}. Ничего делать не нужно.")
            if sent:
                await set_state(key, current)


# ── Dead-man switch ──────────────────────────────────────────────────────────

async def run_heartbeat_watch():
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return
    beats = await get_heartbeats()
    now = datetime.now(UTC)
    for job, interval_min in jobs.items():
        last = parse_sqlite_utc(beats.get(job))
        overdue, detail = False, ""
        if last:
            silence_min = (now - last).total_seconds() / 60
            overdue = silence_min > interval_min * 1.25   # 25% slack for a late cron
            detail = f"последний сигнал: {fmt_duration(silence_min)} назад"
        elif _started_at and (now - _started_at).total_seconds() / 60 > interval_min:
            overdue, detail = True, "ни одного сигнала с момента запуска бота"
        alerted = await get_state(f"hb_alerted:{job}")
        if overdue and not alerted:
            expl = humanize.EXPLANATIONS["heartbeat"]
            sent = await notifier.send(
                f"⚠️ Задача «{esc(job)}» не отчиталась: {detail}, ожидалось каждые "
                f"{fmt_duration(interval_min)}.\n{expl.meaning}\n\n"
                f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(humanize.steps_block(expl))}")
            if sent:
                await set_state(f"hb_alerted:{job}", now.isoformat())
        elif not overdue and alerted:
            await set_state(f"hb_alerted:{job}", None)  # clear a stale flag


# ── Bot-host monitoring ──────────────────────────────────────────────────────

async def run_host_checks():
    result = await check_disk()
    already = await get_state("disk_alerted")
    if result["over_threshold"]:
        if result["cleaned_bytes"]:
            msg = (f"💾 Диск был заполнен на {result['pct']}% — почистил docker "
                   f"(−{result['cleaned_bytes'] / 1e9:.1f} GB), сейчас {result['pct_after']}%")
            if result["pct_after"] < config.disk_alert_pct:
                await set_state("disk_alerted", None)
                await notifier.send(msg)
                return
        if not already:
            expl = humanize.EXPLANATIONS["disk"]
            sent = await notifier.send(
                f"⚠️ Диск на сервере бота заполнен на {result['pct']}% "
                f"({result['used_gb']} из {result['total_gb']} GB).\n{expl.meaning}\n\n"
                f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(humanize.steps_block(expl))}",
                Priority.CRITICAL if result["pct"] >= 95 else Priority.NORMAL)
            if sent:
                await set_state("disk_alerted", "1")
    elif already:
        await set_state("disk_alerted", None)
        await notifier.send(f"✅ Диск на сервере бота снова в норме: {result['pct']}%. Ничего делать не нужно.")


async def run_container_watch():
    for e in await watch_containers():
        if e["restarted"] and e["ok_after"]:
            await notifier.send(f"✅ Сервис «{esc(e['name'])}» на сервере бота был {esc(e['problem'])} — "
                                f"перезапустил сам, работает. Ничего делать не нужно.")
        else:
            expl = humanize.EXPLANATIONS["container"]
            await notifier.send(
                f"🔴 Сервис «{esc(e['name'])}» на сервере бота {esc(e['problem'])}. "
                + ("Перезапустил, но он всё ещё нездоров." if e["restarted"]
                   else "Перезапустить не удалось.") + f"\n{expl.meaning}\n\n{esc(humanize.steps_block(expl))}",
                Priority.CRITICAL)


# ── Escalation ───────────────────────────────────────────────────────────────

async def run_escalation_watch():
    """Re-alert about an unresolved DOWN site every N minutes — bypasses
    mute: a dead site must not be forgettable. Availability only: paging
    every 30 minutes about an expiring cert would train the user to ignore
    alerts. «👀 Видел» snoozes a specific incident."""
    repeat_min = config.escalation_repeat_min
    if not repeat_min:
        return
    now = datetime.now(UTC)
    for inc in await get_active_incidents():
        if inc["severity"] != "critical" or inc["check_type"] != "availability":
            continue
        created = parse_sqlite_utc(inc["created_at"])
        if not created or (now - created) < timedelta(minutes=repeat_min):
            continue
        ack = parse_iso_utc(await get_state(f"ack:{inc['id']}"))
        if ack and ack > now:
            continue
        last = parse_iso_utc(await get_state(f"escalated:{inc['id']}"))
        if last and (now - last) < timedelta(minutes=repeat_min):
            continue
        age_min = (now - created).total_seconds() / 60
        sent = await notifier.send(
            f"🔴 {esc(incident_line(inc))}\nЛежит уже {fmt_duration(age_min)}. Продолжаю проверять "
            f"каждую минуту и напишу, как только поднимется.\n\nЕсли ты уже чинишь — нажми "
            f"«🔧 Я чиню», и я замолчу на два часа.",
            Priority.CRITICAL, site_id=inc["site_id"],
            reply_markup=alert_actions_keyboard(inc["url"], inc["site_id"], inc["id"],
                                                hosting=await get_state(f"hosting:{inc['site_id']}")))
        if sent:
            await set_state(f"escalated:{inc['id']}", now.isoformat())


# ── Self-maintenance ─────────────────────────────────────────────────────────

async def run_self_heartbeat():
    """Ping the external watchdog (healthchecks.io etc.)."""
    if not config.self_heartbeat_url:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            await s.get(config.self_heartbeat_url)
    except Exception as e:
        logger.warning("Self-heartbeat ping failed: %s", e)


async def run_retention():
    try:
        n = await rollup_old_checks(config.retention_days)
        if n:
            logger.info("Retention: rolled up %d old check rows", n)
    except Exception as e:
        logger.error("Retention job failed: %s", e)


def _backups_dir() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "backups")


def _backup_files() -> list[str]:
    d = _backups_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.startswith("bot-") and f.endswith(".db"))


async def run_db_backup():
    """Nightly local DB backup with rotation."""
    try:
        os.makedirs(_backups_dir(), exist_ok=True)
        path = os.path.join(_backups_dir(), f"bot-{datetime.now(UTC):%Y%m%d}.db")
        await backup_db(path)
        for stale in _backup_files()[:-max(1, config.db_backup_keep)]:
            os.remove(os.path.join(_backups_dir(), stale))
        logger.info("DB backup written: %s", path)
    except Exception as e:
        logger.error("DB backup failed: %s", e)
        await notifier.send("⚠️ Не удалось сохранить резервную копию базы бота. Проверь место на диске."
                            + _tech_line(str(e)))


async def send_db_backup_to_telegram():
    """Weekly: push the freshest backup into the admin chat — an off-host
    copy with zero extra infrastructure."""
    if not _backup_files():
        await run_db_backup()
    files = _backup_files()
    if files:
        await notifier.send_document(FSInputFile(os.path.join(_backups_dir(), files[-1])),
                                     "🗄 Еженедельная копия базы бота")


# ── Reports ──────────────────────────────────────────────────────────────────

async def _morning_extras(ssl_results, domain_results) -> list[str]:
    extras: list[str] = []
    sites = await get_all_sites()
    chips = []
    for s in sites:
        stats = await get_uptime_over_days(s["id"], 7)
        if stats["total_checks"]:
            interval = s.get("check_interval_min") or config.check_interval_minutes
            chips.append(f"{esc(site_label(s['url']))} "
                         f"{humanize.downtime(stats['total_checks'], stats['ok_checks'], interval)}")
    if chips:
        extras.append("📈 За неделю: " + " · ".join(chips))

    expiring = [f"сертификат {esc(short_host(r.url))} через {r.ssl_info.days_left} дн."
                for r in ssl_results if r.ssl_info and r.ssl_info.days_left <= 14]
    expiring += [f"домен {esc(r.domain)} через {r.domain_info.days_left} дн."
                 for r in domain_results
                 if r.domain_info and r.domain_info.days_left is not None
                 and r.domain_info.days_left <= 60]
    if expiring:
        extras.append("🔜 Истекает: " + "; ".join(expiring))

    jobs = settings.heartbeat_jobs()
    if jobs:
        beats = await get_heartbeats()
        now = datetime.now(UTC)
        hb = []
        for job, interval_min in jobs.items():
            last = parse_sqlite_utc(beats.get(job))
            if last:
                ago = (now - last).total_seconds() / 60
                ok = ago <= interval_min * 1.25
                hb.append(f"{esc(job)} {'✅' if ok else '⚠️'} {fmt_duration(ago)} назад")
            else:
                hb.append(f"{esc(job)} ❓ ещё не отчитывалась")
        extras.append("⏰ Задачи: " + " · ".join(hb))

    seo_chips, seo_ok = [], True
    for s in sites:
        last = await get_last_check(s["id"], "seo")
        if not last:
            continue
        if last["status"] == "ok":
            seo_chips.append(f"{esc(short_host(s['url']))} ✅")
        else:
            seo_ok = False
            seo_chips.append(f"{esc(short_host(s['url']))} {'🔴' if last['status'] == 'critical' else '⚠️'}")
    if seo_chips:
        extras.append("🔍 В поиске: " + ("✅ всё в порядке" if seo_ok else " · ".join(seo_chips)))
    now = datetime.now(UTC)
    for s in sites:
        added = parse_sqlite_utc(s.get("added_at"))
        if not added or not (timedelta(hours=SEO_GRACE_HOURS) <= now - added < timedelta(hours=SEO_GRACE_HOURS + 24)):
            continue
        last = await get_last_check(s["id"], "seo")
        if last:
            verdict = ("всё в порядке" if last["status"] == "ok" else
                       "есть что поправить" if last["status"] == "warning" else "сайт закрыт от поиска!")
            extras.append(f"🔍 Первый взгляд на {esc(site_label(s['url']))} глазами поисковиков: {verdict} — "
                          f"подробности в «🔍 Поиск и ИИ»")

    try:
        disk = await check_disk(auto_cleanup=False)
        extras.append(f"💾 Диск сервера: {'⚠️' if disk['over_threshold'] else '✅'} занято {disk['pct']}%")
    except Exception as e:
        logger.warning("Disk stat for morning report failed: %s", e)
    return extras


async def _status_snapshot(report_type: str, extras: bool):
    """manage=False: a report is a read-only observer — it must never
    consume incident transitions that belong to the scheduled monitors."""
    urls = await get_active_site_urls()
    http_urls = await get_active_http_site_urls()
    availability = await check_all(urls, manage=False)
    ssl_results = await check_all_ssl(http_urls, manage=False)
    domain_results = await check_all_domains(http_urls, manage=False)
    return format_compact_status_report(
        availability=availability, incidents=await get_active_incidents(),
        ssl_results=ssl_results, domain_results=domain_results, report_type=report_type,
        extras=await _morning_extras(ssl_results, domain_results) if extras else None)


async def send_morning_report():
    # The morning report IS the daily digest — it must arrive even when its
    # hour falls inside quiet hours (still deferred by an explicit mute).
    await notifier.send(clip(await _status_snapshot("morning", extras=True)), Priority.DIGEST)


async def send_evening_report():
    """Evening report — only if there's something to worry about."""
    if settings.evening_hour() is None or not await get_active_incidents():
        return
    await notifier.send(clip(await _status_snapshot("evening", extras=False)))


def pack_blocks(blocks: list[str], limit: int = SAFE_LIMIT) -> list[str]:
    """Pack independent text blocks into as few messages as fit the limit."""
    messages, current = [], ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if current and len(candidate) > limit:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


async def send_weekly_report():
    """Sunday: dependency check, weekly digest, ASCII response-time chart,
    off-host DB copy."""
    try:
        await updates.run_dependency_watch()
    except Exception as e:
        logger.error("Dependency watch failed: %s", e)
    try:
        text, chart_blocks = await build_weekly_report()
        await notifier.send(clip(text), Priority.DIGEST)
        if chart_blocks:
            for msg in pack_blocks(["⏱ Как быстро открывались сайты по дням:", *chart_blocks]):
                await notifier.send(msg, Priority.DIGEST)
        tip = await recommend.weekly_recommendation()
        if tip:
            await notifier.send(tip[0], Priority.DIGEST, reply_markup=tip[1])
    except Exception as e:
        logger.error("Weekly report failed: %s", e)
    await send_db_backup_to_telegram()


# ── Setup ────────────────────────────────────────────────────────────────────

def _cron(**kwargs) -> CronTrigger:
    return CronTrigger(timezone=local_tz(), **kwargs)


def reschedule_report_jobs():
    """Apply UI-changed report hours to the running scheduler on the fly."""
    if _scheduler is None:
        return
    _scheduler.reschedule_job("morning_report", trigger=_cron(hour=settings.morning_hour(), minute=0))
    evening = settings.evening_hour()
    if evening is not None:
        _scheduler.reschedule_job("evening_report", trigger=_cron(hour=evening, minute=0))
    _scheduler.reschedule_job("weekly_report",
                              trigger=_cron(day_of_week="sun", hour=settings.weekly_hour(), minute=0))


def setup_scheduler() -> AsyncIOScheduler:
    global _started_at, _scheduler
    _started_at = datetime.now(UTC)
    scheduler = AsyncIOScheduler(timezone=local_tz())
    _scheduler = scheduler

    def job(fn, trigger, job_id, **kwargs):
        scheduler.add_job(fn, trigger=trigger, id=job_id, replace_existing=True,
                          max_instances=1, **kwargs)

    soon = datetime.now(UTC) + timedelta(minutes=2)
    # Ticks every minute; the job decides which sites are due.
    job(run_availability_checks, IntervalTrigger(minutes=1), "availability_checks",
        misfire_grace_time=60)
    # SSL + domain once a day — the alert ladder prevents repeat noise.
    job(run_ssl_checks, _cron(hour=8, minute=0), "ssl_checks")
    job(run_domain_checks, _cron(hour=8, minute=5), "domain_checks")
    # SEO/GEO audit before the morning report, so the digest shows fresh results.
    job(run_seo_checks, _cron(hour=7, minute=30), "seo_checks", misfire_grace_time=600)
    job(run_index_checks, _cron(hour=7, minute=45), "index_checks", misfire_grace_time=600)
    # Links: first run shortly after boot, then every N hours.
    job(run_links_checks, IntervalTrigger(hours=config.links_check_interval_hours, start_date=soon),
        "links_checks", misfire_grace_time=300)
    # DNS + deep 5xx probe hourly (offset so they don't pile up).
    job(run_dns_checks, _cron(minute=20), "dns_checks", misfire_grace_time=300)
    job(run_deep_checks, _cron(minute=40), "deep_checks", misfire_grace_time=300)
    # Watchers.
    job(run_heartbeat_watch, IntervalTrigger(minutes=10), "heartbeat_watch")
    job(run_host_checks, IntervalTrigger(minutes=30), "host_checks")
    job(run_container_watch, IntervalTrigger(minutes=10), "container_watch")
    job(run_escalation_watch, IntervalTrigger(minutes=5), "escalation_watch")
    job(run_self_heartbeat, IntervalTrigger(minutes=5), "self_heartbeat")
    job(notifier.flush_queue, IntervalTrigger(minutes=10), "quiet_flush")
    # Nightly maintenance.
    job(run_retention, _cron(hour=3, minute=30), "retention")
    job(run_db_backup, _cron(hour=3, minute=45), "db_backup")
    # Reports.
    job(send_morning_report, _cron(hour=settings.morning_hour(), minute=0), "morning_report")
    job(send_evening_report,
        _cron(hour=settings.evening_hour() if settings.evening_hour() is not None
              else config.evening_report_hour, minute=0), "evening_report")
    job(send_weekly_report, _cron(day_of_week="sun", hour=settings.weekly_hour(), minute=0),
        "weekly_report", misfire_grace_time=3600)
    return scheduler
