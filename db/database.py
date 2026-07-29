import asyncio
import os

import aiosqlite

from config import config

# One shared connection for the whole process — aiosqlite serialises statements
# internally, so per-call connect/close churn buys nothing. `_write_lock` groups
# multi-statement write sequences so concurrent tasks can't interleave
# statements into each other's transactions on the shared connection.
_conn: aiosqlite.Connection | None = None
_conn_lock = asyncio.Lock()
_write_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        async with _conn_lock:
            if _conn is None:
                path = config.db_path
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                conn = await aiosqlite.connect(path)
                conn.row_factory = aiosqlite.Row
                # Make concurrent reads + single-writer cleanly tolerated.
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                _conn = conn
    return _conn


async def close_db():
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


async def init_db():
    db = await get_db()
    async with _write_lock:
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

            -- Tracks the last alert-ladder threshold we notified for SSL/domain
            -- so we don't ping daily about the same "expires in 14 days" warning.
            CREATE TABLE IF NOT EXISTS alert_state (
                site_id INTEGER NOT NULL,
                check_type TEXT NOT NULL,
                last_threshold INTEGER,
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (site_id, check_type)
            );

            -- Generic key/value store for bot state (mute deadline, etc.)
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_checks_site_type
                ON checks(site_id, check_type, checked_at);
            CREATE INDEX IF NOT EXISTS idx_incidents_active
                ON incidents(site_id, resolved);
        """)
        await db.commit()


async def get_or_create_site(url: str, name: str | None = None) -> int:
    db = await get_db()
    async with _write_lock:
        # Upsert-style insert instead of check-then-insert: two concurrent
        # callers can't race into an IntegrityError on the UNIQUE url.
        await db.execute(
            "INSERT INTO sites (url, name) VALUES (?, ?) ON CONFLICT(url) DO NOTHING",
            (url, name or url),
        )
        await db.commit()
    cursor = await db.execute("SELECT id FROM sites WHERE url = ?", (url,))
    row = await cursor.fetchone()
    return row[0]


async def save_check(site_id: int, check_type: str, status: str,
                     response_time_ms: int | None = None,
                     status_code: int | None = None,
                     details: str | None = None):
    db = await get_db()
    async with _write_lock:
        await db.execute(
            """INSERT INTO checks (site_id, check_type, status,
                                   response_time_ms, status_code, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (site_id, check_type, status, response_time_ms, status_code, details),
        )
        await db.commit()


async def save_incident(site_id: int, check_type: str, message: str,
                        severity: str = "warning") -> tuple[int, bool]:
    """Insert or reuse an open incident. Returns (incident_id, is_new).

    `is_new=True` means this is the first time we've seen this problem since the
    last resolution — callers use it to fire alerts only on state change.
    """
    db = await get_db()
    async with _write_lock:
        # The lock makes select-then-insert atomic w.r.t. other tasks.
        cursor = await db.execute(
            """SELECT id FROM incidents
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type),
        )
        existing = await cursor.fetchone()
        if existing:
            return existing[0], False
        cursor = await db.execute(
            """INSERT INTO incidents (site_id, check_type, message, severity)
               VALUES (?, ?, ?, ?)""",
            (site_id, check_type, message, severity),
        )
        await db.commit()
        return cursor.lastrowid, True


async def resolve_incident(site_id: int, check_type: str) -> bool:
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type),
        )
        await db.commit()
        return cursor.rowcount > 0


async def resolve_all_incidents() -> int:
    """Mark every open incident as resolved. Returns how many were closed.

    Useful after a deploy that changed alerting heuristics — lets the user
    clear stale incidents created by old code.
    """
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE resolved = 0"""
        )
        n = cursor.rowcount
        # Also reset the alert ladder so SSL/domain warnings re-arm cleanly.
        await db.execute("DELETE FROM alert_state")
        await db.commit()
        return n


async def get_active_incidents() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT i.*, s.url, s.name FROM incidents i
           JOIN sites s ON i.site_id = s.id
           WHERE i.resolved = 0
           ORDER BY i.created_at DESC"""
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def get_latest_checks(site_id: int | None = None) -> list[dict]:
    db = await get_db()
    if site_id:
        cursor = await db.execute(
            """SELECT c.*, s.url, s.name FROM checks c
               JOIN sites s ON c.site_id = s.id
               WHERE c.site_id = ?
               ORDER BY c.checked_at DESC LIMIT 20""",
            (site_id,),
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


async def get_recent_check_statuses(site_id: int, check_type: str,
                                    limit: int = 5) -> list[str]:
    """Return the most recent N check statuses for a site/type, newest first."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT status FROM checks
           WHERE site_id = ? AND check_type = ?
           ORDER BY id DESC LIMIT ?""",
        (site_id, check_type, limit),
    )
    rows = await cursor.fetchall()
    return [r[0] for r in rows]


async def get_all_sites() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM sites WHERE active = 1 ORDER BY url"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def save_feedback(site_url: str, page_url: str, message: str,
                        user_agent: str = "", ip_address: str = "") -> int:
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """INSERT INTO feedback (site_url, page_url, message,
                                     user_agent, ip_address)
               VALUES (?, ?, ?, ?, ?)""",
            (site_url, page_url, message, user_agent, ip_address),
        )
        await db.commit()
        return cursor.lastrowid


async def get_uptime_stats(site_id: int, hours: int = 24) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as ok_count,
            AVG(response_time_ms) as avg_response_time
           FROM checks
           WHERE site_id = ? AND check_type = 'availability'
             AND checked_at >= datetime('now', ?)""",
        (site_id, f"-{hours} hours"),
    )
    row = await cursor.fetchone()
    total = row[0] or 0
    ok = row[1] or 0
    return {
        "total_checks": total,
        "ok_checks": ok,
        "uptime_pct": round(ok / total * 100, 2) if total > 0 else 0,
        "avg_response_ms": round(row[2]) if row[2] else 0,
    }


# ── Alert ladder state ───────────────────────────────────────────────────────

async def get_last_alert_threshold(site_id: int, check_type: str) -> int | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT last_threshold FROM alert_state WHERE site_id = ? AND check_type = ?",
        (site_id, check_type),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_last_alert_threshold(site_id: int, check_type: str,
                                   threshold: int | None):
    db = await get_db()
    async with _write_lock:
        await db.execute(
            """INSERT INTO alert_state (site_id, check_type, last_threshold, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(site_id, check_type) DO UPDATE SET
                 last_threshold = excluded.last_threshold,
                 updated_at = datetime('now')""",
            (site_id, check_type, threshold),
        )
        await db.commit()


# ── bot_state KV ─────────────────────────────────────────────────────────────

async def get_state(key: str) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM bot_state WHERE key = ?", (key,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_state(key: str, value: str | None):
    db = await get_db()
    async with _write_lock:
        if value is None:
            await db.execute("DELETE FROM bot_state WHERE key = ?", (key,))
        else:
            await db.execute(
                """INSERT INTO bot_state (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = excluded.value,
                     updated_at = datetime('now')""",
                (key, value),
            )
        await db.commit()
