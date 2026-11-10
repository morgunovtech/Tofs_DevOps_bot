"""The process itself: a start that fails ends the process (Docker restarts
what exits, not what hangs), a wrong setting is one sentence and exit code 2,
Telegram missing at the start is waited for, SIGTERM during the start stops
cleanly, and a stop lets a running job finish."""

import asyncio
import os
import pathlib
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.methods import GetMe
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import main
from db import database
from db.database import get_state, set_state

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _bot_env(tmp_path, **overrides) -> dict:
    env = {**os.environ, "DB_PATH": str(tmp_path / "bot.db"), "WEBHOOK_PORT": str(_free_port()),
           "TELEGRAM_PROXY": "", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(overrides)
    return env


def _run_bot(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "main.py"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_a_failed_start_exits_instead_of_hanging(tmp_path):
    # A port somebody already listens on: the web server fails AFTER the
    # database is open — the case that used to hang forever.
    with socket.socket() as taken:
        taken.bind(("0.0.0.0", 0))
        taken.listen()
        out = _run_bot(_bot_env(tmp_path, WEBHOOK_PORT=str(taken.getsockname()[1])))
    assert out.returncode == 1
    assert "Shutting down" in out.stderr


def test_a_broken_database_file_exits_instead_of_hanging(tmp_path):
    (tmp_path / "bot.db").write_bytes(b"this is not a database" * 100)
    out = _run_bot(_bot_env(tmp_path))
    assert out.returncode == 1


def test_a_wrong_token_is_one_sentence_and_exit_code_2(cfg, caplog):
    cfg(bot_token="your_token_here")
    assert main.run() == 2
    assert "TELEGRAM_BOT_TOKEN" in caplog.text and "Traceback" not in caplog.text


async def test_telegram_missing_at_the_start_is_waited_for():
    calls = []

    async def me():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("network is unreachable")
        return SimpleNamespace(username="tofs_bot")

    assert await main.whoami(SimpleNamespace(me=me), first_delay=0.01) == "tofs_bot"
    assert len(calls) == 3


async def test_a_token_telegram_refuses_is_a_setting_error():
    async def me():
        raise TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")

    with pytest.raises(main.ConfigError, match="TELEGRAM_BOT_TOKEN"):
        await main.whoami(SimpleNamespace(me=me), first_delay=0.01)


async def test_a_stop_lets_a_running_job_finish(db, tmp_path):
    done = []

    async def job():
        await asyncio.sleep(0.5)
        await set_state("job_done", "1")
        done.append(True)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(job, "date", run_date=datetime.now(UTC))
    scheduler.start()
    await asyncio.sleep(0.1)                                   # the job is running now

    async def close():
        return None

    await main.stop(scheduler, None, SimpleNamespace(session=SimpleNamespace(close=close)))
    assert done == [True] and await get_state("job_done") == "1"
    with sqlite3.connect(tmp_path / "test.db") as conn:          # the file tests/conftest.py gives `db`
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


async def test_the_shared_connection_keeps_its_safety_pragmas(db):
    """The guarded open must not drop what the backup item set: secure_delete
    wipes a disconnected token from the free pages, WAL keeps readers going."""
    conn = await database.get_db()
    assert (await (await conn.execute("PRAGMA secure_delete")).fetchone())[0] == 1
    assert str((await (await conn.execute("PRAGMA journal_mode")).fetchone())[0]).lower() == "wal"


@pytest.mark.skipif(sys.platform == "win32", reason="no SIGTERM handlers on Windows")
def test_sigterm_during_the_start_stops_cleanly(tmp_path):
    log = tmp_path / "bot.log"
    proxy = f"socks5://127.0.0.1:{_free_port()}"               # nobody listens: Telegram «unreachable»
    with log.open("w") as stderr:
        proc = subprocess.Popen([sys.executable, "-B", "main.py"], cwd=ROOT,
                                env=_bot_env(tmp_path, TELEGRAM_PROXY=proxy),
                                stdout=subprocess.DEVNULL, stderr=stderr)
        try:
            deadline = time.monotonic() + 30
            while "Telegram is not reachable yet" not in log.read_text() and time.monotonic() < deadline:
                assert proc.poll() is None, log.read_text()
                time.sleep(0.2)
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(timeout=30) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
    assert "Shutting down" in log.read_text()
