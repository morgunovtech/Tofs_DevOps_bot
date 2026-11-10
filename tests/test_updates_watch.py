"""What the bot says about its own versions: every installed package goes to
OSV, a group PR with a major update inside waits for a human, a new build
without new versions is not a message, an unknown repository is said once."""

import json
import logging
from pathlib import Path

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_state, set_state
from services import updates

GROUP_BODY = ("Bumps the python-deps group with 2 updates: [aiogram](https://github.com/aiogram/aiogram) "
              "and [aiohttp](https://github.com/aio-libs/aiohttp).\n\n"
              "Updates `aiogram` from 3.31.0 to 4.0.0\n- [Release notes](https://github.com/aiogram/aiogram/releases)\n\n"
              "Updates `aiohttp` from 3.14.5 to 3.14.6\n- [Release notes](https://github.com/aio-libs/aiohttp/releases)\n")


def test_the_named_packages_are_the_requirements():
    req = Path(__file__).resolve().parent.parent / "requirements.in"   # the lock has every package
    direct = {updates._canonical(line.split("==")[0].strip())
              for line in req.read_text().splitlines() if line.strip() and not line.startswith(("#", " ", "-"))}
    assert set(updates.PACKAGES) == direct


def test_installed_versions_are_canonical_and_without_the_installer():
    versions = updates.installed_versions()
    assert set(updates.PACKAGES) <= set(versions)                 # «APScheduler», «aiohttp_socks» → PEP 503 names
    assert not {"pip", "setuptools", "wheel"} & set(versions)
    assert all(name == updates._canonical(name) for name in versions)
    assert updates._canonical("python_dotenv") == "python-dotenv" and updates._canonical("APScheduler") == "apscheduler"


async def test_osv_is_asked_about_every_installed_package(db, monkeypatch):
    asked: list[str] = []

    async def osv(request):
        asked.extend(q["package"]["name"] for q in (await request.json())["queries"])
        return web.json_response({"results": [{} for _ in asked]})

    app = web.Application()
    app.router.add_post("/v1/querybatch", osv)
    async with TestServer(app) as server:
        monkeypatch.setattr(updates, "OSV_BATCH", f"http://127.0.0.1:{server.port}/v1/querybatch")
        async with aiohttp.ClientSession() as session:
            assert await updates._vulnerabilities(session, updates.installed_versions()) == {}
    assert sorted(asked) == sorted(updates.installed_versions())
    assert {"aiohttp-socks", "yarl", "multidict", "certifi", "idna"} <= set(asked)   # were never asked
    assert "pip" not in asked


async def test_a_new_build_without_new_versions_is_not_a_message(bot, cfg, caplog):
    cfg(app_commit="313e13b0c0de")
    await updates.report_startup_changes()                    # first boot: snapshot only
    cfg(app_commit="334d7dd0c0de")                            # Dependabot bumped ruff — not in the image
    caplog.set_level(logging.INFO, logger="services.updates")
    assert await updates.report_startup_changes() is True     # still «updated»: the SEO audit re-runs
    assert bot.sent == []
    assert any("334d7dd0c0de" in r.getMessage() for r in caplog.records)
    assert await updates.report_startup_changes() is False    # same build again
    snap = json.loads(await get_state("deps:snapshot"))
    snap["packages"]["yarl"] = "0.0.1"
    await set_state("deps:snapshot", json.dumps(snap))
    await updates.report_startup_changes()
    text = bot.sent[-1]["text"]
    assert text.startswith("🔄 Бот обновился:") and "вспомогательный пакет: yarl" in text and "сборка" not in text


async def test_a_direct_package_is_named_and_an_old_snapshot_still_reads(bot, cfg):
    """A snapshot written before this change holds 8 packages under their
    PyPI names; «APScheduler» there is «apscheduler» now."""
    cfg(app_commit="313e13b0c0de")
    await updates.report_startup_changes()
    snap = json.loads(await get_state("deps:snapshot"))
    snap["packages"] = {"aiohttp": "3.10.11", "APScheduler": "0.1", "aiosqlite": snap["packages"]["aiosqlite"]}
    await set_state("deps:snapshot", json.dumps(snap))
    assert await updates.report_startup_changes() is True
    text = bot.sent[-1]["text"]
    assert "  • aiohttp 3.10.11 → " in text and "  • apscheduler 0.1 → " in text and "aiosqlite" not in text
    assert text.index("aiohttp") < text.index("apscheduler")   # in the order of PACKAGES, not of the snapshot


def test_a_group_pr_is_read_by_its_text():
    assert updates.is_major_bump("Bump the python-deps group with 2 updates", GROUP_BODY)
    assert not updates.is_major_bump("Bump the python-deps group with 2 updates", GROUP_BODY.replace("4.0.0", "3.32.0"))
    assert updates.is_major_bump("Bump the pip group", "update-type: version-update:semver-major")
    assert updates.short_bump("Bump the python-deps group with 2 updates", GROUP_BODY) == "aiogram 3.31.0→4.0.0, aiohttp 3.14.5→3.14.6"
    three = GROUP_BODY + "Updates `yarl` from 1.0.0 to 1.1.0\n"
    assert updates.short_bump("Bump the python-deps group with 3 updates", three) == "aiogram 3.31.0→4.0.0, aiohttp 3.14.5→3.14.6…"
    # The title's own bump is not repeated when the text names it again.
    assert updates.short_bump("Bump aiogram from 3.31.0 to 4.0.0", "Updates `aiogram` from 3.31.0 to 4.0.0") == "aiogram 3.31.0→4.0.0"
    assert updates.short_bump("Something else entirely", "") == "Something else entirely"


async def test_a_group_pr_with_a_major_update_inside_waits_for_a_human(monkeypatch):
    fresh = "2099-01-01T00:00:00Z"

    async def pulls(_):
        return web.json_response([
            {"number": 22, "title": "Bump the python-deps group with 2 updates", "body": GROUP_BODY,
             "html_url": "u22", "created_at": fresh, "user": {"login": "dependabot[bot]"},
             "head": {"ref": "dependabot/pip/python-deps-0a1b2c"}},
            {"number": 23, "title": "Bump python from 3.14-slim to 3.14.7-slim", "body": "",
             "html_url": "u23", "created_at": fresh, "user": {"login": "dependabot[bot]"},
             "head": {"ref": "dependabot/docker/python-3.14.7-slim"}},
            {"number": 24, "title": "Bump the python-deps group with 2 updates",
             "body": GROUP_BODY.replace("4.0.0", "3.32.0"), "html_url": "u24", "created_at": fresh,
             "user": {"login": "dependabot[bot]"}, "head": {"ref": "dependabot/pip/python-deps-3c4d5e"}},
        ])

    app = web.Application()
    app.router.add_get("/repos/me/bot/pulls", pulls)
    async with TestServer(app) as server:
        monkeypatch.setenv("GITHUB_REPOSITORY", "me/bot")
        monkeypatch.setattr(updates, "GITHUB_PULLS", f"http://127.0.0.1:{server.port}/repos/{{slug}}/pulls")
        async with aiohttp.ClientSession() as session:
            prs = await updates.waiting_prs(session)
    # 24 is a fresh minor group: auto-merge takes it.
    assert [(p["number"], p["major"]) for p in prs] == [(22, True), (23, True)]
    assert prs[0]["label"] == "aiogram 3.31.0→4.0.0, aiohttp 3.14.5→3.14.6"
    lines = updates.summary_lines({"vulns": {}, "waiting_prs": prs})
    assert any("Ждут твоего решения: 2 PR" in line and "aiogram 3.31.0→4.0.0" in line
               and "python 3.14-slim→3.14.7-slim" in line and "базовый образ" in line for line in lines)
    assert not any("тесты не прошли" in line for line in lines)
    # A result stored before «label» existed still reads by its title.
    old = updates.summary_lines({"vulns": {}, "waiting_prs": [{"number": 1, "title": "Bump x from 1.0 to 2.0",
                                                               "url": "u", "major": True}]})
    assert any("x 1.0→2.0" in line for line in old)


async def test_an_unknown_repository_is_said_in_the_log_once(cfg, monkeypatch, caplog):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_REPO_OWNER", raising=False)
    cfg(app_commit="c370117c0c25")
    caplog.set_level(logging.INFO, logger="services.updates")
    async with aiohttp.ClientSession() as session:
        assert await updates.failed_deploy(session) is None
        assert await updates.waiting_prs(session) == []
        assert await updates.failed_deploy(session) is None
    assert sum("GITHUB_REPOSITORY" in r.getMessage() for r in caplog.records) == 1
