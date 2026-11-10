from datetime import UTC, datetime, timedelta

from db.database import resolve_incident_by_id, save_incident
from reports.weekly import build_weekly_report
from services import sitestatus


async def test_weekly_lists_open_problems_by_level_and_facts_without_icons(bot, db):
    """«Остальное (🟠) — в воскресном отчёте» must hold even for a problem
    opened three weeks ago; a certificate that renewed by itself is a fact
    line here and nowhere else."""
    sid = await db.activate_or_create_site("https://ex.test")
    old_id, _ = await save_incident(sid, "seo", "2 помехи в поиске, напр.: Нет карты сайта", "warning")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-20 days') WHERE id = ?", (old_id,))
    await conn.commit()
    down_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await resolve_incident_by_id(down_id)
    await sitestatus.update(sid, "ssl", days_left=80, issuer="LE", not_after=None, error=None,
                            renewed_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds"))
    text, _ = await build_weekly_report()
    assert "Открыто сейчас (1):\n🟠 ex.test: нет карты сайта и ещё 1 помеха — с " in text
    assert "Решено за неделю: 1\n✓ " in text and "ex.test не открывается" in text
    assert "⚠️ Проблем" not in text and "🔴" not in text            # a solved outage is ✓, not 🔴
    assert "🔒 Сертификаты продлились сами: ex.test" in text
