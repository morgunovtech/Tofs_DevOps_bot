"""Merged but not deployed: an API check whose «version» is behind the
release of its homepage's repository, a Worker deployed before the newest
commit under worker/ — said once, ✅ only on a verdict. GitHub, the API's
answer and Cloudflare are stubs or a local server."""

from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from test_i18n import screen_english

from services import cloudflare, github, integrations, lag
from utils import i18n

ZONE = "c" * 32
ACCOUNT = "acc3"
NOW = datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def test_versions():
    assert lag.version_of('{"status": "ok", "version": "v2.2.1"}') == "2.2.1"
    assert lag.version_of('{"version": 3}') == "3"
    assert lag.version_of("<html>") is None and lag.version_of('{"ok": true}') is None and lag.version_of(None) is None
    assert lag.behind("2.2.1", "v2.2.2") and lag.behind("2.9", "2.10.0")
    assert not lag.behind("2.2.2", "v2.2.2") and not lag.behind("3.0.0-rc1", "v2.9.9") and not lag.behind("dev", "v1")


@pytest.fixture
def world(monkeypatch):
    """The repositories, the API's answer and the newest worker/ commit."""
    state = {"answer": '{"version": "2.2.1"}',
             "repos": [{"name": "me/fp", "url": "https://github.com/me/fp", "homepage": "https://fp.ex.test",
                        "release": "v2.2.2", "released_at": iso(NOW - timedelta(days=3))}],
             "commit": NOW - timedelta(days=2)}

    async def repos():
        return state["repos"]

    async def answer(site):
        return state["answer"]

    async def commit(name, path):
        assert (name, path) == ("me/fp", "worker")
        return state["commit"]

    monkeypatch.setattr(github, "available", lambda: True)
    monkeypatch.setattr(github, "published_repos", repos)
    monkeypatch.setattr(github, "last_commit_at", commit)
    monkeypatch.setattr(lag, "answer_text", answer)
    return state


async def test_an_api_behind_its_release_is_said_once_and_caught_up(bot, db, world):
    await db.activate_or_create_site("https://fp.ex.test/api/health")
    await lag.run()
    [msg] = bot.sent
    assert msg["text"].split("\n")[0] == "🟠 API fp.ex.test/api/health отстаёт от релиза v2.2.2 уже 3 дн.: отвечает версией 2.2.1"
    assert msg["silent"] is True
    assert msg["reply_markup"].inline_keyboard[0][0].url == "https://github.com/me/fp/releases/tag/v2.2.2"
    await lag.run()
    assert len(bot.sent) == 1                                     # said once

    world["answer"] = None                                        # the API is down: no verdict, no ✅
    await lag.run()
    assert len(bot.sent) == 1
    world["answer"] = '{"version": "2.2.2"}'
    await lag.run()
    assert bot.sent[-1]["text"] == "✅ API fp.ex.test/api/health: задеплоено то, что влито, — больше не отстаёт."


async def test_a_fresh_release_or_a_site_without_a_version_is_quiet(bot, db, world):
    await db.activate_or_create_site("https://fp.ex.test/api/health")
    await db.activate_or_create_site("https://fp.ex.test")                    # a page: not an API check
    world["repos"][0]["released_at"] = iso(NOW - timedelta(hours=3))        # a deployment may still come
    await lag.run()
    world["repos"][0]["released_at"] = iso(NOW - timedelta(days=3))
    world["answer"] = '{"ok": true}'
    await lag.run()
    world["repos"].append({**world["repos"][0], "name": "me/other"})       # two repositories claim the host
    world["answer"] = '{"version": "1.0"}'
    await lag.run()
    assert bot.sent == []


@pytest.fixture
async def workers(monkeypatch, world):
    state = {"modified_on": iso(NOW - timedelta(days=5))}

    def serve(result):
        async def handler(request):
            return web.json_response({"success": True, "errors": [], "result": result()})
        return handler

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE}", serve(lambda: {"id": ZONE, "name": "ex.test", "account": {"id": ACCOUNT}}))
    app.router.add_get(f"/client/v4/zones/{ZONE}/workers/routes",
                       serve(lambda: [{"id": "r", "pattern": "fp.ex.test/api/*", "script": "fp-api"}]))
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/workers/scripts",
                       serve(lambda: [{"id": "fp-api", "modified_on": state["modified_on"]}]))
    async with TestServer(app) as server:
        monkeypatch.setattr(cloudflare, "API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE)
        yield state


async def test_a_worker_deployed_before_its_newest_commit(bot, db, world, workers):
    await lag.run()
    [msg] = bot.sent
    assert msg["text"].startswith("🟠 Worker fp-api отстаёт от кода: последний коммит в worker/ от ")
    assert "на fp.ex.test работает прежняя версия" in msg["text"]
    workers["modified_on"] = iso(NOW - timedelta(hours=1))
    await lag.run()
    assert bot.sent[-1]["text"] == "✅ Worker fp-api: задеплоено то, что влито, — больше не отстаёт."


async def test_a_commit_of_today_waits_for_its_deployment(bot, db, world, workers):
    world["commit"] = NOW - timedelta(hours=2)
    await lag.run()
    assert bot.sent == []


async def test_in_english(bot, db, world, workers):
    await db.activate_or_create_site("https://fp.ex.test/api/health")
    i18n.set_language("en")
    try:
        await lag.run()
    finally:
        i18n.set_language("ru")
    assert len(bot.sent) == 2
    for msg in bot.sent:
        screen_english(msg["text"], msg["reply_markup"])


async def test_the_newest_commit_under_a_path(db, monkeypatch):
    seen = []

    async def commits(request):
        seen.append(dict(request.query))
        if request.match_info["repo"] == "empty":
            return web.json_response([])
        return web.json_response([{"sha": "abc", "commit": {"committer": {"date": "2026-11-20T10:00:00Z"}}}])

    app = web.Application()
    app.router.add_get("/repos/me/{repo}/commits", commits)
    async with TestServer(app) as server:
        monkeypatch.setattr(github, "COMMITS", f"http://127.0.0.1:{server.port}/repos/{{name}}/commits")
        assert await github.last_commit_at("me/fp", "worker") == datetime(2026, 11, 20, 10, tzinfo=UTC)
        assert await github.last_commit_at("me/empty", "worker") is None
    assert seen[0] == {"path": "worker", "per_page": "1"}
