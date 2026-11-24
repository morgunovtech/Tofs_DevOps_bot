"""What is published but not watched (DNS, Pages, Worker routes, GitHub
homepages → one Sunday line with «➕ Следить» / «Не надо»), and what in the
zone points nowhere: a takeover risk rings once and says when it is gone,
a dead end and a leftover of a former mail service wait for Sunday.
Cloudflare is a local server, DNS lookups and GitHub are stubs."""

import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeState, buttons
from test_i18n import screen_english

from handlers import published as pub_buttons
from handlers import sites
from monitors.base import AvailabilityResult, SslResult
from reports import scheduler
from services import cloudflare, github, integrations, published, sitestatus
from utils import i18n

ZONE = "b" * 32
ACCOUNT = "acc2"


def _items(line: str) -> list[str]:
    """The addresses of «🟠 N адресов без присмотра: a, b — опубликованы…»."""
    return re.findall(r"[^,\s][^,(]* \([^)]*\)", line.split(": ", 1)[1].split(" — ")[0])


def rec(rtype: str, name: str, content: str, proxied: bool = False) -> dict:
    return {"id": f"{rtype}-{name}", "type": rtype, "name": name, "content": content, "proxied": proxied}


ZONE_RECORDS = [
    rec("A", "ex.test", "192.0.2.1", proxied=True),                       # the apex: a redirect host
    rec("CNAME", "shop.ex.test", "shop-1x.pages.dev", proxied=True),      # watched
    rec("CNAME", "fp.ex.test", "fp.ex.test", proxied=True),               # a Worker route on it
    rec("A", "mail.ex.test", "192.0.2.9"),                                # not the web: not proxied
    rec("CNAME", "old.ex.test", "gone-proj.pages.dev", proxied=True),    # 🔴 no such Pages project
    rec("CNAME", "lost.ex.test", "app.expired-domain.test"),              # 🔴 the domain is free to buy
    rec("CNAME", "dead.ex.test", "nothing.example.org"),                  # 🟠 a dead end
    rec("CNAME", "wk.ex.test", "api.acme.workers.dev"),                   # our Worker: fine
    rec("MX", "ex.test", "mail.protonmail.ch"),
    rec("TXT", "ex.test", "v=spf1 include:_spf.protonmail.ch ~all"),
    rec("TXT", "ex.test", "apple-domain=abc123"),                          # 🟠 iCloud is gone
    rec("CNAME", "sig1._domainkey.ex.test", "sig1.dkim.ex.test.at.icloudmailadmin.com"),
    rec("TXT", "_dmarc.ex.test", "v=DMARC1; p=quarantine"),
]


@pytest.fixture
async def cf(monkeypatch):
    state = {"records": list(ZONE_RECORDS), "refuse": set(),
             "projects": [{"name": "shop", "subdomain": "shop-1x.pages.dev", "domains": ["shop.ex.test", "good.ex.test"]}],
             "routes": [{"id": "r1", "pattern": "fp.ex.test/api/*", "script": "fp-api"}],
             "scripts": [{"id": "api", "modified_on": "2026-11-20T10:00:00Z"}]}

    def answer(kind, result, info=None):
        if kind in state["refuse"]:
            return web.json_response({"success": False, "errors": [{"code": 9109, "message": "Unauthorized"}]},
                                     status=403)
        return web.json_response({"success": True, "errors": [], "result": result,
                                  "result_info": info or {"page": 1, "total_pages": 1}})

    def serve(kind, result):
        async def handler(request):
            return answer(kind, result())
        return handler

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE}",
                       serve("zone", lambda: {"id": ZONE, "name": "ex.test", "account": {"id": ACCOUNT}}))
    app.router.add_get(f"/client/v4/zones/{ZONE}/dns_records", serve("dns", lambda: state["records"]))
    app.router.add_get(f"/client/v4/zones/{ZONE}/workers/routes", serve("routes", lambda: state["routes"]))
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/pages/projects", serve("pages", lambda: state["projects"]))
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/workers/scripts", serve("scripts", lambda: state["scripts"]))
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/workers/subdomain",
                       serve("scripts", lambda: {"subdomain": "acme"}))

    async def resolve(host, rtype):
        if host in ("app.expired-domain.test", "nothing.example.org"):
            return []                                        # NXDOMAIN
        if rtype == "NS":
            return [] if host == "expired-domain.test" else ["ns1.example.org"]
        return ["192.0.2.50"]

    async def repos():
        return [{"name": "me/good", "url": "https://github.com/me/good", "homepage": "https://good.ex.test/",
                 "release": "v1.0.0", "released_at": "2026-11-01T00:00:00Z"},
                # a page on someone else's site is not the owner's site (2026-11-24: Railway templates)
                {"name": "me/bot", "url": "https://github.com/me/bot", "homepage": "https://railway.com/deploy/abc",
                 "release": None, "released_at": None}]

    monkeypatch.setattr(published, "resolve", resolve)
    monkeypatch.setattr(github, "published_repos", repos)
    monkeypatch.setattr(github, "available", lambda: True)
    async with TestServer(app) as server:
        monkeypatch.setattr(cloudflare, "API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE)
        yield state


async def test_what_is_published_but_not_watched(bot, db, cf):
    await db.activate_or_create_site("https://shop.ex.test")
    await published.run()
    text, kb = await published.sunday_message()
    first = text.split("\n")[0]
    assert _items(first) == ["ex.test (DNS)", "fp.ex.test (DNS)", "fp.ex.test/api/* (Worker)",
                             "good.ex.test (Pages, GitHub)", "wk.ex.test (DNS)"]
    assert first.startswith("🟠 5 адресов без присмотра:")
    # shop is watched; mail.ex.test is not the web; old.ex.test is a takeover risk, said on its own;
    # dead.ex.test is a DNS-only CNAME to no platform: not a published site.
    assert buttons(kb)[:2] == ["➕ Следить: ex.test", "Не надо"]
    assert all(len(b.callback_data.encode()) <= 64 for row in kb.inline_keyboard for b in row)


async def test_an_api_check_under_a_route_covers_it_and_no_is_remembered(bot, db, cf):
    await db.activate_or_create_site("https://shop.ex.test")
    await db.activate_or_create_site("https://fp.ex.test/api/health")
    await published.run()
    text, _ = await published.sunday_message()
    assert _items(text.split("\n")[0]) == ["ex.test (DNS)", "good.ex.test (Pages, GitHub)", "wk.ex.test (DNS)"]
    call = FakeCall(f"pub:no:{published.key_hash('ex.test')}")
    await pub_buttons.cb_published(call, FakeState())
    assert "ex.test не предлагаю" in call.message.texts[-1]
    text, kb = await published.sunday_message()
    assert _items(text.split("\n")[0]) == ["good.ex.test (Pages, GitHub)", "wk.ex.test (DNS)"]
    assert buttons(kb)[0] == "➕ Следить: good.ex.test"


async def test_follow_adds_the_site_and_a_route_asks_for_its_api(bot, db, cf, monkeypatch):
    async def avail(url, manage=True):
        site = await db.get_site_by_url(url)
        await sitestatus.update(site["id"], "avail", status="ok", ms=40, error=None)
        return AvailabilityResult(url=url, site_id=site["id"], response_time_ms=40)

    async def nothing(*args, **kwargs):
        return None

    async def cert(url, manage=True):
        return SslResult(url=url, site_id=0)
    for name in ("alt_host_note", "suggest_keyword"):
        monkeypatch.setattr(sites, name, nothing)
    monkeypatch.setattr(sites, "check_availability", avail)
    monkeypatch.setattr(sites, "check_ssl", cert)
    await db.activate_or_create_site("https://shop.ex.test")
    await published.run()
    call = FakeCall(f"pub:add:{published.key_hash('good.ex.test')}")
    await pub_buttons.cb_published(call, FakeState())
    assert any("good.ex.test под присмотром" in text for text in call.message.texts)
    assert await db.get_site_by_url("https://good.ex.test")

    state = FakeState()
    call = FakeCall(f"pub:add:{published.key_hash('fp.ex.test/api/*')}")
    await pub_buttons.cb_published(call, state)
    assert state.state == sites.AddSiteForm.url
    assert "<code>fp.ex.test/api/health</code>" in call.message.texts[-1]


async def test_a_record_anyone_can_take_over_rings_once_and_says_when_it_is_gone(bot, db, cf):
    await published.run()
    said = [m for m in bot.sent if m["text"].startswith("🔴")]
    assert [m["text"].split("\n")[0] for m in said] == [
        "🔴 lost.ex.test можно захватить: запись CNAME ведёт на app.expired-domain.test, а домена, на который она "
        "ведёт, не существует — его может купить кто угодно",
        "🔴 old.ex.test можно захватить: запись CNAME ведёт на gone-proj.pages.dev, а такого проекта Cloudflare "
        "Pages в аккаунте нет"]
    assert all(m["silent"] is False for m in said)
    assert said[0]["reply_markup"].inline_keyboard[0][0].url == f"https://dash.cloudflare.com/{ACCOUNT}/ex.test/dns/records"
    n = len(bot.sent)
    await published.run()
    assert len(bot.sent) == n                                     # said once

    cf["refuse"].add("pages")                                     # no verdict about Pages: nothing said
    await published.run()
    assert len(bot.sent) == n
    cf["refuse"].clear()
    cf["records"] = [r for r in cf["records"] if r["name"] != "old.ex.test"]
    await published.run()
    assert bot.sent[-1]["text"] == "✅ old.ex.test: запись, которая вела на gone-proj.pages.dev, больше не опасна."


async def test_dead_ends_and_leftovers_of_a_former_mail_service_wait_for_sunday(bot, db, cf):
    await published.run()
    text, _ = await published.sunday_message()
    assert "🟠 Записи DNS ведут в никуда: dead.ex.test → nothing.example.org — можно удалить." in text
    assert ("🟠 В DNS остались записи почтовых сервисов, которых нет в MX и SPF: apple-domain=… (iCloud), "
            "sig1._domainkey (iCloud) — можно удалить.") in text
    assert "Proton" not in text                                   # the one in use


async def test_what_could_not_be_read_is_named(bot, db, cf):
    cf["refuse"] |= {"routes", "dns"}
    await published.run()
    text, _ = await published.sunday_message()
    assert "⚠️ Не прочитал маршруты Worker-ов: токену не хватает права Zone → Workers Routes → Read." in text
    h = await integrations.health("cf_zone")
    assert h["refused"] and "Zone → DNS → Read" in h["reason"]
    assert not [m for m in bot.sent if m["text"].startswith("🔴")]   # no DNS, no verdict


async def test_the_sunday_report_sends_it_with_its_buttons(bot, db, cf, monkeypatch):
    async def nothing():
        return None
    monkeypatch.setattr(scheduler.updates, "run_dependency_watch", nothing)
    monkeypatch.setattr(scheduler.github, "run_watch", nothing)
    monkeypatch.setattr(scheduler, "send_db_backup_to_telegram", nothing)
    await scheduler.send_weekly_report()
    msg = next(m for m in bot.sent if "без присмотра" in m["text"])
    assert msg["silent"] is True and "➕ Следить: ex.test" in buttons(msg["reply_markup"])


async def test_in_english(bot, db, cf):
    await published.run()
    i18n.set_language("en")
    try:
        text, kb = await published.sunday_message()
        screen_english(text, kb)
        screen_english(published.dangling_text("old.ex.test", "x.pages.dev", "pages"))
    finally:
        i18n.set_language("ru")
