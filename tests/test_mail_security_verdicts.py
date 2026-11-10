"""Mail and security verdicts: no «✅» without an answer, a new finding
inside an open incident is said, the screens say when a check did not happen."""

import socket

import aiohttp
import dns.resolver
from aiohttp import web
from aiohttp.test_utils import TestServer
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from conftest import FakeCall
from test_mail import PROTON_MX, FakeNet
from test_security import GOOD_HEADERS, FakeWeb

from db.database import get_active_incidents
from handlers import mail as mail_screen
from handlers import security as security_screen
from monitors import dns_checker, mail_checker, second_opinion
from monitors import security_checker as sc
from monitors.mail_checker import assess, carriers, merge_spf, spf_expand
from reports import scheduler
from services import integrations, sitestatus

REAL_MX = mail_checker._mx
GOOGLE_MX = ["aspmx.l.google.com"]


async def _types() -> set[str]:
    return {i["check_type"] for i in await get_active_incidents()}


def _fixed(bot, start: int) -> list[str]:
    return [m["text"] for m in bot.sent[start:] if m["text"].startswith("✅")]


# ── Mail ─────────────────────────────────────────────────────────────────────

async def test_a_domain_gone_from_dns_is_not_mail_fixed(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    net.down = set(PROTON_MX)
    await scheduler.run_mail_checks()
    await scheduler.run_mail_checks()
    assert await _types() == {"mail", "mail_auth"}
    n = len(bot.sent)

    class Gone:
        async def resolve(self, name, rtype):
            raise dns.resolver.NXDOMAIN
    monkeypatch.setattr(mail_checker, "_mx", REAL_MX)              # the real path: query → NXDOMAIN → None
    monkeypatch.setattr(dns_checker, "_resolver", Gone)
    await scheduler.run_mail_checks()
    assert _fixed(bot, n) == [] and await _types() == {"mail", "mail_auth"}
    assert await mail_checker._mx("ex.test") is None


async def test_mx_removed_closes_the_mail_alarms_without_a_fixed_message(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    net.down = set(PROTON_MX)
    await scheduler.run_mail_checks()
    await scheduler.run_mail_checks()
    n = len(bot.sent)
    del net.zone[("MX", "ex.test")]
    net.zone[("TXT", "ex.test")] = ["v=spf1 -all"]                 # no mail on the domain any more
    await scheduler.run_mail_checks()
    assert _fixed(bot, n) == [] and await _types() == set()


async def test_a_new_spf_problem_is_said_while_mail_auth_is_open(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    net.zone[("TXT", "ex.test")] = ["v=spf1 include:_spf.protonmail.ch ~all"]
    del net.zone[("TXT", "_dmarc.ex.test")]                         # «Нет DMARC» stays open for weeks
    await db.activate_or_create_site("https://ex.test")
    await scheduler.run_mail_checks()
    n = len(bot.sent)
    net.zone[("TXT", "ex.test")].append("v=spf1 include:mailgun.org ~all")      # the classic mistake
    await mail_checker.check_mail("ex.test", "https://ex.test", manage=False)  # the screen sees it first
    await scheduler.run_mail_checks()
    said = [m["text"] for m in bot.sent[n:]]
    assert len(said) == 1 and said[0].startswith("🟠 Почта на ex.test") and "2 записи SPF" in said[0]
    await scheduler.run_mail_checks()
    assert len(bot.sent) == n + 1                                    # said once


async def test_an_upgrade_does_not_repeat_open_alerts(bot, db, monkeypatch):
    FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await scheduler.run_mail_checks()
    mail = (await sitestatus.get(sid))["mail"]
    await sitestatus.update(sid, "mail", **{k: v for k, v in mail.items() if k not in ("told", "at")})
    n = len(bot.sent)
    await scheduler.run_mail_checks()
    assert len(bot.sent) == n


async def test_control_server_is_asked_on_the_nodes_that_failed(bot, db, monkeypatch):
    FakeNet(monkeypatch)
    asked = []

    async def by_node(target, nodes=None):
        """These three nodes cannot open port 25 anywhere; others could."""
        asked.append((target, tuple(nodes or ())))
        return dict.fromkeys(nodes or ["closed1", "closed2", "closed3"], False)
    monkeypatch.setattr(mail_checker, "tcp_by_node", by_node)
    await db.activate_or_create_site("https://ex.test")
    for _ in range(3):
        await scheduler.run_mail_checks()
    assert "mail" not in await _types()
    assert ("gmail-smtp-in.l.google.com:25", ("closed1", "closed2", "closed3")) in asked


async def test_switching_the_external_check_off_closes_the_mail_alarm(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    net.down = set(PROTON_MX)
    await scheduler.run_mail_checks()
    await scheduler.run_mail_checks()
    n = len(bot.sent)
    monkeypatch.setattr(integrations, "second_opinion_enabled", lambda: False)
    await scheduler.run_mail_checks()
    assert "mail" not in await _types() and _fixed(bot, n) == []


async def test_tcp_by_node_asks_exactly_the_given_nodes(monkeypatch):
    seen = []

    async def check_tcp(request):
        seen.append(request.query.getall("node", []))
        return web.json_response({"ok": 1, "request_id": "r1", "nodes": {"de1": [], "us1": []}})

    async def result(request):
        return web.json_response({"de1": [{"time": 0.03, "address": "1.2.3.4"}],
                                  "us1": [{"error": "Connection timed out"}]})

    async def public(url):
        return False
    app = web.Application()
    app.router.add_get("/check-tcp", check_tcp)
    app.router.add_get("/check-result/{rid}", result)
    async with TestServer(app) as srv:
        monkeypatch.setattr(second_opinion, "API", str(srv.make_url("")).rstrip("/"))
        monkeypatch.setattr(second_opinion, "POLL_PAUSES", (0,))
        monkeypatch.setattr(second_opinion, "resolves_private", public)
        got = await second_opinion.tcp_by_node("mx.ex.test:25", nodes=["de1", "us1"])
        # An internal address never reaches the service (the one gate of every entry).
        assert await second_opinion.tcp_by_node("192.168.1.10:25") == {}
    assert seen == [["de1", "us1"]] and got == {"de1": True, "us1": False}


def test_one_spf_record_fixes_everything_at_once():
    found = assess("ex.test", GOOGLE_MX, ["v=spf1 +all"], 0, set(), True, ["v=DMARC1; p=reject"])
    assert [f.code for f in found] == ["spf_open", "spf_no_provider"]
    assert found[0].record == "v=spf1 include:_spf.google.com ~all" and found[1].record == ""
    assert "из пункта выше" in found[1].fix


def test_merge_turns_redirect_into_include():
    merged = merge_spf(["v=spf1 redirect=_spf.yandex.net", "v=spf1 redirect=_spf.hosting.example exp=x.test ~all"],
                       None)
    assert merged == "v=spf1 include:_spf.yandex.net include:_spf.hosting.example ~all"
    assert merge_spf(["v=spf1 mx ~all", "v=spf1 +mx a ~all"], None) == "v=spf1 mx a ~all"


async def test_spf_counting_stops_asking_dns_past_the_limit(monkeypatch):
    asked = []

    async def txt(name):
        asked.append(name)
        return []
    monkeypatch.setattr(mail_checker, "_txt", txt)
    lookups, included = await spf_expand("v=spf1 " + " ".join(f"include:s{i}.test" for i in range(25)) + " ~all")
    assert lookups > mail_checker.SPF_LOOKUP_LIMIT and len(asked) <= mail_checker.SPF_LOOKUP_LIMIT
    assert len(included) == 25                       # every top-level sender is still known


def test_platform_domains_have_no_mail_of_the_owner():
    urls = ["https://nas.synology.me", "https://home.duckdns.org", "https://shop.tilda.ws", "https://ex.test"]
    assert carriers(urls) == {"ex.test": "https://ex.test"}


async def test_mail_recheck_from_the_screen_schedules_the_promised_recheck(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    monkeypatch.setattr(scheduler, "_scheduler", AsyncIOScheduler())
    net.down = set(PROTON_MX)
    call = FakeCall(f"mail_check:{sid}")
    await mail_screen.cb_mail_check(call)
    assert "перепроверю через 10 минут" in call.message.texts[-1]
    assert scheduler._scheduler.get_job("mail_recheck") is not None


async def test_mail_recheck_from_the_screen_says_when_it_did_not_happen(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await scheduler.run_mail_checks()

    async def failed(name):
        return None
    monkeypatch.setattr(mail_checker, "_txt", failed)
    call = FakeCall(f"mail_check:{sid}")
    await mail_screen.cb_mail_check(call)
    assert call.message.texts[-1].startswith("⚠️ Сейчас проверить не получилось")
    assert "Письма принимает: Proton" in call.message.texts[-1]    # the last check is still shown
    assert net.asked                                                # the real check-host fake was asked before


# ── Security ─────────────────────────────────────────────────────────────────

GIT = (200, {"Content-Type": "text/plain"}, "ref: refs/heads/main\n")
ENV = (200, {"Content-Type": "text/plain"}, "SECRET_KEY=x\nDB_PASSWORD=y\n")


async def test_a_refused_port_is_told_apart_from_silence():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        assert await sc._get(session, f"http://127.0.0.1:{port}/") == sc.REFUSED
    assert sc._verdict(sc.REFUSED, lambda g: True) is None
    assert sc._verdict((429, {}, ""), lambda g: True) is None and sc._verdict((503, {}, ""), lambda g: True) is None
    assert sc._verdict((404, {}, ""), lambda g: True) is False and sc._verdict(GIT, lambda g: True) is True


async def test_a_probe_without_an_answer_does_not_close_exposed(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    web.pages["https://ex.test/.git/HEAD"] = GIT
    await scheduler.run_security_checks()
    n = len(bot.sent)
    for silent in (None, (429, {}, ""), (503, {}, ""), sc.REFUSED):   # timeout, rate limit, server error, refused
        web.pages["https://ex.test/.git/HEAD"] = silent
        await scheduler.run_security_checks()
        assert "exposed" in await _types() and len(bot.sent) == n
    web.pages["https://ex.test/.git/HEAD"] = (404, {}, "")
    await scheduler.run_security_checks()
    assert any(t.startswith("✅ На ex.test служебные файлы больше не видны") for t in _fixed(bot, n))


async def test_an_open_env_rings_while_git_is_already_open(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    web.pages["https://ex.test/.git/HEAD"] = GIT
    await scheduler.run_security_checks()
    n = len(bot.sent)
    web.pages["https://ex.test/.env"] = ENV
    await scheduler.run_security_checks()
    said = [m for m in bot.sent[n:] if m["text"].startswith("🔴")]
    assert len(said) == 1 and said[0]["silent"] is False and ".env" in said[0]["text"]
    await scheduler.run_security_checks()
    assert len([m for m in bot.sent[n:] if m["text"].startswith("🔴")]) == 1


async def test_an_http_site_gets_the_service_file_probes(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    sid = await db.activate_or_create_site("http://ex.test")
    web.pages.update({"http://ex.test": (200, {"Content-Type": "text/html"}, "<html>hi</html>"),
                      "http://ex.test/.git/HEAD": (404, {}, ""), "http://ex.test/.env": ENV})
    await scheduler.run_security_checks()
    assert await _types() == {"exposed"}
    call = FakeCall(f"sec:{sid}")
    await security_screen.cb_security(call)
    text = call.message.texts[-1]
    assert "🔴 Наружу виден файл .env" in text and "ℹ️ Сайт работает по http://" in text
    assert "заголовки" not in text.split("ℹ️ Сайт работает")[0]


async def test_a_gate_page_is_not_the_site(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await scheduler.run_security_checks()
    before = (await sitestatus.get(sid))["security"]
    web.pages["https://ex.test"] = (403, {"Content-Type": "text/html"}, "<html>checking your browser</html>")
    assert await sc.check_security("https://ex.test") == []
    web.pages["https://ex.test"] = (200, {"CF-Mitigated": "challenge", "Content-Type": "text/html"}, "<html>")
    assert await sc.check_security("https://ex.test") == []
    assert (await sitestatus.get(sid))["security"] == before


async def test_https_on_its_own_port_gets_no_port_80_verdict(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    await db.activate_or_create_site("https://ex.test:8443")
    web.pages.update({"https://ex.test:8443": (200, dict(GOOD_HEADERS), "<html>hi</html>"),
                      "https://ex.test:8443/.git/HEAD": (404, {}, ""), "https://ex.test:8443/.env": (404, {}, ""),
                      "http://ex.test/": (200, {}, "<html>another service</html>")})
    await scheduler.run_security_checks()
    assert await _types() == set()


async def test_recheck_from_the_screen_says_when_the_site_did_not_answer(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await scheduler.run_security_checks()
    web.pages["https://ex.test"] = (503, {}, "")
    call = FakeCall(f"sec_check:{sid}")
    await security_screen.cb_security_check(call)
    assert call.message.texts[-1].startswith("⚠️ Сайт сейчас не ответил — показываю прошлую проверку")
    assert "🛡 Безопасность ex.test" in call.message.texts[-1]


async def test_port_80_that_never_answers_counts_as_closed(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    web.pages["http://ex.test/"] = (200, {}, "<html>hi</html>")      # http:// shows the site
    await scheduler.run_security_checks()
    assert await _types() == {"security"}
    n = len(bot.sent)
    del web.pages["http://ex.test/"]                                 # a firewall now drops port 80
    await scheduler.run_security_checks()
    await scheduler.run_security_checks()
    assert await _types() == {"security"} and len(bot.sent) == n     # one silence is not a verdict
    await scheduler.run_security_checks()
    assert await _types() == set() and "✅ ex.test: защита сайта в порядке." in _fixed(bot, n)
