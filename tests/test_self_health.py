"""The bot's own «I am alive» signals tell the truth: /health, the external
watchdog's mark and the in-process watchdog go red when checks stall or
Telegram stops taking the bot's calls; a job that keeps failing is told."""

import asyncio
import os
import sys
from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe, SendMessage
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, JobExecutionEvent

import main
from reports import scheduler, weekly
from services import health, heartbeats, integrations, notifier
from web.webhook import create_app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def fresh_health():
    health.reset()
    scheduler._job_fails.clear()
    scheduler._job_told.clear()
    yield
    health.reset()


def _ago(seconds: float) -> float:
    return health._clock() - seconds


async def test_health_is_503_when_checks_stall_and_says_why_only_in_the_log(db, caplog):
    async with TestClient(TestServer(create_app())) as c:
        assert (await c.get("/health")).status == 200
        health._tick_done = _ago(6 * 60)
        r = await c.get("/health")
        assert r.status == 503 and await r.json() == {"status": "stale"}   # the public page keeps the details
        assert "checks stalled" in caplog.text
        health.tick_done()
        assert (await c.get("/health")).status == 200


async def test_a_finished_tick_is_marked_even_with_nothing_due(db):
    await scheduler.run_availability_checks()               # no sites at all
    assert health._tick_done is not None and not health.problems()


async def test_the_watchdog_is_not_marked_while_the_bot_cannot_work(db, monkeypatch):
    await integrations.set_value("self_heartbeat_url", "https://hc-ping.com/abc")
    pings = []

    async def ping(url):
        pings.append(url)
        return None
    monkeypatch.setattr(heartbeats, "ping_watchdog", ping)
    health._tick_done = _ago(10 * 60)                       # the tick hangs or keeps failing
    await scheduler.run_self_heartbeat()
    assert pings == []
    health.tick_done()
    health.telegram_refused()                               # the token was revoked
    await scheduler.run_self_heartbeat()
    assert pings == []
    health.telegram_ok()
    await scheduler.run_self_heartbeat()
    assert pings == ["https://hc-ping.com/abc"]
    await integrations.set_value("self_heartbeat_url", None)


async def test_in_process_watchdog_gives_a_reason_to_exit():
    health._tick_done = _ago(6 * 60)
    reason = await asyncio.wait_for(health.watchdog(every=0.01), timeout=2)
    assert "has not finished for 5 minutes" in reason
    health.tick_done()
    health._telegram_refused_since = _ago(11 * 60)
    assert "refused the bot token" in await asyncio.wait_for(health.watchdog(every=0.01), timeout=2)
    health._telegram_refused_since = _ago(2 * 60)           # a blip: not yet a reason
    assert health.fatal() is None


def test_a_self_check_failure_is_exit_code_3(monkeypatch):
    async def failing_main():
        raise main.SelfCheckFailed("the availability check has not finished for 5 minutes")
    monkeypatch.setattr(main, "main", failing_main)
    assert main.run() == 3


async def test_every_telegram_call_is_counted():
    watch = health.TelegramWatch()

    async def answer(bot, method):
        return "ok"

    def failing(exc):
        async def call(bot, method):
            raise exc
        return call

    with pytest.raises(TelegramUnauthorizedError):
        await watch(failing(TelegramUnauthorizedError(GetMe(), "Unauthorized")), None, GetMe())
    assert health._telegram_refused_since and "telegram refuses the token" in health.problems()
    with pytest.raises(TelegramNetworkError):
        await watch(failing(TelegramNetworkError(GetMe(), "timeout")), None, GetMe())
    assert health._telegram_ok is None                       # nobody answered
    assert await watch(answer, None, GetMe()) == "ok"
    assert health._telegram_ok and health._telegram_refused_since is None


async def test_a_bot_that_cannot_write_to_its_owner_is_not_healthy(bot, db):
    """Telegram answers every call, but refuses to deliver to the admin chat
    (blocked, thrown out of the group): the watchdog must see that too."""
    bot.fail_with = [TelegramForbiddenError(SendMessage(chat_id=1, text="x"), "Forbidden: bot was blocked")]
    assert await notifier.send("🔴 a.test", notifier.Priority.CRITICAL) is False
    assert health._delivery_failed_since is not None and "cannot deliver" not in health.problems()   # a blip so far
    health._delivery_failed_since = _ago(16 * 60)
    assert "cannot deliver to the owner" in health.problems()
    assert await notifier.send("✅ a.test", notifier.Priority.NORMAL)
    assert health._delivery_failed_since is None             # delivered again: the mark is gone


async def test_a_job_that_keeps_failing_is_told_once(bot, db):
    sch = scheduler.setup_scheduler()
    assert any(cb is scheduler._on_job_event for cb, _mask in sch._listeners)

    def event(code, job_id, exc=None):
        return JobExecutionEvent(code, job_id, "default", datetime.now(UTC), exception=exc)

    async def settle():
        await asyncio.gather(*scheduler._listener_tasks)

    scheduler._on_job_event(event(EVENT_JOB_ERROR, "retention", OSError("disk I/O error")))
    await settle()
    assert bot.sent[-1]["text"].startswith("⚠️ У меня не работает «ночная очистка базы»: OSError")
    scheduler._on_job_event(event(EVENT_JOB_ERROR, "retention", OSError("disk I/O error")))
    await settle()
    assert len(bot.sent) == 1                                # once per streak
    for _ in range(2):
        scheduler._on_job_event(event(EVENT_JOB_ERROR, "availability_checks", OverflowError()))
    await settle()
    assert len(bot.sent) == 1                                # a minute job: two failures are a blip
    scheduler._on_job_event(event(EVENT_JOB_ERROR, "availability_checks", OverflowError()))
    await settle()
    assert "«проверка сайтов»: OverflowError" in bot.sent[-1]["text"]
    scheduler._on_job_event(event(EVENT_JOB_EXECUTED, "availability_checks"))
    assert "availability_checks" not in scheduler._job_told  # works again: the next streak is told anew


async def test_a_broken_weekly_section_costs_only_itself(bot, db, monkeypatch):
    async def broken():
        raise RuntimeError("bad snapshot")
    monkeypatch.setattr(weekly, "incidents_block", broken)
    text, _ = await weekly.build_weekly_report()
    assert "⚠️ Инциденты: не смог собрать" in text and "Итоги недели" in text


async def test_container_healthcheck_reads_the_bot_not_the_proxy(unused_tcp_port):
    status = [200]

    async def handle(_):
        return web.json_response({}, status=status[0])
    app = web.Application()
    app.router.add_get("/health", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    env = {**os.environ, "WEBHOOK_PORT": str(unused_tcp_port), "http_proxy": "http://127.0.0.1:9",
           "HTTP_PROXY": "http://127.0.0.1:9", "PYTHONDONTWRITEBYTECODE": "1"}

    async def check() -> int:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-B", "-m", "web.healthcheck", cwd=ROOT, env=env)
        return await proc.wait()
    try:
        assert await check() == 0                            # a proxy in the environment is not followed
        status[0] = 503
        assert await check() == 1                            # «stale» is unhealthy
    finally:
        await runner.cleanup()
