from datetime import UTC, datetime

from handlers.seo import fix_text, seo_text
from monitors.base import (
    AvailabilityResult,
    DnsChange,
    DomainInfo,
    DomainResult,
    LinkCheck,
    LinksResult,
    SeoProblem,
    SeoResult,
    SslInfo,
    SslResult,
)
from reports.formatter import (
    external_links_block,
    format_availability_alert,
    format_compact_status_report,
    format_dns_change,
    format_links_report,
    format_recovery_alert,
    format_seo_alert,
    format_ssl_alert,
)

NOW = datetime.now(UTC).isoformat(timespec="seconds")


def test_availability_alert_escapes_and_omits_code_for_tcp():
    r = AvailabilityResult(url="https://ex.com", site_id=1, status="error",
                           error="<script>", status_code=502, response_time_ms=10)
    text = format_availability_alert(r)
    assert "&lt;script&gt;" in text and "<script>" not in text
    assert text.startswith("🔴 ex.com не открывается") and "Продолжаю проверять" in text
    assert "Что делать:" not in text            # steps live behind the «ℹ️ Что делать» button
    r502 = AvailabilityResult(url="https://ex.com", site_id=1, status="error", error="HTTP 502",
                              status_code=502, external_ok=False)
    text = format_availability_alert(r502)
    assert "хостинг отвечает ошибкой 502" in text and "не открывается ни у кого" in text
    tcp = AvailabilityResult(url="tcp://ex.com:25", site_id=1, status="error", error="Connection refused")
    assert "ex.com:25 не отвечает" in format_availability_alert(tcp)
    kw = AvailabilityResult(url="https://ex.com", site_id=1, status="error",
                            error="на странице нет фразы «Корзина»", keyword_failed=True)
    assert "показывает не то" in format_availability_alert(kw)


def test_recovery_includes_duration_and_cause():
    r = AvailabilityResult(url="https://ex.com", site_id=1, status_code=200, response_time_ms=90,
                           recovered=True, resolved_incident={
                               "created_at": "2026-09-15 08:00:00", "message": "Site down: HTTP 502"})
    text = format_recovery_alert(r)
    assert "снова открывается" in text and "Лежал" in text and "ошибкой 502" in text
    assert "Ничего делать не нужно" in text


def test_compact_report_chips():
    avail = [AvailabilityResult(url="https://ex.com", site_id=1, response_time_ms=95, status_code=200),
             AvailabilityResult(url="tcp://ex.com:25", site_id=2, response_time_ms=12)]
    ssl = [SslResult(url="https://ex.com", site_id=1, ssl_info=SslInfo("ex.com", "LE", "", "", 10, "1"))]
    dom = [DomainResult(url="https://ex.com", site_id=1, domain="ex.com",
                        domain_info=DomainInfo("ex.com", "R", None, None, 200, []))]
    text = format_compact_status_report(avail, [], ssl, dom, "morning", extras=["📈 x"])
    assert "Доброе утро" in text and "домен" not in text and "📈 x" in text
    # A 14-day ladder step is 🟠 (act, not today); ≤3 days is 🔴, like the alert.
    assert "🟠 ex.com — открывается, но сертификат истекает через 10 дн." in text
    assert "ex.com:25 — в порядке, быстро (0.01 с)" in text
    soon = [SslResult(url="https://ex.com", site_id=1, ssl_info=SslInfo("ex.com", "LE", "", "", 2, "1"))]
    assert "🔴 ex.com — открывается, но сертификат истекает через 2 дн." in format_compact_status_report(avail, [], soon, dom)
    unchecked = [SslResult(url="https://ex.com", site_id=1, status="error", error="SSL check failed: x", transient=True)]
    assert "⚠️ ex.com — открывается, но сертификат не удалось проверить" in format_compact_status_report(avail, [], unchecked, dom)
    unsupported = [DomainResult(url="https://ex.com", site_id=1, domain="ex.com", unsupported=True)]
    assert "домен" not in format_compact_status_report(avail, [], ssl, unsupported)
    down = [AvailabilityResult(url="https://ex.com", site_id=1, status="error", error="Timeout (15s)")]
    assert "🔴 ex.com — не ответил за 15 секунд" in format_compact_status_report(down, [])


def test_compact_report_open_problems_carry_their_own_level():
    """Before: «🔴 Открытых проблем: N» for two warning-level SEO incidents.
    The counter takes the worst level, every line its own."""
    avail = [AvailabilityResult(url="https://ex.com", site_id=1, response_time_ms=95, status_code=200)]
    warnings = [{"check_type": "seo", "severity": "warning", "url": "https://ex.com",
                 "message": "2 помехи в поиске, напр.: Нет карты сайта"},
                {"check_type": "links", "severity": "warning", "url": "https://ex.com",
                 "message": "3 broken link(s) found on https://ex.com"}]
    text = format_compact_status_report(avail, warnings)
    assert "🟠 Открытых проблем: 2" in text and "🔴" not in text
    assert "  🟠 ex.com: 2 помехи в поиске" in text and "  🟠 ex.com: 3 ссылок ведут в никуда" in text
    mixed = warnings + [{"check_type": "availability", "severity": "critical", "url": "https://ex.com",
                         "message": "Site down: HTTP 502"}]
    text = format_compact_status_report(avail, mixed)
    assert "🔴 Открытых проблем: 3" in text and "  🔴 ex.com не открывается" in text and "  🟠 ex.com: 2 помехи" in text


def test_alert_icons_follow_the_level():
    warn = SslResult(url="https://ex.com", site_id=1, status="warning", error="SSL expires in 10 days",
                     ssl_info=SslInfo("ex.com", "LE", "", "", 10, "1"))
    assert format_ssl_alert(warn).startswith("🟠 ex.com: сертификат истекает через 10 дн.")
    crit = SslResult(url="https://ex.com", site_id=1, status="critical", error="SSL expires in 2 days!",
                     ssl_info=SslInfo("ex.com", "LE", "", "", 2, "1"))
    assert format_ssl_alert(crit).startswith("🔴 ex.com: сертификат истекает через 2 дн.")
    ns = DnsChange(host="ex.com", rtype="NS", old="a", new="b", critical=True)
    mx = DnsChange(host="ex.com", rtype="MX", old="a", new="b", critical=False)
    assert format_dns_change(ns).startswith("🔴") and format_dns_change(mx).startswith("🟠")


def test_seo_texts_are_html_safe():
    r = SeoResult(url="https://ex.com", site_id=1, status="critical",
                  problems=[SeoProblem("noindex_meta", "critical", "главная: meta robots noindex <title>"),
                            SeoProblem("no_h1", "warning", "главная: нет <h1>")],
                  pages_checked=3, no_js_chars=42)
    alert = format_seo_alert(r)
    assert "&lt;title&gt;" in alert and "исчезает из поиска" in alert and "запрет на индексацию" in alert
    assert "&lt;h1&gt;" not in alert                   # only what takes the site out of search is alerted
    warn_only = SeoResult(url="https://ex.com", site_id=1, status="warning",
                          problems=[SeoProblem("no_title", "warning", "главная: нет <title>")])
    assert format_seo_alert(warn_only) == ""
    seo = {"status": "critical", "problems": [p.as_dict() for p in r.problems], "pages": 3, "no_js_chars": 42,
           "at": NOW}
    screen = seo_text("https://ex.com", {"seo": seo}, google_on=True)
    assert "&lt;title&gt;" in screen and "&lt;h1&gt;" in screen and "<title>" not in screen
    assert "42 символа" in screen and "Сайт закрыт от поиска" in screen and "1. 🔴" in screen and "2. 🟠" in screen
    assert "проверил 3 страницы" in screen and "Google: подключён, данные появятся" in screen
    steps = fix_text("https://ex.com", "noindex_meta", seo, None)
    assert "Чем грозит" in steps and "1. WordPress" in steps and "&lt;title&gt;" in steps


def test_links_report_lists_external_links_too():
    ext = [LinkCheck(url="https://other.test/a", status_code=404, ok=False),
           LinkCheck(url="https://dead.test/b", status_code=None, ok=False, error="timeout")]
    r = LinksResult(url="https://ex.com", site_id=1, total_links=52, broken_external=ext)
    text = format_links_report(r)
    assert "https://other.test/a — страница не найдена (ошибка 404)" in text
    assert "https://dead.test/b — timeout" in text and "2 ссылки на ex.com" in text
    block = external_links_block(ext)
    assert block[0].startswith("ℹ️ 2 ссылки на чужие сайты не открываются")
