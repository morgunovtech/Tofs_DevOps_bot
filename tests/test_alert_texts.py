"""Alert and report texts say what really happened, in words, at the owner's
time — and an alert that is over stops looking like a live one."""

import socket
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aiogram import Bot
from aiogram.methods import EditMessageText, SendMessage
from conftest import FakeCall, buttons
from test_dispatcher import DP, MockedSession, _callback

from db.database import get_active_incidents, resolve_incident, save_incident
from handlers import about, alerts, incidents, mute, speed
from monitors.availability import check_availability
from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainInfo,
    DomainResult,
    SeoResult,
    SslInfo,
    SslResult,
)
from reports import scheduler
from reports.formatter import format_compact_status_report, format_dns_change, format_domain_alert
from reports.weekly import renewals_block
from services import integrations, maintenance, notifier, settings, sitestatus
from services.actions import explain_keyboard, heartbeat_keyboard
from services.humanize import classify, describe_error
from utils import clock


def test_connection_errors_are_said_in_words_without_raw_text():
    raw = "Cannot connect to host 127.0.0.1:1 ssl:default [Connect call failed ('127.0.0.1', 1)]"
    assert describe_error(raw) == "не удалось соединиться с сервером"
    refused = "Cannot connect to host 127.0.0.1:1 [Connection refused]"
    assert describe_error(refused) == "сервер отказал в соединении"
    assert classify("availability", f"Site down: {refused}") == "refused"
    assert describe_error("Server disconnected") == "сервер оборвал соединение"
    assert describe_error("[Errno 104] Connection reset by peer") == "сервер оборвал соединение"
    leaked = "0, message='', url='http://127.0.0.1:8/api/v1/status?token=SECRET'"
    assert describe_error(leaked) == "сервер ответил ошибкой" and "SECRET" not in describe_error(leaked)
    assert describe_error("ClientPayloadError") == "необычная ошибка соединения"
    assert describe_error("timeout") == "не ответил вовремя"
    assert describe_error("что-то своё") == "что-то своё"            # Russian text passes as is


async def test_a_closed_port_is_called_refused(db):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    url = f"http://127.0.0.1:{port}/"
    await db.activate_or_create_site(url)
    r = await check_availability(url, manage=False)
    assert describe_error(r.error) == "сервер отказал в соединении"
    assert classify("availability", f"Site down: {r.error}") == "refused"
    r = await check_availability(f"tcp://127.0.0.1:{port}", manage=False)
    assert describe_error(r.error) == "сервер отказал в соединении"


def test_dns_alert_agrees_and_does_not_reassure_when_a_record_is_gone():
    gone = format_dns_change(DnsChange(host="ex.com", rtype="A", old="93.184.216.34", new="—", critical=False))
    assert "У ex.com пропал адрес сервера (запись A)" in gone
    assert "Пока ничего не сломалось" not in gone and "запомнил новое значение" not in gone
    changed = format_dns_change(DnsChange(host="ex.com", rtype="A", old="1.1.1.1", new="2.2.2.2", critical=False))
    assert "У ex.com изменился адрес сервера (запись A)" in changed
    ns = format_dns_change(DnsChange(host="ex.com", rtype="NS", old="a", new="b", critical=True))
    assert ns.startswith("🔴 У ex.com изменились NS-серверы домена")


def test_the_last_days_read_as_words_and_the_domain_header_says_it_once():
    assert describe_error("Domain expires in 0 days!") == "домен истекает сегодня"
    assert describe_error("SSL expires in 1 days!") == "сертификат истекает завтра"
    assert describe_error("SSL expires in 10 days") == "сертификат истекает через 10 дн."
    r = DomainResult(url="https://ex.test", site_id=1, status="critical", error="Domain expires in 0 days!",
                     domain="ex.test", domain_info=DomainInfo("ex.test", "R", None, None, 0, []))
    assert format_domain_alert(r).startswith("🔴 ex.test: домен истекает сегодня")
    avail = [AvailabilityResult(url="https://ex.test", site_id=1, response_time_ms=95, status_code=200)]
    ssl = [SslResult(url="https://ex.test", site_id=1, ssl_info=SslInfo("ex.test", "LE", "", "", 0, "1"))]
    assert "сертификат истекает сегодня" in format_compact_status_report(avail, [], ssl, [])


async def test_renewal_date_is_the_owners_date(bot, db):
    sid = await db.activate_or_create_site("https://far.test")
    at = (datetime.now(UTC) - timedelta(days=1)).replace(hour=22, minute=0, second=0, microsecond=0)
    await sitestatus.update(sid, "ssl", days_left=80, issuer="LE", not_after=None, error=None,
                            renewed_at=at.isoformat(timespec="seconds"))
    clock.set_timezone("Asia/Vladivostok")              # UTC+10, no DST: 22:00 UTC is already tomorrow
    try:
        lines = await renewals_block([await db.get_site(sid)])
    finally:
        clock.set_timezone(None)
    assert f"far.test ({(at + timedelta(hours=10)).strftime('%d.%m')})" in lines[0]


async def test_screen_buttons_on_alerts_answer_below(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    r = SimpleNamespace(site_id=sid, incident_id=7, kind="mail_auth", status_page=None, provider=None,
                        url="https://ex.test")
    datas = [b.callback_data for kb in (scheduler._mail_keyboard(r), scheduler._security_keyboard(r),
                                        scheduler._visitors_keyboard(r))
             for row in kb.inline_keyboard for b in row if b.callback_data]
    assert sorted(d.split(":")[0] for d in datas if not d.startswith("inc_explain")) == ["mail", "sec", "spd"]
    assert all(d.endswith(":a") for d in datas)
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    for n, prefix in enumerate(("sec", "spd", "mail"), 1):
        session.calls.clear()
        await DP.feed_raw_update(tg, _callback(777, f"{prefix}:{sid}:a", 900 + n))
        assert any(isinstance(m, SendMessage) for m in session.calls), prefix
        assert not any(isinstance(m, EditMessageText) for m in session.calls), prefix
    await tg.session.close()


async def test_ringing_promises_follow_the_setting(bot, db, monkeypatch):
    monkeypatch.setattr(settings, "ring_only_down", lambda: False)
    assert "Звоню только по 🔴" not in about.features_text()
    text = await mute._mute("8h")
    assert "Остальное пришлю потом одним сообщением" not in text and "снова открылся" in text
    monkeypatch.setattr(settings, "ring_only_down", lambda: True)
    assert "только когда сайт лёг" in about.features_text()
    assert "снова открылся" not in await mute._mute("8h")
    await notifier.set_mute(None)


async def test_speed_screen_names_the_real_next_run(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    await integrations.set_value("crux_key", "k" * 39)
    tomorrow = clock.now_local().replace(hour=8, minute=10, second=0, microsecond=0) + timedelta(days=1)
    job = SimpleNamespace(next_run_time=tomorrow)
    monkeypatch.setattr(scheduler, "_scheduler", SimpleNamespace(get_job=lambda job_id: job))
    assert scheduler.next_run_words("visitor_checks") == "завтра в 08:10"
    text, _ = await speed.speed_page(await db.get_site(sid))
    assert "сегодня в 08:10" not in text and "завтра в 08:10" in text
    monkeypatch.setattr(scheduler, "_scheduler", None)
    assert scheduler.next_run_words("visitor_checks") is None      # no scheduler: no promise with a time
    assert "в ближайшее утро" in (await speed.speed_page(await db.get_site(sid)))[0]
    await integrations.set_value("crux_key", None)


def test_silent_task_buttons_say_what_they_do():
    assert buttons(heartbeat_keyboard(["backup"])) == ["🔗 Подключить backup", "🗑 Удалить"]
    assert buttons(heartbeat_keyboard(["backup"], seen={"backup"})) == ["🔍 Что с backup", "🗑 Удалить"]


async def test_ns_change_alert_links_the_registrar(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.com")
    await sitestatus.update(sid, "domain", registrar="NameCheap, Inc.", days_left=200)

    async def fake_dns(urls):
        return [DnsChange(host="ex.com", rtype="NS", old="a", new="b", critical=True)]

    monkeypatch.setattr(scheduler, "check_all_dns", fake_dns)
    await scheduler.run_dns_checks()
    kb = bot.sent[-1]["reply_markup"]
    assert kb is not None and any("namecheap" in (b.url or "") for row in kb.inline_keyboard for b in row)


async def test_quiet_queue_is_one_digest_with_times_and_numbered_buttons(bot, db):
    await notifier.set_mute(timedelta(hours=1))
    for i in (1, 2, 3):
        assert await notifier.send(f"🟠 site{i}.test: защиту сайта стоит усилить", reply_markup=explain_keyboard(i))
    conn = await db.get_db()
    await conn.execute("UPDATE pending_notifications SET created_at = '2026-11-08 20:15:00'")
    await conn.commit()
    await notifier.set_mute(None)
    await notifier.flush_queue()
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert text.startswith("🌙 Накопилось") and text.count("23:15") == 3     # 20:15 UTC in Moscow
    assert "1. " in text and "3. " in text
    kb = bot.sent[0]["reply_markup"]
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        "inc_explain:1:a", "inc_explain:2:a", "inc_explain:3:a"]
    assert buttons(kb) == ["ℹ️ 1", "ℹ️ 2", "ℹ️ 3"]


async def test_recovery_settles_the_original_alert_and_a_working_site_is_not_paused(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await save_incident(sid, "performance", "Slow responses: ~4000ms", "warning")
    scheduler._slow_streak[sid] = 3
    down = AvailabilityResult(url="https://ex.test", site_id=sid, status="error", error="HTTP 502",
                              status_code=502, incident_new=True, incident_id=inc_id)
    batches = [[down]]

    async def fake_check_all(urls, **kwargs):
        return batches.pop(0)

    monkeypatch.setattr(scheduler, "check_all", fake_check_all)
    monkeypatch.setattr(scheduler, "_next_avail_check", {})
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()                  # the tick only checks: the worker alerts
    alert_id = len(bot.sent)                              # FakeBot: message_id = position in bot.sent
    assert bot.sent[-1]["text"].startswith("🔴 ex.test не открывается")
    # One problem, not two: «медленно» is closed when the site goes down.
    assert [i["check_type"] for i in await get_active_incidents()] == ["availability"]
    assert sid not in scheduler._slow_streak

    up = AvailabilityResult(url="https://ex.test", site_id=sid, status_code=200, response_time_ms=90,
                            recovered=True, resolved_incident=await resolve_incident(sid, "availability"))
    batches.append([up])
    monkeypatch.setattr(scheduler, "_next_avail_check", {})
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()
    assert bot.sent[-1]["text"].startswith("✅ ex.test снова открывается")
    edit = next(e for e in bot.edited if e["message_id"] == alert_id)
    assert edit["text"].startswith("✅ Поднялся в ") and "не открывается" in edit["text"]
    datas = [b.callback_data for row in (edit["reply_markup"] or SimpleNamespace(inline_keyboard=[])).inline_keyboard
             for b in row]
    assert datas and not any(d.startswith("act:") for d in datas)
    assert await db.get_state(f"alert_msgs:{inc_id}") is None    # settled once

    call = FakeCall(f"act:fix:{sid}")                     # «🔧 Я чиню» on the old alert
    await alerts.cb_alert_action(call)
    assert not await maintenance.is_paused(sid) and "работает" in call.answers[-1]
    # Pages with errors (a «deep» incident) are still something to fix: the pause is granted.
    await save_incident(sid, "deep", "5xx на 3 из 10 проверенных страниц", "warning")
    call = FakeCall(f"act:fix:{sid}")
    await alerts.cb_alert_action(call)
    assert await maintenance.is_paused(sid)


async def test_a_closed_problem_reads_as_past_and_offers_no_pause(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    inc = await db.get_incident(inc_id)
    assert incidents.explain_text(inc).startswith("🔴 ex.test не открывается")
    datas = [b.callback_data for row in (await incidents.explain_keyboard(inc)).inline_keyboard for b in row]
    assert f"act:fix:{sid}" in datas
    await resolve_incident(sid, "availability")
    inc = await db.get_incident(inc_id)
    assert incidents.explain_text(inc).startswith("✅ Было: ex.test не открывается")
    datas = [b.callback_data for row in (await incidents.explain_keyboard(inc)).inline_keyboard for b in row]
    assert not any((d or "").startswith("act:") for d in datas)


async def test_the_quiet_first_audit_is_said_in_the_next_morning_digest(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://new-shop.test")
    await db.save_check(sid, "availability", "ok", response_time_ms=100)

    async def fake_seo(urls, manage=True):
        if not urls:
            return []
        await db.save_check(sid, "seo", "critical", details="noindex")
        return [SeoResult(url="https://new-shop.test", site_id=sid, status="critical", problems=[],
                          pages_checked=1, no_js_chars=0)]

    monkeypatch.setattr(scheduler, "check_all_seo", fake_seo)
    await scheduler.run_seo_checks()                      # added minutes ago: audited quietly
    assert bot.sent == []
    extras = "\n".join(await scheduler._morning_extras([], [], await get_active_incidents()))
    assert "🔎 Первый аудит new-shop.test для поиска и ИИ: 🔴 сайт закрыт от поиска" in extras
    again = "\n".join(await scheduler._morning_extras([], [], await get_active_incidents()))
    assert "Первый аудит" not in again                    # said once
