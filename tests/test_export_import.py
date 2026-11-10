"""Restore what a person configured: an old JSON export, and the whole
database from a copy sent to the chat."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeCall, FakeMessage, FakeState

from db import database, migrations
from handlers import sites
from services import integrations, maintenance, settings


async def test_export_v2_roundtrip_and_db_restore(bot, db, tmp_path):
    sid = await db.activate_or_create_site("https://a.test")
    await db.update_site_settings(sid, keyword="hello")
    await settings.set_morning_hour(6)
    await settings.set_quiet_hours("23-7")
    await settings.set_ring_only_down(True)
    await maintenance.add_window(sid, [0, 6], 120, 180)
    await settings.add_heartbeat_job("backup", 120)
    await integrations.set_value("yandex_token", "y" * 40)

    # A JSON export as older versions wrote it (there is no export button now:
    # the bot's database copy is the one way out, and such files still restore).
    doc = {"version": 2, "sites": [{"url": "https://a.test", "keyword": "hello"}],
           "settings": {"morning_hour": 6, "evening_hour": None, "weekly_hour": 11, "quiet_hours": "23-7",
                        "status_page": False, "timezone": "Europe/Moscow", "ring_only_down": True},
           "maintenance_windows": [{"site_id": sid, "days": [0, 6], "start_min": 120, "end_min": 180,
                                    "site_url": "https://a.test"}],
           "heartbeat_jobs": {"backup": 120}, "integrations": {"yandex_token": "y" * 40}}

    await database.close_db()
    database.configure(str(tmp_path / "second.db"))
    await database.init_db()
    await sites.reload_services()
    assert not await db.get_all_sites() and settings.morning_hour() != 6 and not maintenance.windows()

    c = await sites.apply_document(doc)
    assert c["added"] == 1 and c["settings"] >= 5 and c["windows"] == 1 and c["jobs"] == 1 and c["integrations"] == 1
    assert settings.morning_hour() == 6 and settings.quiet_hours() == (23, 7) and settings.ring_only_down()
    site = await db.get_site_by_url("https://a.test")
    assert site["keyword"] == "hello" and maintenance.windows()[0]["site_id"] == site["id"]
    assert settings.ui_heartbeat_jobs() == {"backup": 120} and integrations.yandex_token().startswith("y")

    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    await database.close_db()
    database.configure(str(tmp_path / "third.db"))
    await database.init_db()
    await sites.reload_services()
    assert not await db.get_all_sites()

    n, bak = await database.restore_from_file(str(copy))
    await sites.reload_services()
    assert n == 1 and (await db.get_site_by_url("https://a.test"))["keyword"] == "hello"
    assert settings.morning_hour() == 6 and Path(bak).exists()
    for junk in (b"not a database at all", b"SQLite format 3\x00" + b"\x00" * 200):
        bad = tmp_path / "junk.db"
        bad.write_bytes(junk)
        with pytest.raises(ValueError):
            await database.restore_from_file(str(bad))
    assert (await db.get_site_by_url("https://a.test"))["keyword"] == "hello"   # a bad file changes nothing


def _fake_bot(files: dict[str, bytes]):
    async def download(file, destination=None, timeout=30):
        Path(destination).write_bytes(files[getattr(file, "file_id", file)])
    return SimpleNamespace(download=download)


def _doc_message(file_id: str, data: bytes, fake_bot, name: str = "bot-copy.db") -> FakeMessage:
    msg = FakeMessage()
    msg.document = SimpleNamespace(file_id=file_id, file_unique_id=file_id, file_size=len(data), file_name=name)
    msg.bot = fake_bot
    return msg


async def test_sending_the_copy_to_the_chat_restores_after_a_yes(bot, db, tmp_path):
    """No «📥 Импорт» button: the bot recognises its own copy in the chat
    and asks before replacing anything."""
    await db.activate_or_create_site("https://kept.test")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    data = copy.read_bytes()
    await db.activate_or_create_site("https://later.test")

    fake_bot = _fake_bot({"f1": data, "pdf": b"%PDF-1.4"})
    msg = _doc_message("f1", data, fake_bot)
    state = FakeState()
    await sites.msg_backup_file(msg, state)
    assert msg.texts[-1].startswith("💾 «bot-copy.db» — копия базы бота") and "restore:yes" in str(msg.reply_markup)
    assert "Пропадут: https://later.test" in msg.texts[-1]          # said BEFORE the button
    call = FakeCall("restore:yes")
    call.bot = fake_bot
    await sites.cb_restore(call, state)
    assert "Восстановил из копии: активных сайтов 1" in call.message.texts[-1]
    assert [s["url"] for s in await db.get_all_sites()] == ["https://kept.test"]
    assert not list(tmp_path.glob("upload-*.tmp"))                 # the upload is gone

    other = _doc_message("pdf", b"%PDF-1.4", fake_bot, "scan.pdf")
    await sites.msg_backup_file(other, FakeState())
    assert "Не знаю, что делать с этим файлом" in other.texts[-1]


# ── A copy is data, not a grant of access ────────────────────────────────────

def _evil_copy(path: Path, src: Path):
    """The owner's own copy with a stranger's helper, admin seat and a trigger."""
    con = sqlite3.connect(src)
    con.execute("VACUUM INTO ?", (str(path),))
    con.close()
    con = sqlite3.connect(path)
    con.executescript("""
        INSERT OR REPLACE INTO bot_state (key, value) VALUES ('admin_chat_id', '666'), ('admin_user_id', '666'),
            ('helpers', '[{"chat_id": "666", "name": "Аня"}]');
        CREATE TRIGGER t_admin AFTER UPDATE ON bot_state BEGIN
            UPDATE bot_state SET value = '666' WHERE key IN ('admin_chat_id', 'admin_user_id'); END;
    """)
    con.commit()
    con.close()


async def _counts() -> dict[str, int]:
    conn = await database.get_db()
    tables = [r[0] for r in await (await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")).fetchall()]
    return {t: (await (await conn.execute(f'SELECT COUNT(*) FROM "{t}"')).fetchone())[0] for t in tables}


async def test_a_copy_with_a_trigger_is_refused_and_changes_nothing(bot, db, tmp_path):
    await db.activate_or_create_site("https://mine.test")
    live = tmp_path / "live-copy.db"
    await database.backup_db(str(live))
    evil = tmp_path / "evil.db"
    _evil_copy(evil, live)
    with pytest.raises(ValueError, match="trigger"):
        await database.restore_from_file(str(evil))
    assert [s["url"] for s in await db.get_all_sites()] == ["https://mine.test"]
    assert await db.get_state("helpers") is None


async def test_access_keys_stay_as_in_the_live_database(bot, db, tmp_path):
    await db.activate_or_create_site("https://mine.test")
    await db.set_state("admin_chat_id", "777")
    await db.set_state("admin_user_id", "777")
    await db.set_state("secret:heartbeat", "the-live-heartbeat-secret")
    live = tmp_path / "live-copy.db"
    await database.backup_db(str(live))
    planted = tmp_path / "planted.db"
    _evil_copy(planted, live)
    con = sqlite3.connect(planted)
    con.execute("DROP TRIGGER t_admin")                              # data only: no trigger to refuse
    con.execute("UPDATE bot_state SET value = 'planted-secret' WHERE key = 'secret:heartbeat'")
    con.commit()
    con.close()

    n, _ = await database.restore_from_file(str(planted))
    assert n == 1
    assert await db.get_state("admin_chat_id") == "777" and await db.get_state("admin_user_id") == "777"
    assert await db.get_state("helpers") is None
    assert await db.get_state("secret:heartbeat") == "the-live-heartbeat-secret"


async def test_a_real_copy_keeps_every_row_of_every_table(bot, db, tmp_path):
    sid = await db.activate_or_create_site("https://a.test")
    await db.save_check(sid, "availability", "ok", 120, 200)
    await db.save_incident(sid, "ssl", "Сертификат скоро истечёт", "warning")
    await db.heartbeat_ping("backup")
    await db.outbox_add("не дошло", None, "critical")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    before = await _counts()
    await db.activate_or_create_site("https://later.test")

    await database.restore_from_file(str(copy))
    assert await _counts() == before


async def test_a_failure_after_the_swap_puts_the_previous_database_back(bot, db, tmp_path, monkeypatch):
    await db.activate_or_create_site("https://kept.example")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    await db.activate_or_create_site("https://later.example")
    real_init = database.init_db
    calls = {"n": 0}

    async def init_db_fails_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        await real_init()
    monkeypatch.setattr(database, "init_db", init_db_fails_once)

    with pytest.raises(ValueError, match="вернул прежнюю"):
        await database.restore_from_file(str(copy))
    assert sorted(s["url"] for s in await db.get_all_sites()) == ["https://kept.example", "https://later.example"]


async def test_only_the_newest_question_restores(bot, db, tmp_path):
    """Two files in a row: «✅» under the first question must not apply the second file."""
    await db.activate_or_create_site("https://kept.test")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    data = copy.read_bytes()
    fake_bot = _fake_bot({"f1": data, "f2": data})
    state = FakeState()
    first = _doc_message("f1", data, fake_bot)
    await sites.msg_backup_file(first, state)
    second = _doc_message("f2", data, fake_bot)
    second.message_id = 2
    await sites.msg_backup_file(second, state)
    call = FakeCall("restore:yes")                                   # pressed under question 1
    call.message.message_id = 1
    await sites.cb_restore(call, state)
    assert "старый вопрос" in call.message.texts[-1]


async def test_three_previous_databases_are_kept(bot, db, tmp_path):
    await db.activate_or_create_site("https://a.test")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    for _ in range(4):
        await database.restore_from_file(str(copy))
    assert len(list(tmp_path.glob("test.db.*.bak"))) == 3


async def test_restore_works_with_the_default_relative_db_path(bot, db, tmp_path, monkeypatch):
    await db.activate_or_create_site("https://a.test")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    await database.close_db()
    monkeypatch.chdir(tmp_path)
    database.configure("data/bot.db")          # config.py default when DB_PATH is not set
    await database.init_db()
    n, _ = await database.restore_from_file(str(copy))
    assert n == 1


async def test_an_old_copy_is_caught_up_by_migrations_that_change_data(bot, db, tmp_path, monkeypatch):
    """A copy from before a migration that fills a new column: the restored
    rows get the fill too, not just the empty fresh schema."""
    sid = await db.activate_or_create_site("https://a.test")
    await db.save_incident(sid, "ssl", "Сертификат скоро истечёт", "warning")
    copy = tmp_path / "old.db"
    await database.backup_db(str(copy))                      # made at the current (older) version

    async def m_fill_note(conn):
        await conn.execute("ALTER TABLE incidents ADD COLUMN note TEXT")
        await conn.execute("UPDATE incidents SET note = 'filled' WHERE note IS NULL")
    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS, m_fill_note])

    n, _ = await database.restore_from_file(str(copy))
    conn = await database.get_db()
    rows = await (await conn.execute("SELECT note FROM incidents")).fetchall()
    assert n == 1 and [r[0] for r in rows] == ["filled"]
