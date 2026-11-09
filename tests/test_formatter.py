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
    format_availability_alert,
    format_compact_status_report,
    format_dns_change,
    format_links_report,
    format_recovery_alert,
    format_seo_alert,
    format_ssl_alert,
    links_summary,
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


def test_bots_and_watchdog_sit_under_the_sites():
    """Bots, tasks and the watchdog are a block of their own right under the
    sites, and a silent one takes «Всё спокойно» away like a fallen site."""
    avail = [AvailabilityResult(url="https://ex.com", site_id=1, response_time_ms=95, status_code=200)]
    fine = [("✅ socialtracker — работает, отметка 1 дн назад (жду раз в сутки)", True)]
    text = format_compact_status_report(avail, [], report_type="morning", extras=["📈 x"], watch=fine)
    lines = text.split("\n")
    assert lines[2:7] == ["✅ ex.com — в порядке, быстро (0.10 с)", "", fine[0][0], "", "Всё спокойно 🐕"]
    silent = fine + [("⚠️ backup — ни одной отметки: подключи или удали, кнопки ниже", False)]
    text = format_compact_status_report(avail, [], report_type="morning", watch=silent)
    assert "⚠️ backup" in text and "Всё спокойно" not in text
    # Only tasks, no sites: no empty gap under the header.
    assert format_compact_status_report([], [], watch=fine).split("\n")[1:3] == ["", fine[0][0]]


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
    # No leading spaces (Telegram wraps them back to column 0); a search
    # finding leads with what is wrong, the count after.
    assert "\n🟠 ex.com: нет карты сайта и ещё 1 помеха" in text and "\n🟠 ex.com: 3 ссылки не открываются" in text
    assert "напр." not in text and "\n  " not in text
    mixed = warnings + [{"check_type": "availability", "severity": "critical", "url": "https://ex.com",
                         "message": "Site down: HTTP 502"}]
    text = format_compact_status_report(avail, mixed)
    assert "🔴 Открытых проблем: 3" in text and "\n🔴 ex.com не открывается" in text and "\n🟠 ex.com: нет карты" in text


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


def test_a_dead_link_is_a_problem_wherever_it_points():
    """Owner, 03.11.2026: «неработающая ссылка = неработающая ссылка» — own
    and other sites' links are one list, one 🟠, with the reason and a fix."""
    broken = [LinkCheck(url="https://ex.com/old", status_code=404, ok=False),
              LinkCheck(url="https://github.com/me/private-repo", status_code=404, ok=False),
              LinkCheck(url="https://gone.example/", status_code=None, ok=False,
                        error="Cannot connect to host gone.example:443 [Name or service not known]")]
    r = LinksResult(url="https://ex.com", site_id=1, total_links=52, broken=broken)
    text = format_links_report(r)
    assert text.startswith("🟠 На ex.com 3 ссылки не открываются")
    assert "• https://ex.com/old — страница не найдена (ошибка 404)" in text
    assert "• https://github.com/me/private-repo — закрытый или удалённый репозиторий" in text
    assert "адрес сайта не находится" in text and "чужих" not in text and "не считаю проблемой" not in text
    assert text.splitlines()[-1].startswith("Что сделать:")
    clean = LinksResult(url="https://b.ex", site_id=2)
    lines = links_summary([r, clean])
    assert lines[0] == "🟠 3 ссылки не открываются:" and "ex.com:" in lines and "b.ex:" not in lines
    assert links_summary([clean]) == [] and format_links_report(clean) == ""
