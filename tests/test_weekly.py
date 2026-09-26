from datetime import UTC, datetime, timedelta

from db.database import resolve_incident_by_id, save_incident
from reports.scheduler import pack_blocks
from reports.weekly import build_weekly_report, render_ascii_chart
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
    assert "Открыто сейчас (1):" in text and "  🟠 ex.test: 2 помехи в поиске" in text
    assert "Решено за неделю: 1" in text and "  ✓ " in text and "ex.test не открывается" in text
    assert "⚠️ Проблем" not in text and "🔴" not in text            # a solved outage is ✓, not 🔴
    assert "🔒 Сертификаты продлились сами: ex.test" in text


def test_ascii_chart_blocks():
    series = {"ex.com": [{"day": "2026-09-08", "avg_ms": 100}, {"day": "2026-09-09", "avg_ms": 200}],
              "empty": []}
    blocks = render_ascii_chart(series)
    assert len(blocks) == 1 and blocks[0].startswith("<pre>ex.com")
    assert "08.09" in blocks[0] and "0.20 с" in blocks[0]
    assert render_ascii_chart({"x": [{"day": "2026-09-08", "avg_ms": None}]}) == []


def test_pack_blocks():
    blocks = ["a" * 300, "b" * 300, "c" * 300]
    assert pack_blocks(blocks, limit=650) == ["a" * 300 + "\n\n" + "b" * 300, "c" * 300]
    assert pack_blocks([], limit=10) == []
