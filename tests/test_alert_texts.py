"""Alert and report texts say what really happened, in words, at the owner's
time — and an alert that is over stops looking like a live one."""

import socket
from datetime import UTC, datetime, timedelta

from monitors.availability import check_availability
from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainInfo,
    DomainResult,
    SslInfo,
    SslResult,
)
from reports.formatter import format_compact_status_report, format_dns_change, format_domain_alert
from reports.weekly import renewals_block
from services import sitestatus
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
