"""The main screen and the reports never hide a problem and do not repeat
what is fine: problems first and counted in full, a quiet morning is a few
lines, an old 🟠 does not bring the evening back, the week shows what changed."""

from conftest import FakeCall

from db.database import save_incident
from handlers import menu, sites
from monitors.base import AvailabilityResult
from reports import scheduler
from reports.formatter import format_compact_status_report
from reports.weekly import build_weekly_report, incidents_block
from services import screenshots, sitestatus
from services import settings as settings_service


async def _const(value):
    return value


async def test_main_screen_counts_every_site_and_puts_problems_first(bot, db):
    sids = [await db.activate_or_create_site(f"https://s{i:02d}.test") for i in range(15)]
    for sid in sids:
        await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.update(sids[12], "avail", status="error", ms=None, error="HTTP 502")
    await save_incident(sids[12], "availability", "Site down: HTTP 502", "critical")
    header, _ = await menu.build_main_menu()
    assert header.startswith("🔴 1 проблема")
    assert "🔴 s12.test · хостинг отвечает ошибкой 502" in header
    assert header.index("s12.test") < header.index("s00.test")       # the problem on top
    assert "… и ещё 5" in header

    for sid in sids[:12]:                                             # 13 down: 3 of them below the fold
        await sitestatus.update(sid, "avail", status="error", ms=None, error="HTTP 502")
    header, _ = await menu.build_main_menu()
    assert header.startswith("🔴 13 проблем") and "… и ещё 5, из них с проблемами: 3" in header


async def test_a_task_without_sites_still_offers_to_add_a_site(bot, db):
    await settings_service.add_heartbeat_job("backup", 1440)
    _, kb = await menu.build_main_menu()
    assert "site_add" in [b.callback_data for row in kb.inline_keyboard for b in row]
    await settings_service.remove_heartbeat_job("backup")


def test_the_digest_never_hides_a_red_problem_and_does_not_repeat_the_site_line():
    avail = [AvailabilityResult(url=f"https://site{i}.test", site_id=i, response_time_ms=90, status_code=200)
             for i in range(1, 7)]
    avail[0] = AvailabilityResult(url="https://site1.test", site_id=1, status="error", error="HTTP 502")
    incidents = [{"check_type": "security", "severity": "warning", "url": f"https://site{i}.test",
                  "message": "нет HSTS"} for i in range(6, 1, -1)]                 # newest first, as the DB
    incidents += [{"check_type": "availability", "severity": "critical", "url": "https://site1.test",
                   "message": "Site down: HTTP 502"},
                  {"check_type": "exposed", "severity": "critical", "url": "https://site6.test",
                   "message": "наружу виден .git"}]
    text = format_compact_status_report(avail, incidents)
    assert "🔴 Открытых проблем: 7" in text
    assert "🔴 site6.test: наружу виден .git" in text                     # the old 🔴 is not cut off
    assert text.count("site1.test") == 1                                  # its line above says it
    assert "… и ещё 1 — весь список" in text
    short = format_compact_status_report(avail, incidents, short=True)
    assert "✅ Остальные в порядке: 5" in short and "✅ site2.test" not in short


async def test_weekly_lists_every_open_problem_red_first(bot, db):
    sids = [await db.activate_or_create_site(f"https://site{i}.test") for i in range(1, 7)]
    old, _ = await save_incident(sids[0], "exposed", "наружу виден .git", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-20 days') WHERE id = ?", (old,))
    await conn.commit()
    for sid in sids[1:]:
        await save_incident(sid, "security", "нет HSTS", "warning")
    text = "\n".join(await incidents_block())
    assert "Открыто сейчас (6):" in text and "… и ещё" not in text
    assert text.index("🔴 site1.test") < text.index("🟠")


async def _quiet_morning(db, monkeypatch, n: int, down: tuple[str, ...] = ()):
    for i in range(n):
        sid = await db.activate_or_create_site(f"https://s{i:02d}.test")
        await db.save_check(sid, "availability", "ok", response_time_ms=100)

    async def all_up(urls, manage=True):
        return [AvailabilityResult(url=u, site_id=0, status="error", error="HTTP 502") if u in down
                else AvailabilityResult(url=u, site_id=0, response_time_ms=100, status_code=200) for u in urls]

    monkeypatch.setattr(scheduler, "check_all", all_up)
    monkeypatch.setattr(scheduler, "check_all_ssl", lambda urls, manage=True: _const([]))
    monkeypatch.setattr(scheduler, "check_all_domains", lambda urls, manage=True: _const([]))
    monkeypatch.setattr(scheduler, "check_disk", lambda auto_cleanup=True: _const(
        {"over_threshold": False, "pct": 41}))


async def test_a_quiet_morning_is_a_few_lines_unless_asked_for_more(bot, db, monkeypatch):
    await _quiet_morning(db, monkeypatch, 20)
    await scheduler.send_morning_report()
    text = bot.sent[-1]["text"]
    assert "✅ Сайты в порядке: 20" in text and "s05.test" not in text
    assert len(text.split("\n")) <= 12
    await settings_service.set_digest_full(True)
    await scheduler.send_morning_report()
    assert "s05.test" in bot.sent[-1]["text"]
    await settings_service.set_digest_full(False)


async def test_a_down_site_and_five_search_findings_name_each_site_once(bot, db, monkeypatch):
    """The down site's own incident is said by its line, so it takes no slot
    of the five: all five 🟠 are listed, and «🔎 Поиск и ИИ» has nothing
    left to repeat."""
    await _quiet_morning(db, monkeypatch, 6, down=("https://s00.test",))
    sites = {s["url"]: s["id"] for s in await db.get_all_sites()}
    await save_incident(sites["https://s00.test"], "availability", "Site down: HTTP 502", "critical")
    for i in range(1, 6):
        sid = sites[f"https://s{i:02d}.test"]
        await db.save_check(sid, "seo", "warning", details="2 проблемы")
        await save_incident(sid, "seo", "2 помехи в поиске, напр.: Нет карты сайта (sitemap.xml)", "warning")
    text = await scheduler._status_snapshot("morning", extras=True)
    assert "🔴 Открытых проблем: 6" in text and "… и ещё" not in text and "Поиск и ИИ" not in text
    assert all(text.count(f"s{i:02d}.test") == 1 for i in range(6))


async def test_a_long_morning_is_split_not_cut(bot, db, monkeypatch):
    long = "🌅 Доброе утро\n" + "\n".join(f"🔴 site{i}.test — хостинг отвечает ошибкой 502" + "!" * 40
                                          for i in range(60))

    async def snapshot(report_type, extras, short=False):
        return long

    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    await scheduler.send_morning_report()
    texts = [m["text"] for m in bot.sent]
    assert len(texts) >= 2 and "(обрезано)" not in "".join(texts) and "site59.test" in texts[-1]


async def test_an_old_orange_problem_does_not_bring_the_evening_back(bot, db, monkeypatch):
    a = await db.activate_or_create_site("https://a.test")
    b = await db.activate_or_create_site("https://b.test")
    old, _ = await save_incident(a, "security", "нет HSTS", "warning")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-23 days') WHERE id = ?", (old,))
    await conn.commit()

    async def snapshot(report_type, extras, short=False):
        return report_type

    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    await scheduler.send_morning_report()
    sent = len(bot.sent)
    await scheduler.send_evening_report()
    assert len(bot.sent) == sent                                     # nothing new since the morning
    await save_incident(b, "links", "2 broken links", "warning")
    await scheduler.send_evening_report()
    assert len(bot.sent) == sent + 1 and bot.sent[-1]["text"] == "evening"


async def test_the_week_shows_what_changed_not_every_quiet_day(bot, db):
    for i in range(20):
        sid = await db.activate_or_create_site(f"https://s{i:02d}.test")
        conn = await db.get_db()
        for day in range(1, 8):
            ms = 2500 if (i == 3 and day == 4) else 300
            await conn.execute("INSERT INTO checks (site_id, check_type, status, response_time_ms, checked_at) "
                               "VALUES (?, 'availability', 'ok', ?, datetime('now', ?))", (sid, ms, f"-{day} days"))
        await conn.commit()
    text, chart = await build_weekly_report()
    assert "s03.test" in "\n".join(chart) and not any("s05.test" in line for line in chart)
    assert "<pre>" not in "\n".join(chart)
    assert sum(1 for line in text.split("\n") if "без сбоев" in line) == 1   # quiet sites: one line


async def test_card_does_not_say_all_is_well_before_the_first_check_or_under_downtime(bot, db, monkeypatch):
    monkeypatch.setattr(screenshots, "is_due", lambda sid, url: _const(False))
    sid = await db.activate_or_create_site("https://fresh.test")
    call = FakeCall(f"check_site:{sid}")
    await sites.render_card(call, await db.get_site(sid))
    assert "✅ Всё в порядке" not in call.message.texts[-1] and "⏳ Ещё не проверял" in call.message.texts[-1]
    conn = await db.get_db()
    for minutes in range(0, 60, 5):
        await conn.execute("INSERT INTO checks (site_id, check_type, status, checked_at) "
                           "VALUES (?, ?, ?, datetime('now', ?))",
                           (sid, "availability", "error" if minutes < 30 else "ok", f"-{minutes} minutes"))
    await conn.commit()
    lines = await sites._history_lines(await db.get_site(sid))
    assert lines and not lines[0].startswith("✅") and "Простой" in lines[0]
