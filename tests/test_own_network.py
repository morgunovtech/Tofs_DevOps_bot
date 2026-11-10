"""A failure on the bot's side is not a site's failure: no network or DNS on
the bot's server, ping without the right to open an ICMP socket, request
settings aiohttp refuses — none of these opens an incident or counts as
downtime."""

import json
import logging
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents, get_uptime_stats, set_state
from monitors import availability, netcheck, second_opinion
from monitors.availability import check_availability
from monitors.base import AvailabilityResult
from reports import scheduler
from reports.formatter import format_availability_alert, format_compact_status_report
from services import sitestatus
from web import status_page

NO_DNS = "Cannot connect to host shop.test:443 ssl:default [Temporary failure in name resolution]"


async def _site_server():
    async def handler(_):
        return web.Response(text="<html>ok</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    return TestServer(app)


async def _tick():
    netcheck._checked_at = None
    scheduler._next_avail_check.clear()
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()


async def test_probe_needs_an_ip_and_a_name(cfg, monkeypatch):
    cfg(net_canaries=("192.0.2.1", "canary.test"))
    answers = {"192.0.2.1": True, "canary.test": False}

    async def tcp(ip):
        return answers[ip]

    async def resolves(name):
        return answers[name]

    monkeypatch.setattr(netcheck, "_tcp", tcp)
    monkeypatch.setattr(netcheck, "_resolves", resolves)
    assert await netcheck.probe() is False          # uplink fine, resolver broken
    answers["canary.test"] = True
    assert await netcheck.probe() is True
    answers["192.0.2.1"] = False
    assert await netcheck.probe() is False          # resolver fine, no uplink
    cfg(net_canaries=())
    assert await netcheck.probe() is True           # NET_CANARIES= switches it off


async def test_one_probe_per_tick(cfg, monkeypatch):
    cfg(net_canaries=("192.0.2.1",))
    calls = []

    async def probe():
        calls.append(1)
        return False

    monkeypatch.setattr(netcheck, "probe", probe)
    for _ in range(20):
        assert await netcheck.own_network_ok() is False
    assert len(calls) == 1


async def test_no_network_on_the_bot_opens_no_incidents_and_says_it_once(bot, db, cfg, monkeypatch):
    cfg(net_canaries=("192.0.2.1",))
    async with await _site_server() as server:
        urls = [str(server.make_url(f"/s{i}")) for i in range(3)]
        for u in urls:
            sid = await db.activate_or_create_site(u)
            await db.update_site_settings(sid, fail_threshold=1)
        network = {"up": False}
        real_probe = availability._probe_http

        async def probe_http(url, site, r):
            if network["up"]:
                await real_probe(url, site, r)
            else:
                r.status, r.error = "error", NO_DNS

        async def canaries():
            return network["up"]

        monkeypatch.setattr(availability, "_probe_http", probe_http)
        monkeypatch.setattr(netcheck, "probe", canaries)
        for _ in range(3):                                   # three ticks without network
            await _tick()
        assert await get_active_incidents() == []
        assert bot.sent == []                                # not a single alert
        assert (await get_uptime_stats(sid))["total_checks"] == 0   # and no downtime
        assert (await sitestatus.get(sid))["avail"]["status"] == "unknown"
        assert await netcheck.outage_since() is not None
        # The sites live on 127.0.0.1 — an internal address is never published at all;
        # the «nodata» verdict itself is covered in tests/test_status_page.py.
        assert (await status_page.build_payload())["sites"] == []

        network["up"] = True                                 # the network is back
        await _tick()
        assert len(bot.sent) == 1
        text = bot.sent[0]["text"]
        assert "не было сети" in text and "Сейчас всё открывается" in text
        assert bot.sent[0]["silent"] is True
        assert await netcheck.outage_since() is None
        assert (await get_uptime_stats(sid))["uptime_pct"] == 100


async def test_blocked_canaries_do_not_silence_a_real_outage(bot, db, cfg, monkeypatch):
    """A firewall cuts 1.1.1.1:443 while a site answers 200: the network is
    up, and a site that refuses connections is really down."""
    cfg(net_canaries=("192.0.2.1",))

    async def dead_canaries():
        return False
    monkeypatch.setattr(netcheck, "probe", dead_canaries)
    async with await _site_server() as server:
        up = await db.activate_or_create_site(str(server.make_url("/up")))
        down = await db.activate_or_create_site("http://127.0.0.1:1/")          # nothing listens on :1
        for sid in (up, down):
            await db.update_site_settings(sid, fail_threshold=1)
        await _tick()
    incidents = await get_active_incidents()
    assert [i["site_id"] for i in incidents] == [down]
    assert any("не открывается" in m["text"] for m in bot.sent)
    assert await netcheck.outage_since() is None


async def test_start_check_switches_itself_off_when_sites_answer(bot, db, cfg, monkeypatch):
    cfg(net_canaries=("192.0.2.1",))

    async def dead_canaries():
        return False
    monkeypatch.setattr(netcheck, "probe", dead_canaries)
    netcheck.note_answer()                                   # the first tick already heard from a site
    await scheduler.check_own_network_at_start()
    assert not netcheck.enabled() and "Самопроверку сети я выключил" in bot.sent[-1]["text"]
    assert await netcheck.own_network_ok() is True


async def test_escalation_waits_while_the_bot_has_no_network(bot, db, cfg):
    cfg(escalation_repeat_min=1)
    sid = await db.get_or_create_site("https://down.test")
    inc_id, _ = await db.save_incident(sid, "availability", "Site down", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-10 minutes') WHERE id = ?",
                       (inc_id,))
    await conn.commit()
    await set_state(netcheck.OUTAGE_KEY, "2026-01-01T10:00:00+00:00")
    await scheduler.run_escalation_watch()
    assert bot.sent == []


def _fake_ping(tmp_path, output: str, code: int):
    """A stand-in for iputils ping, first in PATH: records its arguments, prints `output`."""
    ping = tmp_path / "ping"
    ping.write_text(f"""#!/bin/sh
for a in "$@"; do printf '%s ' "$a"; done > "{tmp_path}/argv"
printf '%s' "{output}"
exit {code}
""")
    ping.chmod(0o755)
    return tmp_path / "argv"


@pytest.mark.parametrize("output", [
    "/usr/bin/ping: socket: Address family not supported by protocol",
    "/usr/bin/ping: socket: Operation not permitted",
])
async def test_ping_without_icmp_rights_is_the_bots_problem(bot, db, tmp_path, monkeypatch, output):
    argv = _fake_ping(tmp_path, output, 2)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    sid = await db.activate_or_create_site("ping://10.0.0.1")
    await db.update_site_settings(sid, fail_threshold=1)
    for _ in range(3):
        r = await check_availability("ping://10.0.0.1")
        assert r.status == "unknown" and r.transient and "ICMP" in r.error
    assert await get_active_incidents() == []
    assert argv.read_text().split() == ["-n", "-c", "1", "-W", "5", "--", "10.0.0.1"]
    await _tick()                                            # the owner hears it once, without a ring
    assert len(bot.sent) == 1 and "не могу проверить" in bot.sent[0]["text"] and bot.sent[0]["silent"]
    await _tick()
    assert len(bot.sent) == 1
    report = format_compact_status_report(await availability.check_all(["ping://10.0.0.1"], manage=False), [], [])
    assert "⚠️ ping 10.0.0.1 — не могу проверить" in report and "🔴" not in report


async def test_ping_reads_rtt_and_a_silent_host(db, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    argv = _fake_ping(tmp_path, "64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=12.3 ms", 0)
    r = await check_availability("ping://10.0.0.2", manage=False)
    assert r.ok and r.response_time_ms == 12
    assert argv.read_text().split() == ["-n", "-c", "1", "-W", "5", "--", "10.0.0.2"]
    _fake_ping(tmp_path, "1 packets transmitted, 0 received, 100% packet loss, time 0ms", 1)
    r = await check_availability("ping://10.0.0.2", manage=False)
    assert r.status == "error" and r.error == "хост не отвечает на ping"


async def test_rejected_request_settings_are_not_an_outage(bot, db, caplog):
    async with await _site_server() as server:
        url = str(server.make_url("/api"))
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1,
                                      http_headers=json.dumps({"X-Token": "abc\nX-Injected: 1"}))
        with caplog.at_level(logging.WARNING, logger="monitors.availability"):
            r = await check_availability(url)
        assert r.status == "unknown" and r.transient and "настройки запроса" in r.error
        assert await get_active_incidents() == []
        assert any("request settings rejected" in rec.getMessage() for rec in caplog.records)


async def test_down_from_here_but_up_outside_is_not_downtime(db, monkeypatch):
    async def up_outside(url, region=None):
        return second_opinion.Verdict(True, 3, 3)

    monkeypatch.setattr(availability, "second_opinion", up_outside)
    monkeypatch.setattr(availability.integrations, "second_opinion_enabled", lambda: True)
    sid = await db.activate_or_create_site("http://127.0.0.1:1/")       # nothing listens on :1
    await db.update_site_settings(sid, fail_threshold=1)
    for _ in range(5):                                                   # odd and even checks alike
        r = await check_availability("http://127.0.0.1:1/")
        assert r.status in ("unknown", "error")
        if r.status == "unknown":
            assert "снаружи открывается" in r.error
        assert (await status_page.build_payload())["sites"] == []        # internal: not on the page at all
        assert (await get_uptime_stats(sid))["total_checks"] == 0       # nothing known about the site ≠ its downtime
    assert await get_active_incidents() == []
    assert r.status == "unknown"


def test_alert_without_external_check_does_not_claim_everyone():
    r = AvailabilityResult(url="https://shop.test", site_id=1, status="error",
                           error="Cannot connect to host shop.test:443 ssl:default [Connect call failed]")
    text = format_availability_alert(r, second_opinion=True)
    assert "ни у кого" not in text and "снаружи проверить не удалось" in text
    r.external_ok = False
    assert "не открывается ни у кого" in format_availability_alert(r, second_opinion=True)
