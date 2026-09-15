import sqlite3

from db import database


async def test_legacy_schema_upgrades_in_place(tmp_path):
    """A pre-versioning DB (user_version 0, with the dead columns) must
    migrate to the current schema without losing rows."""
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE sites (id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE NOT NULL,
            name TEXT, added_at TEXT DEFAULT (datetime('now')), active INTEGER DEFAULT 1);
        CREATE TABLE heartbeats (job TEXT PRIMARY KEY, last_ping TEXT, ping_count INTEGER DEFAULT 0);
        CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        INSERT INTO sites (url, name) VALUES ('https://example.com', 'https://example.com');
        INSERT INTO heartbeats (job, last_ping, ping_count) VALUES ('backup', '2026-09-01 00:00:00', 3);
    """)
    raw.commit()
    raw.close()

    await database.close_db()
    database.configure(str(path))
    await database.init_db()
    db = await database.get_db()
    version = (await (await db.execute("PRAGMA user_version")).fetchone())[0]
    assert version == 3
    cols = {r[1] for r in await (await db.execute("PRAGMA table_info(sites)")).fetchall()}
    assert {"check_interval_min", "slow_ms", "http_method"} <= cols
    assert "name" not in cols
    assert await database.get_active_site_urls() == ["https://example.com"]
    assert (await database.get_heartbeats())["backup"] == "2026-09-01 00:00:00"
    # Idempotent: a second init is a no-op.
    await database.init_db()
    await database.close_db()


async def test_uptime_union_of_raw_and_rollups(db):
    sid = await db.get_or_create_site("https://example.com")
    for status, ms in (("ok", 100), ("ok", 200), ("error", None)):
        await db.save_check(sid, "availability", status, response_time_ms=ms)
    conn = await db.get_db()
    # Two rolled-up days from before the raw window.
    await conn.execute(
        "INSERT INTO checks_daily VALUES (?, 'availability', date('now', '-10 days'), 10, 9, 1000)", (sid,))
    await conn.execute(
        "INSERT INTO checks_daily VALUES (?, 'availability', date('now', '-100 days'), 10, 0, 0)", (sid,))
    await conn.commit()

    day = await db.get_uptime_stats(sid, hours=24)
    assert day["total_checks"] == 3 and day["ok_checks"] == 2

    month = await db.get_uptime_over_days(sid, 30)
    assert month["total_checks"] == 13 and month["ok_checks"] == 11   # raw + the -10d rollup
    quarter = await db.get_uptime_over_days(sid, 90)
    assert quarter["total_checks"] == 13                              # -100d is outside 90d
    rows = await db.get_daily_availability(sid, 30)
    assert len(rows) == 2 and rows[0]["avg_ms"] == 100


async def test_rollup_moves_rows_into_daily(db):
    sid = await db.get_or_create_site("https://example.com")
    conn = await db.get_db()
    await conn.execute(
        """INSERT INTO checks (site_id, check_type, status, response_time_ms, checked_at)
           VALUES (?, 'availability', 'ok', 50, datetime('now', '-40 days'))""", (sid,))
    await conn.commit()
    await db.save_check(sid, "availability", "ok", response_time_ms=70)
    assert await db.rollup_old_checks(30) == 1
    assert await db.rollup_old_checks(30) == 0
    assert (await db.get_uptime_over_days(sid, 90))["total_checks"] == 2
    assert (await db.get_uptime_stats(sid, hours=24))["total_checks"] == 1


async def test_incident_lifecycle_and_feedback(db):
    sid = await db.get_or_create_site("https://example.com")
    inc_id, new = await db.save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    assert new
    assert (await db.save_incident(sid, "availability", "again"))[1] is False
    assert (await db.get_incident(inc_id))["url"] == "https://example.com"
    resolved = await db.resolve_incident(sid, "availability")
    assert resolved and resolved["id"] == inc_id
    assert await db.resolve_incident(sid, "availability") is None

    fid = await db.save_feedback("https://example.com", "https://example.com/p", "typo", "UA", "1.2.3.4")
    assert await db.count_feedback() == 1
    assert (await db.list_feedback())[0]["id"] == fid

    await db.update_site_settings(sid, slow_ms=1500, http_method="POST")
    site = await db.get_site(sid)
    assert site["slow_ms"] == 1500 and site["http_method"] == "POST"
    try:
        await db.update_site_settings(sid, url="https://evil")
    except ValueError:
        pass
    else:
        raise AssertionError("non-whitelisted column accepted")


async def test_deactivate_closes_incidents_and_keeps_history(db):
    sid = await db.get_or_create_site("https://example.com")
    await db.save_incident(sid, "ssl", "expiring")
    assert await db.deactivate_site(sid)
    assert await db.get_active_incidents() == []
    assert await db.get_active_site_urls() == []
    assert await db.activate_or_create_site("https://example.com") == sid
