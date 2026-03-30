import aiosqlite
import os
from datetime import datetime

DB_PATH = "data/bot.db"


async def get_db() -> aiosqlite.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                name TEXT,
                added_at TEXT DEFAULT (datetime('now')),
                active INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                check_type TEXT NOT NULL,
                status TEXT NOT NULL,
                response_time_ms INTEGER,
                status_code INTEGER,
                details TEXT,
                checked_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                check_type TEXT NOT NULL,
                message TEXT NOT NULL,
                severity TEXT DEFAULT 'warning',
                resolved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                resolved_at TEXT,
                FOREIGN KEY (site_id) REFERENCES sites(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_url TEXT,
                page_url TEXT,
                message TEXT NOT NULL,
                user_agent TEXT,
                ip_address TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_checks_site_type
                ON checks(site_id, check_type, checked_at);
            CREATE INDEX IF NOT EXISTS idx_incidents_active
                ON incidents(site_id, resolved);
        """)
        await db.commit()
    finally:
        await db.close()


async def get_or_create_site(url: str, name: str | None = None) -> int:
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM sites WHERE url = ?", (url,))
        row = await cursor.fetchone()
        if row:
            return row[0]
        cursor = await db.execute(
            "INSERT INTO sites (url, name) VALUES (?, ?)",
            (url, name or url)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def save_check(site_id: int, check_type: str, status: str,
                     response_time_ms: int | None = None,
                     status_code: int | None = None,
                     details: str | None = None):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO checks (site_id, check_type, status, response_time_ms, status_code, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (site_id, check_type, status, response_time_ms, status_code, details)
        )
        await db.commit()
    finally:
        await db.close()


async def save_incident(site_id: int, check_type: str, message: str,
                        severity: str = "warning") -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT id FROM incidents
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type)
        )
        existing = await cursor.fetchone()
        if existing:
            return existing[0]
        cursor = await db.execute(
            "INSERT INTO incidents (site_id, check_type, message, severity) VALUES (?, ?, ?, ?)",
            (site_id, check_type, message, severity)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def resolve_incident(site_id: int, check_type: str) -> bool:
    db = await get_db()
    try:
        cursor = await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type)
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_active_incidents() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT i.*, s.url, s.name FROM incidents i
               JOIN sites s ON i.site_id = s.id
               WHERE i.resolved = 0
               ORDER BY i.created_at DESC"""
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_latest_checks(site_id: int | None = None) -> list[dict]:
    db = await get_db()
    try:
        if site_id:
            cursor = await db.execute(
                """SELECT c.*, s.url, s.name FROM checks c
                   JOIN sites s ON c.site_id = s.id
                   WHERE c.site_id = ?
                   ORDER BY c.checked_at DESC LIMIT 20""",
                (site_id,)
            )
        else:
            cursor = await db.execute(
                """SELECT c.*, s.url, s.name
                   FROM checks c
                   JOIN sites s ON c.site_id = s.id
                   WHERE c.id IN (
                       SELECT MAX(id) FROM checks
                       GROUP BY site_id, check_type
                   )
                   ORDER BY s.url, c.check_type"""
            )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_all_sites() -> list[dict]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM sites WHERE active = 1 ORDER BY url"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def save_feedback(site_url: str, page_url: str, message: str,
                        user_agent: str = "", ip_address: str = "") -> int:
    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO feedback (site_url, page_url, message, user_agent, ip_address)
               VALUES (?, ?, ?, ?, ?)""",
            (site_url, page_url, message, user_agent, ip_address)
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


async def get_uptime_stats(site_id: int, hours: int = 24) -> dict:
    db = await get_db()
    try:
        cursor = await db.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_count,
                AVG(response_time_ms) as avg_response_time
               FROM checks
               WHERE site_id = ? AND check_type = 'availability'
                 AND checked_at >= datetime('now', ?)""",
            (site_id, f"-{hours} hours")
        )
        row = await cursor.fetchone()
        total = row[0] or 0
        ok = row[1] or 0
        return {
            "total_checks": total,
            "ok_checks": ok,
            "uptime_pct": round(ok / total * 100, 2) if total > 0 else 0,
            "avg_response_ms": round(row[2]) if row[2] else 0
        }
    finally:
        await db.close()
