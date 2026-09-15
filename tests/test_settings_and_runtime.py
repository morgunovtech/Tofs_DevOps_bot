from config import config
from services import runtime, settings


async def test_overrides_roundtrip(db):
    assert settings.morning_hour() == config.morning_report_hour
    await settings.set_morning_hour(7)
    await settings.set_evening_hour(None)
    await settings.set_weekly_hour(18)
    await settings.set_quiet_hours("0-7")
    await settings.set_status_page(True)
    await settings.add_heartbeat_job("backup", 1440)
    await settings.load()   # re-read from the DB
    assert settings.morning_hour() == 7 and settings.evening_hour() is None
    assert settings.weekly_hour() == 18 and settings.quiet_hours() == (0, 7)
    assert settings.status_page_enabled() and settings.heartbeat_jobs() == {"backup": 1440}
    await settings.set_quiet_hours(None)
    assert settings.quiet_hours() is None
    assert await settings.remove_heartbeat_job("backup") and not await settings.remove_heartbeat_job("x")
    await settings.set_status_page(False)


async def test_first_start_claims_admin(db, monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", None)
    monkeypatch.setattr(runtime, "_user_id", None)
    assert not runtime.has_admin() and not runtime.is_admin(1)
    assert await runtime.claim(chat_id=-100, user_id=1)
    assert runtime.is_admin(1) and not runtime.is_admin(2)
    assert not await runtime.claim(chat_id=5, user_id=2)       # already taken
    monkeypatch.setattr(runtime, "_chat_id", None)
    await runtime.load()                                        # restored from the DB
    assert runtime.admin_chat_id() == "-100" and runtime.is_admin(1)
