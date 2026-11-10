"""APScheduler jobs: monitors, watchers, reports and self-maintenance.
Every outgoing message goes through services.notifier, which owns the
mute / quiet-hours / pause rules."""

import asyncio
import contextlib
import functools
import logging
import os
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta

from aiogram.types import FSInputFile, InlineKeyboardButton, Message
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MAX_INSTANCES
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import config
from db.database import (
    backup_db,
    clear_incident_alerted,
    get_active_host_urls,
    get_active_incidents,
    get_active_page_urls,
    get_active_site_urls,
    get_all_sites,
    get_heartbeats,
    get_last_check,
    get_state,
    get_unalerted_incidents,
    get_uptime_over_days,
    mark_incident_alerted,
    resolve_incident,
    rollup_old_checks,
    save_incident,
    set_dns_state,
    set_state,
    shareable_copy,
)
from monitors import netcheck, region
from monitors.availability import check_all, check_availability
from monitors.base import CheckResult
from monitors.deep_checker import check_all_deep
from monitors.dns_checker import check_all_dns
from monitors.domain_checker import check_all_domains
from monitors.host_checker import (
    DISK_CRITICAL_PCT,
    HEALTHY_RESET,
    RESTART_BUDGET,
    check_disk,
    watch_containers,
)
from monitors.links_checker import check_all_links
from monitors.mail_checker import MAIL_CHECKS, carriers, check_all_mail
from monitors.second_opinion import repeatable
from monitors.security_checker import SECURITY_CHECKS, check_all_security
from monitors.seo_checker import check_all_seo
from monitors.ssl_checker import check_all_ssl
from monitors.visitor_checker import check_all_visitors
from reports.formatter import (
    format_availability_alert,
    format_compact_status_report,
    format_deep_alert,
    format_dns_change,
    format_domain_alert,
    format_links_report,
    format_mail_alert,
    format_mail_recovery,
    format_recovery_alert,
    format_security_alert,
    format_security_recovery,
    format_seo_alert,
    format_ssl_alert,
    format_visitors_alert,
    format_visitors_recovery,
    incident_line,
)
from reports.weekly import build_weekly_report
from services import (
    crux,
    github,
    gsc,
    health,
    heartbeats,
    humanize,
    index_status,
    integrations,
    maintenance,
    notifier,
    railway,
    recommend,
    runtime,
    screenshots,
    settings,
    sitestatus,
    updates,
    yandex_webmaster,
)
from services.actions import (
    alert_actions_keyboard,
    deep_alert_keyboard,
    deploy_hook_for,
    domain_keyboard,
    explain_keyboard,
    heartbeat_keyboard,
    seo_fix_keyboard,
    trigger_redeploy,
)
from services.notifier import Priority
from utils import validate
from utils.clock import local_tz, to_local
from utils.text import SAFE_LIMIT, chunks, clip, esc, fmt_duration, parse_iso_utc, parse_sqlite_utc, plural
from utils.urls import is_api_check, is_web_page, short_host, site_label

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

SLOW_RESPONSE_STREAK = 3
_slow_streak: dict[int, int] = {}
# Sites already told about «down from here, up from outside» — said once per
# episode, forgotten as soon as the site opens from the bot's network again.
_ext_ok_noted: set[int] = set()
_cannot_check_noted: set[int] = set()   # sites already told «не могу проверить» this episode
OUTAGE_REMIND_AFTER = timedelta(hours=1)
OUTAGE_REMIND_EVERY = timedelta(hours=24)
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


@contextlib.contextmanager
def _isolated(what: str):
    """One site's failure — a malformed stored address, a database error
    inside notifier.send, a keyboard that cannot be built — is logged with
    its traceback and does not cost the sites after it their alerts.
    Cancellation (a BaseException, not an Exception) still goes through."""
    try:
        yield
    except Exception:
        logger.exception("%s failed", what)


DEFAULT_SLOW_MS = 3000


def _interval_min(site: dict) -> int:
    """Minutes between checks of one site, always 1..1440 (the chat's own
    rule, utils.validate): a 0, a negative or an absurd value from an old
    import or a restored DB must not break the tick for every site
    (OverflowError in the date arithmetic)."""
    minutes = validate.int_in(site.get("check_interval_min"), *validate.CHECK_INTERVAL_MIN)
    if minutes is None:
        minutes = validate.int_in(config.check_interval_minutes, *validate.CHECK_INTERVAL_MIN)
    return minutes if minutes is not None else 5


def _slow_ms(site: dict) -> int:
    """«Slow» threshold of one site, within utils.validate.SLOW_MS; 0 or
    negative would call every answer slow."""
    ms = validate.int_in(site.get("slow_ms"), *validate.SLOW_MS)
    if ms is None:
        ms = validate.int_in(config.slow_response_ms, *validate.SLOW_MS)
    return ms if ms is not None else DEFAULT_SLOW_MS


# ── Availability ─────────────────────────────────────────────────────────────

async def run_availability_checks():
    await _availability_tick()
    health.tick_done()          # only a tick that finished counts: /health and both watchdogs read it


async def _availability_tick():
    now = datetime.now(UTC)
    back_since = await _own_network_back()
    if back_since:
        _next_avail_check.clear()           # the network is back: look at every site in this very tick
    await _remind_long_outage(now)
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
            _next_avail_check[s["id"]] = now + timedelta(minutes=_interval_min(s))
            due.append(s["url"])
    results = await check_all(due) if due else []
    # The tick only checks; every message goes to the alert worker, so a
    # silent Telegram or a slow deploy hook never holds up the next check.
    for r in results:
        site = by_url.get(r.url, {})
        if r.incident_new:
            _enqueue(_send_availability_alert, r, site, incident_id=r.incident_id)
        elif r.external_ok is True:
            _enqueue(_note_local_network_problem, r.url, r.site_id, r.external_ups, r.external_total)
        elif r.transient and not r.own_outage:
            _enqueue(_note_cannot_check, r.url, r.site_id, r.error)
        elif r.recovered:
            _enqueue(_send_recovery, r)
        if r.ok:
            _cannot_check_noted.discard(r.site_id)
            _enqueue(_site_is_up, r, _slow_ms(site))
    if back_since:
        _enqueue(_report_own_outage, back_since, now, results)


async def _own_network_back() -> datetime | None:
    """When the bot's own network has just come back: since when it was gone."""
    since = await netcheck.outage_since()
    if since is None or not await netcheck.own_network_ok():
        return None
    await netcheck.end_outage()
    await set_state("net_outage_reminded", None)
    return since


async def _remind_long_outage(now: datetime):
    """A «no network» episode that lasts for hours is more likely canaries a
    firewall blocks than a real outage: said once a day while it lasts."""
    since = await netcheck.outage_since()
    if since is None or now - since < OUTAGE_REMIND_AFTER:
        return
    reminded = parse_iso_utc(await get_state("net_outage_reminded"))
    if reminded and now - reminded < OUTAGE_REMIND_EVERY:
        return
    await set_state("net_outage_reminded", now.isoformat())
    _enqueue(notifier.send,
             f"⚠️ Уже {fmt_duration((now - since).total_seconds() / 60)} сервер бота считает, что у него нет сети: "
             f"не отвечают адреса из NET_CANARIES. Всё это время сайт без ответа я считаю сбоем своей сети и "
             f"тревогу не поднимаю. Если сеть на самом деле есть — впиши в NET_CANARIES адреса, которые сервер "
             f"пропускает, или оставь переменную пустой.", Priority.NORMAL)


async def _report_own_outage(since: datetime, until: datetime, results: list):
    """ONE message for the whole episode instead of an alert per site."""
    minutes = (until - since).total_seconds() / 60
    fmt = "%H:%M" if minutes < 20 * 60 else "%d.%m %H:%M"
    lines = [f"⚠️ С {to_local(since).strftime(fmt)} до {to_local(until).strftime(fmt)} у сервера бота "
             f"не было сети или не работал DNS: проверить сайты было нельзя, тревоги о них я не поднимал."]
    down = [esc(site_label(r.url)) for r in results if not r.ok and not r.transient]
    if down:
        lines.append(f"Сейчас не открываются: {', '.join(down)}. Если не поднимутся — пришлю обычную тревогу.")
    elif results:
        lines.append("Сейчас всё открывается.")
    await notifier.send("\n".join(lines), Priority.NORMAL)


async def check_own_network_at_start():
    """Once, soon after a start. Canaries that never answer (a firewall that
    lets out only the monitored sites) would make every unanswered check look
    like «no network on the bot's server» — and silence real alerts. When the
    sites answer while the canaries do not, the self-check switches itself off."""
    if not netcheck.enabled() or await netcheck.probe():
        return
    if netcheck.recently_answered():
        netcheck.disable()
        await _say_fail_open()
        return
    await notifier.send(
        "⚠️ Не проходит проверка сети сервера бота: не отвечают адреса из NET_CANARIES "
        "(по умолчанию 1.1.1.1, 8.8.8.8, 9.9.9.9 и имена DNS-сервисов). Пока так, сайт без ответа я "
        "посчитаю сбоем своей сети и тревогу не подниму. Если сервер за файрволом — впиши в NET_CANARIES "
        "адреса, которые он пропускает, или оставь переменную пустой, чтобы выключить проверку.",
        Priority.NORMAL)


async def _say_fail_open():
    await notifier.send(
        "⚠️ Адреса из NET_CANARIES не отвечают, а сайты отвечают — похоже, сервер за файрволом. Самопроверку "
        "сети я выключил до перезапуска: сайт без ответа снова считается сбоем сайта. Чтобы она работала, "
        "впиши в NET_CANARIES адреса, которые сервер пропускает.", Priority.NORMAL)


async def _note_cannot_check(url: str, site_id: int, reason: str | None):
    """The check itself cannot run (no ICMP socket, request settings
    rejected): said once per episode — this is not «the site is down»."""
    if site_id in _cannot_check_noted:
        return
    sent = await notifier.send(
        f"⚠️ {esc(site_label(url))}: не могу проверить — {esc(reason or 'неизвестная причина')}. "
        f"Тревоги по нему не будет, пока это не исправить.", Priority.NORMAL, site_id=site_id)
    if sent:
        _cannot_check_noted.add(site_id)


# ── Alert delivery: at least once ────────────────────────────────────────────
#
# A check writes its incident BEFORE the alert goes out. An exception, a
# stopped job or a killed process in between used to lose the alert for
# good: the next check sees the incident as old. incidents.alerted_at is set
# only once the alert went out; deliver_missed_alerts() sends what is owed.
# A pause or a maintenance window holds the full alert in bot_state (held:…,
# services.notifier.release_held says it after) — the re-send leaves those alone.

MISSED_ALERT_MIN = 2            # a fresh incident gets this long to be alerted by its own job
_in_flight: set[int] = set()    # incidents whose alert is queued or being sent right now
_busy: Counter[str] = Counter()  # check types whose job is running: it alerts on its own incidents
_sent_unrecorded: set[int] = set()  # alert went out, but the DB refused the mark (no re-send storm)
_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None


def _enqueue(fn, *args, incident_id: int | None = None):
    """Run fn(*args) in the alert worker: one at a time, in order, never
    inside the tick."""
    global _queue, _worker
    loop = asyncio.get_running_loop()
    if _worker is None or _worker.done() or _worker.get_loop() is not loop:
        _queue = asyncio.Queue()
        _worker = loop.create_task(_work(_queue), name="alert-worker")
    if incident_id is not None:
        _in_flight.add(incident_id)
    _queue.put_nowait((fn, args, incident_id))


async def _work(queue: asyncio.Queue):
    while True:
        fn, args, incident_id = await queue.get()
        try:
            await fn(*args)
        except Exception:
            logger.exception("Alert worker: %s failed", fn.__name__)
        finally:
            if incident_id is not None:
                _in_flight.discard(incident_id)
            queue.task_done()


async def drain_alert_worker():
    """Wait until everything queued for the alert worker is done (tests, shutdown)."""
    if _queue is not None and _worker is not None and not _worker.done():
        await _queue.join()


def cancel_alert_worker():
    """Shutdown: the worker waits for work forever — it is not a task to wait for."""
    if _worker is not None and not _worker.done():
        _worker.cancel()


_running: set[str] = set()


def _once(name: str, *, mark: bool = False):
    """One copy of a job at a time, whichever id started it — the cron run,
    its «soon» twin, a re-check or a catch-up: APScheduler's max_instances
    counts per id, and two ids of one check used to run it twice in
    parallel. mark=True: a daily job — when it last finished goes to
    bot_state, and the start catches up a run that a restart or a clock step
    skipped (catch_up_missed). A set, not an asyncio.Lock: a lock binds to
    one event loop, and the tests start a new loop per test."""
    def wrap(fn):
        @functools.wraps(fn)
        async def run(*args, **kwargs):
            if name in _running:
                logger.info("%s is already running — this start is skipped", name)
                return None
            _running.add(name)
            try:
                result = await fn(*args, **kwargs)
            finally:
                _running.discard(name)
            if mark:
                await set_state(f"last_run:{name}", datetime.now(UTC).isoformat())
            return result
        return run
    return wrap


def _alerts_on(*check_types: str):
    """For a job that opens incidents and alerts on them itself: while it
    runs, deliver_missed_alerts() leaves those incidents to it."""
    def wrap(fn):
        @functools.wraps(fn)
        async def run(*args, **kwargs):
            _busy.update(check_types)
            try:
                return await fn(*args, **kwargs)
            finally:
                _busy.subtract(check_types)
        return run
    return wrap


async def _alert_once(incident_id: int | None, site_id: int, text: str, priority: Priority, kb,
                      *, always_ring: bool = False, to_helpers: bool = False) -> Message | bool | None:
    """Send an incident's alert and record that it went out. None — the site
    is paused or inside a maintenance window: the full alert is held by the
    notifier (held:<id>) and said once the pause is over; the incident stays
    owed until then."""
    if incident_id is not None:
        _in_flight.add(incident_id)
    try:
        if incident_id is not None:
            await _record_alert(incident_id, False)
        paused = await maintenance.is_paused(site_id)
        sent = await notifier.send(text, priority, site_id=site_id, reply_markup=kb, always_ring=always_ring,
                                   to_helpers=to_helpers, announces=incident_id)
        if paused:
            return None
        if incident_id is not None:
            await _record_alert(incident_id, True)
        return sent
    finally:
        if incident_id is not None:
            _in_flight.discard(incident_id)


async def _record_alert(incident_id: int, alerted: bool):
    """The mark must never cost the alert itself: a DB that refuses writes
    is logged, and a sent alert is remembered in memory instead."""
    try:
        await (mark_incident_alerted if alerted else clear_incident_alerted)(incident_id)
        _sent_unrecorded.discard(incident_id)
    except Exception as e:
        logger.warning("Could not record the alert of incident %s: %s", incident_id, e)
        if alerted:
            _sent_unrecorded.add(incident_id)


def _missed_alert_text(inc: dict) -> str:
    since = parse_sqlite_utc(inc.get("created_at"))
    when = f" с {to_local(since).strftime('%d.%m %H:%M')}" if since else ""
    return (f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}\n"
            f"⏳ Тревога об этом не ушла вовремя — проблема открыта{when}. Проверки продолжаются.")


async def deliver_missed_alerts():
    """Every minute: alerts still owed — their sending failed or was cut
    off (an exception, a stop, a crash), or the site was paused. A short
    line per incident, marked as late."""
    for inc in await get_unalerted_incidents(MISSED_ALERT_MIN):
        if inc["id"] in _in_flight or inc["id"] in _sent_unrecorded or _busy[inc["check_type"]] > 0:
            continue
        if await get_state(f"{notifier.HELD}{inc['id']}"):
            continue        # the full alert waits for the end of the pause: release_held says it
        try:
            if inc["check_type"] == "seo" and inc["severity"] != "critical":
                await _record_alert(inc["id"], True)   # 🟠 search findings are never alerted (format_seo_alert)
                continue
            availability = inc["check_type"] == "availability"
            kb = (alert_actions_keyboard(inc["url"], inc["site_id"], inc["id"],
                                         hosting=await get_state(f"hosting:{inc['site_id']}"),
                                         api=is_api_check(inc["url"], inc.get("http_method")))
                  if availability else explain_keyboard(inc["id"]))
            text = _missed_alert_text(inc)
            sent = await _alert_once(inc["id"], inc["site_id"], text, notifier.priority_for(inc["severity"]), kb,
                                     always_ring=availability, to_helpers=availability)
            if availability and sent is not None:
                _after_availability_alert(inc["url"], inc["site_id"], getattr(sent, "message_id", None), text, kb)
        except Exception:
            logger.exception("Re-sending the alert of incident %s failed", inc["id"])


async def _send_availability_alert(r, site: dict):
    msg = format_availability_alert(r, integrations.second_opinion_enabled() and repeatable(site))
    kb = alert_actions_keyboard(r.url, r.site_id, r.incident_id, hosting=r.hosting, api=r.api)
    sent = await _alert_once(r.incident_id, r.site_id, msg, Priority.CRITICAL, kb, always_ring=True, to_helpers=True)
    if sent is not None:
        _after_availability_alert(r.url, r.site_id, getattr(sent, "message_id", None), msg, kb)


async def _send_recovery(r):
    # Rings by default; with «будить только когда сайт лёг» only the fall does.
    text = format_recovery_alert(r)
    inc_id = (r.resolved_incident or {}).get("id")
    if inc_id and await notifier.announced_at(inc_id) and await maintenance.paused_until(r.site_id):
        # «🔧 Я чиню» promised «напишу, когда поднимется»: the fix ends the silence.
        # A pause set before an unannounced fall stays — send() drops the ✅ then.
        await maintenance.pause_site(r.site_id, None)
        text += "\n\n🔧 Тишину снял — снова пишу обо всём."
    await notifier.send(text, Priority.CRITICAL, site_id=r.site_id, resolves=inc_id, to_helpers=True)


async def _site_is_up(r, slow_ms: int):
    if r.site_id in _ext_ok_noted:
        _ext_ok_noted.discard(r.site_id)
        await notifier.send(f"✅ {esc(site_label(r.url))} снова открывается и с моего сервера.",
                            Priority.NORMAL, site_id=r.site_id)
    await _track_slow(r, slow_ms)


def _after_availability_alert(url: str, site_id: int, message_id: int | None, text: str, kb):
    """Once the alert is out: the deploy hook (AUTO_REDEPLOY) — it reports
    into the same message — and the 2- and 5-minute re-checks."""
    if config.auto_redeploy and deploy_hook_for(url):
        _enqueue(_redeploy_after_alert, url, site_id, message_id, text, kb)
    elif message_id is not None:
        _schedule_followups(url, site_id, message_id, text, kb)


async def _redeploy_after_alert(url: str, site_id: int, message_id: int | None, text: str, kb):
    res = await trigger_redeploy(url)
    line = f"🤖 Уже сделал сам: 🚀 {res.text}" if res.ok else f"🤖 Пробовал пересобрать сайт — не вышло. {res.text}"
    if message_id is None:      # the alert sits in the outbox or the quiet queue — say it separately
        await notifier.send(f"{esc(site_label(url))}: {line}", Priority.NORMAL, site_id=site_id)
        return
    text = clip(f"{text}\n\n{line}")
    await notifier.edit(message_id, text, reply_markup=kb)
    _schedule_followups(url, site_id, message_id, text, kb)


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
    elif r.transient:
        line = f"\n\n🔁 Через {minutes} мин: проверить не смог — {esc(r.error)}."
    else:
        line = f"\n\n🔁 Через {minutes} мин: всё ещё не открывается ({esc(humanize.describe_error(r.error))})."
    await notifier.edit(message_id, clip(text + line), reply_markup=kb)


async def _followup_slow(url: str, site_id: int, message_id: int, text: str, kb, slow_ms: int):
    r = await check_availability(url, manage=False)
    ms = r.response_time_ms or 0
    if r.ok and ms < slow_ms:
        line = f"\n\n🔁 Через 5 мин: {humanize.fmt_seconds(ms)}, уже нормально."
    elif r.ok:
        line = f"\n\n🔁 Через 5 мин: всё ещё медленно ({humanize.fmt_seconds(ms)})."
    else:
        line = "\n\n🔁 Через 5 мин: теперь вообще не открывается — напишу отдельно."
    await notifier.edit(message_id, clip(text + line), reply_markup=kb)


async def _note_local_network_problem(url: str, site_id: int, ups: int = 0, total: int = 0):
    """Down from the bot's server but open from most external nodes — said
    once per episode, without a ring. Either the bot's network or the bot's
    region (a block, a provider, a CDN node): the owner knows which."""
    if site_id in _ext_ok_noted:
        return
    where = f"с {ups} из {total} внешних серверов" if total else "с внешних серверов"
    sent = await notifier.send(
        f"🟠 {esc(site_label(url))} не открылся с моего сервера, но открывается {where}. Если посетители "
        f"сайта в том же регионе, что и мой сервер, у них он, скорее всего, тоже не открывается "
        f"(блокировка, сбой у провайдера или CDN); если нет — дело в сети моего сервера. Тревогу со звонком "
        f"не поднимаю; напишу, когда снова откроется.",
        Priority.NORMAL, site_id=site_id)
    if sent:
        _ext_ok_noted.add(site_id)


async def _track_slow(r, slow_ms: int):
    """The site is up, but consistently slow → a 'performance' incident
    after a streak; resolved (unconditionally) on the first fast reply."""
    ms = r.response_time_ms or 0
    if ms >= slow_ms:
        _slow_streak[r.site_id] = _slow_streak.get(r.site_id, 0) + 1
        if _slow_streak[r.site_id] == SLOW_RESPONSE_STREAK:
            inc_id, is_new = await save_incident(r.site_id, "performance",
                                                 f"Slow responses: ~{ms}ms", severity="warning")
            if is_new:
                expl = humanize.EXPLANATIONS["slow"]
                text = (f"🟠 {esc(site_label(r.url))} открывается медленно: {humanize.fmt_seconds(ms)} "
                        f"вместо обычных долей секунды, {SLOW_RESPONSE_STREAK} "
                        f"{plural(SLOW_RESPONSE_STREAK, 'проверка', 'проверки', 'проверок')} подряд.\n"
                        f"{expl.meaning}")
                sent = await _alert_once(inc_id, r.site_id, text, Priority.NORMAL, explain_keyboard(inc_id))
                if isinstance(sent, Message) and _scheduler is not None:
                    _scheduler.add_job(_followup_slow, "date",
                                       run_date=datetime.now(UTC) + timedelta(minutes=5),
                                       args=[r.url, r.site_id, sent.message_id, text, explain_keyboard(inc_id), slow_ms],
                                       id=f"followup-slow:{sent.message_id}", replace_existing=True)
        return
    _slow_streak[r.site_id] = 0
    resolved = await resolve_incident(r.site_id, "performance")
    if resolved:
        await notifier.send(f"✅ {esc(site_label(r.url))} снова открывается за {humanize.fmt_seconds(ms)}. "
                            f"Ничего делать не нужно.", Priority.NORMAL, site_id=r.site_id,
                            resolves=resolved["id"])


# ── Generic "alert on transition" for the daily/hourly monitors ──────────────

async def _notify_transitions(results: list[CheckResult], alert, recovery,
                              priority=lambda r: Priority.NORMAL, keyboard=None):
    """Alert on incident_new (with «ℹ️ Что делать» for the incident),
    a one-liner on recovery."""
    for r in results:
        with _isolated(f"Alert for {site_label(r.url)} ({type(r).__name__})"):
            if r.incident_new:
                msg = alert(r)
                if msg:
                    kb = keyboard(r) if keyboard else explain_keyboard(r.incident_id)
                    await _alert_once(r.incident_id, r.site_id, clip(msg), priority(r), kb)
                elif r.incident_id:
                    await _record_alert(r.incident_id, True)   # silent by design: nothing is owed
            elif r.recovered:
                await notifier.send(recovery(r), Priority.NORMAL, site_id=r.site_id,
                                    resolves=(r.resolved_incident or {}).get("id"))


def _by_level(r: CheckResult) -> Priority:
    """🔴 in the text means it rings; 🟠 arrives silently."""
    return notifier.priority_for(r.severity)


PAGE_CHECKS = ("links", "deep", "seo")


async def close_inapplicable(check_types: tuple[str, ...], urls: list[str]):
    """A monitor that left a check's input — it became an API check (POST,
    an /api/ address), or its host is now covered by a site — keeps no open
    finding of that check: nothing would ever close it. Silently: nothing
    was fixed, the question stopped applying."""
    keep = set(urls)
    for inc in await get_active_incidents():
        if inc["check_type"] in check_types and inc["url"] not in keep:
            await resolve_incident(inc["site_id"], inc["check_type"])


@_once("ssl_checks", mark=True)
@_alerts_on("ssl")
async def run_ssl_checks():
    """Expiry ladder alerts only. A certificate that renewed by itself is
    normal operation: no message — the date goes into the snapshot and the
    weekly report mentions it."""
    urls = await get_active_host_urls()
    await close_inapplicable(("ssl",), urls)
    await _notify_transitions(
        await check_all_ssl(urls), format_ssl_alert,
        lambda r: f"✅ Сертификат {esc(site_label(r.url))} обновлён, всё в порядке. Ничего делать не нужно.",
        priority=_by_level)


@_once("domain_checks", mark=True)
@_alerts_on("domain")
async def run_domain_checks():
    urls = await get_active_host_urls()
    await close_inapplicable(("domain",), urls)
    await _notify_transitions(
        await check_all_domains(urls), format_domain_alert,
        lambda r: f"✅ Домен {esc(r.domain or r.url)} продлён, всё в порядке. Ничего делать не нужно.",
        priority=_by_level,
        keyboard=lambda r: domain_keyboard(r.domain_info.registrar if r.domain_info else None, r.incident_id))


@_alerts_on("links")
async def run_links_checks():
    urls = await get_active_page_urls()
    await close_inapplicable(("links",), urls)
    await _notify_transitions(await check_all_links(urls), format_links_report, _links_recovered)


def _links_recovered(r) -> str:
    """Closed while some link still fails once in a while: say so, rather
    than «всё работает» next to a link that does not open right now."""
    label = esc(site_label(r.url))
    if not r.pending:
        return f"✅ На {label} все ссылки снова работают."
    n = len(r.pending)
    return (f"✅ На {label} снимаю тревогу по ссылкам: {n} {plural(n, 'ссылка', 'ссылки', 'ссылок')} "
            f"сейчас не {'открывается' if n == 1 else 'открываются'}, но это похоже на временный сбой на той "
            f"стороне. Перепроверю через несколько часов и напишу, только если "
            f"{'она не откроется' if n == 1 else 'они не откроются'} и тогда.")


@_alerts_on("deep")
async def run_deep_checks():
    urls = await get_active_page_urls()
    await close_inapplicable(("deep",), urls)
    await _notify_transitions(
        await check_all_deep(urls), format_deep_alert,
        lambda r: f"✅ На {esc(site_label(r.url))} все страницы снова отвечают без ошибок.",
        keyboard=lambda r: deep_alert_keyboard(r.url, r.site_id, r.incident_id))


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


@_once("seo_checks", mark=True)
@_alerts_on("seo")
async def run_seo_checks():
    """Daily SEO/GEO audit. Only critical findings (noindex, search bots
    blocked) become alerts — the site is disappearing from search right
    now, so they bypass mute. Improvements wait for the weekly report.
    A site added today is audited quietly: its first findings arrive in
    the next morning digest, not as ten separate alerts."""
    urls = await get_active_page_urls()
    await close_inapplicable(("seo",), urls)
    fresh = await _recently_added()
    if fresh:
        await check_all_seo([u for u in urls if u in fresh], manage=False)
    await _notify_transitions(
        await check_all_seo([u for u in urls if u not in fresh]), format_seo_alert,
        # The closed incident may have been a 🟠 «нет sitemap», not a closed
        # site — the recovery line must be true for both levels.
        lambda r: f"✅ {esc(site_label(r.url))}: поисковикам больше ничего не мешает. Ничего делать не нужно.",
        priority=lambda r: Priority.CRITICAL,
        keyboard=lambda r: seo_fix_keyboard(
            r.site_id, list(dict.fromkeys(p.code for p in r.problems if p.severity == "critical")), on_alert=True))


@_once("card_pictures", mark=True)
async def run_card_pictures():
    """Once a day, after the search audit: a fresh picture of every page
    for its card (services.screenshots). One at a time — the free
    screenshot service allows about fifty a day."""
    for site in await get_all_sites():
        with _isolated(f"Card picture of {site_label(site['url'])}"):
            if is_web_page(site["url"], site.get("http_method")) \
                    and await screenshots.is_due(site["id"]):
                await screenshots.refresh(site["id"], site["url"])


def rerun_seo_after_update(minutes: int = 3):
    """A new build may have fixed a check. Yesterday's findings — maybe
    false ones — must not wait for 07:30: audit again a few minutes after
    the start, with incidents, so stale ones close and say so."""
    if _scheduler is not None:
        _scheduler.add_job(run_seo_checks, DateTrigger(run_date=datetime.now(UTC) + timedelta(minutes=minutes), timezone=UTC),
                           id="seo_after_update", replace_existing=True, misfire_grace_time=600)


REGION_PAUSE = 2.0   # seconds between sites: be polite to check-host.net


async def run_region_checks():
    """Hourly: how fast each site opens from the audience's region — a
    check-host node picked by the owner's timezone. Same opt-in as the
    second opinion (same external service). Sites that are down right now
    are skipped: a timing of a broken site is noise."""
    if not integrations.second_opinion_enabled():
        return
    country = region.country_for_timezone(settings.timezone())
    for site in await get_all_sites():
        with _isolated(f"Region timing of {site_label(site['url'])}"):
            if not is_web_page(site["url"], site.get("http_method")):
                continue
            snap = await sitestatus.get(site["id"])
            if (snap.get("avail") or {}).get("status") not in (None, "ok"):
                continue
            measured = await region.measure(site["url"], country)
            if measured:
                await sitestatus.update(site["id"], "region", ms=measured[0], name=measured[1])
            await asyncio.sleep(REGION_PAUSE)


async def run_dns_checks():
    """The new baseline is committed only after the alert is delivered or
    queued — otherwise a failed send would swallow the change forever."""
    for change in await check_all_dns(await get_active_host_urls()):
        with _isolated(f"DNS alert for {change['host']} {change['rtype']}"):
            sent = await notifier.send(format_dns_change(change),
                                       Priority.CRITICAL if change["critical"] else Priority.NORMAL)
            if sent:
                await set_dns_state(change["host"], change["rtype"], change["new"])


MAIL_RECHECK_MIN = 10


@_once("mail_checks")
@_alerts_on(*MAIL_CHECKS)
async def run_mail_checks():
    """Hourly: mail on the sites' domains (monitors.mail_checker). A domain
    whose mail did not answer once is asked again in ten minutes — a dead
    mailbox is said within the quarter hour, not in two."""
    urls = await get_active_host_urls()
    await close_inapplicable(MAIL_CHECKS, list(carriers(urls).values()))
    results = await check_all_mail(urls)
    await _notify_transitions(results, format_mail_alert, format_mail_recovery, priority=_by_level,
                              keyboard=_mail_keyboard)
    if _scheduler is not None and any(r.kind == "mail" and r.ok and r.delivery.ok is False for r in results):
        _scheduler.add_job(run_mail_checks, DateTrigger(run_date=datetime.now(UTC) + timedelta(minutes=MAIL_RECHECK_MIN), timezone=UTC),
                           id="mail_recheck", replace_existing=True, misfire_grace_time=300)


def _mail_keyboard(r):
    status = ([InlineKeyboardButton(text=f"↗️ Статус {r.provider}", url=r.status_page)]
              if r.kind == "mail" and r.status_page else [])
    mail = [InlineKeyboardButton(text="✉️ Почта", callback_data=f"mail:{r.site_id}")]
    return explain_keyboard(r.incident_id, status, mail)


@_once("security_checks")
@_alerts_on(*SECURITY_CHECKS)
async def run_security_checks():
    """Every six hours: service files open to anyone (🔴, rings), plain
    http and protective headers (🟠, quiet) — monitors.security_checker.
    One check per host, web pages only."""
    hosts = set(await get_active_host_urls())
    urls = [u for u in await get_active_page_urls() if u in hosts]
    await close_inapplicable(SECURITY_CHECKS, urls)
    await _notify_transitions(await check_all_security(urls), format_security_alert, format_security_recovery,
                              priority=_by_level, keyboard=_security_keyboard)


def _security_keyboard(r):
    return explain_keyboard(r.incident_id, [InlineKeyboardButton(text="🛡 Безопасность",
                                                                 callback_data=f"sec:{r.site_id}")])


@_once("visitor_checks", mark=True)
@_alerts_on("visitors")
async def run_visitor_checks():
    """Daily: real visitors' speed from Google's Chrome data, when a key is set."""
    urls = await get_active_page_urls()
    await close_inapplicable(("visitors",), urls)
    await _notify_transitions(await check_all_visitors(urls), format_visitors_alert, format_visitors_recovery,
                              keyboard=_visitors_keyboard)


def _visitors_keyboard(r):
    return explain_keyboard(r.incident_id,
                            [InlineKeyboardButton(text="↗️ PageSpeed Insights", url=crux.pagespeed_url(r.url))],
                            [InlineKeyboardButton(text="👥 Скорость у посетителей", callback_data=f"spd:{r.site_id}")])


@_once("index_checks", mark=True)
async def run_index_checks():
    """Daily indexing watch via GSC / Yandex.Webmaster (when tokens are set):
    the answers go into the site snapshot for the «🔎 Поиск и ИИ» screen,
    and a change of state becomes a message — nothing else does."""
    if not gsc.available() and not yandex_webmaster.available():
        return
    yx = await yandex_webmaster.get_summaries() if yandex_webmaster.available() else None
    for site in await get_all_sites():
        with _isolated(f"Index check of {site_label(site['url'])}"):
            url = site["url"]
            if not is_web_page(url, site.get("http_method")):
                continue
            google, yandex = await index_status.refresh(site["id"], url, yx)
            if google:
                key = f"gsc_idx:{short_host(url)}"
                prev = await get_state(key)
                current = f"{google['verdict']}|{google['coverage']}"
                if prev != current:
                    prev_verdict = (prev or "").split("|", 1)[0]
                    sent = True
                    if google["verdict"] == "PASS":
                        if prev and prev_verdict != "PASS":
                            sent = await notifier.send(
                                f"✅ Google снова показывает {esc(site_label(url))} в поиске. Ничего делать не нужно."
                                + _tech_line(google["coverage"]), site_id=site["id"])
                    elif prev_verdict != google["verdict"]:
                        sent = await notifier.send(
                            f"🔴 Google убрал {esc(site_label(url))} из поиска\n"
                            f"Люди больше не найдут сайт через Google.\n\n"
                            f"Что делать:\n• Открыть Google Search Console → Проверка URL для главной: там будет "
                            f"написана причина.\n• Чаще всего это noindex на странице или запрет в robots.txt — "
                            f"проверь «🔎 Поиск и ИИ» в боте." + _tech_line(google["coverage"]), Priority.CRITICAL,
                            site_id=site["id"])
                    if sent:
                        await set_state(key, current)
            if yandex:
                host = short_host(url)
                key = f"yx_problems:{host}"
                prev = await get_state(key) or ""
                problems = yandex["alert_problems"]
                current = ",".join(sorted(problems))
                if current == prev:
                    continue
                sent = True
                if current:
                    level = index_status.yandex_problem_level(problems)
                    plist = "\n".join(f"  • {esc(k)}: {esc(v)}" for k, v in problems.items())
                    sent = await notifier.send(
                        f"{humanize.level_icon(level)} Яндекс видит проблемы на {esc(host)}:\n{plist}\n\n"
                        f"Что делать: открыть Яндекс.Вебмастер → Диагностика сайта, там каждая проблема "
                        f"с инструкцией.", notifier.priority_for(level), site_id=site["id"])
                elif prev:
                    sent = await notifier.send(f"✅ Яндекс больше не видит проблем на {esc(host)}. Ничего делать не нужно.",
                                               site_id=site["id"])
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
        with _isolated(f"Heartbeat check of {job}"):
            last = parse_sqlite_utc(beats.get(job))
            # From the last mark or the job's addition, less the bot's own downtime
            # (services.heartbeats): neither a new job nor a redeploy is silence.
            silence_min = await heartbeats.silent_minutes(job, last, now)
            if last:
                overdue = heartbeats.is_late(interval_min, silence_min)
                detail = f"последний раз на связи {fmt_duration((now - last).total_seconds() / 60)} назад"
            else:
                overdue, detail = silence_min > interval_min, "на связь так и не выходит"
            alerted = await get_state(f"hb_alerted:{job}")
            if overdue and not alerted:
                expl = humanize.EXPLANATIONS["heartbeat"]
                if last:
                    text = (f"🟠 «{esc(job)}» молчит: {detail}, а выходит на связь "
                            f"{heartbeats.every(interval_min)}.\n{expl.meaning}\n\n"
                            f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(humanize.steps_block(expl))}")
                else:
                    # Never marked at all: the task was added but not connected —
                    # nothing failed, so no «крон упал»; the fix is one tap.
                    text = (f"⚠️ «{esc(job)}» так и не выходит на связь — похоже, не подключили.\n"
                            f"Жду связи {heartbeats.every(interval_min)}, но её ещё не было, и я не могу сказать, "
                            f"работает ли «{esc(job)}».\n\n"
                            f"Такой бот или задача есть — нажми «Подключить», там всё по шагам. "
                            f"Нет — «Удалить».")
                sent = await notifier.send(text, reply_markup=heartbeat_keyboard([job]))
                if sent:
                    await set_state(f"hb_alerted:{job}", now.isoformat())
            elif not overdue and alerted:
                await set_state(f"hb_alerted:{job}", None)  # clear a stale flag


# ── Bot-host monitoring ──────────────────────────────────────────────────────

_DISK_RANK = {"warning": 1, "critical": 2, "1": 1}     # "1" — the flag older versions wrote
_disk_level_unsaved: str | None = None                 # the level said when the flag could not be written


async def run_host_checks():
    global _disk_level_unsaved
    result = await check_disk()
    already = await get_state("disk_alerted") or _disk_level_unsaved
    cleaned = result["cleaned"]
    if cleaned and cleaned["bytes"]:
        # Every prune is said, with what it removed — never a silent deletion.
        await notifier.send(
            f"💾 Диск был заполнен на {result['pct']}% — удалил в docker висячие слои образов "
            f"({cleaned['layers']}) и кэш сборки старше суток ({cleaned['caches']}): "
            f"−{cleaned['bytes'] / 1e9:.1f} GB, сейчас {result['pct_after']}%. Остановленные контейнеры "
            f"и образы с тегами не трогаю; следующая очистка — не раньше чем через 12 ч.")
        if result["pct_after"] < config.disk_alert_pct:
            _disk_level_unsaved = None
            await set_state("disk_alerted", None)
            return
    if result["over_threshold"]:
        # 🟠 at the threshold, 🔴 when writes are about to fail: a rise is a new message.
        level = "critical" if result["pct_after"] >= DISK_CRITICAL_PCT else "warning"
        if _DISK_RANK[level] > _DISK_RANK.get(already or "", 0):
            expl = humanize.EXPLANATIONS["disk"]
            useless = ("\n\nПочистить docker было нечего: место занимают не образы — смотри тома и логи "
                       "(docker system df)." if cleaned is not None and not cleaned["bytes"] else "")
            # Percent and gigabytes are both measured after the prune, so they agree.
            sent = await notifier.send(
                f"{humanize.level_icon(level)} {result['label']} заполнен на {result['pct_after']}% "
                f"({result['used_gb_after']} из {result['total_gb']} GB)."
                + (" Почистить docker сам не смог: Docker не отвечает." if result.get("cleanup_failed") else "")
                + f"\n{expl.meaning}\n\n"
                f"Что это обычно значит: {esc(expl.cause)}.\n\n{esc(humanize.steps_block(expl))}" + useless,
                notifier.priority_for(level))
            if sent:
                try:
                    await set_state("disk_alerted", level)
                    _disk_level_unsaved = None
                except sqlite3.OperationalError as e:
                    # The flag lives on the full volume: remember it here, or 🔴 rings every 30 min.
                    health.db_write_failed(e)
                    _disk_level_unsaved = level
    elif already:
        _disk_level_unsaved = None
        await set_state("disk_alerted", None)
        await notifier.send(f"✅ {result['label']} снова в норме: {result['pct']}%. Ничего делать не нужно.")


async def run_container_watch():
    """One message per change, never one per tick (the watch runs every 10
    minutes): broken — 🔴 once, it rings through mute; back to work — ✅ only
    after HEALTHY_RESET of health in a row, so a flapping container is not a
    page every 20 minutes; a missing container and a silent Docker — 🟠 once.
    A restart whose verdict the run could not wait for is said as that (🟠),
    and the verdict comes with the next look."""
    for e in await watch_containers():
        with _isolated(f"Container alert for {e.get('name', 'docker')}"):
            if e.get("api_down"):
                await _container_note("docker_api_down", True, (
                    "🟠 Не могу достучаться до Docker на сервере бота — автоперезапуск контейнеров и очистка "
                    "диска сейчас не работают: сокет Docker принадлежит группе root: нужна "
                    "<code>ALLOW_ROOT_GROUP_SOCKET=1</code> в .env или отфильтрованный доступ через "
                    "socket-proxy (docs/SETUP.md).")
                    if e["mounted"] and config.docker_socket_root_group else (
                    "🟠 Не могу достучаться до Docker на сервере бота — автоперезапуск контейнеров и очистка "
                    "диска сейчас не работают. Обычно помогает перезапуск контейнера бота: "
                    "<code>docker compose up -d</code> (сокет смонтируется заново).")
                    if e["mounted"] else (
                    "🟠 AUTORESTART_CONTAINERS задан, но сокет Docker в контейнер бота не смонтирован — "
                    "автоперезапуск не работает. Как подключить: «🔌 Подключения» → «🐳 Docker на хосте»."))
                return
            await _container_note("docker_api_down", False, "✅ Docker на сервере бота снова отвечает.")
            name = e["name"]
            label = esc(name)
            if e.get("missing"):
                await _container_note(f"container_missing:{name}", True, (
                    f"🟠 Сервис «{label}» из AUTORESTART_CONTAINERS не найден в Docker: проверь имя "
                    f"(<code>docker ps -a</code>) — контейнер могли переименовать или удалить."))
                continue
            await _container_note(f"container_missing:{name}", False, None)
            key = f"container_alerted:{name}"
            unverified_key = f"container_unverified:{name}"
            if e.get("healthy"):
                # The verdict the last run could not wait for: the container came up.
                await _container_note(unverified_key, False,
                                      f"✅ Сервис «{label}» на сервере бота заработал после перезапуска. "
                                      f"Ничего делать не нужно.")
                if e["healthy_for"] >= HEALTHY_RESET:
                    await set_state(f"container_gave_up:{name}", None)
                    if await get_state(key):
                        await set_state(key, None)
                        await notifier.send(f"✅ Сервис «{label}» на сервере бота снова работает. "
                                            f"Ничего делать не нужно.")
                continue
            if e.get("waiting"):
                continue
            if e.get("gave_up"):
                await _container_note(unverified_key, False, None)
                if not await get_state(f"container_gave_up:{name}"):
                    # Rings only if this episode has not rung yet (every restart «helped» for a while).
                    paged = await get_state(key)
                    sent = await notifier.send(
                        f"{'🟠' if paged else '🔴'} Сервис «{label}» на сервере бота {esc(e['problem'])}. "
                        f"Перезапускал его {RESTART_BUDGET} раза — не помогает, больше сам не трогаю: нужен "
                        f"человек.\n\n{_container_steps(name)}",
                        Priority.NORMAL if paged else Priority.CRITICAL)
                    if sent:
                        await set_state(f"container_gave_up:{name}", "1")
                        await set_state(key, "1")
                continue
            if e.get("unverified"):
                # Restarted, but the run's budget ran out while the container was still
                # starting: not «still broken» — «no verdict yet», the next look tells.
                await _container_note(unverified_key, True, (
                    f"🟠 Сервис «{label}» на сервере бота {esc(e['problem'])}. Перезапустил его, но дождаться, "
                    f"заработал ли он, в этот раз не успел — он ещё запускается. Посмотрю через 10 минут "
                    f"и скажу, чем кончилось."))
                continue
            await _container_note(unverified_key, False, None)
            if e["restarted"] and e["ok_after"]:
                await set_state(key, None)
                await notifier.send(f"✅ Сервис «{label}» на сервере бота снова работает — перезапустил его сам "
                                    f"(было: {esc(e['problem'])}). Ничего делать не нужно.")
            elif not await get_state(key):
                expl = humanize.EXPLANATIONS["container"]
                sent = await notifier.send(
                    f"🔴 Сервис «{label}» на сервере бота {esc(e['problem'])}. "
                    + ("Перезапустил, но он всё ещё нездоров." if e["restarted"]
                       else "Перезапустить не удалось.") + f"\n{expl.meaning}\n\n{_container_steps(name)}",
                    Priority.CRITICAL)
                if sent:
                    await set_state(key, "1")


async def _container_note(key: str, on: bool, text: str | None):
    """A state said once: when it starts (text) — and, if a text is given for
    the way back, when it ends."""
    was = await get_state(key)
    if on and not was:
        if await notifier.send(text):
            await set_state(key, "1")
    elif not on and was:
        await set_state(key, None)
        if text:
            await notifier.send(text)


def _container_steps(name: str) -> str:
    """«Что делать» with commands ready to copy: the real name instead of «<имя>»."""
    steps = (re.sub(r"docker (\w+) &lt;имя&gt;",
                    lambda m: f"<code>docker {m.group(1)} {esc(name)}</code>", esc(s))
             for s in humanize.EXPLANATIONS["container"].steps)
    return "Что делать:\n" + "\n".join(f"• {s}" for s in steps)


# ── Escalation ───────────────────────────────────────────────────────────────

async def run_escalation_watch():
    """Re-alert about an unresolved DOWN site every N minutes — bypasses
    mute: a dead site must not be forgettable. Availability only: paging
    every 30 minutes about an expiring cert would train the user to ignore
    alerts. «🔧 Я чиню» snoozes it for maintenance.FIX_MINUTES."""
    repeat_min = config.escalation_repeat_min
    if repeat_min <= 0:     # 0 = off, as .env.example promises; a negative one is not «every 5 minutes»
        return
    if await netcheck.outage_since():
        return              # «лежит уже …» without a network is a guess; it would only pile up in the outbox
    now = datetime.now(UTC)
    for inc in await get_active_incidents():
        with _isolated(f"Escalation of incident #{inc['id']}"):
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
            if await get_state(f"{notifier.HELD}{inc['id']}"):
                continue        # held by a pause or a window: release_held says it first, once
            announced = await notifier.announced_at(inc["id"])
            if announced and (now - announced) < timedelta(minutes=repeat_min):
                continue        # the held alert was just said — no repeat on its heels
            age_min = (now - created).total_seconds() / 60
            api = is_api_check(inc["url"], inc.get("http_method"))
            if (inc.get("message") or "").startswith("Bad content: "):
                # The site opens; its text is wrong — not «лежит», not «поднимется».
                so_far = f"Так уже {fmt_duration(age_min)}"
                back = f"когда {'в ответе' if api else 'на странице'} снова будет как надо"
            else:
                so_far, back = f"Лежит уже {fmt_duration(age_min)}", "как только поднимется"
            sent = await notifier.send(
                f"🔴 {esc(incident_line(inc))}\n{so_far}. Продолжаю проверять "
                f"каждую минуту и напишу, {back}.\n\nЕсли ты уже чинишь — нажми "
                f"«🔧 Я чиню», и я замолчу на {maintenance.fix_length()}.",
                Priority.CRITICAL, site_id=inc["site_id"], always_ring=True,
                reply_markup=alert_actions_keyboard(inc["url"], inc["site_id"], inc["id"],
                                                    hosting=await get_state(f"hosting:{inc['site_id']}"),
                                                    api=api))
            if sent:
                await set_state(f"escalated:{inc['id']}", now.isoformat())


# ── The bot's own downtime ───────────────────────────────────────────────────

DOWNTIME_NOTE_MIN = 10      # a redeploy takes a minute or two; longer is worth a word
LATE_MORNING_UNTIL = 21     # a missed morning report still comes, but not at night


async def run_alive():
    """A mark every minute: after a restart, the gap since the last one is
    how long nobody was watching. The same write probes the database: when
    writes fail, the owner hears it straight from Telegram."""
    try:
        await set_state("alive_at", datetime.now(UTC).isoformat())
    except sqlite3.OperationalError as e:
        health.db_write_failed(e)
    await report_db_health()


def _db_failure_text(error: str) -> str:
    low = error.lower()
    if "full" in low:
        why, fix = ("на диске с базой кончилось место",
                    "освободи место на этом диске (например, старые копии в data/backups) или увеличь его")
    elif "locked" in low:
        why, fix = ("базу держит другая программа",
                    "закрой программу, которая открыла файл базы (sqlite3, второй экземпляр бота), "
                    "или перезапусти бота")
    else:
        why, fix = "база не принимает запись", "посмотри логи бота и место на диске"
    return (f"🔴 Не могу записать в базу: {why}.\n"
            f"Проверки идут и тревоги приходят, но история не сохраняется, а то, что Telegram "
            f"не принял, держу только в памяти до перезапуска.\n\n"
            f"Что сделать: {fix}." + _tech_line(error))


async def report_db_health():
    """«Не могу записать в базу» — past the outbox (it lives in that very
    database), at most once an hour while it lasts; one «✅» after."""
    if health.db_alert_due():
        if await notifier.send_direct(_db_failure_text(health.db_error())):
            health.mark_db_alerted()
    elif health.db_recovery_due() and await notifier.send(
            "✅ База снова записывается: история проверок и очередь сообщений работают. "
            "Ничего делать не нужно."):
        health.mark_db_recovery_said()


# Daily jobs worth running late rather than not at all: a restart (an image
# update, a Dependabot merge — those land at 08:04–08:13 Moscow time) or a
# clock step across their time used to skip them for the day.
CATCH_UP = ("retention", "db_backup", "seo_checks", "index_checks", "card_pictures", "ssl_checks",
            "domain_checks", "visitor_checks", "morning_report", "evening_report", "weekly_report")
# After a restart these run anyway, from their «soon» twins in setup_scheduler.
_SOON_AFTER_START = frozenset({"card_pictures", "visitor_checks"})
CATCH_UP_WITHIN = timedelta(hours=12)
_catch_up_tasks: set[asyncio.Task] = set()


def _last_due(trigger, now: datetime, within: timedelta) -> datetime | None:
    """The latest time the trigger fired (or should have) in (now - within, now]."""
    t = trigger.get_next_fire_time(None, now - within)
    last = None
    while t is not None and t <= now:
        last = t
        t = trigger.get_next_fire_time(t, t + timedelta(seconds=1))
    return last


async def _run_catch_up(job_id: str, fn, due: datetime, **kwargs):
    """The late run itself — unless the job ran since it was planned (its own
    cron fired within misfire_grace_time, a «soon» twin): one run, not two."""
    ran = parse_iso_utc(await get_state(f"last_run:{job_id}"))
    if ran is not None and ran >= due:
        logger.info("Catch-up of %s skipped: it ran at %s", job_id, ran)
        return
    await fn(**kwargs)


async def catch_up_missed(now: datetime, last_alive: datetime | None = None, *,
                          skip: frozenset[str] = frozenset(), min_late: timedelta = timedelta(0)):
    """Run late the daily jobs whose time passed without a run — judged by
    when each one last finished (last_run:<job>), not by how long the bot
    was away. A job never marked yet (the first start of this version) is
    judged by when the bot was last alive; the very first start catches up
    nothing. min_late: a run due less than this ago is APScheduler's own to
    make up (misfire_grace_time) — not planned twice."""
    if _scheduler is None:
        return
    specs = _cron_specs()
    for i, job_id in enumerate(CATCH_UP):
        if job_id in skip:
            continue
        fn = getattr(_scheduler.get_job(job_id), "func", None)
        due = _last_due(_cron(**specs[job_id]), now, CATCH_UP_WITHIN)
        ran = parse_iso_utc(await get_state(f"last_run:{job_id}")) or last_alive
        if fn is None or due is None or ran is None or ran >= due or now - due < min_late:
            continue
        if job_id == "morning_report" and to_local(now).hour >= LATE_MORNING_UNTIL:
            continue                                   # a missed morning report still comes, but not at night
        late = job_id == "morning_report" and now - due > timedelta(minutes=DOWNTIME_NOTE_MIN)
        logger.info("Catching up %s: due at %s, last ran %s", job_id, due, ran)
        _scheduler.add_job(_run_catch_up, DateTrigger(run_date=now + timedelta(seconds=60 + 30 * i), timezone=UTC),
                           args=[job_id, fn, due], kwargs={"late": True} if late else {},
                           id=f"catchup:{job_id}", replace_existing=True, misfire_grace_time=600)


def clock_stepped(seconds: float):
    """The wall clock stepped (NTP right after a boot, a resumed VM, a hand
    edit). Interval jobs count their next run on the wall clock: after a step
    back every one of them would wait out the whole step — move them back by
    it. After a step forward the daily jobs whose time was jumped over run
    late; what fell inside misfire_grace_time APScheduler makes up itself."""
    if _scheduler is None:
        return
    now = datetime.now(UTC)
    if seconds < 0:
        for job in _scheduler.get_jobs():
            if isinstance(job.trigger, IntervalTrigger) and job.next_run_time:
                job.modify(next_run_time=max(now, job.next_run_time + timedelta(seconds=seconds)))
        _next_avail_check.clear()       # per-site due times are on the wall clock too
        return
    task = asyncio.get_running_loop().create_task(
        catch_up_missed(now, now - timedelta(seconds=seconds), min_late=timedelta(seconds=600)))
    _catch_up_tasks.add(task)
    task.add_done_callback(_catch_up_tasks.discard)


async def catch_up_after_restart():
    """Once at startup. While the bot was gone its silence did not mean
    «всё в порядке» — a pause longer than a redeploy is said out loud, and
    the daily jobs that fell into it run late rather than not at all."""
    if _failed_jobs:
        await notifier.send(f"⚠️ Не смог запустить по расписанию: {esc(', '.join(_failed_jobs))}. "
                            "Остальное работает. Подробности — в логе бота.")
    last = parse_iso_utc(await get_state("alive_at"))
    now = datetime.now(UTC)
    # The gap is taken out of the tasks' silence: the bot heard nothing in it.
    await heartbeats.note_downtime(last, now)
    await run_alive()
    await catch_up_missed(now, last, skip=_SOON_AFTER_START)
    if not last or now - last < timedelta(minutes=DOWNTIME_NOTE_MIN) or _scheduler is None:
        return
    # After the scheduler's first look at the sites (the tick comes at +60 s),
    # so the line repeats the monitor's verdict instead of guessing.
    _scheduler.add_job(_report_downtime, DateTrigger(run_date=now + timedelta(seconds=90), timezone=UTC),
                       args=[last, now], id="downtime_note", replace_existing=True)


async def _report_downtime(start: datetime, end: datetime):
    minutes = (end - start).total_seconds() / 60
    fmt = "%H:%M" if minutes < 20 * 60 else "%d.%m %H:%M"
    lines = [f"⏸ Меня не было {fmt_duration(minutes)} ({to_local(start).strftime(fmt)}–"
             f"{to_local(end).strftime(fmt)}): всё это время я не следил за сайтами и не смог бы "
             f"ничего сообщить. Сейчас снова на посту."]
    sites = await get_all_sites()
    if sites:
        # The monitor's own verdict (threshold, second opinion), not one more
        # probe of a network that may not be up yet after a reboot.
        down_ids = {i["site_id"] for i in await get_active_incidents() if i["check_type"] == "availability"}
        down = [esc(site_label(s["url"])) for s in sites if s["id"] in down_ids]
        unsure = [esc(site_label(s["url"])) for s in sites if s["id"] not in down_ids
                  and ((await get_last_check(s["id"], "availability")) or {}).get("status") not in (None, "ok")]
        if down:
            lines.append(f"🔴 Сейчас недоступно: {', '.join(down)}.")
        if unsure:
            lines.append(f"⚠️ С первой попытки не открылись: {', '.join(unsure)} — перепроверяю, "
                         f"напишу, если не поднимутся.")
        if not down and not unsure:
            lines.append(f"✅ Сейчас всё работает: {len(sites)} из {len(sites)}.")
    await notifier.send("\n".join(lines), Priority.NORMAL)


# ── Self-maintenance ─────────────────────────────────────────────────────────

async def run_self_heartbeat():
    """Mark in with the external watchdog (healthchecks.io etc.) and keep
    its answer: the morning report and the main screen say whether it is
    on watch. Half an hour of refused marks is said once — a watchdog that
    does not take them would stay silent when the bot dies."""
    url = integrations.self_heartbeat_url()
    if not url:
        return
    problems = health.problems()
    if problems:
        # While the bot cannot do its job the watchdog must see it as down:
        # no mark this time — it raises the alarm on its own schedule.
        logger.error("Not marking in with the watchdog: %s", ", ".join(problems))
        return
    rec = await heartbeats.record_watchdog(await heartbeats.ping_watchdog(url))
    name = esc(heartbeats.watchdog_name(url))
    failing = heartbeats.failing_minutes(rec)
    alerted = await get_state("watchdog_alerted")
    if failing >= heartbeats.WATCHDOG_ALERT_MIN and not alerted:
        sent = await notifier.send(
            f"🟠 Сторож {name} не на связи уже {fmt_duration(failing)}: {esc(rec.get('error'))}. "
            f"Пока так, если я упаду, тебе никто не напишет.\n\n"
            f"Что делать: открой {name} и проверь, что check на месте. Удалил его или ссылка поменялась — "
            f"вставь новую: ⚙️ Настройки → 🔌 Подключения → 🛡 Внешний сторож.")
        if sent:
            await set_state("watchdog_alerted", "1")
    elif not failing and alerted:
        await set_state("watchdog_alerted", None)
        await notifier.send(f"✅ Сторож {name} снова на связи. Ничего делать не нужно.")


@_once("retention", mark=True)
async def run_retention():
    # No try/except: a failure reaches the job listener (one message to the
    # owner) and the log keeps its traceback.
    n = await rollup_old_checks(config.retention_days)
    if n:
        logger.info("Retention: rolled up %d old check rows", n)


def _backups_dir() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "backups")


def _backup_files() -> list[str]:
    d = _backups_dir()
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.startswith("bot-") and f.endswith(".db"))


def backup_info() -> tuple[str, datetime] | None:
    """(path, when) of the freshest local backup, or None before the first."""
    files = _backup_files()
    if not files:
        return None
    path = os.path.join(_backups_dir(), files[-1])
    return path, datetime.fromtimestamp(os.path.getmtime(path), UTC)


@_once("db_backup", mark=True)
async def run_db_backup() -> bool:
    """Nightly local DB backup with rotation. True when the copy was written."""
    try:
        os.makedirs(_backups_dir(), mode=0o700, exist_ok=True)
        path = os.path.join(_backups_dir(), f"bot-{datetime.now(UTC):%Y%m%d}.db")
        await backup_db(path)
        for stale in _backup_files()[:-max(1, config.db_backup_keep)]:
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(_backups_dir(), stale))
        logger.info("DB backup written: %s", path)
        return True
    except Exception as e:
        logger.exception("DB backup failed")
        await notifier.send("🟠 Не удалось сохранить резервную копию базы бота. Проверь место на диске."
                            + _tech_line(str(e)))
        return False


# What the Bot API lets a bot send, and what it lets a bot download (a copy
# sent back to restore from) — taken as decimal megabytes, to be safe.
# Source: the aiogram 3 docstrings, which quote the Bot API — «Bots can
# currently send files of any type of up to 50 MB in size»
# (aiogram/methods/send_document.py) and «bots can download files of up to
# 20MB in size» (aiogram/methods/get_file.py).
TG_SEND_LIMIT = 50_000_000
TG_DOWNLOAD_LIMIT = 20_000_000
_share_lock = asyncio.Lock()


async def send_copy_to_owner(caption: str) -> str | None:
    """A fresh copy without tokens and secrets (db.database.shareable_copy)
    for the owner's private chat. None when sent; otherwise why not.
    Its own file: the nightly copies stay as they were, and the rotation
    and the screens (`_backup_files`, bot-*.db) never see it."""
    async with _share_lock:
        os.makedirs(_backups_dir(), mode=0o700, exist_ok=True)
        path = os.path.join(_backups_dir(), f"share-{datetime.now(UTC):%Y%m%d-%H%M%S}.db")
        try:
            await shareable_copy(path)
            size = os.path.getsize(path)
            if size > TG_SEND_LIMIT:
                return (f"копия весит {size / 1e6:.0f} МБ, а Telegram принимает от бота файлы до 50 МБ. "
                        f"Полные ночные копии лежат на сервере: {_backups_dir()}")
            if size > TG_DOWNLOAD_LIMIT:
                caption += (f"\n\nФайл {size / 1e6:.0f} МБ — больше 20 МБ, которые я могу скачать: прислать "
                            f"его мне обратно не получится. Восстановить можно на сервере: останови бота и "
                            f"положи файл на место bot.db.")
            return await notifier.send_document_to_owner(FSInputFile(path), caption)
        except Exception as e:
            logger.exception("Copy for the owner failed")
            return f"не получилось сделать копию ({type(e).__name__}) — чаще всего кончилось место на диске"
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)


async def send_db_backup_to_telegram():
    """Weekly: a copy of the database for the owner's private chat — the
    off-host copy with zero extra infrastructure. Without tokens and
    secrets, never into a group; it can be switched off in «🔔 Когда писать»."""
    if not settings.weekly_backup():
        return
    problem = await send_copy_to_owner(
        "🗄 Еженедельная копия базы бота: сайты, настройки, история. Ключей подключений и секретов в ней "
        "нет — после восстановления на новом сервере подключения нужно будет ввести заново. Чтобы "
        "восстановить — пришли мне этот файл.")
    if problem:
        await notifier.send(f"🟠 Еженедельную копию базы не прислал: {esc(problem)}.\n"
                            f"Ночные копии на сервере целы. Попробовать ещё раз — 🏠 Главная → «🤖 Боты» → "
                            f"«💾 Скачать копию базы».")


# ── Reports ──────────────────────────────────────────────────────────────────

async def _morning_extras(ssl_results, domain_results, incidents: list[dict]) -> list[str]:
    extras: list[str] = []
    sites = await get_all_sites()
    # One phrase for a clean week; a list only of the sites that went down.
    down, clean = [], 0
    for s in sites:
        stats = await get_uptime_over_days(s["id"], 7)
        if not stats["total_checks"]:
            continue
        if stats["ok_checks"] >= stats["total_checks"]:
            clean += 1
            continue
        interval = s.get("check_interval_min") or config.check_interval_minutes
        down.append(f"• {esc(site_label(s['url']))} — "
                    f"{humanize.downtime(stats['total_checks'], stats['ok_checks'], interval)}")
    if down:
        extras.append("📈 За неделю:\n" + "\n".join(down) + ("\n• остальные — без сбоев" if clean else ""))
    elif clean:
        extras.append("📈 За неделю: " + ("все сайты без сбоев" if clean > 1 else "без сбоев"))

    # A certificate within 14 days and a domain within 30 are already on the
    # site line above; this line is only the early heads-up for domains.
    expiring = [f"домен {esc(r.domain)} через {r.domain_info.days_left} дн."
                for r in domain_results
                if r.domain_info and r.domain_info.days_left is not None
                and 30 < r.domain_info.days_left <= 60]
    if expiring:
        extras.append("🔜 Истекает: " + "; ".join(expiring))

    # Search findings are open incidents and already listed above. This line
    # says only what that list cannot: all clear, or which audits failed.
    # API checks are not audited — a stale audit from before must not speak.
    now = datetime.now(UTC)
    pages = [s for s in sites if is_web_page(s["url"], s.get("http_method"))]
    first_audit = [s for s in pages if (added := parse_sqlite_utc(s.get("added_at")))
                   and timedelta(hours=SEO_GRACE_HOURS) <= now - added < timedelta(hours=SEO_GRACE_HOURS + 24)]
    listed = {i["site_id"] for i in incidents if i["check_type"] == "seo"} | {s["id"] for s in first_audit}
    seo_chips, audited, all_clear = [], False, True
    for s in pages:
        last = await get_last_check(s["id"], "seo")
        if not last:
            continue
        audited = True
        if last["status"] == "ok":
            continue
        all_clear = False
        if s["id"] not in listed:
            seo_chips.append(f"{esc(short_host(s['url']))} {_seo_chip(last['status'], await sitestatus.get(s['id']))}")
    if seo_chips:
        extras.append("🔎 Поиск и ИИ: " + " · ".join(seo_chips))
    elif audited and all_clear:
        extras.append("🔎 Поиск и ИИ: помех нет")
    for s in first_audit:
        last = await get_last_check(s["id"], "seo")
        if last:
            n = ((await sitestatus.get(s["id"])).get("seo") or {}).get("improve") or 0
            verdict = ("помех нет" if last["status"] == "ok" else
                       f"🟠 {n} {plural(n, 'помеха', 'помехи', 'помех')}" if last["status"] == "warning" else
                       "🔴 сайт закрыт от поиска" if last["status"] == "critical" else "⚠️ не смог проверить")
            extras.append(f"🔎 Первый аудит {esc(site_label(s['url']))} для поиска и ИИ: {verdict} — "
                          f"подробности в «🔎 Поиск и ИИ»")

    try:
        disk = await check_disk(auto_cleanup=False)
        icon = "✅" if not disk["over_threshold"] else humanize.level_icon("critical" if disk["pct"] >= DISK_CRITICAL_PCT else "warning")
        extras.append(f"💾 Диск сервера: {icon} занято {disk['pct']}%")
    except Exception as e:
        logger.warning("Disk stat for morning report failed: %s", e)
    return extras


def _seo_chip(status: str, snap: dict) -> str:
    """'🔴' · '🟠 3' · '⚠️' for the morning line."""
    if status == "critical":
        return "🔴"
    if status == "warning":
        return f"🟠 {(snap.get('seo') or {}).get('improve') or ''}".rstrip()
    return "⚠️"


async def _status_snapshot(report_type: str, extras: bool):
    """manage=False: a report is a read-only observer — it must never
    consume incident transitions that belong to the scheduled monitors."""
    urls = await get_active_site_urls()
    host_urls = await get_active_host_urls()
    availability = await check_all(urls, manage=False)
    ssl_results = await check_all_ssl(host_urls, manage=False)
    domain_results = await check_all_domains(host_urls, manage=False)
    incidents = await get_active_incidents()
    return format_compact_status_report(
        availability=availability, incidents=incidents,
        ssl_results=ssl_results, domain_results=domain_results, report_type=report_type,
        extras=await _morning_extras(ssl_results, domain_results, incidents) if extras else None,
        watch=await _watch_lines(morning=extras))


async def _watch_lines(morning: bool) -> list[tuple[str, bool]]:
    """Bots and tasks under the sites, a line each like a site's, and last
    the watchdog — the answer to «and if the bot itself goes down?». A
    silent task gets its «🔗 Подключить / 🗑 Удалить» buttons under the
    digest; a missing watchdog is mentioned in the morning only."""
    jobs = await heartbeats.job_states()
    lines: list[tuple[str, bool]] = []
    for j in jobs:   # what bots report comes first, a block of its own
        status = await heartbeats.job_status(j.name)
        if status:
            lines += [(f"{esc(heartbeats.status_title(j, status))}:", True),
                      *((f"{icon} {esc(name)} — {esc(text)}", icon == "✅")
                        for icon, name, text in heartbeats.status_items(j, status)),
                      ("", True)]
    lines += [heartbeats.digest_line(j) for j in jobs]
    w = await heartbeats.watchdog()
    if w or morning:
        lines.append(heartbeats.watchdog_digest_line(w))
    return lines


@_once("morning_report", mark=True)
async def send_morning_report(late: bool = False):
    # The morning report IS the daily digest — it must arrive even when its
    # hour falls inside quiet hours (still deferred by an explicit mute).
    text = clip(await _status_snapshot("morning_late" if late else "morning", extras=True))
    silent_jobs = [j.name for j in await heartbeats.job_states() if j.needs_you]
    await notifier.send(text, Priority.DIGEST, reply_markup=heartbeat_keyboard(silent_jobs))


@_once("evening_report", mark=True)
async def send_evening_report():
    """Evening report — only if there's something to worry about."""
    if settings.evening_hour() is None or not await get_active_incidents():
        return
    await notifier.send(clip(await _status_snapshot("evening", extras=False)), Priority.DIGEST)


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


@_once("weekly_report", mark=True)
async def send_weekly_report():
    """Sunday: dependency check, GitHub hygiene, weekly digest, ASCII
    response-time chart, off-host DB copy. A failed side check never
    stops the digest."""
    try:
        await updates.run_dependency_watch()
    except Exception:
        logger.exception("Dependency watch failed")
    try:
        await github.run_watch()
    except Exception:
        logger.exception("GitHub hygiene check failed")
    failed = None
    try:
        text, chart_blocks = await build_weekly_report()
        # Split, not clipped: the sections at the end (dependencies, GitHub)
        # are the ones a long site list would cut off.
        for part in chunks(text):
            await notifier.send(part, Priority.DIGEST)
        if chart_blocks:
            for msg in pack_blocks(["⏱ Как быстро открывались сайты по дням:", *chart_blocks]):
                await notifier.send(msg, Priority.DIGEST)
        tip = await recommend.weekly_recommendation()
        if tip:
            await notifier.send(tip[0], Priority.DIGEST, reply_markup=tip[1])
    except Exception as e:
        logger.exception("Weekly report failed")
        failed = e
    try:
        await send_db_backup_to_telegram()
    except Exception:
        logger.exception("Weekly copy of the database failed")
    if failed:
        raise failed            # the off-host copy went out; now the job listener tells the owner


# ── The scheduler's own account of every run ────────────────────────────────

# Jobs that run every minute: one failure is a blip, three in a row is broken.
_EVERY_MINUTE = frozenset({"availability_checks", "alive", "outbox_flush", "missed_alerts", "held_release"})
_JOB_TITLES = {"availability_checks": "проверка сайтов", "morning_report": "утренняя сводка",
               "weekly_report": "недельный отчёт", "retention": "ночная очистка базы",
               "db_backup": "ночная копия базы", "outbox_flush": "досылка сообщений"}
_job_fails: dict[str, int] = {}
_job_told: set[str] = set()
_listener_tasks: set[asyncio.Task] = set()


def _on_job_event(event):
    """APScheduler's own account of every run. A job that keeps failing, or
    keeps being skipped because its last run still hangs, is told to the
    owner once per streak — the container log alone is read by nobody."""
    job_id = event.job_id
    if ":" in job_id:                  # one-off follow-ups (followup:…): their failure is in the log
        return
    if event.code == EVENT_JOB_EXECUTED:
        _job_fails.pop(job_id, None)
        _job_told.discard(job_id)
        return
    n = _job_fails[job_id] = _job_fails.get(job_id, 0) + 1
    if job_id in _job_told or n < (3 if job_id in _EVERY_MINUTE else 1):
        return
    _job_told.add(job_id)
    exc = getattr(event, "exception", None)
    why = type(exc).__name__ if exc else "прошлый запуск ещё не закончился"
    task = asyncio.get_running_loop().create_task(_tell_job_broken(_JOB_TITLES.get(job_id, job_id), why))
    _listener_tasks.add(task)
    task.add_done_callback(_listener_tasks.discard)


async def _tell_job_broken(title: str, why: str):
    try:
        await notifier.send(f"⚠️ У меня не работает «{esc(title)}»: {esc(why)}. Остальное идёт как обычно; "
                            f"подробности — в логе бота.")
    except Exception:
        logger.exception("Could not tell the owner about the broken job %s", title)


# ── Setup ────────────────────────────────────────────────────────────────────

def _cron(**kwargs) -> CronTrigger:
    return CronTrigger(timezone=local_tz(), **kwargs)


def _hour(value, default: int) -> int:
    """A broken hour (an old .env, a restored DB) falls back to the default
    instead of stopping the whole scheduler at every start."""
    h = validate.hour(value)
    return default if h is None else h


def _before_morning(minutes: int) -> dict:
    """The daily audits run so many minutes before the morning report, so the
    digest shows today's results whatever hour the owner picked (at 9:00 —
    the old fixed 07:30–08:10)."""
    t = (_hour(settings.morning_hour(), 9) * 60 - minutes) % (24 * 60)
    return dict(hour=t // 60, minute=t % 60)


def audit_time(job_id: str) -> str:
    """«08:10» — when a daily audit runs today, for the texts that name it."""
    spec = _cron_specs()[job_id]
    return f"{spec['hour']:02d}:{spec['minute']:02d}"


def _cron_specs() -> dict[str, dict]:
    """Every cron-based job with its local-time schedule, resolved now —
    so a changed report hour or timezone can re-create all triggers."""
    evening = settings.evening_hour()
    return {
        "ssl_checks": _before_morning(60),
        "domain_checks": _before_morning(55),
        "seo_checks": _before_morning(90),
        "index_checks": _before_morning(75),
        "card_pictures": _before_morning(70),
        "dns_checks": dict(minute=20),
        "mail_checks": dict(minute=25),
        "security_checks": dict(hour="*/6", minute=35),
        "visitor_checks": _before_morning(50),
        "region_checks": dict(minute=17),
        "deep_checks": dict(minute=40),
        "retention": dict(hour=3, minute=30),
        "db_backup": dict(hour=3, minute=45),
        "morning_report": dict(hour=_hour(settings.morning_hour(), 9), minute=0),
        "evening_report": dict(hour=_hour(evening if evening is not None else config.evening_report_hour, 21),
                               minute=0),
        "weekly_report": dict(day_of_week="sun", hour=_hour(settings.weekly_hour(), 11), minute=0),
    }


def pause_jobs():
    """Hold every job while the database file is swapped (handlers/sites.py):
    a job started in that window would write into a closed connection."""
    if _scheduler is not None and _scheduler.running:
        _scheduler.pause()


def resume_jobs():
    if _scheduler is not None and _scheduler.running:
        _scheduler.resume()


# Jobs setup_scheduler could not register: named to the owner once the bot
# is up (catch_up_after_restart). Every other job runs.
_failed_jobs: list[str] = []


def reschedule_all() -> list[str]:
    """Apply UI-changed hours or timezone to the running scheduler. A job
    whose new trigger cannot be built keeps its old schedule and is
    returned by id, so the caller can say that the hours did not apply."""
    failed: list[str] = []
    if _scheduler is None:
        return failed
    for job_id, spec in _cron_specs().items():
        if not _scheduler.get_job(job_id):
            continue
        try:
            _scheduler.reschedule_job(job_id, trigger=_cron(**spec))
        except Exception:
            logger.exception("Could not reschedule %s — its old schedule stays", job_id)
            failed.append(job_id)
    return failed


def setup_scheduler() -> AsyncIOScheduler:
    global _scheduler
    # APScheduler's default grace is 1 s: an event loop busy parsing a page at
    # 09:00:00 would silently skip that day's morning report.
    scheduler = AsyncIOScheduler(timezone=local_tz(), job_defaults={"misfire_grace_time": 600})
    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED | EVENT_JOB_MAX_INSTANCES)
    _scheduler = scheduler
    _failed_jobs.clear()

    def job(fn, make_trigger, job_id, **kwargs):
        """One broken schedule costs only its own job: logged, named to the
        owner after the start (catch_up_after_restart), the rest registered.
        The trigger is built here, inside the try — a bad hour or an absurd
        interval raises while the trigger is made, not in add_job."""
        try:
            scheduler.add_job(fn, trigger=make_trigger(), id=job_id, replace_existing=True,
                              max_instances=1, **kwargs)
        except Exception:
            logger.exception("Could not schedule %s — the other jobs still run", job_id)
            _failed_jobs.append(job_id)

    soon = datetime.now(UTC) + timedelta(minutes=2)
    specs = _cron_specs()
    # Ticks every minute; the job decides which sites are due.
    job(run_availability_checks, lambda: IntervalTrigger(minutes=1, timezone=UTC), "availability_checks",
        misfire_grace_time=60)
    job(check_own_network_at_start, lambda: DateTrigger(run_date=soon, timezone=UTC), "net_canaries_soon")
    # SSL + domain once a day — the alert ladder prevents repeat noise.
    job(run_ssl_checks, lambda: _cron(**specs["ssl_checks"]), "ssl_checks")
    job(run_domain_checks, lambda: _cron(**specs["domain_checks"]), "domain_checks")
    # SEO/GEO audit before the morning report, so the digest shows fresh results.
    job(run_seo_checks, lambda: _cron(**specs["seo_checks"]), "seo_checks", misfire_grace_time=600)
    job(run_index_checks, lambda: _cron(**specs["index_checks"]), "index_checks", misfire_grace_time=600)
    job(run_card_pictures, lambda: _cron(**specs["card_pictures"]), "card_pictures", misfire_grace_time=3600)
    job(run_card_pictures, lambda: DateTrigger(run_date=soon, timezone=UTC), "card_pictures_soon")
    # Links: first run shortly after boot, then every N hours; 0 = never.
    if config.links_check_interval_hours:
        job(run_links_checks,
            lambda: IntervalTrigger(hours=config.links_check_interval_hours, start_date=soon, timezone=UTC),
            "links_checks", misfire_grace_time=300)
    # DNS + deep 5xx probe hourly (offset so they don't pile up).
    job(run_dns_checks, lambda: _cron(**specs["dns_checks"]), "dns_checks", misfire_grace_time=300)
    # Mail hourly, and soon after a start — so the cards show it right away.
    job(run_mail_checks, lambda: _cron(**specs["mail_checks"]), "mail_checks", misfire_grace_time=600)
    job(run_mail_checks, lambda: DateTrigger(run_date=soon, timezone=UTC), "mail_checks_soon")
    job(run_security_checks, lambda: _cron(**specs["security_checks"]), "security_checks", misfire_grace_time=600)
    job(run_security_checks, lambda: DateTrigger(run_date=soon + timedelta(minutes=1), timezone=UTC),
        "security_checks_soon")
    job(run_visitor_checks, lambda: _cron(**specs["visitor_checks"]), "visitor_checks", misfire_grace_time=3600)
    job(run_visitor_checks, lambda: DateTrigger(run_date=soon + timedelta(minutes=2), timezone=UTC),
        "visitor_checks_soon")
    job(run_region_checks, lambda: _cron(**specs["region_checks"]), "region_checks", misfire_grace_time=600)
    job(run_deep_checks, lambda: _cron(**specs["deep_checks"]), "deep_checks", misfire_grace_time=300)
    # Watchers.
    job(run_heartbeat_watch, lambda: IntervalTrigger(minutes=10, timezone=UTC), "heartbeat_watch")
    job(run_host_checks, lambda: IntervalTrigger(minutes=30, timezone=UTC), "host_checks")
    job(run_container_watch, lambda: IntervalTrigger(minutes=10, timezone=UTC), "container_watch")
    job(run_escalation_watch, lambda: IntervalTrigger(minutes=5, timezone=UTC), "escalation_watch")
    job(run_self_heartbeat, lambda: IntervalTrigger(minutes=5, timezone=UTC), "self_heartbeat")
    job(runtime.warn_if_seat_free, lambda: IntervalTrigger(minutes=30, timezone=UTC), "seat_free_warning")
    job(run_alive, lambda: IntervalTrigger(minutes=1, timezone=UTC), "alive", misfire_grace_time=60)
    job(notifier.flush_outbox, lambda: IntervalTrigger(minutes=1, timezone=UTC), "outbox_flush",
        misfire_grace_time=60)
    job(notifier.release_held, lambda: IntervalTrigger(minutes=1, timezone=UTC), "held_release",
        misfire_grace_time=60)
    job(deliver_missed_alerts, lambda: IntervalTrigger(minutes=1, timezone=UTC), "missed_alerts",
        misfire_grace_time=60)
    job(updates.run_deploy_watch, lambda: IntervalTrigger(hours=1, start_date=soon, timezone=UTC), "deploy_watch")
    job(notifier.flush_queue, lambda: IntervalTrigger(minutes=10, timezone=UTC), "quiet_flush")
    if config.on_railway:
        # One reminder around day 25 of Railway's 30-day trial (services.railway).
        job(railway.remind_if_due, lambda: IntervalTrigger(hours=6, start_date=soon, timezone=UTC),
            "railway_trial")
    # Nightly maintenance.
    job(run_retention, lambda: _cron(**specs["retention"]), "retention")
    job(run_db_backup, lambda: _cron(**specs["db_backup"]), "db_backup")
    # Reports.
    job(send_morning_report, lambda: _cron(**specs["morning_report"]), "morning_report")
    job(send_evening_report, lambda: _cron(**specs["evening_report"]), "evening_report")
    job(send_weekly_report, lambda: _cron(**specs["weekly_report"]), "weekly_report", misfire_grace_time=3600)
    return scheduler
