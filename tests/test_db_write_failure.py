"""A database that refuses writes (full disk, «database is locked») must not
silence the monitoring: the verdict, the alert and the undelivered messages
live on in memory, and the owner hears that the database is in trouble."""

import contextlib
import logging
import os
import sqlite3

from aiohttp import web
from aiohttp.test_utils import TestServer

from db import database
from db.database import outbox_peek
from monitors import host_checker
from monitors.base import gather_checks
from reports import scheduler
from services import health, notifier
from services.notifier import Priority

_WRITES = ("INSERT", "UPDATE", "DELETE", "REPLACE")


class _RefusingWrites:
    """The shared connection as on a full volume: reads work, writes raise."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, sql, *args, **kwargs):
        if sql.lstrip().upper().startswith(_WRITES):
            raise sqlite3.OperationalError("database or disk is full")
        return await self._conn.execute(sql, *args, **kwargs)

    async def executemany(self, sql, *args, **kwargs):
        raise sqlite3.OperationalError("database or disk is full")


@contextlib.contextmanager
def _refusing_writes():
    real = database._conn
    database._conn = _RefusingWrites(real)
    try:
        yield
    finally:
        database._conn = real


async def _server(state: dict):
    async def handler(_):
        return web.Response(status=state["status"], text="<html>hi</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/", handler)
    return TestServer(app)


async def _tick():
    scheduler._next_avail_check.clear()
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()


async def test_site_down_is_alerted_while_the_database_refuses_writes(bot, db):
    state = {"status": 500}
    async with await _server(state) as server:
        url = str(server.make_url("")).rstrip("/")
        await db.activate_or_create_site(url)
        with _refusing_writes():
            await _tick()
            assert bot.sent == []                                  # one failure is not an alert yet
            await _tick()
            alerts = [m for m in bot.sent if "не открывается" in m["text"]]
            assert len(alerts) == 1 and alerts[0]["silent"] is False
            await _tick()
            assert len([m for m in bot.sent if "не открывается" in m["text"]]) == 1   # said once
            state["status"] = 200
            await _tick()
            assert "снова открывается" in bot.sent[-1]["text"]


async def test_a_check_starts_without_writing(db):
    await db.get_or_create_site("https://a.test")
    with _refusing_writes():
        assert (await db.get_site_by_url("https://a.test"))["url"] == "https://a.test"


async def test_telegram_away_and_database_full_message_waits_in_memory(bot, db):
    bot.fail = True
    with _refusing_writes():
        assert await notifier.send("🔴 a.test не открывается", Priority.CRITICAL) is True
        assert bot.sent == []
        bot.fail = False
        await notifier.flush_outbox()
        await notifier.flush_outbox()
    assert len(bot.sent) == 1
    assert bot.sent[0]["text"].startswith("⏳ Задержалось с ") and "a.test не открывается" in bot.sent[0]["text"]


async def test_quiet_queue_refused_delivers_now_silently(bot, db, monkeypatch):
    monkeypatch.setattr(notifier, "in_quiet_hours", lambda hour=None: True)
    with _refusing_writes():
        assert await notifier.send("🟠 копия базы не сохранилась", Priority.NORMAL)
    assert bot.sent[-1]["text"] == "🟠 копия базы не сохранилась" and bot.sent[-1]["silent"] is True


async def test_outbox_row_is_not_sent_twice_when_its_delete_fails(bot, db, monkeypatch):
    monkeypatch.setattr(notifier, "FLUSH_PACE_SEC", 0)
    bot.fail = True
    await notifier.send("🔴 b.test не открывается", Priority.CRITICAL)      # an outbox row
    bot.fail = False
    with _refusing_writes():
        await notifier.flush_outbox()
        await notifier.flush_outbox()
    health.reset()                                                         # writes work again
    await notifier.flush_outbox()
    assert len(bot.sent) == 1 and await outbox_peek() == []


async def test_database_trouble_is_said_once_an_hour_then_once_fixed(bot, db, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(health, "_clock", lambda: now[0])
    with _refusing_writes():
        await scheduler.run_alive()
        await scheduler.run_alive()
    assert len(bot.sent) == 1 and bot.sent[0]["silent"] is False
    assert bot.sent[0]["text"].startswith("🔴 Не могу записать в базу: на диске с базой кончилось место")
    now[0] += health.DB_ALERT_EVERY_SEC + 1
    with _refusing_writes():
        await scheduler.run_alive()
    assert len(bot.sent) == 2                                              # an hour later, once more
    await scheduler.run_alive()                                            # works, but just failed
    assert len(bot.sent) == 2
    now[0] += health.FAILING_FOR_SEC + 1
    await scheduler.run_alive()
    assert bot.sent[-1]["text"].startswith("✅ База снова записывается")
    await scheduler.run_alive()
    assert len(bot.sent) == 3


async def test_database_volume_is_watched_and_escalates_to_ringing(bot, db, cfg, monkeypatch):
    db_dir = os.path.dirname(os.path.abspath(database.db_file()))
    usage = {"/": (6, 6.0, 100.0), db_dir: (86, 0.86, 1.0)}
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": usage[path])
    cfg(auto_cleanup=False)
    await scheduler.run_host_checks()
    assert bot.sent[-1]["text"].startswith("🟠 Диск с базой бота заполнен на 86%")
    assert bot.sent[-1]["silent"] is True
    usage[db_dir] = (97, 0.97, 1.0)
    await scheduler.run_host_checks()
    assert bot.sent[-1]["text"].startswith("🔴 Диск с базой бота заполнен на 97%")
    assert bot.sent[-1]["silent"] is False
    n = len(bot.sent)
    await scheduler.run_host_checks()
    assert len(bot.sent) == n


async def test_disk_level_is_remembered_when_the_flag_cannot_be_written(bot, db, cfg, monkeypatch):
    """The flag lives on the full volume: without memory 🔴 would ring every half hour."""
    db_dir = os.path.dirname(os.path.abspath(database.db_file()))
    usage = {"/": (6, 6.0, 100.0), db_dir: (97, 0.97, 1.0)}
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": usage[path])
    cfg(auto_cleanup=False)
    with _refusing_writes():
        await scheduler.run_host_checks()
        await scheduler.run_host_checks()
    assert len([m for m in bot.sent if "заполнен на 97%" in m["text"]]) == 1


def test_disk_percent_counts_like_df(monkeypatch):
    usage = type("U", (), {"total": 100, "used": 90, "free": 5})()       # 5 blocks reserved for root
    monkeypatch.setattr(host_checker.shutil, "disk_usage", lambda path: usage)
    assert host_checker.disk_usage_pct("/")[0] == 95                     # df: 90 / (90 + 5)


async def test_crashed_check_is_logged_with_the_site_and_the_traceback(caplog):
    async def check(url, **kwargs):
        if "bad" in url:
            raise ValueError("Invalid IPv6 URL")
        return url

    with caplog.at_level(logging.ERROR, logger="monitors.base"):
        out = await gather_checks(["https://ok.test", "https://bad.test/x?token=1"], check, "links")
    assert out == ["https://ok.test"]
    record = caplog.records[-1]
    assert "bad.test/x" in record.getMessage() and "token" not in record.getMessage()
    assert record.exc_info and record.exc_info[0] is ValueError
