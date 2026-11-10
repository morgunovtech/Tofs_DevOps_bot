"""External services: a refused key or a day of silence is said once (and
its return), Google and Yandex verdicts mean what the alerts say, «no
answer» is never «zero» or «none»."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import buttons
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from db import database
from db.database import get_active_incidents, save_incident, set_state
from handlers import settings as settings_screen
from monitors import visitor_checker
from reports import scheduler, weekly
from services import crux, github, gsc, index_status, integrations, sitestatus, updates, yandex_webmaster
from utils.text import err_text


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    """Module-level caches and asyncio locks outlive a test's event loop."""
    monkeypatch.setattr(database, "_write_lock", asyncio.Lock())
    monkeypatch.setattr(sitestatus, "_lock", asyncio.Lock())
    gsc.reset_token_cache()


class FakeGoogle:
    """OAuth token endpoint + Search Analytics + URL Inspection on 127.0.0.1."""

    def __init__(self):
        self.token_status, self.inspect_body, self.tokens = 200, None, 0
        app = web.Application()
        app.router.add_post("/token", self.token)
        app.router.add_post("/sites/{prop}/searchAnalytics/query", self.query)
        app.router.add_post("/inspect", self.inspect)
        self.server = TestServer(app)

    async def token(self, request):
        self.tokens += 1
        if self.token_status != 200:
            return web.json_response({"error": "invalid_grant"}, status=self.token_status)
        return web.json_response({"access_token": f"t{self.tokens}", "expires_in": 3600})

    async def query(self, request):
        return web.json_response({"rows": [{"clicks": 120, "impressions": 3400, "position": 4.2}]})

    async def inspect(self, request):
        return web.json_response(self.inspect_body or {"inspectionResult": {"indexStatusResult": {
            "verdict": "PASS", "coverageState": "Submitted and indexed"}}})

    async def __aenter__(self):
        await self.server.start_server()
        base = str(self.server.make_url("")).rstrip("/")
        self.patch = pytest.MonkeyPatch()
        self.patch.setattr(gsc, "TOKEN_URL", base + "/token")
        self.patch.setattr(gsc, "ANALYTICS_URL", base + "/sites/{prop}/searchAnalytics/query")
        self.patch.setattr(gsc, "INSPECT_URL", base + "/inspect")
        return self

    async def __aexit__(self, *exc):
        self.patch.undo()
        await self.server.close()


def _key(email: str = "sa@p.iam.gserviceaccount.com") -> str:
    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return json.dumps({"client_email": email, "private_key": pem})


async def _connect_gsc():
    await integrations.set_value("gsc_property", "sc-domain:ex.test")
    await integrations.set_value("gsc_key", _key())


# ── The health record and its messages ───────────────────────────────────────

async def test_a_refused_key_is_said_once_and_its_return_too(bot, db):
    await _connect_gsc()
    async with FakeGoogle() as google:
        google.token_status = 400
        assert await gsc.search_totals("2026-11-01", "2026-11-07") is None
        assert await gsc.search_totals("2026-11-01", "2026-11-07") is None
        assert google.tokens == 1                                    # the failure is remembered, not retried
        await scheduler.run_integration_health()
        await scheduler.run_integration_health()
        said = [m for m in bot.sent if m["text"].startswith("🟠 Google Search Console")]
        assert len(said) == 1 and "invalid_grant" in said[0]["text"]
        assert "🔌 Проверить подключение" in buttons(said[0]["reply_markup"])
        assert "Google ⚠️ не отвечает" in await settings_screen.hub_text()
        google.token_status = 200
        gsc.reset_token_cache()
        assert (await gsc.search_totals("2026-11-01", "2026-11-07"))["clicks"] == 120
        await scheduler.run_integration_health()
        assert bot.sent[-1]["text"] == "✅ Google Search Console: снова работает."


async def test_a_new_key_gets_a_new_token(bot, db):
    await _connect_gsc()
    async with FakeGoogle() as google:
        await gsc.search_totals("2026-11-01", "2026-11-07")
        await integrations.set_value("gsc_key", _key("other@p.iam.gserviceaccount.com"))
        await gsc.search_totals("2026-11-01", "2026-11-07")
        assert google.tokens == 2


async def test_silence_without_a_refusal_is_said_after_a_day(bot, db, monkeypatch):
    monkeypatch.setattr(integrations, "second_opinion_enabled", lambda: True)
    await integrations.record_result("checkhost", False, "HTTP 403")
    await scheduler.run_integration_health()
    assert bot.sent == []
    h = await integrations.health("checkhost")
    h["fail_since"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat(timespec="seconds")
    await set_state("int_health:checkhost", json.dumps(h))
    await scheduler.run_integration_health()
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🟠 check-host.net")


def test_err_text_is_never_empty():
    assert err_text(TimeoutError()) == "TimeoutError" and err_text(ValueError("bad")) == "bad"


async def test_an_unreadable_key_file_is_named(bot, db, cfg, tmp_path):
    cfg(gsc_service_account_file=str(tmp_path))                      # a directory where the file should be
    assert integrations.gsc_credentials() is None and "IsADirectoryError" in integrations.gsc_key_problem()
    (tmp_path / "key.json").write_text('["not", "a", "key"]')
    cfg(gsc_service_account_file=str(tmp_path / "key.json"))
    assert integrations.gsc_credentials() is None and not integrations.gsc_configured()


# ── Google verdicts ──────────────────────────────────────────────────────────

async def test_an_answer_without_a_verdict_is_no_verdict(bot, db):
    await _connect_gsc()
    async with FakeGoogle() as google:
        google.inspect_body = {"inspectionResult": {}}
        assert await gsc.inspect_url("https://ex.test/") is None
        google.inspect_body = {"inspectionResult": {"indexStatusResult": {"verdict": "VERDICT_UNSPECIFIED"}}}
        assert await gsc.inspect_url("https://ex.test/") is None


async def test_removed_means_a_pass_that_stopped_being_one(bot, db, monkeypatch):
    main = await db.activate_or_create_site("https://shop.test")
    await db.activate_or_create_site("https://shop.test/catalog")    # a page of the same host
    verdicts = {"https://shop.test/": "PASS", "https://shop.test/catalog/": "NEUTRAL"}

    async def inspect(url):
        return {"verdict": verdicts[url], "coverage": "Crawled - currently not indexed", "last_crawl": None}
    monkeypatch.setattr(gsc, "available", lambda: True)
    monkeypatch.setattr(gsc, "inspect_url", inspect)
    for _ in range(3):                         # an inner page that was never indexed is a quiet baseline
        await scheduler.run_index_checks()
    assert bot.sent == []
    verdicts["https://shop.test/"] = "NEUTRAL"
    await scheduler.run_index_checks()
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🔴 Google убрал shop.test из поиска")
    assert buttons(bot.sent[0]["reply_markup"]) == ["🔎 Поиск и ИИ", "↗️ Search Console"]
    verdicts["https://shop.test/"] = "PASS"
    await scheduler.run_index_checks()
    assert bot.sent[-1]["text"].startswith("✅ Google снова показывает shop.test") and main


async def test_a_first_look_at_an_error_is_said_without_history(bot, db, monkeypatch):
    await db.activate_or_create_site("https://ex.test")

    async def inspect(url):
        return {"verdict": "FAIL", "coverage": "Excluded by noindex", "last_crawl": None}
    monkeypatch.setattr(gsc, "available", lambda: True)
    monkeypatch.setattr(gsc, "inspect_url", inspect)
    await scheduler.run_index_checks()
    assert bot.sent[-1]["text"].startswith("🔴 Google не показывает ex.test в поиске")


# ── Yandex ───────────────────────────────────────────────────────────────────

async def test_a_mirror_does_not_hide_the_main_host_and_one_host_does_not_lose_all(bot, db, monkeypatch):
    async def user(request):
        return web.json_response({"user_id": 7})

    async def hosts(request):
        return web.json_response({"hosts": [
            {"host_id": "https:example.com:443", "ascii_host_url": "https://example.com/", "verified": True},
            {"host_id": "https:broken.test:443", "ascii_host_url": "https://broken.test/", "verified": True},
            {"host_id": "http:example.com:80", "ascii_host_url": "http://example.com/", "verified": True,
             "main_mirror": {"host_id": "https:example.com:443"}}]})

    async def summary(request):
        if "broken" in request.match_info["host"]:
            return web.Response(text="<html>oops</html>", content_type="text/html")
        if request.match_info["host"].startswith("http:"):
            return web.json_response({"searchable_pages_count": 0, "sqi": 0, "site_problems": {}})
        return web.json_response({"searchable_pages_count": 120, "sqi": 310, "site_problems": {"FATAL": 1}})
    app = web.Application()
    app.router.add_get("/user", user)
    app.router.add_get("/user/7/hosts", hosts)
    app.router.add_get("/user/7/hosts/{host}/summary", summary)
    async with TestServer(app) as srv:
        monkeypatch.setattr(yandex_webmaster, "BASE", str(srv.make_url("")).rstrip("/"))
        await integrations.set_value("yandex_token", "y0_fake")
        got = await yandex_webmaster.get_summaries()
    assert got["example.com"]["alert_problems"] == {"FATAL": 1} and "broken.test" not in got


async def test_yandex_alert_speaks_words_and_is_asked_once(bot, db, monkeypatch):
    for i in range(3):
        await db.activate_or_create_site(f"https://s{i}.test")
    calls = []

    async def summaries(only=None):
        calls.append(only)
        return {"s0.test": {"searchable_pages": 5, "sqi": 10, "alert_problems": {"FATAL": 1, "CRITICAL": 2}}}
    monkeypatch.setattr(yandex_webmaster, "available", lambda: True)
    monkeypatch.setattr(yandex_webmaster, "get_summaries", summaries)
    await scheduler.run_index_checks()
    assert calls == [None]                                           # once for all sites, not per site
    text = bot.sent[-1]["text"]
    assert "1 критическая проблема и 2 серьёзные проблемы на s0.test" in text and "FATAL" not in text
    assert buttons(bot.sent[-1]["reply_markup"]) == ["🔎 Поиск и ИИ", "↗️ Вебмастер"]
    assert index_status.yandex_problem_count({"FATAL": 1, "CRITICAL": 5}) == 6


# ── Weekly report ────────────────────────────────────────────────────────────

async def test_weekly_says_what_it_could_not_get(bot, db, monkeypatch):
    answers = {"cur": {"clicks": 120, "impressions": 4000, "position": 3.0}, "prev": None}

    async def totals(start, end, page_prefix=None):
        return answers["cur"] if start > "2000" and end >= str(datetime.now(UTC).date() - timedelta(days=3)) \
            else answers["prev"]
    await integrations.set_value("gsc_property", "sc-domain:ex.test")
    monkeypatch.setattr(gsc, "available", lambda: True)
    monkeypatch.setattr(gsc, "search_totals", totals)
    lines = await weekly.search_metrics_lines(["https://ex.test", "https://other.org"])
    assert lines == ["🔎 Google (неделя к неделе):",
                     "  ex.test: 120 кликов, 4000 показов (с прошлой неделей сравнить не удалось)"]
    answers["cur"] = None
    await integrations.record_result("gsc", False, "Search Console ответил HTTP 403", refused=True)
    lines = await weekly.search_metrics_lines(["https://ex.test"])
    assert lines == ["⚠️ Google Search Console: не смог получить данные — Search Console ответил HTTP 403"]


async def test_osv_silence_is_not_no_vulnerabilities(bot, db, cfg, monkeypatch):
    cfg(dependency_watch=True)

    async def no_answer(session, versions):
        return None

    async def no_prs(session):
        return []
    monkeypatch.setattr(updates, "_vulnerabilities", no_answer)
    monkeypatch.setattr(updates, "waiting_prs", no_prs)
    await updates.run_dependency_watch()
    assert updates.summary_lines(await updates.last_check())[0].startswith("⚠️ Уязвимости: база OSV не ответила")


# ── CrUX ─────────────────────────────────────────────────────────────────────

async def test_a_refused_crux_key_and_a_removed_one(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")

    async def refused(request):
        return web.json_response({"error": {"code": 403}}, status=403)
    app = web.Application()
    app.router.add_post("/q", refused)
    async with TestServer(app) as srv:
        monkeypatch.setattr(crux, "API", str(srv.make_url("/q")))
        await integrations.set_value("crux_key", "AIza" + "x" * 35)
        assert await visitor_checker.check_all_visitors(["https://ex.test"]) == []
        assert (await integrations.health("crux"))["refused"]
        assert await crux.key_works("AIza" + "x" * 35) is False
    await save_incident(sid, "visitors", "у посетителей страница появляется за 5.2 с")
    await integrations.set_value("crux_key", None)
    await visitor_checker.check_all_visitors(["https://ex.test"])
    assert await get_active_incidents() == []                        # the owner switched it off: closed quietly


# ── GitHub ───────────────────────────────────────────────────────────────────

async def test_a_partial_github_answer_is_said_and_archives_have_no_alerts(bot, db, monkeypatch):
    nodes = [{"nameWithOwner": "me/site", "url": "https://github.com/me/site", "deleteBranchOnMerge": True,
              "defaultBranchRef": {"name": "main"}},
             {"nameWithOwner": "me/old", "isArchived": True, "defaultBranchRef": {"name": "main"}}]

    async def repositories(session, fields, owner):
        return "me", nodes, False, 2

    async def alerts(session, owner):
        return {"me/old": 3, "me/site": 1}
    monkeypatch.setattr(github, "_repositories", repositories)
    monkeypatch.setattr(github, "_alerts", alerts)
    report = await github.check()
    assert report["vulns"] == {"me/site": 1}
    assert github.summary_lines(report)[1].startswith("⚠️ GitHub: 2 репозитория не прочитано")
