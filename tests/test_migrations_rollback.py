"""A schema change has a way back: a copy is taken first, every step is all
or nothing, a database newer than the code stops the start with a sentence,
and a multi-statement write is never half-committed by the next writer."""

import asyncio
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

from db import database, migrations
from db.migrations import MIGRATIONS, SchemaTooNew

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _version(path) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _set_version(path, version: int):
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA user_version = {version}")


async def _reopen(path):
    await database.close_db()
    database.configure(str(path))
    await database.init_db()


def _copies(tmp_path) -> list[str]:
    folder = tmp_path / "backups"
    return sorted(p.name for p in folder.glob("schema-v*.db")) if folder.exists() else []


async def test_an_upgrade_takes_a_copy_first(db, tmp_path):
    await db.get_or_create_site("https://example.com")
    await database.close_db()
    _set_version(tmp_path / "test.db", len(MIGRATIONS) - 1)       # as the previous version left it
    await _reopen(tmp_path / "test.db")
    copies = _copies(tmp_path)
    assert len(copies) == 1 and copies[0].startswith(f"schema-v{len(MIGRATIONS) - 1:03d}-")   # named by what it holds
    assert _version(tmp_path / "backups" / copies[0]) == len(MIGRATIONS) - 1
    assert _version(tmp_path / "test.db") == len(MIGRATIONS)
    assert await db.get_active_site_urls() == ["https://example.com"]
    assert database.migrated_without_copy() is None


async def test_a_fresh_or_current_database_needs_no_copy(db, tmp_path):
    await _reopen(tmp_path / "test.db")                           # already current
    assert _copies(tmp_path) == []


async def test_a_newer_database_is_refused_untouched(db, tmp_path):
    await database.close_db()
    _set_version(tmp_path / "test.db", len(MIGRATIONS) + 2)
    with pytest.raises(SchemaTooNew, match=f"v{len(MIGRATIONS) + 2}"):
        await _reopen(tmp_path / "test.db")
    assert _version(tmp_path / "test.db") == len(MIGRATIONS) + 2
    assert _copies(tmp_path) == []


async def test_a_failing_step_leaves_the_database_as_it_was(db, monkeypatch):
    async def broken_step(conn):
        await conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise sqlite3.OperationalError("disk I/O error (injected)")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*MIGRATIONS, broken_step])
    conn = await db.get_db()
    with pytest.raises(sqlite3.OperationalError):
        await migrations.migrate(conn)
    assert await migrations.schema_version(conn) == len(MIGRATIONS)
    cursor = await conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'half_done'")
    assert await cursor.fetchone() is None


async def test_only_the_last_three_copies_are_kept_by_age_not_by_name(db, tmp_path):
    """v9, v10, v11 sort wrong as strings («v10» < «v9»): the rotation goes by time."""
    folder = tmp_path / "backups"
    folder.mkdir()
    for n, name in enumerate(("schema-v009-20261201-000000.db", "schema-v010-20270101-000000.db",
                              "schema-v011-20270201-000000.db", "schema-v012-20270301-000000.db")):
        p = folder / name
        p.write_bytes(b"old copy")
        os.utime(p, (time.time() - 1000 + n, time.time() - 1000 + n))
    await database.close_db()
    _set_version(tmp_path / "test.db", len(MIGRATIONS) - 1)
    await _reopen(tmp_path / "test.db")
    copies = _copies(tmp_path)
    assert len(copies) == database.PRE_MIGRATION_KEEP
    assert "schema-v009-20261201-000000.db" not in copies and "schema-v010-20270101-000000.db" not in copies
    assert "schema-v012-20270301-000000.db" in copies


async def test_a_failed_copy_is_said_and_the_upgrade_goes_on(db, tmp_path, monkeypatch, caplog):
    """No copy is bad; a bot that does not run alerts nobody — worse."""
    async def no_space(path):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(database, "backup_db", no_space)
    await database.close_db()
    _set_version(tmp_path / "test.db", len(MIGRATIONS) - 1)
    await _reopen(tmp_path / "test.db")
    assert _version(tmp_path / "test.db") == len(MIGRATIONS)
    assert "No space left" in (database.migrated_without_copy() or "")
    assert "migrating without a way back" in caplog.text


class _Failing:
    """The shared connection, with one statement made to fail or to hang."""

    def __init__(self, conn, marker: str, hang: bool = False):
        self._conn, self._marker, self._hang = conn, marker, hang

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def execute(self, sql, *args, **kwargs):
        if self._marker in sql:
            if self._hang:
                await asyncio.sleep(3600)
            raise sqlite3.OperationalError("disk I/O error (injected)")
        return await self._conn.execute(sql, *args, **kwargs)


async def _site_with_incident(db) -> int:
    sid = await db.get_or_create_site("https://example.com")
    await db.save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    return sid


async def _is_untouched(db, sid) -> bool:
    await db.set_state("another_writer", "1")                      # commits whatever is left open
    site = await db.get_site(sid)
    return site["active"] == 1 and len(await db.get_active_incidents()) == 1


async def test_a_half_done_write_is_rolled_back(db):
    sid = await _site_with_incident(db)
    real = database._conn
    database._conn = _Failing(real, "UPDATE incidents")
    try:
        with pytest.raises(sqlite3.OperationalError):
            await db.deactivate_site(sid)
    finally:
        database._conn = real
    assert await _is_untouched(db, sid)


async def test_a_cancelled_write_is_rolled_back(db):
    sid = await _site_with_incident(db)
    real = database._conn
    database._conn = _Failing(real, "UPDATE incidents", hang=True)
    try:
        task = asyncio.create_task(db.deactivate_site(sid))
        await asyncio.sleep(0.1)                                   # the first UPDATE is done
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        database._conn = real
    assert await _is_untouched(db, sid)


def test_the_bot_does_not_start_on_a_newer_database(tmp_path):
    path = tmp_path / "bot.db"
    _set_version(path, len(MIGRATIONS) + 2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DB_PATH": str(path), "WEBHOOK_PORT": str(port), "TELEGRAM_PROXY": "",
           "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-B", "main.py"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2
    assert "База от более новой версии бота" in out.stderr and "Traceback" not in out.stderr
    assert _version(path) == len(MIGRATIONS) + 2
