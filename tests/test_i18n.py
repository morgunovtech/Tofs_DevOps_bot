"""BOT_LANG=en: every screen, alert and report renders without a Cyrillic
letter. Russian is the source and the lookup key (utils/i18n.py), so a key
that drifted from its English entry falls back to Russian and shows up here.
Russian itself is covered by every other test: with BOT_LANG=ru t() returns
its argument."""

import dataclasses
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import FakeCall, FakeMessage, FakeState, buttons
from test_docs import _config_with

import main
from db.database import heartbeat_ping, queue_notification, save_feedback, save_incident
from handlers import (
    about,
    alerts,
    automation,
    blocks,
    common,
    feedback,
    incidents,
    menu,
    mute,
    site_settings,
    sites,
    speed,
    start,
)
from handlers import (
    heartbeats as hb_screens,
)
from handlers import (
    mail as mail_screen,
)
from handlers import (
    maintenance as maint_screen,
)
from handlers import (
    security as security_screen,
)
from handlers import (
    seo as seo_screen,
)
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
from monitors.mail_checker import Delivery, MailResult
from monitors.security_checker import SecurityResult
from monitors.visitor_checker import VisitorsResult
from reports import formatter, scheduler, weekly
from services import (
    actions,
    heartbeats,
    humanize,
    index_status,
    maintenance,
    notifier,
    recommend,
    screenshots,
    sitestatus,
)
from services import settings as settings_service
from utils import i18n
from utils.i18n import fmt_duration, plural, t

CYRILLIC = re.compile("[А-Яа-яЁё]")


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("ru")


def english_only(text: str | None) -> None:
    assert text is not None
    hit = CYRILLIC.search(text)
    assert hit is None, f"Russian leaked at …{text[max(0, hit.start() - 40):hit.start() + 60]}…"


def screen_english(text: str, kb=None) -> None:
    english_only(text)
    for label in buttons(kb) if kb is not None else []:
        english_only(label)


def test_layer():
    assert t("🏠 Главная") == "🏠 Home"
    assert t("нет такого ключа — остаётся русским") == "нет такого ключа — остаётся русским"
    assert plural(1, "проблема", "проблемы", "проблем") == "problem"
    assert plural(21, "проблема", "проблемы", "проблем") == "problems"
    assert plural(2, "нет такого ключа", "x", "no key: the Russian many form") == "no key: the Russian many form"
    assert fmt_duration(185) == "3 h 5 min" and fmt_duration(2 * 1440 + 180) == "2 d 3 h"
    i18n.set_language("ru")
    assert t("🏠 Главная") == "🏠 Главная"
    assert plural(22, "проблема", "проблемы", "проблем") == "проблемы"
    assert fmt_duration(185) == "3 ч 5 мин"
    with pytest.raises(ValueError):
        i18n.set_language("de")


def test_config_lang_and_the_timezone_default():
    """BOT_LANG=en moves the default TIMEZONE to UTC (BOT_PRINCIPLES §5); an
    explicit TIMEZONE wins; a wrong language falls back to Russian."""
    assert _config_with({})["lang"] == "ru" and _config_with({})["timezone"] == "Europe/Moscow"
    en = _config_with({"BOT_LANG": "EN"})
    assert en["lang"] == "en" and en["timezone"] == "UTC"
    assert _config_with({"BOT_LANG": "en", "TIMEZONE": "Europe/Berlin"})["timezone"] == "Europe/Berlin"
    assert _config_with({"BOT_LANG": "fr"})["lang"] == "ru"


def test_bottom_buttons_and_common_texts():
    labels = [b.text for row in common.bottom().keyboard for b in row]
    assert labels == ["🏠 Home", "📖 What I do", "❓ Icons"]
    for text in (common.DENIED, common.HELPER_NOTE, common.ELSEWHERE, common.NO_OWNER, common.HEARTBEAT_SECRET_NOTE,
                 common.NOT_DELETED, common.STALE_BUTTON, common.NOT_UNDERSTOOD, common.STALE_TAP,
                 start.PRIVATE_ONLY, start.TRY_AGAIN, start.HELP, start.BOTTOM_NOTE, *start._HELPER_ANSWERS.values()):
        english_only(t(text))
    assert buttons(common.back_button()) == ["🏠 Home"]
    assert buttons(common.back_button("← Что я умею", "about:main")) == ["← What I do"]
    assert buttons(common.cancel_kb()) == ["✖️ Cancel"]
    assert buttons(start.tz_button()) == ["🕐 Change the time zone"]


async def test_start_and_help(bot, db, monkeypatch):
    msg = FakeMessage("/start")
    msg.from_user, msg.chat = SimpleNamespace(id=777, is_bot=False), SimpleNamespace(id=777, type="private")
    await start.cmd_start(msg, FakeState())
    for text, kb in zip(msg.texts, msg.markups, strict=True):
        screen_english(text, kb if kb is not None and hasattr(kb, "inline_keyboard") else None)
    english_only(start.rules_of_the_game())
    monkeypatch.setattr(start, "config", dataclasses.replace(start.config, escalation_repeat_min=0))
    english_only(start._down_line())
    help_msg = FakeMessage("/help")
    help_msg.from_user, help_msg.chat = msg.from_user, msg.chat
    await start.cmd_help(help_msg)
    screen_english(help_msg.texts[-1], help_msg.markups[-1])
    stop_msg = FakeMessage("/stop")
    stop_msg.from_user, stop_msg.chat = msg.from_user, msg.chat
    await start.cmd_stop(stop_msg)
    english_only(stop_msg.texts[-1])
    bot.sent.clear()
    await main._show_bottom_buttons(bot)   # a state without the bottom-buttons mark
    await db.set_state(start.BOTTOM_SHOWN, None)
    await main._show_bottom_buttons(bot)
    english_only(bot.sent[-1]["text"])


async def test_main_screen(bot, db):
    screen_english(*await menu.build_main_menu())                       # nothing watched yet
    sid = await db.activate_or_create_site("https://ex.test")
    screen_english(*await menu.build_main_menu())                       # before the first check
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    text, kb = await menu.build_main_menu()
    assert text.startswith("✅ All is well · checked at ") and "✅ ex.test · 0.09 s" in text
    screen_english(text, kb)
    await maintenance.pause_site(sid, 30)
    await notifier.set_mute(timedelta(hours=1))
    text, kb = await menu.build_main_menu()
    assert "🔧 ex.test · being fixed, 0.09 s" in text and "Silence until " in text
    screen_english(text, kb)
    await maintenance.pause_site(sid, None)
    await notifier.set_mute(None)
    for i in range(menu.MAIN_SITES + 1):                                # the fold: «… and N more»
        await db.activate_or_create_site(f"https://site{i}.test")
    text, kb = await menu.build_main_menu()
    assert "… and 2 more" in text
    screen_english(text, kb)
    english_only(menu.headline(["🟠", "🔴"], menu.checked_at(None)))
    english_only(menu._due("сертификат", 5))
    english_only(menu._due("домен", -1))
    for icon, name, state in await menu.system_lines(None):
        english_only(f"{icon} {name} {state}")
    for part in blocks.backup_state():
        english_only(part)


async def test_about(db):
    screen_english(about.features_text(), about.features_keyboard())
    screen_english(*await about.modules_page())


def test_alert_words():
    """services/humanize: the words every alert, card and report is built from."""
    cases = ["HTTP 502", "Site down: HTTP 404", "Site down: HTTP 403", "HTTP 429", "HTTP 418", "Timeout (15s)",
             "Site down", "SSL expires in 5 days!", "SSL expires in 0 days", "SSL expired 3 days ago",
             "SSL certificate has expired", "SSL certificate invalid: hostname mismatch", "SSL check failed: x",
             "Domain expires in 12 days", "Domain expired 1 days ago", "Lookup failed: rdap", "Slow responses: ~5000ms",
             "3 broken link(s) found on https://x", "1 broken link(s)", "5xx на 3 из 10 проверенных страниц",
             "5xx или нет ответа на 1 из 2 проверенных страниц", "Could not fetch page",
             "Cannot connect to host x:443 ssl:default [Name or service not known]",
             "Cannot connect to host x:443 ssl:default [certificate has expired]", "certificate verify failed",
             "Connect call failed ('1.2.3.4', 443) Connection refused", "No route to host", "Cannot connect to host x",
             "Server disconnected", "Response payload is not completed", "Too many redirects", "0, message='', url='x'",
             "хост не отвечает на ping", "Bad content: на странице стоп-фраза «Connection refused»",
             "в ответе нет фразы «ok»", "TimeoutError", None]
    for case in cases:
        english_only(humanize.describe_error(case))
    # A search finding's own title is a monitor's text: it gets its entry together with monitors/.
    assert humanize.describe_error("2 помехи в поиске, напр.: Нет карты сайта") == "2 search obstacles, e.g.: Нет карты сайта"
    assert humanize.describe_error("закрыт от поиска — Сайт закрыт от поисковиков в robots.txt") == \
        "closed to search — Сайт закрыт от поисковиков в robots.txt"
    assert humanize.describe_error("HTTP 502") == "the hosting answers with error 502"
    assert humanize.describe_error("SSL expires in 5 days!") == "certificate expires in 5 d"
    assert humanize.describe_error("3 broken link(s)") == "3 links do not open"
    for check, message in (("availability", "Site down: HTTP 502"), ("availability", "Bad content: в ответе нет фразы «ok»"),
                           ("availability", "Cannot connect to host x [certificate has expired]"), ("performance", "Slow responses: ~3000ms"),
                           ("performance", "x"), ("ssl", "SSL expires in 3 days!"), ("domain", "Domain expired 2 days ago"),
                           ("links", "2 broken link(s)"), ("deep", "5xx на 3 из 10 проверенных страниц"),
                           ("mail", "x")):
        english_only(humanize.incident_headline(check, message, "ex.test"))
        english_only(humanize.incident_headline(check, message, "ex.test", page=False))
        expl = humanize.explain(check, message)
        english_only(expl.meaning + expl.cause + humanize.steps_block(expl))
    assert humanize.incident_headline("availability", "Site down: HTTP 502", "ex.test") == \
        "ex.test does not open: the hosting answers with error 502"
    assert humanize.incident_headline("seo", "3 помехи в поиске, напр.: Нет карты сайта", "ex.test") == \
        "ex.test: нет карты сайта and 2 more obstacles"     # the finding's own title gets its entry with monitors/
    assert humanize.incident_headline("seo", "закрыт от поиска — Сайт закрыт от поисковиков в robots.txt", "ex.test") == \
        "ex.test: closed to search — Сайт закрыт от поисковиков в robots.txt"
    for check in ("seo", "mail", "mail_auth", "exposed", "security", "visitors"):
        expl = humanize.explain(check, "закрыт от поиска — x" if check == "seo" else "x")
        english_only(expl.meaning + expl.cause + humanize.steps_block(expl))
    for key in humanize.EXPLANATIONS:
        expl = humanize.explanation(key)
        english_only(expl.meaning + expl.cause + humanize.steps_block(expl))
    for words in (humanize.downtime({}), humanize.downtime({"total_checks": 5}), humanize.downtime({"total_checks": 5, "down_min": 90}),
                  humanize.every_minutes(1), humanize.every_minutes(5), humanize.every_minutes(60),
                  humanize.times_in_a_row(1), humanize.times_in_a_row(2), humanize.times_in_a_row(3),
                  humanize.link_reason(404, None, "https://github.com/a/b"), humanize.link_reason(500, None),
                  humanize.link_reason(None, "timeout"), humanize.speed(None), humanize.speed(120, "from the US"),
                  humanize.due_words(0), humanize.due_words(1), humanize.due_words(5),
                  humanize.expires_words("сертификат", 5), humanize.expires_words("домен", 0), humanize.fmt_seconds(12000)):
        english_only(words)


async def test_notifier_texts(bot, db):
    english_only(notifier.what_rings_through_mute())
    await settings_service.set_ring_only_down(True)
    english_only(notifier.what_rings_through_mute())
    english_only(notifier._maybe_note())
    english_only(notifier._late_note(notifier.datetime.now(notifier.UTC)))
    await queue_notification("alert one")
    await queue_notification("alert two")
    await db.set_state(notifier.PENDING_DROPPED, "3")
    await notifier.flush_queue()
    english_only(bot.sent[-1]["text"])
    assert bot.sent[-1]["text"].startswith("🌙 Piled up while it was quiet:")
    english_only(await notifier.send_document_to_owner(b"x", "copy") or "")
    english_only(t(notifier.HELPER_FOOTER))


async def test_alert_keyboards_and_actions(db, monkeypatch):
    monkeypatch.setattr(actions.integrations, "deploy_hooks", lambda: {"ex.test": "https://hook.test/x"})
    monkeypatch.setattr(actions.integrations, "cf_purge_configured", lambda: True)
    monkeypatch.setattr(actions.integrations, "cf_zone_name", lambda: "ex.test")
    monkeypatch.setattr(actions.integrations, "auto_enabled", lambda kind: True)
    screen_english("", actions.alert_actions_keyboard("https://ex.test", 1, 5, "cloudflare"))
    screen_english("", actions.alert_actions_keyboard("tcp://ex.test:25", 1, None, None, api=True))
    screen_english("", actions.group_keyboard(7))
    screen_english("", actions.deep_alert_keyboard("https://ex.test", 1, 5))
    screen_english("", actions.heartbeat_keyboard(["backup", "sync"], {"backup"}))
    screen_english("", actions.domain_keyboard("Cloudflare", 5))
    monkeypatch.setattr(actions.integrations, "deploy_hooks", lambda: {})
    english_only((await actions.trigger_redeploy("https://ex.test")).text)
    monkeypatch.setattr(actions.integrations, "cf_purge_configured", lambda: False)
    english_only(await actions.purge_cf_cache("https://ex.test"))


async def test_jobs_watchdog_and_small_services(db, monkeypatch):
    await settings_service.add_heartbeat_job("backup", 1440)
    await settings_service.add_heartbeat_job("sync", 90)
    await settings_service.add_heartbeat_job("silent", 60)
    await heartbeat_ping("backup")
    await db.set_state("hb_alerted:silent", "1")
    for job in await heartbeats.job_states():
        english_only(job.state)
        english_only(heartbeats.digest_line(job)[0])
        english_only(heartbeats.status_title(job, {}))
        for *_, text in heartbeats.status_items(job, {"at": "2000-01-01T00:00:00+00:00", "items": []}):
            english_only(text)
    late = heartbeats.Job("backup", "🟠", "x", 60, 500.0)
    english_only(heartbeats.digest_line(late)[0])
    for interval in (60, 360, 1440, 10080, 45):
        english_only(heartbeats.every(interval))
    english_only(heartbeats.watchdog_digest_line(None)[0])
    for w in (heartbeats.Watchdog("✅", "hc", "x"), heartbeats.Watchdog("🟠", "hc", "x"), heartbeats.Watchdog("⏳", "hc", "x")):
        english_only(heartbeats.watchdog_digest_line(w)[0])
    english_only(maintenance.fix_length())
    for window in ({"days": [], "start_min": 120, "end_min": 240}, {"days": [0, 1, 2, 3, 4], "start_min": 0, "end_min": 60},
                   {"days": [5, 6], "start_min": 0, "end_min": 60}, {"days": [1, 3], "start_min": 0, "end_min": 60}):
        english_only(maintenance.fmt_window(window))
    english_only(index_status.yandex_problems_words({"FATAL": 1, "CRITICAL": 2}))
    for reason in (screenshots.QUOTA_REASON, screenshots.OFF_REASON, screenshots.PRIVATE_REASON):
        english_only(t(reason))
    monkeypatch.setattr(recommend.public_urls, "is_public", lambda: True)
    await db.activate_or_create_site("https://ex.test")
    monkeypatch.setattr(recommend, "suggest_keyword", _no_phrase)
    for _, text, buttons_ in await recommend._candidates():
        english_only(text)
        for label, _ in buttons_:
            english_only(label)
    english_only(t(main.runtime.NO_VOLUME))


async def _no_phrase(url):
    return None


# Monitors' own texts (a finding's title, a search problem's message) get
# their entries together with monitors/; here they are English stand-ins.
_FINDING = {"code": "spf", "title": "Two SPF records", "why": "mail may land in spam", "fix": "Keep one record",
            "record": "v=spf1 include:_spf.google.com ~all", "note": "Wait for DNS"}


def _avail(**kw) -> AvailabilityResult:
    return AvailabilityResult(url="https://ex.test", site_id=1, **kw)


def test_alert_texts():
    """reports/formatter: every alert, recovery and digest line in English."""
    incident = {"started_at": "2026-11-20 10:00:00", "created_at": "2026-11-20 10:05:00",
                "resolved_at": "2026-11-20 10:17:00", "message": "Site down: HTTP 502"}
    down = [
        _avail(status="error", error="HTTP 502", failed_of=(3, 5)),
        _avail(status="error", error="Timeout (15s)", external_ok=False),
        _avail(status="error", error="HTTP 503", external_region="us", external_ups=2, external_total=3),
        _avail(status="error", error="HTTP 503", external_region="de"),
        _avail(status="error", error="HTTP 503", external_ups=1, external_total=3),
        _avail(status="error", error="Cannot connect to host x [certificate has expired]", cert_failed=True),
        _avail(status="error", error="Bad content: на странице нет фразы «Cart»", keyword_failed=True),
        AvailabilityResult(url="tcp://ex.test:25", site_id=2, status="error", error="Connection refused", api=True),
    ]
    for r in down:
        english_only(formatter.format_availability_alert(r, since=scheduler.datetime.now(scheduler.UTC)))
        english_only(formatter.format_availability_alert(r, second_opinion=False))
    up = _avail(status_code=200, response_time_ms=90, resolved_incident=incident)
    english_only(formatter.format_recovery_alert(up))
    english_only(formatter.format_recovery_alert(_avail(resolved_incident={**incident, "message": "Bad content: x",
                                                                            "unseen_until": "2026-11-20 10:20:00"})))
    english_only(formatter.format_recovery_alert(AvailabilityResult(url="tcp://ex.test:25", site_id=2, api=True)))
    assert formatter.incident_duration_line(incident).startswith("17 min (")

    info = SslInfo("ex.test", "Let's Encrypt", "", "", 5, "1")
    english_only(formatter.format_ssl_alert(SslResult(url="https://ex.test", site_id=1, status="warning",
                                                      error="SSL expires in 5 days!", ssl_info=info, renewal_note="x")))
    dom = DomainInfo("ex.test", "Cloudflare", None, None, 12, [])
    english_only(formatter.format_domain_alert(DomainResult(url="https://ex.test", site_id=1, status="warning",
                                                            error="Domain expires in 12 days", domain="ex.test", domain_info=dom)))
    broken = [LinkCheck("https://ex.test/a", 404, False), LinkCheck("https://github.com/a/b", 404, False),
              LinkCheck("https://ex.test/c", None, False, "timeout")]
    english_only(formatter.format_links_report(LinksResult(url="https://ex.test", site_id=1, broken=broken * 4)))
    english_only(formatter.pending_note(broken))
    english_only(formatter.pending_note(broken[:1]))
    deep = SimpleNamespace(url="https://ex.test", error="5xx на 3 из 10 проверенных страниц",
                           errors=[("https://ex.test/p", 502), ("https://ex.test/q", 0)])
    english_only(formatter.format_deep_alert(deep))
    seo = SeoResult(url="https://ex.test", site_id=1, status="critical",
                    problems=[SeoProblem("noindex", "critical", "the homepage carries noindex")])
    english_only(formatter.format_seo_alert(seo).replace(seo.problems[0].message, "")
                 .replace(formatter.seo_fixes.fix("noindex").title, ""))   # the fix title: services/seo_fixes, next PR
    for rtype, old, new in (("A", "1.1.1.1", "2.2.2.2"), ("A", "1.1.1.1", "—"), ("NS", "a", "b"), ("MX", "a", "—"),
                            ("CNAME", "a", "b"), ("TXT", "a", "b")):
        english_only(formatter.format_dns_change(DnsChange(host="ex.test", rtype=rtype, old=old, new=new, critical=False)))
    for rtype in ("A", "NS"):
        english_only(formatter.format_dns_failing(DnsChange(host="ex.test", rtype=rtype, old="1.1.1.1", new="—",
                                                            critical=True, failing=True)))
    for mail in (MailResult(url="https://ex.test", site_id=1, domain="ex.test", delivery=Delivery(ok=False, reason="nowhere")),
                 MailResult(url="https://ex.test", site_id=1, domain="ex.test", provider="Google Workspace",
                            status_page="https://status.example", delivery=Delivery(ok=False, reason="silent")),
                 MailResult(url="https://ex.test", site_id=1, domain="ex.test", kind="mail_auth", findings=[_FINDING]),
                 MailResult(url="https://ex.test", site_id=1, domain="ex.test", kind="mail_auth", findings=[_FINDING, _FINDING])):
        english_only(formatter.format_mail_alert(mail))
        english_only(formatter.format_mail_recovery(MailResult(url="https://ex.test", site_id=1, domain="ex.test",
                                                               kind=mail.kind, resolved_incident=incident)))
    for kind, n in (("exposed", 1), ("security", 2)):
        sec = SecurityResult(url="https://ex.test", site_id=1, kind=kind, findings=[_FINDING] * n)
        english_only(formatter.format_security_alert(sec))
        english_only(formatter.format_security_recovery(sec))
    vis = VisitorsResult(url="https://ex.test", site_id=1, values={"lcp": 5200, "inp": 650, "cls": 0.3})
    english_only(formatter.format_visitors_alert(vis))
    english_only(formatter.format_visitors_recovery(VisitorsResult(url="https://ex.test", site_id=1,
                                                                   values={"lcp": 1800, "inp": 120, "cls": 0.02})))
    english_only(formatter.format_feedback("https://ex.test", "https://ex.test/p", "hello", 7, "1.2.3.4"))

    avail = [_avail(response_time_ms=95, status_code=200), AvailabilityResult(url="tcp://ex.test:25", site_id=2, api=True,
                                                                                response_time_ms=12),
             AvailabilityResult(url="https://down.test", site_id=3, status="error", error="HTTP 502"),
             AvailabilityResult(url="https://odd.test", site_id=4, status="unknown", error="no socket")]
    ssl = [SslResult(url="https://ex.test", site_id=1, ssl_info=SslInfo("ex.test", "LE", "", "", 3, "1")),
           SslResult(url="https://down.test", site_id=3)]
    doms = [DomainResult(url="https://ex.test", site_id=1, domain="ex.test", domain_info=DomainInfo("ex.test", "R", None, None, -2, []))]
    incidents = [{"id": 1, "url": "https://down.test", "check_type": "availability", "message": "Site down: HTTP 502", "severity": "critical"},
                 {"id": 2, "url": "https://ex.test", "check_type": "links", "message": "2 broken link(s)", "severity": "warning"}]
    for report_type in ("morning", "morning_late", "evening", "status"):
        for short in (False, True):
            english_only(formatter.format_compact_status_report(avail, incidents, ssl, doms, report_type=report_type,
                                                                extras=["x"], watch=[("✅ job", True)], short=short))
    english_only(formatter.format_compact_status_report(avail[:1], [], None, None, watch=[("✅ job", True)]))
    english_only(formatter.format_compact_status_report(avail[:1], incidents[1:], None, None))


async def test_digests_and_the_weekly_report(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    await db.save_check(sid, "availability", "ok", response_time_ms=100)
    await sitestatus.update(sid, "avail", status="ok", ms=100, error=None)
    await save_incident(sid, "links", "2 broken link(s)", "warning")
    old_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await db.resolve_incident_by_id(old_id)
    await sitestatus.update(sid, "ssl", days_left=80, issuer="LE", not_after=None, error=None,
                            renewed_at=scheduler.datetime.now(scheduler.UTC).isoformat(timespec="seconds"))

    async def _const(value):
        return value

    monkeypatch.setattr(scheduler, "check_all", lambda urls, manage=True: _const(
        [AvailabilityResult(url=u, site_id=sid, response_time_ms=100, status_code=200) for u in urls]))
    monkeypatch.setattr(scheduler, "check_all_ssl", lambda urls, manage=True: _const([]))
    monkeypatch.setattr(scheduler, "check_all_domains", lambda urls, manage=True: _const([]))
    monkeypatch.setattr(scheduler, "check_disk", lambda auto_cleanup=True: _const({"over_threshold": False, "pct": 41}))
    for report_type in ("morning", "evening"):
        english_only(await scheduler._status_snapshot(report_type, extras=True))
    await settings_service.set_digest_full(True)
    english_only(await scheduler._status_snapshot("morning", extras=True))
    text, speed = await weekly.build_weekly_report()
    english_only(text)
    for line in speed:
        english_only(line)
    english_only("\n".join(weekly.speed_lines({"a": [{"avg_ms": 100}] * 3, "b": [{"avg_ms": 100}, {"avg_ms": 900}, {"avg_ms": 100}]},
                                               {"a": 3000, "b": 3000})))


async def test_scheduler_messages(bot, db):
    now = scheduler.datetime.now(scheduler.UTC)
    inc = {"id": 1, "site_id": 1, "url": "https://ex.test", "check_type": "availability", "message": "Site down: HTTP 502",
           "severity": "critical", "created_at": "2026-11-20 10:05:00", "started_at": "2026-11-20 10:00:00"}
    content = {**inc, "message": "Bad content: на странице нет фразы «x»"}
    api = {**inc, "url": "https://api.ex.test/api/v1", "http_method": "POST"}
    english_only(scheduler._missed_alert_text(inc))
    english_only(scheduler._missed_alert_text(inc, scheduler.helper_label("https://ex.test/secret?token=1")))
    english_only(scheduler.helper_label("tcp://10.0.0.1:25"))
    text, helper = scheduler._group_down_texts([inc, content], ["🤖 redeploy"])
    english_only(text)
    english_only(helper)
    english_only(scheduler._up_text([scheduler._up_line(_avail(resolved_incident={**inc, "resolved_at": "2026-11-20 10:17:00"})),
                                     scheduler._up_line(_avail(resolved_incident=content))]))
    for incident in (inc, content, api):
        english_only(scheduler._escalation_head(incident, 95.0, "every 5 min"))
        english_only(scheduler._still_down(incident, now))
    for error in ("database or disk is full", "database is locked", "disk I/O error"):
        english_only(scheduler._db_failure_text(error))
    english_only(scheduler._down_cause(content))
    await scheduler._report_downtime(now - scheduler.timedelta(hours=3), now)
    english_only(bot.sent[-1]["text"])
    sid = await db.activate_or_create_site("https://ex.test")
    await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await scheduler._report_downtime(now - scheduler.timedelta(hours=3), now)
    english_only(bot.sent[-1]["text"])
    await scheduler._tell_job_broken(t(scheduler._JOB_TITLES["morning_report"]), t("прошлый запуск ещё не закончился"))
    english_only(bot.sent[-1]["text"])
    english_only(scheduler._links_recovered(SimpleNamespace(url="https://ex.test", pending=[])))
    english_only(scheduler._links_recovered(SimpleNamespace(url="https://ex.test", pending=[1])))
    english_only(scheduler._links_recovered(SimpleNamespace(url="https://ex.test", pending=[1, 2])))
    english_only(scheduler._container_steps("web"))
    screen_english("", scheduler._mail_keyboard(SimpleNamespace(kind="mail", status_page="https://s.test", provider="Google",
                                                                site_id=1, incident_id=1)))
    screen_english("", scheduler._security_keyboard(SimpleNamespace(site_id=1, incident_id=1)))
    screen_english("", scheduler._visitors_keyboard(SimpleNamespace(site_id=1, incident_id=1, url="https://ex.test")))
    screen_english("", scheduler._watchdog_kb())
    screen_english("", scheduler._index_keyboard(1, "↗️ Search Console", "https://x.test"))


def _calls_english(call) -> None:
    for text, kb in zip(call.message.texts, call.message.markups, strict=True):
        screen_english(text, kb if kb is not None and hasattr(kb, "inline_keyboard") else None)
    for answer in call.answers:
        english_only(answer or "")


async def test_site_card_settings_and_list(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    for _ in range(3):
        await db.save_check(sid, "availability", "ok", response_time_ms=120)
    await sitestatus.update(sid, "ssl", days_left=5, issuer="LE", not_after="2026-12-01T00:00:00+00:00", error=None)
    await sitestatus.update(sid, "domain", days_left=12, expiration="2026-12-10", registrar="Cloudflare", error=None)
    await sitestatus.update(sid, "links", status="warning", broken=1, pending=0, total=24,
                            items=[{"url": "https://github.com/x/gone", "code": 404, "error": None}])
    await sitestatus.update(sid, "deep", status="warning", total=10, bad=2, silent=True,
                            broken=[("/a", 502), ("/b", 0)])
    await sitestatus.update(sid, "seo", status="ok", critical=0, improve=0, pages=3, no_js_chars=500, problems=[])
    await sitestatus.update(sid, "security", plain_http=False, redirects=True, missing=["X-Frame-Options"],
                            headers_ok=False, exposed=[], findings=[_FINDING], csp=False)
    await save_incident(sid, "performance", "Slow responses: ~3000ms", "warning")
    await maintenance.pause_site(sid, 30)
    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    _calls_english(call)
    await maintenance.pause_site(sid, None)
    await sitestatus.update(sid, "avail", status="error", ms=None, error="HTTP 502")
    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    _calls_english(call)
    api = await db.activate_or_create_site("https://api.ex.test/api/v1/health")
    await sitestatus.update(api, "avail", status="unknown", ms=None, error="no socket")
    call = FakeCall(f"check_site:{api}")
    await sites.cb_check_single_site(call)
    _calls_english(call)
    call = FakeCall("blk:sites")
    await sites.cb_sites_block(call)
    _calls_english(call)
    call = FakeCall(f"delsite:{sid}")
    await sites.cb_site_del_confirm(call)
    _calls_english(call)
    call = FakeCall("site_add")
    await sites.cb_site_add(call, FakeState())
    _calls_english(call)
    call = FakeCall("site_add_adv")
    await sites.cb_site_add_advanced(call, FakeState())
    _calls_english(call)
    for raw in ("not a host!", "tcp://x", "tcp://mail.ex.test", "ping://1.2.3", "https://ex.test/" + "a" * 300):
        _, problem = sites.parse_site_input(raw)
        english_only(problem or "")
    english_only(sites.api_line({"url": "https://api.ex.test/api/v1", "http_method": "POST", "keyword": "ok", "keyword_mode": "present"}))
    english_only(sites._limit_screen()[0])
    english_only(await sites._restore_bytes(b"not json"))
    english_only(sites._short_downtime({"total_checks": 5, "down_min": 12}))
    english_only(sites._short_downtime({}))
    call = FakeCall(f"pause:{sid}:120")
    await sites.cb_pause(call)
    _calls_english(call)

    site = await db.get_site(sid)
    screen_english(*await site_settings.sset_screen(site))
    screen_english(*await site_settings.advanced_screen(site))
    await db.update_site_settings(sid, http_method="HEAD", keyword="shop", keyword_mode="present")
    site = await db.get_site(sid)
    screen_english(*await site_settings.sset_screen(site))
    screen_english(*await site_settings.advanced_screen(site))
    screen_english(*await site_settings.advanced_screen(await db.get_site(api)))
    for kind in ("i", "f", "s", "c", "k", "m", "h", "b"):
        call = FakeCall(f"ssp:{kind}:{sid}:x")
        await site_settings.cb_site_setting_picker(call, FakeState())
        _calls_english(call)
    english_only(site_settings._mismatch_text(site, "shop", "present"))
    english_only(site_settings._mismatch_text(site, "shop", "absent"))
    screen_english("", site_settings._save_anyway_kb(sid))


async def test_problem_screens(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await save_incident(sid, "links", "2 broken link(s)", "warning")
    call = FakeCall("menu_incidents")
    await incidents.cb_incidents(call)
    _calls_english(call)
    call = FakeCall(f"inc_explain:{inc_id}")
    await incidents.cb_incident_explain(call)
    _calls_english(call)
    call = FakeCall("incidents_clear")
    await incidents.cb_incidents_clear(call)
    _calls_english(call)
    call = FakeCall(f"inc_quiet:{inc_id}")
    await incidents.cb_incident_quiet(call)
    _calls_english(call)
    for action in ("redeploy", "purge"):
        call = FakeCall(f"act:{action}:{sid}")
        await alerts._ask(call, action, await db.get_site(sid))
        _calls_english(call)

    await settings_service.add_heartbeat_job("backup", 1440)
    await settings_service.add_heartbeat_job("sync", 60)
    await heartbeat_ping("sync")
    for name in ("backup", "sync"):
        screen_english(*await hb_screens.job_screen(name))
    for data in ("hb_show:backup", "hb_ask:backup", "hb_bot:backup", "hb_cron:backup", "hb_del:backup", "hb_rm:backup"):
        call = FakeCall(data)
        await getattr(hb_screens, {"hb_show": "cb_hb_show", "hb_ask": "cb_hb_ask", "hb_bot": "cb_hb_bot", "hb_cron": "cb_hb_cron",
                                   "hb_del": "cb_hb_del", "hb_rm": "cb_hb_remove_ask"}[data.split(":")[0]])(call)
        _calls_english(call)
    call = FakeCall("hb_add")
    await hb_screens.cb_hb_add(call, FakeState())
    _calls_english(call)
    for raw in ("кириллица", "backup", "new-job"):
        msg = FakeMessage(raw)
        await hb_screens.msg_hb_name(msg, FakeState())
        for text in msg.texts:
            english_only(text)
    screen_english(*await blocks.bots_page())

    fine = {"mx": ["mx.proton.me"], "provider": "Proton", "accepts": True, "spf": 1, "dkim": True, "dmarc": "reject",
            "findings": [], "domain": "ex.test", "status_page": "https://status.proton.me"}
    for mail in (None, fine, {**fine, "accepts": False, "fails": 3, "reason": "nowhere"}, {**fine, "accepts": False, "fails": 1},
                 {**fine, "dmarc": "none", "dkim": None, "findings": [_FINDING]}, {"mx": [], "locked": True, "findings": []},
                 {"mx": [], "sends_only": True, "locked": False, "findings": [], "spf": 1}):
        for line in mail_screen.mail_lines(mail):
            english_only(line)
        english_only(mail_screen.mail_brief(mail)[1])
    await sitestatus.update(sid, "mail", **{**fine, "findings": [_FINDING]})
    call = FakeCall(f"mail:{sid}")
    await mail_screen.cb_mail(call)
    _calls_english(call)

    for sec in (None, {"plain_http": False, "redirects": True, "missing": [], "exposed": [], "findings": [], "csp": True},
                {"plain_http": True, "redirects": None, "missing": ["X-Frame-Options"], "exposed": [], "findings": [_FINDING],
                 "csp": False, "github_pages": True}):
        for line in security_screen.security_lines(sec):
            english_only(line)
    await sitestatus.update(sid, "security", plain_http=False, redirects=True, missing=["X-Frame-Options"],
                            exposed=[], findings=[_FINDING], csp=False)
    call = FakeCall(f"sec:{sid}")
    await security_screen.cb_security(call)
    _calls_english(call)

    for seo in ({"status": "ok", "problems": [], "pages": 3, "no_js_chars": 500}, {"status": "unknown", "problems": []},
                {"status": "critical", "problems": [], "partial": True}):
        english_only(seo_screen.headline(seo, [], [], ai_checked=True))
        english_only(seo_screen.headline(seo, [], ["Google", t("Яндекс")], ai_checked=False))
    english_only(seo_screen.seo_text("https://ex.test", {"seo": {"status": "ok", "problems": [], "pages": 3, "no_js_chars": 500}}))
    screen_english("", seo_screen.seo_keyboard(sid, {"status": "ok", "problems": []}))

    for values in (None, {"none": True}, {"lcp": 5200, "inp": 650, "cls": 0.3, "ttfb": 900}):
        await sitestatus.update(sid, "visitors", **(values or {}))
        screen_english(*await speed.speed_page(await db.get_site(sid)))
    monkeypatch.setattr(speed.crux, "available", lambda: True)
    screen_english(*await speed.speed_page(await db.get_site(sid)))

    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    _calls_english(call)
    await save_feedback("https://ex.test", "https://ex.test/p", "hello", "", "1.1.1.1")
    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    _calls_english(call)

    monkeypatch.setattr(automation.integrations, "deploy_hooks", lambda: {"ex.test": "https://hook.test"})
    screen_english(*await automation.auto_screen())
    call = FakeCall("auto:pause:2")
    await automation.cb_auto_pause(call)
    _calls_english(call)
    screen_english(*await automation.auto_screen())

    await maintenance.add_window(None, [0, 1, 2, 3, 4], 120, 240)
    screen_english(*await maint_screen._screen())
    for data, handler in (("mw_add", "cb_mw_add"), ("mw_scope:0", "cb_mw_scope"), ("mw_days:0:w", "cb_mw_days")):
        call = FakeCall(data)
        await getattr(maint_screen, handler)(call)
        _calls_english(call)
    call = FakeCall("mw_custom:0:w")
    await maint_screen.cb_mw_custom(call, FakeState())
    _calls_english(call)
    msg = FakeMessage("garbage")
    await maint_screen.msg_mw_time(msg, FakeState(mw_scope="0", mw_days="w"))
    english_only(msg.texts[-1])

    english_only(await mute._mute("8h") or "")
    english_only(await mute._mute("30d") or "")
    english_only(t(mute._USAGE))
    call = FakeCall("menu_mute")
    await mute.cb_mute_menu(call)
    _calls_english(call)
    call = FakeCall("mute:off")
    await mute.cb_mute_pick(call)
    _calls_english(call)
