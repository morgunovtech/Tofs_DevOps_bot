"""Export everything a person configured, wipe, import — and restore the
whole database from an uploaded copy."""

import pytest

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

    doc = await sites.export_document()
    assert doc["version"] == 2 and doc["settings"]["morning_hour"] == 6 and doc["settings"]["ring_only_down"]
    assert doc["integrations"]["yandex_token"].startswith("y")
    assert doc["maintenance_windows"][0]["site_url"] == "https://a.test" and doc["heartbeat_jobs"] == {"backup": 120}

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
