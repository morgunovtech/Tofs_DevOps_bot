"""Dead-man switch: silence is counted from the right moment (the last mark
or the job's addition, less the bot's own downtime), a re-added job starts
clean, only known jobs with their own token are marked, and what a bot
sends is checked and shown with its age."""

import json
from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import get_heartbeats, get_state, set_state
from handlers import heartbeats as hb_screens
from reports import scheduler
from services import heartbeats, public_urls, secrets, settings
from utils.text import parse_iso_utc
from web.webhook import create_app


async def _client():
    return TestClient(TestServer(create_app()))


async def _marked(db, job: str, ago: timedelta):
    conn = await db.get_db()
    await conn.execute("INSERT INTO heartbeats (job, last_ping) VALUES (?, ?)",
                       (job, (datetime.now(UTC) - ago).strftime("%Y-%m-%d %H:%M:%S")))
    await conn.commit()


async def _bot_down(start: timedelta, end: timedelta):
    """The bot's last downtime: from `start` ago until `end` ago."""
    now = datetime.now(UTC)
    await heartbeats.note_downtime(now - start, now - end)


async def test_a_job_added_today_is_not_silent_since_the_bots_start(bot, db):
    await settings.add_heartbeat_job("nightly-backup", 1440)
    await scheduler.run_heartbeat_watch()
    assert bot.sent == []                                            # not «так и не выходит на связь»
    [job] = await heartbeats.job_states()
    assert job.icon == "⏳"
    # Added long ago and never marked: now it is the job's silence.
    await set_state("hb_added:nightly-backup", (datetime.now(UTC) - timedelta(days=2)).isoformat())
    await scheduler.run_heartbeat_watch()
    assert bot.sent and "так и не выходит на связь" in bot.sent[-1]["text"]


async def test_a_job_from_the_server_variable_counts_from_when_the_bot_first_saw_it(bot, db, cfg):
    cfg(heartbeat_jobs={"env-job": 60})
    await scheduler.run_heartbeat_watch()                            # first sight: added now
    assert bot.sent == []
    added = parse_iso_utc(await get_state("hb_added:env-job"))
    assert added and datetime.now(UTC) - added < timedelta(minutes=1)


async def test_the_bots_own_downtime_is_not_the_jobs_silence(bot, db):
    """CR-1012(б): the bot was down 2.5 h, the hourly job last marked 2 h 50 min
    ago — 20 minutes of silence the bot could hear, not 170."""
    await settings.add_heartbeat_job("hourly-sync", 60)
    await set_state("hb_added:hourly-sync", (datetime.now(UTC) - timedelta(days=5)).isoformat())
    await _marked(db, "hourly-sync", timedelta(minutes=170))
    await _bot_down(timedelta(minutes=160), timedelta(minutes=10))   # back after 2.5 h
    await scheduler.run_heartbeat_watch()
    assert bot.sent == []
    [job] = await heartbeats.job_states()
    assert job.icon == "✅" and "2 ч 50 мин назад" in job.state
    await set_state("bot_down", None)                                # it was running all along
    await scheduler.run_heartbeat_watch()
    assert bot.sent and "молчит" in bot.sent[-1]["text"] and "2 ч 50 мин назад" in bot.sent[-1]["text"]
    [job] = await heartbeats.job_states()
    assert job.icon == "🟠" and job.state == "молчит 2 ч 50 мин"


async def test_a_dead_weekly_job_is_announced_despite_a_redeploy(bot, db):
    """A weekly job silent for 9 days; the bot restarted 2 days ago after a
    2-minute redeploy. Only those 2 minutes are forgiven."""
    await settings.add_heartbeat_job("weekly-report", 10080)
    await set_state("hb_added:weekly-report", (datetime.now(UTC) - timedelta(days=30)).isoformat())
    await _marked(db, "weekly-report", timedelta(days=9))
    await _bot_down(timedelta(days=2, minutes=2), timedelta(days=2))
    [job] = await heartbeats.job_states()
    assert job.icon == "🟠" and job.state == "молчит 9 дн"
    await scheduler.run_heartbeat_watch()
    assert bot.sent and bot.sent[-1]["text"].startswith("🟠 «weekly-report» молчит")
    assert await get_state("hb_alerted:weekly-report")


async def test_a_restart_does_not_forgive_a_job_already_known_to_be_silent(bot, db, monkeypatch):
    """A daily job silent 2 days, alerted. A one-minute redeploy: still 🟠,
    the alert flag stays — nothing is counted from the bot's start."""
    await settings.add_heartbeat_job("nightly-backup", 1440)
    await set_state("hb_added:nightly-backup", (datetime.now(UTC) - timedelta(days=30)).isoformat())
    await _marked(db, "nightly-backup", timedelta(days=2))
    await scheduler.run_heartbeat_watch()
    assert len(bot.sent) == 1 and await get_state("hb_alerted:nightly-backup")
    # The redeploy as the bot records it at startup: last alive 2 min ago, starting now.
    monkeypatch.setattr(scheduler, "_scheduler", None)
    await set_state("alive_at", (datetime.now(UTC) - timedelta(minutes=2)).isoformat())
    await scheduler.catch_up_after_restart()
    down = await heartbeats.last_downtime()
    assert down and timedelta(seconds=110) < down[1] - down[0] < timedelta(seconds=130)
    [job] = await heartbeats.job_states()
    assert job.icon == "🟠" and job.state == "молчит 2 дн"
    await scheduler.run_heartbeat_watch()
    assert len(bot.sent) == 1 and await get_state("hb_alerted:nightly-backup")   # not cleared, not repeated
    # A broken record is ignored, not a crash.
    await set_state("bot_down", json.dumps(["garbage", 5]))
    assert await heartbeats.last_downtime() is None
    await set_state("bot_down", "not json")
    assert await heartbeats.last_downtime() is None


async def test_a_job_added_again_does_not_inherit_the_old_mark(bot, db):
    await settings.add_heartbeat_job("backup", 1440)
    await _marked(db, "backup", timedelta(days=3))
    await set_state("hb_legacy:backup", "1")
    await set_state("hb_alerted:backup", "1")
    await settings.remove_heartbeat_job("backup")
    assert "backup" not in await get_heartbeats()
    assert await get_state("hb_legacy:backup") is None and await get_state("hb_alerted:backup") is None
    await settings.add_heartbeat_job("backup", 1440)
    [job] = await heartbeats.job_states()
    assert job.icon == "⏳"
    await scheduler.run_heartbeat_watch()
    assert bot.sent == []


async def test_only_a_known_job_with_its_own_token_is_marked(bot, db):
    await settings.add_heartbeat_job("backup", 1440)
    await settings.add_heartbeat_job("db-dump", 1440)
    backup = secrets.heartbeat_token("backup")
    async with await _client() as c:
        assert (await c.get(f"/api/heartbeat/{backup}/db-dump")).status == 403      # one leaked line ≠ every job
        assert (await c.head(f"/api/heartbeat/{backup}/backup")).status == 405       # a link preview is not a run
        assert "backup" not in await get_heartbeats()
        assert (await c.get(f"/api/heartbeat/{backup}/backup")).status == 200
        assert "backup" in await get_heartbeats()
        shared = secrets.heartbeat_secret()
        assert (await c.get(f"/api/heartbeat/{shared}/typo-bakup")).status == 404    # cron learns about the typo
        assert "typo-bakup" not in await get_heartbeats()
        assert (await c.get(f"/api/heartbeat/{backup}/typo-bakup")).status == 403    # …but only with a secret
        assert (await c.get(f"/api/heartbeat/{shared}/db-dump")).status == 200       # old address: still works…
        assert "старому адресу" in bot.sent[-1]["text"]                              # …and the owner hears it once
        n = len(bot.sent)
        assert (await c.get(f"/api/heartbeat/{shared}/db-dump")).status == 200
        assert len(bot.sent) == n and await get_state("hb_legacy:db-dump")
        screen = await hb_screens.job_screen("db-dump")
        assert screen and "по старому адресу" in screen[0]
        assert (await c.get(f"/api/heartbeat/{secrets.heartbeat_token('db-dump')}/db-dump")).status == 200
        assert await get_state("hb_legacy:db-dump") is None                         # moved to its own address
        screen = await hb_screens.job_screen("db-dump")
        assert screen and "по старому адресу" not in screen[0]
    # Rotating the secret changes every job's token.
    await secrets.regenerate("heartbeat")
    assert secrets.heartbeat_token("backup") != backup
    async with await _client() as c:
        assert (await c.get(f"/api/heartbeat/{backup}/backup")).status == 403


async def test_links_are_percent_encoded(db, monkeypatch):
    monkeypatch.setattr(settings, "heartbeat_jobs", lambda: {"nightly backup": 1440})
    url = public_urls.heartbeat_url("nightly backup")
    assert url.endswith("/nightly%20backup") and " " not in url
    monkeypatch.setattr(public_urls.integrations, "status_page_slug", lambda: "team/ops")
    assert public_urls.status_page_url().endswith("/status/team%2Fops")
    async with await _client() as c:
        assert (await c.get(url.split("bot.example.test", 1)[1])).status == 200
    assert "nightly backup" in await get_heartbeats()


async def test_a_bad_level_or_link_is_a_400_not_a_500(bot, db):
    await settings.add_heartbeat_job("socialbot", 1440)
    token = secrets.heartbeat_token("socialbot")
    async with await _client() as c:
        for level in ([], {}, ["ok"]):
            r = await c.post(f"/api/heartbeat/{token}/socialbot", json={"items": [{"name": "x", "level": level}]})
            assert r.status == 400 and "level" in await r.text()
        for link in ("https://", "https://a b", "javascript:alert(1)", "https://x\n.test"):
            r = await c.post(f"/api/heartbeat/{token}/socialbot",
                             json={"items": [{"name": "x"}], "open_url": link})
            assert r.status == 400 and "open_url" in await r.text()
    assert heartbeats.link_host("https://t.me/some_bot?start=1") == "t.me"


async def test_old_status_lines_are_not_shown_as_current(db):
    await settings.add_heartbeat_job("socialbot", 1440)
    job = heartbeats.Job("socialbot", "✅", "на связи", 1440, 5)
    old = {"items": [{"name": "Канал", "level": "ok", "text": "12 340"}],
           "at": (datetime.now(UTC) - timedelta(days=3)).isoformat()}
    [(icon, _, text)] = heartbeats.status_items(job, old)
    assert icon == "⚠️" and "устарели" in text and "3 дн назад" in text
    fresh = {**old, "at": datetime.now(UTC).isoformat()}
    assert heartbeats.status_items(job, fresh) == [("✅", "Канал", "12 340")]


async def test_adding_an_existing_name_or_a_bad_interval_changes_nothing(bot, db):
    await settings.add_heartbeat_job("backup", 1440)
    state = FakeState()
    await state.set_state(hb_screens.AddHeartbeatForm.name)
    msg = FakeMessage("backup")
    await hb_screens.msg_hb_name(msg, state)
    assert "уже есть" in msg.texts[-1] and settings.heartbeat_jobs() == {"backup": 1440}
    assert state.state is None and "Открыть её" in buttons(msg.reply_markup)
    await state.set_state(hb_screens.AddHeartbeatForm.name)
    await state.update_data(hb_name="nightly")
    call = FakeCall("hbint:0")
    await hb_screens.cb_hb_interval(call, state)
    assert "Не добавил" in call.answers[-1] and "nightly" not in settings.heartbeat_jobs()
    other = FakeState()                                              # an old interval button, another input open
    await other.set_state("SomeOtherForm:url")
    await hb_screens.cb_hb_interval(FakeCall("hbint:60"), other)
    assert other.state == "SomeOtherForm:url" and "nightly" not in settings.heartbeat_jobs()
    await state.set_state(hb_screens.AddHeartbeatForm.name)
    await state.update_data(hb_name="nightly")
    call = FakeCall("hbint:60")
    await hb_screens.cb_hb_interval(call, state)
    assert call.answers[-1] == "Добавил ✅" and settings.heartbeat_jobs()["nightly"] == 60


async def test_buttons_of_a_removed_job_say_so(bot, db):
    for data in ("hb_bot:ghost", "hb_cron:ghost", "hb_show:ghost", "hb_ask:ghost"):
        call = FakeCall(data)
        await {"hb_bot": hb_screens.cb_hb_bot, "hb_cron": hb_screens.cb_hb_cron,
               "hb_show": hb_screens.cb_hb_show, "hb_ask": hb_screens.cb_hb_ask}[data.split(":")[0]](call)
        assert call.answers[-1] == "Задачи больше нет" and call.message.texts == []
    assert await hb_screens.job_screen("ghost") is None


async def test_without_a_public_address_the_screen_says_the_link_will_not_work(bot, db, cfg):
    cfg(public_base_url="", railway_public_domain="")
    await settings.add_heartbeat_job("backup", 1440)
    call = FakeCall("hb_cron:backup")
    await hb_screens.cb_hb_cron(call)
    assert "нет публичного адреса" in call.message.texts[-1]
    assert "🌍 Как задать адрес" in buttons(call.message.reply_markup)
    call = FakeCall("hb_bot:backup")
    await hb_screens.cb_hb_bot(call)
    assert "нет публичного адреса" in call.message.texts[-1]
    assert await get_state("hb_alerted:backup") is None
