"""APScheduler jobs: monitors, watchers, reports and self-maintenance.
Every outgoing message goes through services.notifier, which owns the
mute / quiet-hours / pause rules."""

import asyncio
import contextlib
import copy
import functools
import json
import logging
import os
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta

from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
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
    get_incident,
    get_last_check,
    get_recent_check_statuses,
    get_site,
    get_state,
    get_unalerted_incidents,
    get_uptime_over_days,
    mark_incident_alerted,
    purge_old_rows,
    resolve_incident,
    rollup_old_checks,
    save_incident,
    set_dns_state,
    set_state,
    shareable_copy,
)
from monitors import netcheck, region
from monitors.availability import CONSECUTIVE_FAILURE_THRESHOLD, check_all, check_availability
from monitors.base import CheckResult
from monitors.deep_checker import check_all_deep
from monitors.dns_checker import check_all_dns
from monitors.domain_checker import check_all_domains, domain_groups
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
    incident_duration_line,
    incident_line,
)
from reports.weekly import build_weekly_report
from services import (
    automation,
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
    group_keyboard,
    heartbeat_keyboard,
    seo_fix_keyboard,
)
from services.notifier import Priority
from utils import validate
from utils.clock import local_tz, now_local, to_local
from utils.net import is_private_target
from utils.text import (
    SAFE_LIMIT,
    chunks,
    clip,
    esc,
    fmt_duration,
    incident_start,
    parse_iso_utc,
    parse_sqlite_utc,
    plural,
)
from utils.urls import (
    host_of,
    is_api_check,
    is_http_url,
    is_web_page,
    registrable_domain,
    short_host,
    site_label,
)
from web.status_page import public_label
from web.webhook import send_feedback_summary

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

SLOW_RESPONSE_STREAK = 3
_slow_streak: dict[int, int] = {}
# «Медленно» closes after SLOW_RESPONSE_STREAK clearly fast answers (below
# this share of the threshold); an answer in between resets both streaks —
# a site around the threshold must not open three incidents an hour.
FAST_AGAIN_SHARE = 0.8
_fast_streak: dict[int, int] = {}
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


def forget_slow_streak(site_id: int):
    """A «медленно» finding closed by hand: count slow answers anew, so a site
    that is still slow is reported again after SLOW_RESPONSE_STREAK checks
    (the streak is compared with ==, it would never come back otherwise)."""
    _slow_streak.pop(site_id, None)
    fast = globals().get("_fast_streak")     # the fast-answer counter, where the module keeps one
    if isinstance(fast, dict):
        fast.pop(site_id, None)


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
            _next_avail_check[s["id"]] = now + _spread_interval(s)
            due.append(s["url"])
    results = await check_all(due, sites=by_url) if due else []
    # The tick only checks; every message goes to the alert worker, so a
    # silent Telegram or a slow deploy hook never holds up the next check.
    # The sites that fell in this tick go to the worker together, and so
    # do the ones that came back: several at once are ONE message
    # (_alert_down, _alert_up), one site is the usual alert.
    kinds = [(r, _tick_kind(r)) for r in results]
    down = [r for r, kind in kinds if kind == "down"]
    if down:
        _enqueue(_alert_down, down, by_url, incident_ids=[r.incident_id for r in down])
    up = [r for r, kind in kinds if kind == "up"]
    if up:
        _enqueue(_alert_up, up)
    for r, kind in kinds:
        site = by_url.get(r.url, {})
        if kind == "flap":
            _enqueue(_note_flapping, r)
        elif kind == "ext":
            _enqueue(_note_local_network_problem, r.url, r.site_id, r.external_ups, r.external_total)
        elif kind == "cannot":
            _enqueue(_note_cannot_check, r.url, r.site_id, r.error)
        if r.ok:
            _cannot_check_noted.discard(r.site_id)
            _enqueue(_site_is_up, r, _slow_ms(site))
    if back_since:
        _enqueue(_report_own_outage, back_since, now, results)


# Sites of one interval used to be checked in the same tick from the first
# tick after a start on: a shared hosting going down was one burst of probes
# and one burst of alerts. The next check after the first one is moved once
# by a fixed share of the interval (never into the tick's 30 s tolerance,
# never below a minute), then every site keeps its own step.
SPREAD_STEP_SEC = 37


def _spread_interval(site: dict) -> timedelta:
    """How long after this tick the site is due again: the full interval,
    minus a share of it that is this site's own — on the first check after
    a start or a reset only (reset_site_schedule, a network that came back)."""
    interval = _interval_min(site)
    if site["id"] in _next_avail_check or interval < 2:
        return timedelta(minutes=interval)
    spread = (site["id"] * SPREAD_STEP_SEC) % ((interval - 1) * 60)
    return timedelta(minutes=interval) - timedelta(seconds=spread)


def _tick_kind(r) -> str | None:
    """What a tick's result is worth saying — the first that applies, in
    the order the alerts were always chosen; None — nothing to say."""
    if r.incident_new:
        return "down"
    if r.flapping:
        return "flap"
    if r.external_ok is True:
        return "ext"
    if r.transient and not r.own_outage:
        return "cannot"
    if r.recovered:
        return "up"
    return None


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


def _enqueue(fn, *args, incident_id: int | None = None, incident_ids: list[int] | None = None):
    """Run fn(*args) in the alert worker: one at a time, in order, never
    inside the tick. incident_id / incident_ids — the incidents the item
    alerts on: deliver_missed_alerts leaves them to it while it waits."""
    global _queue, _worker
    loop = asyncio.get_running_loop()
    if _worker is None or _worker.done() or _worker.get_loop() is not loop:
        _queue = asyncio.Queue()
        _worker = loop.create_task(_work(_queue), name="alert-worker")
    ids = [i for i in [incident_id, *(incident_ids or [])] if i is not None]
    _in_flight.update(ids)
    _queue.put_nowait((fn, args, ids))


async def _work(queue: asyncio.Queue):
    while True:
        fn, args, ids = await queue.get()
        try:
            await fn(*args)
        except Exception:
            logger.exception("Alert worker: %s failed", fn.__name__)
        finally:
            _in_flight.difference_update(ids)
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
                      *, always_ring: bool = False, to_helpers: bool = False,
                      helper_text: str | None = None) -> Message | bool | None:
    """Send an incident's alert and record that it went out. None — the site
    is paused or inside a maintenance window: the full alert is held by the
    notifier (held:<id>) and said once the pause is over; the incident stays
    owed until then. to_helpers is the caller's call and only «сайт лёг»
    makes it: security, SSL, NS and the rest never reach a helper."""
    if incident_id is not None:
        _in_flight.add(incident_id)
    try:
        if incident_id is not None:
            await _record_alert(incident_id, False)
        paused = await maintenance.is_paused(site_id)
        sent = await notifier.send(text, priority, site_id=site_id, reply_markup=kb, always_ring=always_ring,
                                   to_helpers=to_helpers, helper_text=helper_text, announces=incident_id)
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


def _missed_alert_text(inc: dict, label: str | None = None) -> str:
    since = parse_sqlite_utc(inc.get("created_at"))
    when = f" с {to_local(since).strftime('%d.%m %H:%M')}" if since else ""
    return (f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc, label))}\n"
            f"⏳ Тревога об этом не ушла вовремя — проблема открыта{when}. Проверки продолжаются.")


def helper_label(url: str, method: str | None = None, *, api: bool = False) -> str:
    """How a helper's copy names a site: what the status page would show
    (the host, «· API» for an API check — never the path, a token may live
    there); a tcp/ping monitor or an internal address is not named at all —
    a helper is an outsider, the owner's network map is not for him. api —
    the check is known to be an API one (AvailabilityResult.api) when the
    method is not at hand."""
    label = public_label(url, method)
    if label is None:
        return "Один из сайтов" if is_http_url(url) else "Один из серверов"
    return f"{label} · API" if api and not label.endswith(" · API") else label


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
            helper_text = (_missed_alert_text(inc, helper_label(inc["url"], inc.get("http_method")))
                           if availability else None)
            sent = await _alert_once(inc["id"], inc["site_id"], text, notifier.priority_for(inc["severity"]), kb,
                                     always_ring=availability, to_helpers=availability, helper_text=helper_text)
            if availability:
                await _remember_alert(inc["id"], sent, text)
            if availability and sent is not None:
                _after_availability_alert(inc["url"], inc["site_id"], inc["id"],
                                          getattr(sent, "message_id", None), text, kb)
        except Exception:
            logger.exception("Re-sending the alert of incident %s failed", inc["id"])


async def _send_availability_alert(r, site: dict):
    # Since when: the first failed check of the series (the incident's
    # started_at). No row while the database refuses writes — no «с ЧЧ:ММ»;
    # the alert itself must never wait for that detail.
    since = None
    if r.incident_id:
        try:
            since = incident_start(await get_incident(r.incident_id))
        except sqlite3.OperationalError as e:
            logger.warning("Alert of incident %s goes without its start: %s", r.incident_id, e)
    dials = dict(threshold=site.get("fail_threshold") or CONSECUTIVE_FAILURE_THRESHOLD,
                 interval_min=_interval_min(site), since=since)
    second = integrations.second_opinion_enabled() and repeatable(site)
    msg = format_availability_alert(r, second, **dials)
    kb = alert_actions_keyboard(r.url, r.site_id, r.incident_id, hosting=r.hosting, api=r.api)
    # The helpers' copy names the site the public way: no path (a token may
    # live there), no port, no internal address.
    sent = await _alert_once(r.incident_id, r.site_id, msg, Priority.CRITICAL, kb, always_ring=True,
                             to_helpers=True, helper_text=format_availability_alert(
                                 r, second, label=helper_label(r.url, site.get("http_method")), **dials))
    await _remember_alert(r.incident_id, sent, msg)
    if sent is not None:
        _after_availability_alert(r.url, r.site_id, r.incident_id, getattr(sent, "message_id", None), msg, kb)


async def _send_recovery(r):
    # Rings by default; with «будить только когда сайт лёг» only the fall
    # does; after a flap — silently, it was said in the hourly 🟠.
    text = format_recovery_alert(r)
    # The helpers' copy: the public name of the site, and nothing about the
    # owner's pause — that silence was his.
    helper_text = format_recovery_alert(r, label=helper_label(r.url, api=r.api))
    inc_id = (r.resolved_incident or {}).get("id")
    if inc_id and await notifier.announced_at(inc_id) and await maintenance.paused_until(r.site_id):
        # «🔧 Я чиню» promised «напишу, когда поднимется»: the fix ends the silence.
        # A pause set before an unannounced fall stays — send() drops the ✅ then.
        await maintenance.pause_site(r.site_id, None)
        text += "\n\n🔧 Тишину снял — снова пишу обо всём."
    flapped = inc_id is not None and await _flapped_recently(inc_id, datetime.now(UTC))
    await notifier.send(text, Priority.NORMAL if flapped else Priority.CRITICAL, site_id=r.site_id,
                        resolves=inc_id, to_helpers=True, helper_text=helper_text)


# ── Several sites at once: one message ───────────────────────────────────────
#
# This many sites going down (or coming back, or still down at an escalation)
# at the same time are said in ONE message: Telegram slows a chat down after
# a short burst (429), and the alerts of the last sites, the helpers' copies
# and the re-check edits are what gets lost. Usually it is one cause anyway.
GROUP_FROM = 3
# The checks of one interval are spread over its minutes, so one outage
# reaches its sites over several ticks: a group message sent less than this
# ago takes the sites that fall after it — by an edit, without a new ring.
GROUP_WINDOW_MIN = 10
GROUP_OPEN = "alert_group:open"         # the open «не открываются сразу N» message
GROUP_UP = "alert_group:up"             # the open «снова открываются N» message
GROUP_UP_SAID = "alert_group:up_said"   # ✅ said one by one lately: the next ones may join them


async def _group_state(key: str) -> dict:
    try:
        value = json.loads(await get_state(key) or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _within_window(group: dict, now: datetime) -> bool:
    at = parse_iso_utc(group.get("at")) if group else None
    return bool(at) and now - at < timedelta(minutes=GROUP_WINDOW_MIN)


async def _not_paused(results: list) -> list:
    """A group message has no single site_id for notifier to hold it by:
    a site on «🔧 Я чиню» or in planned work is left out and goes on its
    own — its alert is held the usual way (_alert_once) and said when the
    silence ends, its ✅ passes the pause by notifier's rule."""
    return [r for r in results if not await maintenance.is_paused(r.site_id)]


def _sites_n(n: int) -> str:
    return f"{n} {plural(n, 'сайт', 'сайта', 'сайтов')}"


def _down_cause(inc: dict) -> str:
    message = inc.get("message") or ""
    if message.startswith("Bad content: "):
        return "открывается, но на странице не то"
    return humanize.describe_error(message.removeprefix("Site down: "))


async def _alert_down(results: list, by_url: dict):
    """New «сайт лёг» of a tick: one by one while they are few; one message
    for an outage of several sites, which then takes the sites falling
    within GROUP_WINDOW_MIN after it. A paused site always goes on its own:
    its alert is held until the silence ends."""
    now = datetime.now(UTC)
    for r in results:
        # One problem, not two: a site that is down is not also «медленно» —
        # whether it is said on its own or in a group message.
        forget_slow_streak(r.site_id)
        try:
            await resolve_incident(r.site_id, "performance")
        except sqlite3.OperationalError as e:
            logger.warning("Could not close the «медленно» of site %s: %s", r.site_id, e)
    fresh = await _not_paused(results)
    for r in results:
        if r not in fresh:
            await _alert_one_down(r, by_url)
    if not fresh:
        return
    group = await _group_state(GROUP_OPEN)
    if _within_window(group, now):
        if not await _group_down(list(group.get("ids", [])), fresh, group):
            for r in fresh:         # the edit did not go through: the usual way
                await _alert_one_down(r, by_url)
        return
    # Sites said one by one a few minutes ago belong to the same outage
    # (those of a group message whose window is over are not listed again;
    # an alert still owed or held is deliver_missed_alerts' and the pause's).
    skip = set(group.get("ids", [])) | {r.incident_id for r in fresh}
    earlier = sorted([inc["id"] for inc in await get_active_incidents()
                      if inc["check_type"] == "availability" and inc["id"] not in skip and inc.get("alerted_at")
                      and now - (parse_sqlite_utc(inc["created_at"]) or now) < timedelta(minutes=GROUP_WINDOW_MIN)
                      and not await maintenance.is_paused(inc["site_id"])])
    if len(fresh) + len(earlier) >= GROUP_FROM and await _group_down(earlier, fresh, None):
        return
    for r in fresh:
        await _alert_one_down(r, by_url)


async def _alert_one_down(r, by_url: dict):
    with _isolated(f"Alert worker: down alert of {site_label(r.url)}"):
        await _send_availability_alert(r, by_url.get(r.url, {}))


def _group_down_texts(down: list[dict], redeploys: list[str]) -> tuple[str, str]:
    """The owner's text and the helpers' copy: the same lines with the
    public names of the sites, and nothing about the deploy hooks."""
    def body(label) -> str:
        lines = []
        for inc in down:
            since = incident_start(inc)
            when = f", с {to_local(since).strftime('%H:%M')}" if since else ""
            lines.append(f"• {esc(label(inc))} — {esc(_down_cause(inc))}{when}")
        return (f"🔴 Не открываются сразу {_sites_n(len(down))} — похоже на общий сбой хостинга или сети:\n"
                + "\n".join(lines) + "\nПродолжаю проверять и напишу, когда поднимутся.")
    text = body(lambda inc: site_label(inc["url"]))
    if redeploys:
        text += "\n\n" + "\n".join(redeploys)
    return clip(text), clip(body(lambda inc: helper_label(inc["url"], inc.get("http_method"))))


async def _group_down(known: list[int], fresh: list, group: dict | None) -> bool:
    """Send (group=None) or update by an edit the message about an outage
    of several sites, mark every incident of it as alerted and remember it
    as the open group of this outage. False — nothing went out."""
    ids = known + [r.incident_id for r in fresh]
    by_id = {inc["id"]: inc for inc in await get_active_incidents()}
    down = [by_id[i] for i in ids if i in by_id]
    if not down:
        return False
    redeploys = [line for line in group.get("redeploys", []) if isinstance(line, str)] if group else []
    text, helper_text = _group_down_texts(down, redeploys)
    gid = group["gid"] if group else ids[0]
    kb = group_keyboard(gid)
    if group:
        if not await notifier.edit(group["message_id"], text, reply_markup=kb):
            return False
        message_id, at = group["message_id"], group["at"]
    else:
        sent = await notifier.send(text, Priority.CRITICAL, reply_markup=kb, always_ring=True,
                                   to_helpers=True, helper_text=helper_text)
        if not sent:
            return False
        message_id, at = getattr(sent, "message_id", None), datetime.now(UTC).isoformat()
    # Said — every incident of the group at once: deliver_missed_alerts
    # would otherwise re-send them one by one, and the mark is also «о
    # падении сказано», which lets the ✅ through a «🔧» pause later.
    for r in fresh:
        await _record_alert(r.incident_id, True)
    await set_state(f"alert_group:{gid}", json.dumps(ids))
    # The deploy hooks: whether to fire one is services.automation's call
    # (the «🚀» switch, the pause, the hook's budget), site by site.
    new_lines = []
    for r in fresh:
        if deploy_hook_for(r.url):
            with _isolated(f"Alert worker: rebuild of {site_label(r.url)}"):
                line = await automation.auto_redeploy(r.url, r.site_id, r.incident_id)
                if line:
                    new_lines.append(f"🤖 {esc(site_label(r.url))}: {line.removeprefix('🤖 ')}")
    if message_id is None:
        # Queued or kept in the outbox: there is no message to edit later —
        # the sites falling next come the usual way, the hooks' results apart.
        for line in new_lines:
            await notifier.send(line, Priority.NORMAL)
        return True
    if new_lines:
        redeploys += new_lines
        text, _ = _group_down_texts(down, redeploys)
        await notifier.edit(message_id, text, reply_markup=kb)
    await set_state(GROUP_OPEN, json.dumps(
        {"gid": gid, "message_id": message_id, "at": at, "ids": ids, "redeploys": redeploys}))
    _schedule_group_followups([inc["url"] for inc in down], message_id, text, kb)
    return True


def _up_line(r, label: str | None = None) -> str:
    inc = r.resolved_incident or {}
    duration = incident_duration_line(inc)
    was = "было не так" if (inc.get("message") or "").startswith("Bad content: ") else "лежал"
    return f"• {esc(label or site_label(r.url))}" + (f" — {was} {duration}" if duration else "")


def _up_text(lines: list[str]) -> str:
    return clip(f"✅ Снова открываются {_sites_n(len(lines))}:\n" + "\n".join(lines) + "\nНичего делать не нужно.")


async def _recover_one(r):
    with _isolated(f"Alert worker: recovery message of {site_label(r.url)}"):
        await _send_recovery(r)


async def _alert_up(results: list):
    """«Снова открывается» (_say_up), then the single alerts and reminders
    of every incident that is over are settled: they say «✅ Поднялся» and
    lose their «🔧» (_settle_alerts). A group message is edited by _say_up
    itself and is not touched here."""
    await _say_up(results)
    for r in results:
        with _isolated(f"Settling the alerts of {site_label(r.url)}"):
            await _settle_alerts(r.resolved_incident)


async def _say_up(results: list):
    """One by one while they are few; one message when several come back,
    which then takes the sites coming back within GROUP_WINDOW_MIN after
    it. A paused site goes on its own: whether its ✅ passes the pause is
    notifier's rule (the fall was announced), and the «🔧» pause it ends is
    lifted there (_send_recovery)."""
    now = datetime.now(UTC)
    fresh = await _not_paused(results)
    for r in results:
        if r not in fresh:
            await _recover_one(r)
    if not fresh:
        return
    lines = [_up_line(r) for r in fresh]
    helper_lines = [_up_line(r, helper_label(r.url, api=r.api)) for r in fresh]
    group = await _group_state(GROUP_UP)
    if _within_window(group, now):
        all_lines = [line for line in group.get("lines", []) if isinstance(line, str)] + lines
        if await notifier.edit(group["message_id"], _up_text(all_lines)):
            await set_state(GROUP_UP, json.dumps({**group, "lines": all_lines}))
            return
    # Sites said to be back one by one a few minutes ago: the same outage.
    window = timedelta(minutes=GROUP_WINDOW_MIN)
    said = [e for e in (await _group_state(GROUP_UP_SAID)).get("said", [])
            if isinstance(e, dict) and (at := parse_iso_utc(e.get("at"))) and now - at < window]
    if len(said) + len(fresh) < GROUP_FROM:
        for r in fresh:
            await _recover_one(r)
        said += [{"at": now.isoformat(), "line": line, "helper": helper}
                 for line, helper in zip(lines, helper_lines, strict=True)]
        await set_state(GROUP_UP_SAID, json.dumps({"said": said}))
        return
    all_lines = [str(e.get("line", "")) for e in said] + lines
    all_helper = [str(e.get("helper") or e.get("line", "")) for e in said] + helper_lines
    # After a flap the ✅ goes without a ring (it was said in the hourly 🟠):
    # the group rings when any of its sites earned a ring.
    ring = False
    for r in fresh:
        inc_id = (r.resolved_incident or {}).get("id")
        if not (inc_id and await _flapped_recently(inc_id, now)):
            ring = True
    sent = await notifier.send(_up_text(all_lines), Priority.CRITICAL if ring else Priority.NORMAL,
                               to_helpers=True, helper_text=_up_text(all_helper))
    if not sent:
        return
    await set_state(GROUP_UP_SAID, None)
    message_id = getattr(sent, "message_id", None)
    if message_id is not None:
        await set_state(GROUP_UP, json.dumps({"message_id": message_id, "at": now.isoformat(), "lines": all_lines}))


FLAP_WINDOW = timedelta(hours=1)


async def _flap_times(incident_id: int) -> list[str]:
    """When the incident was reopened (ISO, oldest first) — flap:<id>."""
    try:
        times = json.loads(await get_state(f"flap:{incident_id}") or "[]")
    except ValueError:
        return []
    return [t for t in times if isinstance(t, str)] if isinstance(times, list) else []


async def _flapped_recently(incident_id: int, now: datetime) -> bool:
    """The incident is flapping: it was reopened less than an hour ago. One
    reopen followed by hours of solid downtime is not a flap any more — the
    escalation and the ringing ✅ come back."""
    times = await _flap_times(incident_id)
    last = parse_iso_utc(times[-1]) if times else None
    return bool(last) and now - last < FLAP_WINDOW


async def _note_flapping(r):
    """The incident closed minutes ago was reopened: no ring, no rebuild —
    one 🟠 an hour with how often it fell."""
    now = datetime.now(UTC)
    key = f"flap:{r.incident_id}"
    times = [t for t in await _flap_times(r.incident_id)
             if (p := parse_iso_utc(t)) and now - p < FLAP_WINDOW] + [now.isoformat()]
    await set_state(key, json.dumps(times))
    noted = parse_iso_utc(await get_state(f"flap_noted:{r.incident_id}"))
    if noted and now - noted < FLAP_WINDOW:
        return
    n = len(times) + 1          # the first fall opened the incident itself
    sent = await notifier.send(
        f"🟠 {esc(site_label(r.url))} мигает: {n} {plural(n, 'падение', 'падения', 'падений')} за час, "
        f"последнее в {to_local(now).strftime('%H:%M')}. На каждое не звоню; когда успокоится — напишу.",
        Priority.NORMAL, site_id=r.site_id)
    if sent:
        await set_state(f"flap_noted:{r.incident_id}", now.isoformat())


async def _site_is_up(r, slow_ms: int):
    if r.site_id in _ext_ok_noted:
        _ext_ok_noted.discard(r.site_id)
        await notifier.send(f"✅ {esc(site_label(r.url))} снова открывается и с моего сервера.",
                            Priority.NORMAL, site_id=r.site_id)
    await _track_slow(r, slow_ms)


def _after_availability_alert(url: str, site_id: int, incident_id: int | None, message_id: int | None,
                              text: str, kb):
    """Once the alert is out: the deploy hook — whether to fire it is
    services.automation's call (the «🚀» switch, the pause, the hook's
    budget); it reports into the same message — and the 2- and 5-minute
    re-checks."""
    if deploy_hook_for(url):
        _enqueue(_redeploy_after_alert, url, site_id, incident_id, message_id, text, kb)
    elif message_id is not None:
        _schedule_followups(url, site_id, message_id, text, kb)


async def _redeploy_after_alert(url: str, site_id: int, incident_id: int | None, message_id: int | None,
                                text: str, kb):
    line = await automation.auto_redeploy(url, site_id, incident_id)
    if line is None:            # switched off: nothing to say, only the re-checks
        if message_id is not None:
            _schedule_followups(url, site_id, message_id, text, kb)
        return
    if message_id is None:      # the alert sits in the outbox or the quiet queue — say it separately
        await notifier.send(f"{esc(site_label(url))}: {line}", Priority.NORMAL, site_id=site_id)
        return
    text = clip(f"{text}\n\n{line}")
    await notifier.edit(message_id, text, reply_markup=kb)
    _schedule_followups(url, site_id, message_id, text, kb)


FOLLOWUP_MINUTES = (2, 5)
ALERT_MSGS = "alert_msgs:"      # <incident id> → [[message_id, text], …] of its single alerts and reminders
REMEMBERED_ALERTS = 5


def _known_alerts(raw: str | None) -> list[tuple[int, str]]:
    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    return [(int(m), str(t)) for m, t in items if isinstance(m, int) and isinstance(t, str)] \
        if isinstance(items, list) else []


async def _remember_alert(incident_id: int | None, sent, text: str):
    """The message of a single alert or reminder, to settle after the ✅
    (ALERT_MSGS, the last REMEMBERED_ALERTS). True — queued or kept for a
    retry: nothing to edit. A group message is _alert_up's to edit and is
    never remembered here."""
    message_id = getattr(sent, "message_id", None)
    if not incident_id or not message_id:
        return
    key = f"{ALERT_MSGS}{incident_id}"
    try:
        known = _known_alerts(await get_state(key))
        await set_state(key, json.dumps([*known, [message_id, text]][-REMEMBERED_ALERTS:]))
    except sqlite3.OperationalError as e:
        logger.warning("Could not remember the alert of incident %s: %s", incident_id, e)


async def _settle_alerts(incident: dict | None):
    """After the ✅ the alert and the reminders of the incident say
    «✅ Поднялся» and keep only «ℹ️ Что делать»: «🔧» on an old alert would
    pause a working site for an hour. The 2- and 5-minute re-checks of
    those messages are cancelled."""
    if not incident:
        return
    key = f"{ALERT_MSGS}{incident['id']}"
    known = _known_alerts(await get_state(key))
    if not known:
        return
    duration = incident_duration_line(incident)
    when = f"{now_local():%H:%M}"
    if (incident.get("message") or "").startswith("Bad content: "):
        head = f"✅ Снова как надо в {when}" + (f", было не так {duration}" if duration else "")
    else:
        head = f"✅ Поднялся в {when}" + (f", лежал {duration}" if duration else "")
    for message_id, text in known:
        if _scheduler is not None:
            for minutes in FOLLOWUP_MINUTES:
                job = _scheduler.get_job(f"followup:{message_id}:{minutes}")
                if job:
                    job.remove()
        await notifier.edit(message_id, clip(f"{head}. Было:\n{text}"), reply_markup=explain_keyboard(incident["id"]))
    await set_state(key, None)


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


def _schedule_group_followups(urls: list[str], message_id: int, text: str, kb):
    """The same re-checks 2 and 5 minutes later for a group message — one
    edit of it each time, not one per site."""
    if _scheduler is None:
        return
    for minutes in FOLLOWUP_MINUTES:
        _scheduler.add_job(_group_followup, "date",
                           run_date=datetime.now(UTC) + timedelta(minutes=minutes),
                           args=[urls, message_id, text, kb, minutes],
                           id=f"followup:{message_id}:{minutes}", replace_existing=True)


async def _group_followup(urls: list[str], message_id: int, text: str, kb, minutes: int):
    results = await check_all(urls, manage=False)
    up = [esc(site_label(r.url)) for r in results if r.ok]
    down = [esc(site_label(r.url)) for r in results if not r.ok and not r.transient]
    unknown = [esc(site_label(r.url)) for r in results if not r.ok and r.transient]
    if not down and not unknown:
        line = f"\n\n🔁 Через {minutes} мин: все уже открываются. Подтвержу отдельным сообщением."
        for other in FOLLOWUP_MINUTES:
            if other > minutes and _scheduler is not None and (job := _scheduler.get_job(f"followup:{message_id}:{other}")):
                job.remove()
    else:
        parts = ([f"уже открываются: {', '.join(up)}"] if up else []) \
            + ([f"всё ещё не открываются: {', '.join(down)}"] if down else []) \
            + ([f"проверить не смог: {', '.join(unknown)}"] if unknown else [])
        line = f"\n\n🔁 Через {minutes} мин: " + "; ".join(parts) + "."
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
    after a streak; resolved after the same streak of clearly fast replies
    (below FAST_AGAIN_SHARE of the threshold) — a reply in between resets
    both streaks."""
    ms = r.response_time_ms or 0
    if ms >= slow_ms:
        _fast_streak[r.site_id] = 0
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
    if ms >= slow_ms * FAST_AGAIN_SHARE:       # not slow, not clearly fast either
        _fast_streak[r.site_id] = 0
        return
    _fast_streak[r.site_id] = _fast_streak.get(r.site_id, 0) + 1
    if _fast_streak[r.site_id] < SLOW_RESPONSE_STREAK:
        return
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
    """The ladder and the incident of a domain live on its carrier site: a
    finding left on a former carrier, or on an address that has no domain
    of the owner's (an IP, user.github.io), would never close by itself."""
    urls = await get_active_host_urls()
    await close_inapplicable(("domain",), [g[0] for g in domain_groups(urls).values()])
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
        # Audited quietly: the next morning digest says it (seo_first_audit:<id>), once.
        for r in await check_all_seo([u for u in urls if u in fresh], manage=False):
            await set_state(f"seo_first_audit:{r.site_id}", "1")
    results = await check_all_seo([u for u in urls if u not in fresh])
    await _notify_transitions(
        results, format_seo_alert,
        # The closed incident may have been a 🟠 «нет sitemap», not a closed
        # site — the recovery line must be true for both levels.
        lambda r: f"✅ {esc(site_label(r.url))}: поисковикам больше ничего не мешает. Ничего делать не нужно.",
        priority=lambda r: Priority.CRITICAL,
        keyboard=lambda r: seo_fix_keyboard(
            r.site_id, list(dict.fromkeys(p.code for p in r.problems if p.severity == "critical")), on_alert=True))
    # 🔴 → 🟠: the ban is lifted, lesser findings remain. Not a recovery (the
    # incident is open) and not an alert (nothing new is wrong) — one quiet
    # line to the owner. Like the SEO alert above, not for the helpers.
    for r in results:
        if r.downgraded:
            with _isolated(f"Search ban lifted for {site_label(r.url)}"):
                await notifier.send(
                    f"✅ {esc(site_label(r.url))}: запрет для поисковиков снят — сайт снова открыт для поиска. "
                    "Мелкие помехи остались, они в «🔎 Поиск и ИИ».",
                    Priority.NORMAL, site_id=r.site_id)


@_once("card_pictures", mark=True)
async def run_card_pictures():
    """Once a day, after the search audit: a fresh picture of every page
    for its card (services.screenshots). One at a time — the free
    screenshot service has a small daily quota, so only pages whose card
    can show the picture, and never an internal address (is_due)."""
    sites = await get_all_sites()
    with _isolated("Pruning pictures of removed sites"):
        screenshots.prune({s["id"] for s in sites})     # left by versions before forget() existed
    for site in sites:
        with _isolated(f"Card picture of {site_label(site['url'])}"):
            if is_web_page(site["url"], site.get("http_method")) \
                    and await screenshots.is_due(site["id"], site["url"]):
                await screenshots.refresh(site["id"], site["url"])


def rerun_seo_after_update(minutes: int = 3):
    """A new build may have fixed a check. Yesterday's findings — maybe
    false ones — must not wait for 07:30: audit again a few minutes after
    the start, with incidents, so stale ones close and say so."""
    if _scheduler is not None:
        _scheduler.add_job(run_seo_checks, DateTrigger(run_date=datetime.now(UTC) + timedelta(minutes=minutes), timezone=UTC),
                           id="seo_after_update", replace_existing=True, misfire_grace_time=600)


def rerun_visitors_soon(minutes: int = 1):
    """The CrUX key just answered a live request: ask about the sites now,
    not tomorrow morning."""
    if _scheduler is not None:
        _scheduler.add_job(run_visitor_checks, DateTrigger(run_date=datetime.now(UTC) + timedelta(minutes=minutes), timezone=UTC),
                           id="visitor_checks_now", replace_existing=True, misfire_grace_time=600)


def next_run_words(job_id: str) -> str | None:
    """«завтра в 08:10» · «12.11 в 08:10» (or «сегодня в …») — when the job
    really runs next, in the owner's time; None without a scheduler, so the
    text falls back to a promise it can keep."""
    job = _scheduler.get_job(job_id) if _scheduler is not None else None
    when = getattr(job, "next_run_time", None)
    if when is None:
        return None
    local, today = to_local(when), now_local().date()
    day = ("сегодня" if local.date() == today else "завтра" if local.date() == today + timedelta(days=1)
           else local.strftime("%d.%m"))
    return f"{day} в {local:%H:%M}"


REGION_PAUSE = 2.0   # seconds between sites: be polite to check-host.net


async def run_region_checks():
    """Every 6 hours: how fast each site opens from the audience's region —
    a check-host node picked by the owner's timezone. Its own opt-in
    (REGION_SPEED / «⏱ Скорость из региона»), off by default: the address
    of every site goes to the external service. Sites that are down right
    now are skipped: a timing of a broken site is noise."""
    if not integrations.region_speed_enabled():
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


async def _registrar_of(host: str) -> str | None:
    """The registrar from the domain snapshot of a site on this host — that
    is where DNS is fixed."""
    for s in await get_all_sites():
        h = host_of(s["url"])
        if h and host in (h, registrable_domain(h)):
            registrar = ((await sitestatus.get(s["id"])).get("domain") or {}).get("registrar")
            if registrar:
                return registrar
    return None


async def run_dns_checks():
    """The new baseline is committed only after the alert is delivered or
    queued — otherwise a failed send would swallow the change forever.
    The registrar's panel (when known) is a tap away: DNS is fixed there."""
    for change in await check_all_dns(await get_active_host_urls()):
        with _isolated(f"DNS alert for {change['host']} {change['rtype']}"):
            sent = await notifier.send(format_dns_change(change),
                                       Priority.CRITICAL if change["critical"] else Priority.NORMAL,
                                       reply_markup=domain_keyboard(await _registrar_of(change["host"])))
            # A name that stopped answering keeps its baseline: «не отвечает»
            # is not an answer, and the last known one is what the owner compares with.
            if sent and not change.get("failing"):
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
    if any(r.kind == "mail" and r.ok and r.delivery.ok is False for r in results):
        schedule_mail_recheck()


def schedule_mail_recheck():
    """The recheck the «⏳ … перепроверю через 10 минут» line promises —
    from the hourly job and from the ✉️ screen alike: a dead mailbox is
    said within the quarter hour."""
    if _scheduler is not None:
        _scheduler.add_job(run_mail_checks, DateTrigger(run_date=datetime.now(UTC) + timedelta(minutes=MAIL_RECHECK_MIN), timezone=UTC),
                           id="mail_recheck", replace_existing=True, misfire_grace_time=300)


def _mail_keyboard(r):
    status = ([InlineKeyboardButton(text=f"↗️ Статус {r.provider}", url=r.status_page)]
              if r.kind == "mail" and r.status_page else [])
    mail = [InlineKeyboardButton(text="✉️ Почта", callback_data=f"mail:{r.site_id}:a")]
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
                                                                 callback_data=f"sec:{r.site_id}:a")])


@_once("visitor_checks", mark=True)
@_alerts_on("visitors")
async def run_visitor_checks():
    """Daily: real visitors' speed from Google's Chrome data, when a key is
    set. An internal address is not asked about — Google would only learn
    it — and its old finding closes as inapplicable."""
    urls = [u for u in await get_active_page_urls() if not is_private_target(u)]
    await close_inapplicable(("visitors",), urls)
    await _notify_transitions(await check_all_visitors(urls), format_visitors_alert, format_visitors_recovery,
                              keyboard=_visitors_keyboard)


def _visitors_keyboard(r):
    return explain_keyboard(r.incident_id,
                            [InlineKeyboardButton(text="↗️ PageSpeed Insights", url=crux.pagespeed_url(r.url))],
                            [InlineKeyboardButton(text="👥 Скорость у посетителей", callback_data=f"spd:{r.site_id}:a")])


@_once("index_checks", mark=True)
async def run_index_checks():
    """Daily indexing watch via GSC / Yandex.Webmaster (when tokens are set):
    the answers go into the site snapshot for the «🔎 Поиск и ИИ» screen,
    and a change of state becomes a message — nothing else does. Yandex is
    asked once for all sites; «did not answer» stays «did not answer»."""
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
                await _google_change(site, google)
            if yandex:
                await _yandex_change(site, yandex)


def _google_state(raw: str | None) -> dict | None:
    """The stored Google state: JSON {verdict, coverage, told}; the format
    before PLAN-36 was «PASS|coverage», where anything but PASS had been
    said as 🔴."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        verdict, _, coverage = raw.partition("|")
        return {"verdict": verdict, "coverage": coverage, "told": verdict != "PASS"}
    return data if isinstance(data, dict) and data.get("verdict") else None


def _index_keyboard(site_id: int, label: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔎 Поиск и ИИ", callback_data=f"seo_site:{site_id}"),
        InlineKeyboardButton(text=label, url=url)]])


async def _google_change(site: dict, google: dict):
    """One site's Google verdict against what was seen before — per site,
    not per host: two monitors of one host (the site and a page of it) kept
    overwriting each other's state and rang «убрал / снова показывает»
    every day. The first observation is a quiet baseline, unless it is
    FAIL; «убрал» is a PASS that stopped being one; «снова показывает»
    only after a 🔴 was said."""
    url, site_id = site["url"], site["id"]
    key = f"gsc_idx:{site_id}"
    raw = await get_state(key)
    if raw is None:
        raw = await get_state(f"gsc_idx:{short_host(url)}")      # the key before PLAN-36: by host, once
    prev = _google_state(raw)
    verdict = google["verdict"]
    state = {"verdict": verdict, "coverage": google["coverage"], "told": bool(prev and prev.get("told"))}
    label = esc(site_label(url))
    kb = _index_keyboard(site_id, "↗️ Search Console", "https://search.google.com/search-console")
    advice = ("Люди больше не найдут сайт через Google.\n\n"
              "Что делать:\n• Открыть Google Search Console → Проверка URL для главной: там будет "
              "написана причина.\n• Чаще всего это noindex на странице или запрет в robots.txt — "
              "проверь «🔎 Поиск и ИИ» в боте." + _tech_line(google["coverage"]))
    sent = True
    if prev is None:
        if verdict == "FAIL":
            sent = await notifier.send(f"🔴 Google не показывает {label} в поиске\n{advice}", Priority.CRITICAL,
                                       reply_markup=kb, site_id=site_id)
            state["told"] = True
    elif verdict == "PASS":
        if prev.get("told"):
            sent = await notifier.send(
                f"✅ Google снова показывает {label} в поиске. Ничего делать не нужно."
                + _tech_line(google["coverage"]), site_id=site_id)
            state["told"] = False
    elif prev.get("verdict") == "PASS":
        sent = await notifier.send(f"🔴 Google убрал {label} из поиска\n{advice}", Priority.CRITICAL,
                                   reply_markup=kb, site_id=site_id)
        state["told"] = True
    if sent and state != prev:
        await set_state(key, json.dumps(state, ensure_ascii=False))


async def _yandex_change(site: dict, yandex: dict):
    """Problems Yandex.Webmaster sees on the host, said once per set of
    severities, in words («1 критическая проблема»), not in API codes."""
    url, site_id = site["url"], site["id"]
    host = short_host(url)
    key = f"yx_problems:{host}"
    prev = await get_state(key) or ""
    problems = yandex["alert_problems"]
    current = ",".join(sorted(problems))
    if current == prev:
        return
    sent = True
    if current:
        level = index_status.yandex_problem_level(problems)
        fatal = ("\nКритическая — по классификации Яндекса сайт недоступен для робота или под санкциями."
                 if level == "critical" else "")
        sent = await notifier.send(
            f"{humanize.level_icon(level)} Яндекс.Вебмастер: {esc(index_status.yandex_problems_words(problems))} "
            f"на {esc(host)}{fatal}\n\n"
            f"Что делать: открыть Яндекс.Вебмастер → Диагностика сайта, там каждая проблема "
            f"с инструкцией.", notifier.priority_for(level), site_id=site_id,
            reply_markup=_index_keyboard(site_id, "↗️ Вебмастер", "https://webmaster.yandex.ru/"))
    elif prev:
        sent = await notifier.send(f"✅ Яндекс больше не видит проблем на {esc(host)}. Ничего делать не нужно.",
                                   site_id=site_id)
    if sent:
        await set_state(key, current)


# ── Health of the external services ─────────────────────────────────────────

async def run_integration_health():
    """Hourly: one 🟠 when an external service stopped working for the bot —
    its key refused, or a day without a single success — and one ✅ when it
    works again (services.integrations.record_result keeps the record)."""
    used = {"gsc": gsc.available(), "yandex": yandex_webmaster.available(), "crux": crux.available(),
            "github": github.available(), "checkhost": integrations.second_opinion_enabled(),
            "osv": config.dependency_watch, "rdap": True}
    for name, (title, loss, setup) in integrations.HEALTH.items():
        h = await integrations.health(name)
        if not used.get(name):
            if h.get("told"):
                await integrations.set_told(name, False)     # switched off: nothing to say any more
            continue
        if integrations.is_broken(h) and not h.get("told"):
            kb = (InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔌 Проверить подключение",
                                                                              callback_data=setup)]])
                  if setup else None)
            why = f" — {esc(h['reason'])}" if h.get("reason") else ""
            sent = await notifier.send(f"🟠 {esc(title)}: не работает{why}.\nПока это не починить, {esc(loss)}.",
                                       Priority.NORMAL, reply_markup=kb)
            if sent:
                await integrations.set_told(name, True)
        elif h.get("told") and not h.get("fail_since"):
            if await notifier.send(f"✅ {esc(title)}: снова работает.", Priority.NORMAL):
                await integrations.set_told(name, False)


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
                sent = await notifier.send(text, reply_markup=heartbeat_keyboard([job], seen={job} if last else set()))
                if sent:
                    await set_state(f"hb_alerted:{job}", now.isoformat())
            elif not overdue and alerted:
                await set_state(f"hb_alerted:{job}", None)  # clear a stale flag


# ── Bot-host monitoring ──────────────────────────────────────────────────────

_DISK_RANK = {"warning": 1, "critical": 2, "1": 1}     # "1" — the flag older versions wrote
_disk_level_unsaved: str | None = None                 # the level said when the flag could not be written
# «✅ снова в норме» only this far below the threshold: 84 % ↔ 86 % would
# otherwise be a pair of messages every 30 minutes.
DISK_CLEAR_MARGIN = 5


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
    elif already and result["pct"] <= config.disk_alert_pct - DISK_CLEAR_MARGIN:
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
                # Not restarted because the owner said so (the «🔁» switch, the pause): the
                # container is still down, and the owner hears that — and why nothing was done.
                verdict = ("Перезапустил, но он всё ещё нездоров." if e["restarted"]
                           else f"Сам больше не перезапускаю: {esc(e['skipped'])}. Нужен человек."
                           if e.get("skipped") else "Перезапустить не удалось.")
                sent = await notifier.send(
                    f"🔴 Сервис «{label}» на сервере бота {esc(e['problem'])}. {verdict}"
                    f"\n{expl.meaning}\n\n{_container_steps(name)}",
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

ACK_UNTIL_UP = "until_up"          # ack:<incident> — «👀 Не напоминать», until the site is up
ESCALATION_MAX_MIN = 240           # 30 → 60 → 120 → 240, then every 4 h
ESCALATION_QUIET_MIN = 120         # in quiet hours not more often than every 2 h


def escalation_step(sent: int, repeat_min: int) -> int:
    """Minutes to wait after the `sent`-th escalation of an incident."""
    step = min(repeat_min * 2 ** sent, ESCALATION_MAX_MIN)
    return max(step, ESCALATION_QUIET_MIN) if notifier.in_quiet_hours() else step


def _quiet_button(incident_id: int) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="👀 Знаю, не напоминать", callback_data=f"inc_quiet:{incident_id}:a")]


def _escalation_head(inc: dict, age_min: float, every: str, label: str | None = None) -> str:
    """The first lines of a reminder: how long and why. label — a name for
    the site instead of site_label (the helpers' copy)."""
    if (inc.get("message") or "").startswith("Bad content: "):
        # The site opens; its text is wrong — not «лежит», not «поднимется».
        where = "в ответе" if is_api_check(inc["url"], inc.get("http_method")) else "на странице"
        return (f"🔴 {esc(incident_line(inc, label))}\nТак уже {fmt_duration(age_min)}. "
                f"Проверяю {every} и напишу, когда {where} снова будет как надо.")
    cause = humanize.describe_error((inc.get("message") or "").removeprefix("Site down: "))
    return (f"🔴 {esc(label or site_label(inc['url']))} лежит уже {fmt_duration(age_min)}: {esc(cause)}\n"
            f"Проверяю {every} и напишу, как только поднимется.")


async def run_escalation_watch():
    """Re-alert about an unresolved DOWN site with a growing pause (30 → 60
    → 120 → 240 min, in quiet hours not more often than every 2 h) —
    bypasses mute: a dead site must not be forgettable, but neither must it
    page every 30 minutes all night. Availability only: paging about an
    expiring cert would train the user to ignore alerts. «🔧 Я чиню» snoozes
    it for maintenance.FIX_MINUTES, «👀 Знаю, не напоминать» until the site
    is up; a flapping site gets the hourly 🟠 instead; the helpers get the
    first reminder only; a site whose last planned check opened is not
    «лежит» — its ✅ is a check away."""
    repeat_min = config.escalation_repeat_min
    if repeat_min <= 0:     # 0 = off, as .env.example promises; a negative one is not «every 5 minutes»
        return
    if await netcheck.outage_since():
        return              # «лежит уже …» without a network is a guess; it would only pile up in the outbox
    now = datetime.now(UTC)
    due: list[tuple[dict, int]] = []     # (incident, reminders sent so far) — all the filters passed
    for inc in await get_active_incidents():
        with _isolated(f"Escalation of incident #{inc['id']}"):
            if inc["severity"] != "critical" or inc["check_type"] != "availability":
                continue
            created = parse_sqlite_utc(inc["created_at"])
            if not created or (now - created) < timedelta(minutes=repeat_min):
                continue
            acked = await get_state(f"ack:{inc['id']}")
            if acked == ACK_UNTIL_UP or ((until := parse_iso_utc(acked)) and until > now):
                continue
            if await _flapped_recently(inc["id"], now):
                continue        # the hourly 🟠 «мигает» covers it
            sent_n = int((await get_state(f"escalated_n:{inc['id']}") or "0").strip() or 0)
            last = parse_iso_utc(await get_state(f"escalated:{inc['id']}"))
            if last and (now - last) < timedelta(minutes=escalation_step(sent_n, repeat_min)):
                continue
            if await get_state(f"{notifier.HELD}{inc['id']}"):
                continue        # held by a pause or a window: release_held says it first, once
            announced = await notifier.announced_at(inc["id"])
            if announced and (now - announced) < timedelta(minutes=repeat_min):
                continue        # the held alert was just said — no repeat on its heels
            if await get_recent_check_statuses(inc["site_id"], "availability", limit=1,
                                               scheduled_only=True) == ["ok"]:
                continue        # it answered: the next good answer closes the incident
            due.append((inc, sent_n))
    # Several sites still down at once: one reminder (GROUP_FROM) — a site
    # on «🔧» or in planned work is left out, as notifier would drop it.
    if len(due) >= GROUP_FROM:
        due = [(inc, n) for inc, n in due if not await maintenance.is_paused(inc["site_id"])]
    if len(due) >= GROUP_FROM:
        with _isolated("Escalation of several incidents"):
            await _escalate_group(due, now)
        return
    for inc, sent_n in due:
        with _isolated(f"Escalation of incident #{inc['id']}"):
            await _escalate_one(inc, sent_n, now, repeat_min)


async def _escalate_one(inc: dict, sent_n: int, now: datetime, repeat_min: int):
    site = await get_site(inc["site_id"]) or {}
    every = humanize.every_minutes(_interval_min(site))
    # «лежит уже …» counts from the first failed check; whether it is
    # time to remind is still measured from the alert (created_at).
    age_min = (now - (incident_start(inc) or parse_sqlite_utc(inc["created_at"]) or now)).total_seconds() / 60
    api = is_api_check(inc["url"], inc.get("http_method"))
    content = (inc.get("message") or "").startswith("Bad content: ")
    where = "в ответе" if api else "на странице"
    back = f"«✅ {where} снова как надо»" if content else "«✅ поднялся»"
    kb = alert_actions_keyboard(inc["url"], inc["site_id"], inc["id"],
                                hosting=await get_state(f"hosting:{inc['site_id']}"), api=api)
    kb = InlineKeyboardMarkup(inline_keyboard=[*kb.inline_keyboard, _quiet_button(inc["id"])])
    # The helpers get the first reminder only (it is still «сайт лёг»):
    # the public name of the site and no talk of buttons they do not have.
    text = (f"{_escalation_head(inc, age_min, every)}\n\n"
            f"Чинишь — «🔧 Я чиню», замолчу на {maintenance.fix_length()}. "
            f"Знаешь и ждёшь — «👀 Знаю, не напоминать»: следующее сообщение будет {back}.")
    sent = await notifier.send(
        text, Priority.CRITICAL, site_id=inc["site_id"], always_ring=True, reply_markup=kb,
        to_helpers=sent_n == 0,
        helper_text=_escalation_head(inc, age_min, every, helper_label(inc["url"], inc.get("http_method"))))
    await _remember_alert(inc["id"], sent, text)
    if sent:
        await set_state(f"escalated:{inc['id']}", now.isoformat())
        await set_state(f"escalated_n:{inc['id']}", str(sent_n + 1))


def _still_down(inc: dict, now: datetime) -> str:
    age = fmt_duration((now - (incident_start(inc) or now)).total_seconds() / 60)
    content = (inc.get("message") or "").startswith("Bad content: ")
    return f"так уже {age}" if content else f"лежит уже {age}"


async def _escalate_group(due: list[tuple[dict, int]], now: datetime):
    """One reminder about several sites still down: the sites with how long,
    «🔧 Я чиню все» for all of them. The helpers get it like a first
    reminder — when it is the first for any of the sites — by the public
    names, without the talk of buttons."""
    incs = [inc for inc, _ in due]
    gid = incs[0]["id"]
    await set_state(f"alert_group:{gid}", json.dumps([inc["id"] for inc in incs]))

    def body(label) -> str:
        lines = [f"• {esc(label(inc))} — {_still_down(inc, now)}" for inc in incs]
        return (f"🔴 Всё ещё не открываются {_sites_n(len(incs))}:\n" + "\n".join(lines)
                + "\nПродолжаю проверять и напишу, как только поднимутся.")
    sent = await notifier.send(
        clip(body(lambda inc: site_label(inc["url"])) + "\n\nЕсли ты уже чинишь — нажми «🔧 Я чиню все»."),
        Priority.CRITICAL, always_ring=True, reply_markup=group_keyboard(gid),
        to_helpers=any(n == 0 for _, n in due),
        helper_text=clip(body(lambda inc: helper_label(inc["url"], inc.get("http_method")))))
    if sent:
        for inc, sent_n in due:
            await set_state(f"escalated:{inc['id']}", now.isoformat())
            await set_state(f"escalated_n:{inc['id']}", str(sent_n + 1))


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
    """The latest time the trigger fired (or should have) in (now - within, now].
    Read without the trigger's jitter: a random 0–120 s must not make a run
    that just finished look older than its planned time (a second run)."""
    if getattr(trigger, "jitter", None):
        trigger = copy.copy(trigger)
        trigger.jitter = None
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
            f"вставь новую: кнопка ниже.", reply_markup=_WATCHDOG_KB)
        if sent:
            await set_state("watchdog_alerted", "1")
    elif not failing and alerted:
        await set_state("watchdog_alerted", None)
        await notifier.send(f"✅ Сторож {name} снова на связи. Ничего делать не нужно.")


# «onb:watchdog» answers below the message (handlers.setup): the alert and
# the digest stay as they were.
_WATCHDOG_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛡 Внешний сторож", callback_data="onb:watchdog")]])


@_once("retention", mark=True)
async def run_retention():
    # No try/except: a failure reaches the job listener (one message to the
    # owner) and the log keeps its traceback.
    n = await rollup_old_checks(config.retention_days)
    if n:
        logger.info("Retention: rolled up %d old check rows", n)
    # Separate from the rollup, which returns early when there are no old
    # checks: visitors' messages, resolved incidents and old rollups go too.
    purged = {table: count for table, count in (await purge_old_rows()).items() if count}
    if purged:
        logger.info("Retention: deleted %s", ", ".join(f"{count} {table}" for table, count in purged.items()))


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
        if not stats["down_min"]:
            clean += 1
            continue
        down.append(f"• {esc(site_label(s['url']))} — {humanize.downtime(stats)}")
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
    pages = [s for s in sites if is_web_page(s["url"], s.get("http_method"))]
    first_audit = [s for s in pages if await get_state(f"seo_first_audit:{s['id']}")]
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
            await set_state(f"seo_first_audit:{s['id']}", None)      # said once

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
    silent = [j for j in await heartbeats.job_states() if j.needs_you]
    kb = heartbeat_keyboard([j.name for j in silent], seen={j.name for j in silent if j.ago is not None})
    if await heartbeats.watchdog() is None:
        # The digest says there is no watchdog: the way to one is a tap away.
        kb = InlineKeyboardMarkup(inline_keyboard=[*(kb.inline_keyboard if kb else []),
                                                  *_WATCHDOG_KB.inline_keyboard])
    await notifier.send(text, Priority.DIGEST, reply_markup=kb)


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


# The jobs that call free outside services (check-host, the screenshot
# service, GSC and Yandex, RDAP, CrUX) start up to this many seconds after
# their minute: every installation of one timezone used to knock at the
# same second. The digests, retention and the backup keep their minute —
# it is promised to the owner.
OUTSIDE_JITTER_SEC = 120


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
        "domain_checks": dict(_before_morning(55), jitter=OUTSIDE_JITTER_SEC),
        "seo_checks": _before_morning(90),
        "index_checks": dict(_before_morning(75), jitter=OUTSIDE_JITTER_SEC),
        "card_pictures": dict(_before_morning(70), jitter=OUTSIDE_JITTER_SEC),
        "dns_checks": dict(minute=20),
        "mail_checks": dict(minute=25),
        "security_checks": dict(hour="*/6", minute=35),
        "visitor_checks": dict(_before_morning(50), jitter=OUTSIDE_JITTER_SEC),
        "region_checks": dict(hour="*/6", minute=17, jitter=OUTSIDE_JITTER_SEC),
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
    job(run_integration_health, lambda: IntervalTrigger(hours=1, start_date=soon, timezone=UTC),
        "integration_health")
    job(notifier.flush_queue, lambda: IntervalTrigger(minutes=10, timezone=UTC), "quiet_flush")
    job(send_feedback_summary, lambda: IntervalTrigger(hours=1, timezone=UTC), "feedback_summary")
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
