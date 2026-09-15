from handlers.seo import build_seo_report
from monitors.base import (
    AvailabilityResult,
    DomainInfo,
    DomainResult,
    SeoProblem,
    SeoResult,
    SslInfo,
    SslResult,
)
from reports.formatter import (
    format_availability_alert,
    format_compact_status_report,
    format_recovery_alert,
    format_seo_alert,
)


def test_availability_alert_escapes_and_omits_code_for_tcp():
    r = AvailabilityResult(url="https://ex.com", site_id=1, status="error",
                           error="<script>", status_code=502, response_time_ms=10)
    text = format_availability_alert(r)
    assert "&lt;script&gt;" in text and "Код: 502" in text and "НЕДОСТУПЕН" in text
    tcp = AvailabilityResult(url="tcp://ex.com:25", site_id=1, status="error", error="refused")
    assert "Код:" not in format_availability_alert(tcp) and "СЕРВИС" in format_availability_alert(tcp)
    kw = AvailabilityResult(url="https://ex.com", site_id=1, status="error", error="x", keyword_failed=True)
    assert "СОДЕРЖИМОЕ" in format_availability_alert(kw)


def test_recovery_includes_duration_and_cause():
    r = AvailabilityResult(url="https://ex.com", site_id=1, status_code=200, response_time_ms=90,
                           recovered=True, resolved_incident={
                               "created_at": "2026-09-15 08:00:00", "message": "Site down: HTTP 502"})
    text = format_recovery_alert(r)
    assert "ВОССТАНОВЛЕН" in text and "Длительность:" in text and "HTTP 502" in text


def test_compact_report_chips():
    avail = [AvailabilityResult(url="https://ex.com", site_id=1, response_time_ms=95, status_code=200),
             AvailabilityResult(url="tcp://ex.com:25", site_id=2, response_time_ms=12)]
    ssl = [SslResult(url="https://ex.com", site_id=1, ssl_info=SslInfo("ex.com", "LE", "", "", 10, "1"))]
    dom = [DomainResult(url="https://ex.com", site_id=1, domain="ex.com",
                        domain_info=DomainInfo("ex.com", "R", None, None, 200, []))]
    text = format_compact_status_report(avail, [], ssl, dom, "morning", extras=["📈 x"])
    assert "Доброе утро" in text and "SSL ⚠ 10д" in text and "домен" not in text
    assert "ex.com:25 — 12ms" in text and "📈 x" in text
    unsupported = [DomainResult(url="https://ex.com", site_id=1, domain="ex.com", unsupported=True)]
    assert "домен ?" not in format_compact_status_report(avail, [], ssl, unsupported)


def test_seo_texts_are_html_safe():
    r = SeoResult(url="https://ex.com", site_id=1, status="warning",
                  problems=[SeoProblem("warning", "/: нет <title>")], infos=["нет <h1>"],
                  pages_checked=3, no_js_chars=42)
    assert "&lt;title&gt;" in format_seo_alert(r)
    report = build_seo_report([r], {"https://ex.com": "в индексе ✅"}, {})
    assert "&lt;title&gt;" in report and "&lt;h1&gt;" in report and "<title>" not in report
    assert "42 символа" in report
