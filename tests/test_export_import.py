"""Restore what a person configured: an old JSON export, and the whole
database from a copy sent to the chat."""

import io
from types import SimpleNamespace

import pytest
from conftest import FakeCall, FakeMessage, FakeState

from db import database
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
    data = copy.read_bytes()
    await database.close_db()
    database.configure(str(tmp_path / "third.db"))
    await database.init_db()
    await sites.reload_services()
    assert not await db.get_all_sites()

    n = await database.restore_from_bytes(data)
    await sites.reload_services()
    assert n == 1 and (await db.get_site_by_url("https://a.test"))["keyword"] == "hello"
    assert settings.morning_hour() == 6 and (tmp_path / "third.db.bak").exists()
    with pytest.raises(ValueError):
        await database.restore_from_bytes(b"not a database at all")
    with pytest.raises(ValueError):
        await database.restore_from_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    assert (await db.get_site_by_url("https://a.test"))["keyword"] == "hello"   # a bad file changes nothing


async def test_sending_the_copy_to_the_chat_restores_after_a_yes(bot, db, tmp_path):
    """No «📥 Импорт» button: the bot recognises its own copy in the chat
    and asks before replacing anything."""
    await db.activate_or_create_site("https://kept.test")
    copy = tmp_path / "copy.db"
    await database.backup_db(str(copy))
    data = copy.read_bytes()
    await db.activate_or_create_site("https://later.test")

    async def download(file):
        file_id = getattr(file, "file_id", file)                       # a Document or its id
        return io.BytesIO(data if file_id == "f1" else b"%PDF-1.4")
    fake_bot = SimpleNamespace(download=download)
    msg = FakeMessage()
    msg.document, msg.bot = SimpleNamespace(file_id="f1", file_size=len(data)), fake_bot
    state = FakeState()
    await sites.msg_backup_file(msg, state)
    assert msg.texts[-1].startswith("💾 Это копия базы бота") and "restore:yes" in str(msg.reply_markup)
    call = FakeCall("restore:yes")
    call.bot = fake_bot
    await sites.cb_restore(call, state)
    assert "Восстановил из копии: активных сайтов 1" in call.message.texts[-1]
    assert [s["url"] for s in await db.get_all_sites()] == ["https://kept.test"]

    other = FakeMessage()
    other.document, other.bot = SimpleNamespace(file_id="pdf", file_size=10), fake_bot
    await sites.msg_backup_file(other, FakeState())
    assert "Не знаю, что делать с этим файлом" in other.texts[-1]
