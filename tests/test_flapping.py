"""Flapping and escalation: planned checks only, the share of failures,
reopening instead of a new ringing incident, a growing pause between
reminders and «👀 Знаю, не напоминать»."""

from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall

from db.database import get_active_incidents, get_incident, get_state, save_incident
from handlers import incidents
from monitors.availability import check_availability, down_verdict, recovery_streak, up_verdict
from reports import scheduler
from services import helpers


def test_verdicts():
    assert down_verdict(["error", "error"], 2) is True
    assert down_verdict(["error", "ok", "error", "ok", "error", "ok"], 2) == (3, 6)   # every other check
    assert down_verdict(["error", "ok", "ok", "ok", "ok", "ok"], 2) is False
    assert down_verdict(["error", "ok"] * 3, 5) is False          # threshold 5 looks at 10 checks
    assert up_verdict(["ok", "error"]) is False and up_verdict(["ok", "ok", "error"]) is True
    assert up_verdict(["ok", "error"], 1) is True                 # a rarely checked site: one is enough
    assert recovery_streak(5) == 2 and recovery_streak(30) == 1 and recovery_streak(60) == 1


def _flaky_site():
    state = {"code": 500}

    async def handler(_):
        return web.Response(status=state["code"], text="x")

    app = web.Application()
    app.router.add_get("/", handler)
    return TestServer(app), state


async def test_a_manual_check_does_not_count_toward_the_threshold(db):
    server, _ = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        await check_availability(url, manage=False)                 # «🔄 Проверить» a second before
        assert not (await check_availability(url)).incident_new     # 1 planned failure of 2
        assert (await check_availability(url)).incident_new


async def test_every_other_check_failing_opens_an_incident(db):
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        results = []
        for code in (500, 200, 500, 200, 500, 200, 500):
            state["code"] = code
            results.append(await check_availability(url))
        assert [r.incident_new for r in results] == [False] * 6 + [True]
        assert results[-1].failed_of == (3, 6)


async def test_a_rarely_checked_site_is_up_on_the_first_success(db):
    """Interval ≥ 30 min: waiting for a second success would call the site
    down for an hour after it is back."""
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(url)
        await db.update_site_settings(sid, check_interval_min=60)
        for _ in range(2):
            await check_availability(url)
        assert len(await get_active_incidents()) == 1
        state["code"] = 200
        r = await check_availability(url)
        assert r.recovered and not await get_active_incidents()


async def test_the_downtime_ends_with_the_first_good_answer(db):
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(url)
        for _ in range(2):
            await check_availability(url)
        state["code"] = 200
        assert not (await check_availability(url)).recovered
        conn = await db.get_db()                                   # all of that was 10 minutes ago
        await conn.execute("UPDATE checks SET checked_at = datetime('now', '-10 minutes') WHERE site_id = ?",
                           (sid,))
        await conn.commit()
        r = await check_availability(url)
    assert r.recovered
    ended = datetime.strptime(r.resolved_incident["resolved_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert timedelta(minutes=9) < datetime.now(UTC) - ended < timedelta(minutes=11)
    assert (await get_incident(r.resolved_incident["id"]))["resolved_at"] == r.resolved_incident["resolved_at"]


async def _tick(state, code):
    state["code"] = code
    scheduler._next_avail_check.clear()
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()


async def test_a_flap_reopens_quietly_once_an_hour(bot, db):
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        await db.activate_or_create_site(url)
        for code in (500, 500):
            await _tick(state, code)
        first = (await get_active_incidents())[0]["id"]
        rings = [m for m in bot.sent if not m["silent"]]
        assert len(rings) == 1 and "не открывается" in rings[0]["text"]
        assert "Проверил дважды подряд" in rings[0]["text"] and "каждые 5 мин" in rings[0]["text"]
        await _tick(state, 200)
        assert len(await get_active_incidents()) == 1                # one good answer is not «up»
        await _tick(state, 200)
        assert "снова открывается" in bot.sent[-1]["text"] and not await get_active_incidents()
        for code in (500, 500, 200, 200, 500, 500):                  # falls again within 30 minutes, twice
            await _tick(state, code)
        assert (await get_active_incidents())[0]["id"] == first     # the same incident, reopened
        flaps = [m for m in bot.sent if "мигает" in m["text"]]
        assert len(flaps) == 1 and flaps[0]["silent"] and "2 падения за час" in flaps[0]["text"]
        assert len([m for m in bot.sent if not m["silent"]]) == 2   # the first 🔴 and the first ✅ only
        assert len(await db.get_incidents_since(1)) == 1             # one incident in the history, not three


class _Clock(datetime):
    now_value = datetime.now(UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.now_value


async def test_escalation_backs_off_and_stops_on_quiet_ack(bot, db, cfg, monkeypatch):
    cfg(escalation_repeat_min=30)
    await helpers.accept(await helpers.create_invite(), chat_id=555, name="Друг")
    sid = await db.get_or_create_site("https://down.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    start = datetime.now(UTC)
    monkeypatch.setattr(scheduler, "datetime", _Clock)
    sent_at = []
    for minute in range(0, 6 * 60 + 1, 5):                           # the watch runs every 5 minutes
        _Clock.now_value = start + timedelta(minutes=minute)
        n = len(bot.sent)
        await scheduler.run_escalation_watch()
        if any(str(m["chat_id"]) == "777" for m in bot.sent[n:]):
            sent_at.append(minute)
    assert sent_at == [30, 90, 210]                                  # was every 30 minutes: 12 in 6 hours
    assert len([m for m in bot.sent if str(m["chat_id"]) == "555"]) == 1   # helpers get the first one only
    assert "лежит уже 30 мин" in bot.sent[0]["text"].split("\n")[0]
    assert "ошибкой 502" in bot.sent[0]["text"] and "Проверяю каждые 5 мин" in bot.sent[0]["text"]
    labels = [b.text for row in bot.sent[0]["reply_markup"].inline_keyboard for b in row]
    assert "👀 Знаю, не напоминать" in labels and "🔧 Я чиню, час тишины" in labels
    call = FakeCall(f"inc_quiet:{inc_id}:a")
    await incidents.cb_incident_quiet(call)
    assert "больше не напоминаю" in call.message.texts[-1]
    assert await get_state(f"ack:{inc_id}") == scheduler.ACK_UNTIL_UP
    n = len(bot.sent)
    _Clock.now_value = start + timedelta(hours=12)
    await scheduler.run_escalation_watch()
    assert len(bot.sent) == n


async def test_escalation_in_quiet_hours_waits_two_hours(cfg, monkeypatch):
    assert [scheduler.escalation_step(n, 30) for n in range(5)] == [30, 60, 120, 240, 240]
    monkeypatch.setattr(scheduler.notifier, "in_quiet_hours", lambda hour=None: True)
    assert [scheduler.escalation_step(n, 30) for n in range(4)] == [120, 120, 120, 240]


async def test_escalation_resumes_after_a_flap_settles_into_downtime(bot, db, cfg, monkeypatch):
    """One reopen must not switch the reminders off for good: a site that
    flapped once and then lies for hours is a dead site again."""
    cfg(escalation_repeat_min=30)
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        await db.activate_or_create_site(url)
        for code in (500, 500, 200, 200, 500, 500):                  # fell, came back, fell again: reopened
            await _tick(state, code)
        inc = (await get_active_incidents())[0]
        assert await get_state(f"flap:{inc['id']}")
        fell_again = datetime.now(UTC)
        start = fell_again
        monkeypatch.setattr(scheduler, "datetime", _Clock)
        sent_at = []
        for minute in range(0, 6 * 60 + 1, 5):                       # then 6 hours of solid downtime
            _Clock.now_value = start + timedelta(minutes=minute)
            n = len(bot.sent)
            await scheduler.run_escalation_watch()
            sent_at += [minute for m in bot.sent[n:] if "лежит уже" in m["text"]]
        assert sent_at and sent_at[0] <= 60                          # within an hour after the last fall
        assert len(sent_at) >= 3                                     # and it keeps reminding, backing off
        _Clock.now_value = start + timedelta(hours=7)
        for code in (200, 200):                                      # the recovery after that rings
            await _tick(state, code)
    assert "снова открывается" in bot.sent[-1]["text"] and not bot.sent[-1]["silent"]


async def test_a_site_that_answered_is_not_escalated(bot, db, cfg):
    """Between the first and the second planned success the incident is
    still open — a ringing «лежит уже» about a site that answers would be
    a false alarm."""
    cfg(escalation_repeat_min=30)
    server, state = _flaky_site()
    async with server:
        url = str(server.make_url("")).rstrip("/")
        await db.activate_or_create_site(url)
        for code in (500, 500, 200):
            await _tick(state, code)
        assert len(await get_active_incidents()) == 1
        conn = await db.get_db()
        await conn.execute("UPDATE incidents SET created_at = datetime('now', '-40 minutes'), "
                           "alerted_at = datetime('now', '-40 minutes')")
        await conn.commit()
        n = len(bot.sent)
        await scheduler.run_escalation_watch()
        assert len(bot.sent) == n
        await _tick(state, 500)                                      # it fell again: the reminder comes
        await scheduler.run_escalation_watch()
        assert len(bot.sent) == n + 1 and "лежит уже" in bot.sent[-1]["text"]
