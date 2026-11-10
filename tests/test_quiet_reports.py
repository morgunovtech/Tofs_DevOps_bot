"""The main screen and the reports never hide a problem and do not repeat
what is fine: problems first and counted in full, a quiet morning is a few
lines, an old 🟠 does not bring the evening back, the week shows what changed."""

from conftest import FakeCall

from db.database import save_incident
from handlers import menu, sites
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
