import json

from db.database import get_state, set_state
from services import updates
from services.updates import _version_key, installed_versions, summary_lines


def test_version_key():
    assert _version_key("3.14.3") == (3, 14, 3)
    assert _version_key("3.15.0rc1") is None
    assert max(["3.9.1", "3.14.3", "3.10.11"], key=_version_key) == "3.14.3"


def test_installed_versions_and_summary():
    versions = installed_versions()
    assert "aiohttp" in versions and "aiogram" in versions
    assert summary_lines(None) == []
    lines = summary_lines({"outdated": [["aiohttp", "3.10", "3.14"]], "vulns": {"cryptography": ["X"]}})
    assert any("Уязвимые" in line for line in lines) and any("aiohttp 3.10→3.14" in line for line in lines)
    assert summary_lines({"outdated": [], "vulns": {}}) == ["📦 Зависимости актуальны, уязвимостей нет"]


async def test_startup_report_only_on_change(bot):
    await updates.report_startup_changes()          # first boot: snapshot only
    assert bot.sent == []
    await updates.report_startup_changes()          # nothing changed
    assert bot.sent == []
    snap = json.loads(await get_state("deps:snapshot"))
    snap["packages"]["aiohttp"] = "3.10.11"
    await set_state("deps:snapshot", json.dumps(snap))
    await updates.report_startup_changes()
    assert bot.sent and "aiohttp 3.10.11 →" in bot.sent[-1]["text"]
