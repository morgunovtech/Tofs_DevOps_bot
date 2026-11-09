from types import SimpleNamespace

from conftest import FakeMessage, FakeState

from config import config
from handlers import setup, start
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


async def test_a_database_off_the_railway_volume_is_said_at_once(db, cfg, tmp_path, monkeypatch):
    """Without a volume Railway wipes the database on each deploy — with
    weekly auto-updates the bot would forget its sites and owner every week.
    Off Railway the docker volume is the operator's call: no opinion."""
    cfg(on_railway=False)
    assert runtime.db_on_persistent_disk() is None
    cfg(on_railway=True, db_path=str(tmp_path / "data" / "bot.db"))
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setattr(runtime.os.path, "ismount", lambda p: False)
    assert runtime.db_on_persistent_disk() is False
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path / "data"))
    assert runtime.db_on_persistent_disk() is True
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/elsewhere")
    assert runtime.db_on_persistent_disk() is False
    monkeypatch.setattr(runtime.os.path, "ismount", lambda p: p == str(tmp_path / "data"))
    assert runtime.db_on_persistent_disk() is True                 # any mount under the file counts

    monkeypatch.setattr(runtime.os.path, "ismount", lambda p: False)
    items = {i.key: i for i in await setup.build_items()}
    assert items["db"].status == "warn" and "не на постоянном диске" in items["db"].detail

    monkeypatch.setattr(runtime, "_chat_id", None)
    monkeypatch.setattr(runtime, "_user_id", None)
    msg = FakeMessage("/start")
    msg.from_user, msg.chat = SimpleNamespace(id=42), SimpleNamespace(id=42)
    await start.cmd_start(msg, FakeState())
    assert msg.texts[0].startswith("🔑 Ты стал администратором") and "не на постоянном диске Railway" in msg.texts[1]
