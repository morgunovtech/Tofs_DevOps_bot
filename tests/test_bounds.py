"""One set of value rules for .env, buttons, the JSON import and a restored
database: a wrong value falls back (or is refused) with a word about it —
it never stops the scheduler or the start."""

import json
import os
import pathlib
import subprocess
import sys

from conftest import FakeCall

from config import config
from db.database import get_state, set_state
from handlers import settings as settings_screen
from handlers import site_settings, sites
from reports import scheduler
from services import integrations, maintenance, settings
from utils.parse import parse_duration

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = ("links_check_interval_hours", "disk_alert_pct", "escalation_repeat_min", "retention_days",
          "check_interval_minutes", "morning_report_hour", "slow_response_ms", "timezone", "second_opinion",
          "trust_proxy", "docker_socket", "heartbeat_jobs")


def _python(code: str, **env: str) -> subprocess.CompletedProcess:
    """A fresh interpreter: config reads the environment once, at import."""
    return subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **env})


def _config(**env: str) -> tuple[dict, str]:
    code = f"import json; from config import config as c; print(json.dumps({{k: getattr(c, k) for k in {FIELDS!r}}}))"
    proc = _python(code, **env)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1]), proc.stderr


def test_env_numbers_out_of_range_fall_back_with_a_warning():
    got, log = _config(LINKS_CHECK_INTERVAL_HOURS="-1", DISK_ALERT_PCT="0", ESCALATION_REPEAT_MIN="-1",
                       RETENTION_DAYS="0", CHECK_INTERVAL_MINUTES="99999999999999999999",
                       MORNING_REPORT_HOUR="24", SLOW_RESPONSE_MS="0")
    assert got["links_check_interval_hours"] == 6 and got["disk_alert_pct"] == 85
    assert got["escalation_repeat_min"] == 30 and got["retention_days"] == 30
    assert got["check_interval_minutes"] == 5 and got["morning_report_hour"] == 9 and got["slow_response_ms"] == 3000
    assert "LINKS_CHECK_INTERVAL_HOURS=-1 is out of range" in log and "DISK_ALERT_PCT=0 is out of range" in log
    got, _ = _config(LINKS_CHECK_INTERVAL_HOURS="0", ESCALATION_REPEAT_MIN="0")
    assert got["links_check_interval_hours"] == 0 and got["escalation_repeat_min"] == 0   # 0 = «off», allowed


def test_env_words_and_paths_are_checked():
    got, log = _config(TIMEZONE="/etc/localtime", SECOND_OPINION="", TRUST_PROXY="ture", DOCKER_SOCKET="",
                       HEARTBEAT_JOBS="db/backup:60,:60,ok-job:60,Nightly_Full:1440,neg:-5")
    assert got["timezone"] == "Europe/Moscow"                       # not a crash at import
    assert got["second_opinion"] is False and "SECOND_OPINION is empty — treated as off" in log
    assert got["trust_proxy"] is False and "Invalid TRUST_PROXY='ture'" in log
    assert got["docker_socket"] == "/var/run/docker.sock"
    assert got["heartbeat_jobs"] == {"ok-job": 60, "Nightly_Full": 1440}


def test_a_port_that_is_not_one_stops_the_start_with_a_sentence():
    proc = _python("from config import config", WEBHOOK_PORT="70000")
    assert proc.returncode == 2 and "WEBHOOK_PORT='70000' is not a port" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_posix_tz_does_not_stop_the_scheduler(tmp_path):
    """TZ=MSK-3 is valid for glibc and `date`; the bot has its own TIMEZONE."""
    proc = _python("from reports import scheduler; s = scheduler.setup_scheduler(); print(len(s.get_jobs()))",
                   TZ="MSK-3", DB_PATH=str(tmp_path / "bot.db"))
    assert proc.returncode == 0, proc.stderr
    assert int(proc.stdout.strip().splitlines()[-1]) > 20


async def test_a_broken_hour_in_the_db_does_not_stop_the_start(db):
    await set_state("cfg:morning_hour", "24")          # an old bug or a restored file
    await set_state("cfg:maint_windows", "[5]")
    await settings.load()
    await maintenance.load()
    assert settings.morning_hour() == config.morning_report_hour
    sch = scheduler.setup_scheduler()
    assert not scheduler._failed_jobs and "hour='9'" in str(sch.get_job("morning_report").trigger)
    assert scheduler.reschedule_all() == []             # not running: nothing to re-create, nothing failed


async def test_settings_refuse_what_is_not_an_hour(db):
    assert await settings.set_morning_hour(24) is False and await get_state("cfg:morning_hour") is None
    assert await settings.set_quiet_hours("25-8") is False and await settings.set_timezone(5) is False
    assert await settings.set_morning_hour(6) is True and settings.morning_hour() == 6


async def test_hour_buttons_take_only_what_the_screen_offers(bot, db):
    forged = FakeCall("set_m:99")
    await settings_screen.cb_settings_pick(forged)
    assert forged.answers == ["Не понял"] and await get_state("cfg:morning_hour") is None
    real = FakeCall("set_m:8")
    await settings_screen.cb_settings_pick(real)
    assert real.answers == ["Сохранено ✅"] and settings.morning_hour() == 8


async def test_stale_site_buttons_change_nothing(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    for data in (f"ssv:i:{sid}:9999999999", f"ssv:f:{sid}:²", f"ssv:s:{sid}:1"):
        call = FakeCall(data)
        await site_settings.cb_site_setting_value(call)
        assert "Кнопка устарела" in call.answers[-1]
    site = await db.get_site(sid)
    assert site["check_interval_min"] is None and site["slow_ms"] is None
    call = FakeCall(f"pause:{sid}:5000000")
    await sites.cb_pause(call)
    assert "Кнопка устарела" in call.answers[-1] and not await maintenance.is_paused(sid)
    await db.deactivate_site(sid)
    call = FakeCall(f"pause:{sid}:60")
    await sites.cb_pause(call)
    assert "Кнопка устарела" in call.answers[-1]


async def test_broken_maintenance_windows_are_ignored_not_fatal(db):
    sid = await db.activate_or_create_site("https://ex.test")
    good = {"id": 3, "site_id": None, "days": [], "start_min": 60, "end_min": 120}
    await set_state("cfg:maint_windows", json.dumps([5, {"id": 2, "days": "x", "start_min": 0, "end_min": 9},
                                                     good]))
    await maintenance.load()
    assert maintenance.windows() == [good]
    await maintenance.is_paused(sid)                    # used to raise inside every alert
    assert await maintenance.add_window(None, [], 9999, 99999) is None
    for start in range(9):
        assert await maintenance.add_window(None, [1], start, start + 1) is not None
    assert await maintenance.add_window(None, [2], 0, 1) is None       # the 11th window


async def test_import_checks_every_value_and_names_what_it_skipped(bot, db):
    doc = {"version": 2,
           "sites": [{"url": f"bad host {i}!"} for i in range(5)]
           + [{"url": f"https://s{i}.test"} for i in range(19)]
           + [{"url": "https://odd.test", "check_interval_min": 9999999999, "fail_threshold": -1,
               "http_method": "<b>", "http_headers": "5", "slow_ms": 1500}],
           "settings": {"morning_hour": 24, "quiet_hours": 5, "timezone": 5, "weekly_hour": 12},
           "maintenance_windows": ["string", {"days": [], "start_min": 0, "end_min": 1440}],
           "integrations": {"deploy_hooks": json.dumps({"a.test": "http://169.254.169.254/x"}),
                            "cf_zone_id": "../../accounts", "yandex_token": "y" * 40}}
    c = await sites.apply_document(doc)
    assert c["added"] == 20                              # bad entries do not eat the limit of 20
    odd = await db.get_site_by_url("https://odd.test")
    assert odd["check_interval_min"] is None and odd["fail_threshold"] is None and odd["http_method"] is None
    assert odd["slow_ms"] == 1500                        # the good field of the same entry still applies
    skipped = " | ".join(c["skipped"])
    for word in ("bad host 0!", "check_interval_min", "http_headers", "morning_hour", "quiet_hours", "timezone",
                 "тех. окно", "deploy_hooks", "cf_zone_id"):
        assert word in skipped, word
    assert settings.weekly_hour() == 12 and integrations.yandex_token() == "y" * 40
    assert integrations.deploy_hooks() == {} and integrations.cf_zone_id() == ""
    text = await sites._restore_bytes(json.dumps(doc).encode())
    assert "… и ещё" in text


def test_parse_duration_of_blank_is_none():
    assert parse_duration("   ") is None and parse_duration("\t") is None
