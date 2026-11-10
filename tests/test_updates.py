import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_state, set_state
from services import updates
from services.updates import installed_versions, summary_lines


def test_installed_versions_and_summary():
    """A newer version on PyPI is not news — Dependabot installs it on
    Saturday by itself. Only what needs a human is a line."""
    versions = installed_versions()
    assert "aiohttp" in versions and "aiogram" in versions
    assert summary_lines(None) == []
    lines = summary_lines({"outdated": [["aiohttp", "3.10", "3.14"]], "vulns": {"cryptography": ["X"]}})
    assert any("Уязвимые" in line for line in lines) and not any("aiohttp" in line for line in lines)
    assert summary_lines({"vulns": {}, "waiting_prs": []}) == []


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
    lines = summary_lines({"vulns": {}, "waiting_prs": [
        {"number": 57, "title": "Bump aiogram from 3.31.0 to 4.0.0", "url": "https://github.com/x/y/pull/57", "major": True},
        {"number": 58, "title": "Bump aiohttp from 3.14.2 to 3.14.3", "url": "https://github.com/x/y/pull/58", "major": False}]})
    assert any("pull/57" in line and "aiogram 3.31.0→4.0.0" in line and "1 PR" in line for line in lines)
    assert any(line.startswith("🟠 Обновление не встало само") and "pull/58" in line for line in lines)
    # A stored result from before the rename still reads.
    old = summary_lines({"major_prs": [{"number": 57, "title": "Bump aiogram from 3.31.0 to 4.0.0", "url": "u"}]})
    assert any("Ждут твоего решения" in line for line in old)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_REPO_OWNER", raising=False)
    assert updates.repo_slug() is None
    monkeypatch.setenv("RAILWAY_GIT_REPO_OWNER", "me")
    monkeypatch.setenv("RAILWAY_GIT_REPO_NAME", "bot")
    assert updates.repo_slug() == "me/bot"
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/b")
    assert updates.repo_slug() == "a/b"


async def test_waiting_prs_are_majors_and_stuck_minors(monkeypatch):
    fresh, old = "2099-01-01T00:00:00Z", "2020-01-01T00:00:00Z"

    async def pulls(_):
        return web.json_response([
            {"number": 1, "title": "Bump aiogram from 3.31.0 to 4.0.0", "html_url": "u1", "created_at": fresh,
             "user": {"login": "dependabot[bot]"}},
            {"number": 2, "title": "Bump aiohttp from 3.14.2 to 3.14.3", "html_url": "u2", "created_at": fresh,
             "user": {"login": "dependabot[bot]"}},
            {"number": 4, "title": "Bump python from 3.14-slim to 3.15-slim", "html_url": "u4", "created_at": fresh,
             "user": {"login": "dependabot[bot]"}, "head": {"ref": "dependabot/docker/python-3.15-slim"}},
            {"number": 3, "title": "Bump y from 1.0 to 2.0", "html_url": "u3", "created_at": old, "user": {"login": "human"}},
            {"number": 5, "title": "Bump the python-deps group with 2 updates", "html_url": "u5", "created_at": old,
             "user": {"login": "dependabot[bot]"}, "head": {"ref": "dependabot/pip/python-deps-1a2b3c"}},
        ])
    app = web.Application()
    app.router.add_get("/repos/me/bot/pulls", pulls)
    async with TestServer(app) as server:
        monkeypatch.setenv("GITHUB_REPOSITORY", "me/bot")
        monkeypatch.setattr(updates, "GITHUB_PULLS", f"http://127.0.0.1:{server.port}/repos/{{slug}}/pulls")
        async with aiohttp.ClientSession() as session:
            prs = await updates.waiting_prs(session)
    # 2 is fresh and will auto-merge; 4 is the base image — a human merges it
    # even when fresh; 5 has been open for years: its CI is red.
    assert [(p["number"], p["major"]) for p in prs] == [(1, True), (4, True), (5, False)] and prs[0]["url"] == "u1"
    monkeypatch.delenv("GITHUB_REPOSITORY")
    async with aiohttp.ClientSession() as session:
        assert await updates.waiting_prs(session) == []


async def test_a_failed_deploy_is_said_once(bot, db, cfg, monkeypatch):
    """1 Nov 2026: the Python 3.15 image was auto-merged, did not build, and
    the bot kept running the old version without a word for days."""
    deploy = {"state": "failure"}

    async def head(_):
        return web.json_response({"sha": "db167bc206bd", "html_url": "https://github.com/me/bot/commit/db167bc",
                                  "commit": {"message": "Bump python from 3.14-slim to 3.15-slim (#14)\n\nbody"}})

    async def status(_):
        return web.json_response({"statuses": [{"state": deploy["state"], "description": "Deployment failed",
                                                "target_url": "https://railway.com/project/x"}]})

    app = web.Application()
    app.router.add_get("/repos/me/bot/commits/main", head)
    app.router.add_get("/repos/me/bot/commits/db167bc206bd/status", status)
    cfg(app_commit="c370117c0c25", dependency_watch=True)
    async with TestServer(app) as server:
        base = f"http://127.0.0.1:{server.port}/repos/{{slug}}/commits/{{ref}}"
        monkeypatch.setenv("GITHUB_REPOSITORY", "me/bot")
        monkeypatch.setattr(updates, "GITHUB_COMMIT", base)
        monkeypatch.setattr(updates, "GITHUB_STATUS", base + "/status")
        await updates.run_deploy_watch()
        text = bot.sent[-1]["text"]
        assert text.startswith("🟠 Новая версия бота не запустилась") and "3.15-slim" in text and "c370117" in text
        assert bot.sent[-1]["silent"] is True and (await updates.deploy_problem())["sha"] == "db167bc"
        n = len(bot.sent)
        await updates.run_deploy_watch()                          # same commit → said once
        assert len(bot.sent) == n
        deploy["state"] = "success"
        await updates.run_deploy_watch()
        assert await updates.deploy_problem() is None and len(bot.sent) == n
        cfg(app_commit="db167bc206bd")                            # running the newest build → nothing to say
        deploy["state"] = "failure"
        await updates.run_deploy_watch()
        assert await updates.deploy_problem() is None
