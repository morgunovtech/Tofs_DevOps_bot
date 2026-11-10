"""A restart or a step of the wall clock does not cost the day's jobs: what
was skipped runs late, interval jobs are re-armed, one check never runs
twice at once, and «⏸ Меня не было» repeats the monitor's verdict."""

import asyncio
from datetime import UTC, datetime, timedelta

from apscheduler.triggers.interval import IntervalTrigger

from db.database import get_state, save_incident, set_state
from reports import scheduler
from services import settings
from utils.clock import local_tz


def _local(hour: int, minute: int) -> datetime:
    """Today at hour:minute in the bot's timezone, as UTC."""
    return datetime.now(local_tz()).replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(UTC)


def _catch_ups(sched) -> set[str]:
    return {j.id.removeprefix("catchup:") for j in sched.get_jobs() if j.id.startswith("catchup:")}


async def test_a_short_restart_across_the_morning_still_brings_the_report(bot, db, monkeypatch):
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    now = _local(9, 5)                                       # back at 09:05 after a 2-minute redeploy
    for job_id in scheduler.CATCH_UP:
        await set_state(f"last_run:{job_id}", (now - timedelta(days=1)).isoformat())
    await scheduler.catch_up_missed(now, last_alive=now - timedelta(minutes=2), skip=scheduler._SOON_AFTER_START)
    assert {"morning_report", "seo_checks", "ssl_checks", "domain_checks", "retention", "db_backup"} <= _catch_ups(sched)
    assert not _catch_ups(sched) & scheduler._SOON_AFTER_START      # their «soon» twins run anyway
    assert sched.get_job("catchup:morning_report").kwargs == {}     # 5 minutes late: an ordinary report
    for job_id in _catch_ups(sched):
        sched.remove_job(f"catchup:{job_id}")
    for job_id in scheduler.CATCH_UP:
        await set_state(f"last_run:{job_id}", now.isoformat())
    await scheduler.catch_up_missed(now, last_alive=now - timedelta(minutes=2))
    assert _catch_ups(sched) == set()                        # everything ran: nothing to catch up


async def test_a_long_gap_brings_the_morning_report_as_late(bot, db, monkeypatch):
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    now = _local(10, 30)
    await set_state("last_run:morning_report", (now - timedelta(days=1)).isoformat())
    await scheduler.catch_up_missed(now, last_alive=now - timedelta(hours=3))
    assert sched.get_job("catchup:morning_report").kwargs == {"late": True}


async def test_the_very_first_start_catches_up_nothing(bot, db, monkeypatch):
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    await scheduler.catch_up_missed(_local(9, 5), last_alive=None)
    assert _catch_ups(sched) == set()


async def test_a_catch_up_is_skipped_when_the_job_ran_meanwhile(bot, db, monkeypatch):
    """APScheduler made the run up itself (misfire_grace_time) before the
    catch-up fired: one morning report, not two."""
    async def snapshot(report_type, extras):
        return "🌅 Доброе утро"
    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    due = datetime.now(UTC) - timedelta(minutes=5)
    await scheduler.send_morning_report()                    # the scheduler's own late run marks itself
    assert len(bot.sent) == 1
    await scheduler._run_catch_up("morning_report", scheduler.send_morning_report, due)
    assert len(bot.sent) == 1                                # the catch-up fires: nothing to do
    await set_state("last_run:morning_report", (due - timedelta(days=1)).isoformat())
    await scheduler._run_catch_up("morning_report", scheduler.send_morning_report, due, late=True)
    assert len(bot.sent) == 2                                # really missed: sent once
    # A clock step forward smaller than the grace period plans no catch-up at all.
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    now = _local(9, 3)
    await set_state("last_run:morning_report", (now - timedelta(days=1)).isoformat())
    await scheduler.catch_up_missed(now, last_alive=now - timedelta(minutes=5), min_late=timedelta(seconds=600))
    assert "morning_report" not in _catch_ups(sched)


async def test_daily_jobs_mark_when_they_ran(bot, db, monkeypatch):
    async def snapshot(report_type, extras):
        return "🌅 Доброе утро"
    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    await scheduler.send_morning_report()
    assert await get_state("last_run:morning_report")


async def test_one_check_runs_once_at_a_time(bot, db, monkeypatch):
    runs = []

    async def slow(urls, manage=True):
        runs.append(1)
        await asyncio.sleep(0.2)
        return []
    monkeypatch.setattr(scheduler, "check_all_mail", slow)
    await asyncio.gather(scheduler.run_mail_checks(), scheduler.run_mail_checks())  # «mail_checks» + «_soon»
    assert len(runs) == 1


async def test_a_clock_step_back_does_not_freeze_the_checks(db, monkeypatch):
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    sched.start(paused=True)
    try:
        frozen = datetime.now(UTC) + timedelta(minutes=61)    # what a step back of an hour leaves behind
        for job in sched.get_jobs():
            if isinstance(job.trigger, IntervalTrigger):
                job.modify(next_run_time=frozen)
        scheduler.clock_stepped(-3600)
        within_a_minute = datetime.now(UTC) + timedelta(seconds=61)   # the one-minute cadence is back
        assert sched.get_job("availability_checks").next_run_time <= within_a_minute
        assert sched.get_job("outbox_flush").next_run_time <= within_a_minute
    finally:
        sched.shutdown(wait=False)


async def test_the_downtime_note_repeats_the_monitor_not_one_probe(bot, db, monkeypatch):
    a = await db.activate_or_create_site("https://a.test")
    b = await db.activate_or_create_site("https://b.test")
    c = await db.activate_or_create_site("https://c.test")
    await save_incident(a, "availability", "Site down: HTTP 502", "critical")
    await db.save_check(b, "availability", "error", details="Timeout (15s)")
    await db.save_check(c, "availability", "ok", response_time_ms=100)

    async def no_probe(*args, **kwargs):
        raise AssertionError("the note must not probe the sites again")
    monkeypatch.setattr(scheduler, "check_all", no_probe)
    await scheduler._report_downtime(datetime.now(UTC) - timedelta(minutes=30), datetime.now(UTC))
    text = bot.sent[-1]["text"]
    assert "🔴 Сейчас недоступно: a.test." in text and "⚠️ С первой попытки не открылись: b.test" in text
    assert "c.test" not in text


async def test_the_audits_come_before_the_morning_report(db):
    await settings.set_morning_hour(7)
    specs = scheduler._cron_specs()
    for job_id in ("seo_checks", "index_checks", "card_pictures", "ssl_checks", "domain_checks", "visitor_checks"):
        assert specs[job_id]["hour"] * 60 + specs[job_id]["minute"] < 7 * 60, job_id
    assert specs["seo_checks"] == {"hour": 5, "minute": 30}
    assert scheduler.audit_time("visitor_checks") == "06:10"
    await settings.set_morning_hour(9)
    assert scheduler._cron_specs()["seo_checks"] == {"hour": 7, "minute": 30}   # 9:00 keeps the old 07:30
