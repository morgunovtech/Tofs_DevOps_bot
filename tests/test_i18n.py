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
from conftest import FakeMessage, FakeState, buttons
from test_docs import _config_with

import main
from db.database import heartbeat_ping, queue_notification
from handlers import about, blocks, common, menu, start
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
