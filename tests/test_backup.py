"""Copies of the database: what leaves the server carries no tokens or
secrets and goes to the owner's private chat only; a failed copy never
replaces a good one; the owner hears about a copy that did not go."""

import os
import sqlite3
import stat

import aiosqlite
import pytest

from db import database
from db.database import backup_db, restore_from_file, set_state, shareable_copy
from reports import scheduler
from services import runtime, settings

TOKEN = "tok_unit_test_value_0123456789"


async def _with_private_values(db):
    sid = await db.get_or_create_site("https://api.example.test/api/health")
    await db.update_site_settings(sid, http_method="POST",
                                  http_headers="Authorization: Bearer hdr_unit_test_value",
                                  http_body='{"password": "body_unit_test_value"}')
    await set_state("int:github_token", TOKEN)
    await set_state("secret:heartbeat", "hb_unit_test_secret_value")
    await set_state("helper_invite", "invite_unit_test_value")
    conn = await db.get_db()
    await conn.execute("INSERT INTO feedback (site_url, page_url, message, ip_address) VALUES (?, ?, ?, ?)",
                       ("https://api.example.test", "/", "hi", "203.0.113.7"))
    await conn.commit()


async def test_a_copy_that_leaves_the_server_has_no_secrets(db, tmp_path):
    await _with_private_values(db)
    dest = str(tmp_path / "share.db")
    await shareable_copy(dest)
    raw = open(dest, "rb").read()
    for value in (TOKEN, "hdr_unit_test_value", "body_unit_test_value", "hb_unit_test_secret_value",
                  "invite_unit_test_value", "203.0.113.7"):
        assert value.encode() not in raw, value
    con = sqlite3.connect(dest)
    try:
        assert con.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 1          # the data stays
        assert con.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
    finally:
        con.close()
    assert not [n for n in os.listdir(tmp_path) if n.startswith("share.db") and n != "share.db"]


async def test_public_widget_secret_and_plain_settings_survive_the_copy(db, tmp_path):
    """The widget secret ships inside the feedback widget to every visitor,
    and «второе мнение», the zone id, the GSC property and the GitHub owner
    are settings, not credentials: a copy restored on a new server needs
    them as they were — the real tokens next to them are gone."""
    await set_state("secret:webhook", "widget_unit_test_public_value")
    await set_state("int:second_opinion", "0")
    await set_state("int:github_owner", "someone")
    await set_state("int:github_token", TOKEN)
    dest = str(tmp_path / "share.db")
    await shareable_copy(dest)
    con = sqlite3.connect(dest)
    try:
        kept = dict(con.execute("SELECT key, value FROM bot_state WHERE key LIKE 'int:%' "
                                "OR key LIKE 'secret:%'").fetchall())
    finally:
        con.close()
    assert kept == {"secret:webhook": "widget_unit_test_public_value", "int:second_opinion": "0",
                    "int:github_owner": "someone"}


async def test_a_failed_copy_keeps_the_previous_one(db, tmp_path, monkeypatch):
    dest = str(tmp_path / "bot-20261109.db")
    await backup_db(dest)
    good = open(dest, "rb").read()
    real_connect = aiosqlite.connect

    def disk_full(path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            return real_connect(path, *args, **kwargs)
        conn = real_connect(path, *args, **kwargs)
        real_execute = conn.execute

        def execute(sql, *a, **kw):
            if sql.startswith("VACUUM INTO"):
                open(a[0][0], "wb").close()                 # what a full disk leaves behind
                raise sqlite3.OperationalError("database or disk is full")
            return real_execute(sql, *a, **kw)
        conn.execute = execute
        return conn

    monkeypatch.setattr(database.aiosqlite, "connect", disk_full)
    with pytest.raises(sqlite3.OperationalError):
        await backup_db(dest)
    assert open(dest, "rb").read() == good
    assert not [n for n in os.listdir(tmp_path) if ".tmp" in n]           # no half-written leftovers


async def test_copies_are_for_the_bot_user_only(db, tmp_path):
    dest = str(tmp_path / "bot-1.db")
    await backup_db(dest)
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / "test.db").st_mode) == 0o600


async def test_weekly_copy_goes_to_the_owners_private_chat_not_the_group(bot, db, monkeypatch, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    await _with_private_values(db)
    monkeypatch.setattr(runtime, "_chat_id", "-1001234567890")    # the admin chat is a group
    monkeypatch.setattr(runtime, "_user_id", "777")
    await scheduler.send_db_backup_to_telegram()
    docs = [m for m in bot.sent if "document" in m]
    assert [d["chat_id"] for d in docs] == ["777"]
    assert "секретов в ней нет" in docs[0]["caption"]
    assert not [n for n in os.listdir(scheduler._backups_dir()) if n.startswith("share-")]   # nothing left


async def test_without_a_known_private_chat_the_group_gets_words_not_the_file(bot, db, monkeypatch, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    monkeypatch.setattr(runtime, "_chat_id", "-1001234567890")
    monkeypatch.setattr(runtime, "_user_id", None)
    await scheduler.send_db_backup_to_telegram()
    assert not [m for m in bot.sent if "document" in m]
    assert "TELEGRAM_ADMIN_USER_ID" in bot.sent[-1]["text"] and "🤖 Боты" in bot.sent[-1]["text"]


async def test_weekly_copy_can_be_switched_off(bot, db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    await settings.set_weekly_backup(False)
    await scheduler.send_db_backup_to_telegram()
    assert bot.sent == []
    await settings.load()
    assert not settings.weekly_backup()                          # the switch survives a restart
    await settings.set_weekly_backup(True)
    await scheduler.send_db_backup_to_telegram()
    assert [m for m in bot.sent if "document" in m]


async def test_a_copy_too_big_for_telegram_is_said_not_sent(bot, db, monkeypatch, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    monkeypatch.setattr(scheduler, "TG_SEND_LIMIT", 1)
    await scheduler.send_db_backup_to_telegram()
    assert not [m for m in bot.sent if "document" in m]
    assert "50 МБ" in bot.sent[-1]["text"]
    monkeypatch.setattr(scheduler, "TG_SEND_LIMIT", 50_000_000)
    monkeypatch.setattr(scheduler, "TG_DOWNLOAD_LIMIT", 1)      # fits to send, too big to send back
    await scheduler.send_db_backup_to_telegram()
    assert "обратно не получится" in bot.sent[-1]["caption"]


async def test_restoring_a_copy_without_tokens_keeps_the_live_ones(db, tmp_path):
    await _with_private_values(db)
    dest = str(tmp_path / "share.db")
    await shareable_copy(dest)
    await restore_from_file(dest)
    assert await database.get_state("int:github_token") == TOKEN
    assert await database.get_state("secret:heartbeat") == "hb_unit_test_secret_value"
    # Access keys — the pending invite included — stay as in the live database (PLAN-03).
    assert await database.get_state("helper_invite") == "invite_unit_test_value"


async def test_restoring_a_copy_without_tokens_keeps_the_api_check_headers(db, tmp_path):
    """An API check behind a token: the copy drops its headers and body, the
    restore on the same server puts the live ones back — the POST does not
    start going out empty and unauthorised."""
    await _with_private_values(db)
    dest = str(tmp_path / "share.db")
    await shareable_copy(dest)
    await restore_from_file(dest)
    site = await db.get_site_by_url("https://api.example.test/api/health")
    assert site["http_method"] == "POST"
    assert site["http_headers"] == "Authorization: Bearer hdr_unit_test_value"
    assert site["http_body"] == '{"password": "body_unit_test_value"}'


async def test_a_disconnected_token_is_wiped_from_the_live_file(db, tmp_path):
    await set_state("int:github_token", TOKEN)
    await set_state("int:github_token", None)                     # «🗑 Отключить»
    conn = await db.get_db()
    await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert TOKEN.encode() not in open(tmp_path / "test.db", "rb").read()
