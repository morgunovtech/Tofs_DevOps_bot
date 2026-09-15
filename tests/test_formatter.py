from handlers.seo import build_seo_report
from monitors.base import (
    AvailabilityResult,
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
    format_links_report,
    format_recovery_alert,
    format_seo_alert,
)


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
    assert "Доброе утро" in text and "сертификат истекает через 10 дн." in text and "домен" not in text
    assert "ex.com:25 — в порядке, быстро (0.01 с)" in text and "📈 x" in text
    unsupported = [DomainResult(url="https://ex.com", site_id=1, domain="ex.com", unsupported=True)]
    assert "домен" not in format_compact_status_report(avail, [], ssl, unsupported)
    down = [AvailabilityResult(url="https://ex.com", site_id=1, status="error", error="Timeout (15s)")]
    assert "🔴 ex.com — не ответил за 15 секунд" in format_compact_status_report(down, [])


def test_seo_texts_are_html_safe():
    r = SeoResult(url="https://ex.com", site_id=1, status="critical",
                  problems=[SeoProblem("critical", "/: meta robots noindex <title>")], infos=["нет <h1>"],
                  pages_checked=3, no_js_chars=42)
    assert "&lt;title&gt;" in format_seo_alert(r) and "исчезает из поиска" in format_seo_alert(r)
    warn_only = SeoResult(url="https://ex.com", site_id=1, status="warning",
                          problems=[SeoProblem("warning", "/: нет <title>")])
    assert format_seo_alert(warn_only) == ""
    report = build_seo_report([r], {"https://ex.com": "в индексе ✅"}, {})
    assert "&lt;title&gt;" in report and "&lt;h1&gt;" in report and "<title>" not in report
    assert "42 символа" in report


def test_links_report_lists_external_links_too():
    ext = [LinkCheck(url="https://other.test/a", status_code=404, ok=False),
           LinkCheck(url="https://dead.test/b", status_code=None, ok=False, error="timeout")]
    r = LinksResult(url="https://ex.com", site_id=1, total_links=52, broken_external=ext)
    text = format_links_report(r)
    assert "https://other.test/a — страница не найдена (ошибка 404)" in text
    assert "https://dead.test/b — timeout" in text and "2 ссылки на ex.com" in text
    block = external_links_block(ext)
    assert block[0].startswith("ℹ️ 2 ссылки на чужие сайты не открываются")
