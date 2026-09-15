import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

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


def test_major_bump_detection_and_reminder(monkeypatch):
    assert updates.is_major_bump("Bump aiogram from 3.31.0 to 4.0.0")
    assert not updates.is_major_bump("Bump aiohttp from 3.14.2 to 3.14.3")
    assert not updates.is_major_bump("Bump the pip group with 3 updates")
    assert updates.short_bump("Bump aiogram from 3.31.0 to 4.0.0") == "aiogram 3.31.0→4.0.0"
    lines = summary_lines({"outdated": [], "vulns": {}, "major_prs": [
        {"number": 57, "title": "Bump aiogram from 3.31.0 to 4.0.0", "url": "https://github.com/x/y/pull/57"}]})
    assert any("pull/57" in line and "aiogram 3.31.0→4.0.0" in line and "1 PR" in line for line in lines)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_REPO_OWNER", raising=False)
    assert updates.repo_slug() is None
    monkeypatch.setenv("RAILWAY_GIT_REPO_OWNER", "me")
    monkeypatch.setenv("RAILWAY_GIT_REPO_NAME", "bot")
    assert updates.repo_slug() == "me/bot"
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/b")
    assert updates.repo_slug() == "a/b"


async def test_open_major_prs_keeps_only_dependabot_majors(monkeypatch):
    async def pulls(_):
        return web.json_response([
            {"number": 1, "title": "Bump aiogram from 3.31.0 to 4.0.0", "html_url": "u1", "user": {"login": "dependabot[bot]"}},
            {"number": 2, "title": "Bump aiohttp from 3.14.2 to 3.14.3", "html_url": "u2", "user": {"login": "dependabot[bot]"}},
            {"number": 3, "title": "Bump y from 1.0 to 2.0", "html_url": "u3", "user": {"login": "human"}},
        ])
    app = web.Application()
    app.router.add_get("/repos/me/bot/pulls", pulls)
    async with TestServer(app) as server:
        monkeypatch.setenv("GITHUB_REPOSITORY", "me/bot")
        monkeypatch.setattr(updates, "GITHUB_PULLS", f"http://127.0.0.1:{server.port}/repos/{{slug}}/pulls")
        async with aiohttp.ClientSession() as session:
            prs = await updates.open_major_prs(session)
    assert [p["number"] for p in prs] == [1] and prs[0]["url"] == "u1"
    monkeypatch.delenv("GITHUB_REPOSITORY")
    async with aiohttp.ClientSession() as session:
        assert await updates.open_major_prs(session) == []
