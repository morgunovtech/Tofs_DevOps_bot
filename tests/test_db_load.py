"""What the database costs per check and what it keeps: one transaction per
availability check, uptime from the index alone, a bounded quiet queue,
retention of everything nothing reads, no picture of a removed site."""

import os
import re

import aiosqlite
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall

from db import database
from handlers import sites as site_handlers
from monitors.availability import check_availability
from services import notifier, screenshots
from web.webhook import create_app


async def test_a_check_of_a_known_site_is_one_commit(db, monkeypatch):
    async def ok(request):
        return web.Response(text="<html>ok</html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", ok)
    async with TestServer(app) as server:
        url = str(server.make_url("/"))
        await db.get_or_create_site(url)
        commits = 0
        real_commit = aiosqlite.Connection.commit

        async def counting(self):
            nonlocal commits
            commits += 1
            return await real_commit(self)

        monkeypatch.setattr(aiosqlite.Connection, "commit", counting)
        r = await check_availability(url, manage=False)
    assert r.ok
    assert commits == 1          # was 3: site upsert, history row, snapshot
    assert (await db.get_last_check(r.site_id, "availability"))["status"] == "ok"
    assert (await db.get_state(f"status:{r.site_id}")) and "avail" in await db.get_state(f"status:{r.site_id}")


async def test_existing_site_lookup_writes_nothing(db):
    first = await db.get_or_create_site("https://a.example")
    for _ in range(10):
        assert await db.get_or_create_site("https://a.example") == first
    assert await db.get_or_create_site("https://b.example") == first + 1   # no id burned


async def test_quiet_queue_keeps_the_newest_and_says_how_many_did_not_fit(bot):
    for i in range(database.PENDING_MAX + 50):
        await database.queue_notification(f"m{i}")
    rows = await database.peek_notifications()
    assert len(rows) == database.PENDING_MAX and rows[0]["text"] == "m50"
    await notifier.flush_queue()
    assert "ещё 50 старых сообщений не сохранил" in bot.sent[0]["text"]
    assert await database.peek_notifications() == []
    await database.queue_notification("later")
    await notifier.flush_queue()
    assert "не сохранил" not in bot.sent[-1]["text"]          # said once


async def test_dropped_count_waits_for_a_digest_that_got_through(bot):
    for i in range(database.PENDING_MAX + 1):
        await database.queue_notification(f"m{i}")
    bot.fail = True
    await notifier.flush_queue()                              # Telegram took nothing
    assert bot.sent == [] and await database.get_state(database.PENDING_DROPPED) == "1"
    bot.fail = False
    await notifier.flush_queue()
    assert "ещё 1 старое сообщение не сохранил" in bot.sent[0]["text"]
    assert await database.get_state(database.PENDING_DROPPED) is None


async def test_retention_deletes_what_nothing_reads(db):
    sid = await db.get_or_create_site("https://example.com")
    conn = await db.get_db()
    await conn.execute("INSERT INTO feedback (message, ip_address, created_at) "
                       "VALUES ('old', '1.2.3.4', datetime('now', '-120 days')), ('new', '5.6.7.8', datetime('now'))")
    await conn.execute("INSERT INTO incidents (site_id, check_type, message, resolved, created_at, resolved_at) VALUES "
                       "(?, 'ssl', 'old resolved', 1, datetime('now', '-500 days'), datetime('now', '-400 days')), "
                       "(?, 'ssl', 'old but open', 0, datetime('now', '-500 days'), NULL)", (sid, sid))
    await conn.execute("INSERT INTO checks_daily (site_id, check_type, day, total, ok, sum_ms) VALUES "
                       "(?, 'availability', date('now', '-500 days'), 1, 1, 10), "
                       "(?, 'availability', date('now', '-10 days'), 1, 1, 10)", (sid, sid))
    await conn.commit()
    assert await db.purge_old_rows() == {"feedback": 1, "incidents": 1, "checks_daily": 1}
    assert [r["message"] for r in await db.list_feedback()] == ["new"]
    assert [i["message"] for i in await db.get_active_incidents()] == ["old but open"]
    assert await db.purge_old_rows() == {"feedback": 0, "incidents": 0, "checks_daily": 0}


_READS_CHECKS = re.compile(r"^(SEARCH|SCAN) (TABLE )?(checks|c)( |$)")   # `checks` or its alias, not checks_daily


async def test_uptime_queries_read_only_the_index(db):
    sid = await db.get_or_create_site("https://example.com")
    await db.save_check(sid, "availability", "ok", response_time_ms=100)
    conn = await db.get_db()
    seen: list[str] = []
    await conn.set_trace_callback(seen.append)
    await db.get_uptime_stats(sid, hours=24)
    await db.get_uptime_over_days(sid, 30)
    await db.last_failed_check_at(sid)
    await conn.set_trace_callback(None)
    steps = []
    for q in seen:
        for row in await (await conn.execute("EXPLAIN QUERY PLAN " + q)).fetchall():
            if _READS_CHECKS.match(row[3]):
                steps.append(row[3])
    assert len(steps) >= 3                       # all three uptime queries read `checks`
    assert all("COVERING INDEX idx_checks_site_type" in s for s in steps), steps


async def test_the_index_migration_keeps_its_name_and_covers_the_uptime_columns(db):
    conn = await db.get_db()
    cols = [r[2] for r in await (await conn.execute("PRAGMA index_info(idx_checks_site_type)")).fetchall()]
    assert cols == ["site_id", "check_type", "checked_at", "status", "response_time_ms"]


async def test_removing_a_site_removes_its_picture(bot, db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    sid = await db.activate_or_create_site("https://ex.test")
    path = screenshots.shot_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG fake")
    await site_handlers.cb_site_del_do(FakeCall(f"delok:{sid}"))
    assert not os.path.exists(path)
    assert await db.get_state(f"status:{sid}") is None          # forget() leaves no empty snapshot behind
    async with TestClient(TestServer(create_app())) as c:
        assert (await c.get(f"/shot/{screenshots._sig(sid)}/{sid}")).status == 404


async def test_pictures_of_sites_no_longer_watched_are_pruned(db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    for sid in (3, 4):
        os.makedirs(os.path.dirname(screenshots.shot_path(sid)), exist_ok=True)
        with open(screenshots.shot_path(sid), "wb") as f:
            f.write(b"\x89PNG")
    screenshots.prune({3})
    assert os.path.exists(screenshots.shot_path(3)) and not os.path.exists(screenshots.shot_path(4))
    screenshots.prune(set())                                     # idempotent; a missing folder is fine too
    cfg(db_path=str(tmp_path / "elsewhere" / "bot.db"))
    screenshots.prune(set())


async def test_a_failed_picture_write_keeps_yesterdays_picture(db, cfg, tmp_path, monkeypatch):
    cfg(db_path=str(tmp_path / "bot.db"))
    path = screenshots.shot_path(7)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG yesterday")

    async def shot(url):
        return b"\x89PNG today" + b"x" * 4096, None

    def disk_full(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(screenshots, "fetch_screenshot", shot)
    monkeypatch.setattr(screenshots.os, "replace", disk_full)
    assert await screenshots.refresh(7, "https://ex.test") is False
    with open(path, "rb") as f:
        assert f.read() == b"\x89PNG yesterday"
    assert not os.path.exists(path + ".tmp")                     # no half-written file left behind
    assert "shot" not in await screenshots.sitestatus.get(7)     # not recorded as taken either
