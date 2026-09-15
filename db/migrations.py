"""Schema migrations, tracked with PRAGMA user_version.

Every function takes an open aiosqlite connection and is applied once, in
order; the caller (init_db) holds the write lock and commits. Existing
databases created before versioning are at user_version 0 and get the
whole list — migration 1 is idempotent (IF NOT EXISTS / ADD COLUMN guards)
precisely so that legacy files upgrade in place.
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


async def _columns(db, table: str) -> set[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in await cursor.fetchall()}


async def _add_columns(db, table: str, columns: dict[str, str]):
    existing = await _columns(db, table)
    for name, decl in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


async def _drop_column(db, table: str, column: str):
    if column not in await _columns(db, table):
        return
    try:
        await db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    except sqlite3.OperationalError as e:  # SQLite < 3.35
        logger.warning("Could not drop %s.%s (%s) — leaving it in place", table, column, e)


async def m1_base_schema(db):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL REFERENCES sites(id),
            check_type TEXT NOT NULL,
            status TEXT NOT NULL,
            response_time_ms INTEGER,
            status_code INTEGER,
            details TEXT,
            checked_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL REFERENCES sites(id),
            check_type TEXT NOT NULL,
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'warning',
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            resolved_at TEXT
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
        -- Last alert-ladder threshold notified for SSL/domain per site.
        CREATE TABLE IF NOT EXISTS alert_state (
            site_id INTEGER NOT NULL,
            check_type TEXT NOT NULL,
            last_threshold INTEGER,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (site_id, check_type)
        );
        -- Generic key/value store for bot state (mute deadline, secrets…).
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        -- Dead-man switch: external jobs ping the bot.
        CREATE TABLE IF NOT EXISTS heartbeats (
            job TEXT PRIMARY KEY,
            last_ping TEXT
        );
        -- Last known DNS answers per host/record type.
        CREATE TABLE IF NOT EXISTS dns_state (
            host TEXT NOT NULL,
            rtype TEXT NOT NULL,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (host, rtype)
        );
        -- Daily rollups of raw checks older than RETENTION_DAYS.
        CREATE TABLE IF NOT EXISTS checks_daily (
            site_id INTEGER NOT NULL,
            check_type TEXT NOT NULL,
            day TEXT NOT NULL,
            total INTEGER DEFAULT 0,
            ok INTEGER DEFAULT 0,
            sum_ms REAL DEFAULT 0,
            PRIMARY KEY (site_id, check_type, day)
        );
        -- Non-critical notifications held back during quiet hours / mute.
        CREATE TABLE IF NOT EXISTS pending_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_checks_site_type
            ON checks(site_id, check_type, checked_at);
        CREATE INDEX IF NOT EXISTS idx_incidents_active
            ON incidents(site_id, resolved);
    """)
    # Per-site dials that pre-versioning databases may lack.
    await _add_columns(db, "sites", {
        "check_interval_min": "INTEGER",
        "fail_threshold": "INTEGER",
        "accepted_codes": "TEXT",
        "keyword": "TEXT",
        "keyword_mode": "TEXT",
    })


async def m2_site_dials_and_indexes(db):
    await _add_columns(db, "sites", {
        # Per-site "slow response" threshold in ms (NULL = SLOW_RESPONSE_MS).
        "slow_ms": "INTEGER",
        # Custom HTTP request: method, JSON-encoded headers, raw body.
        "http_method": "TEXT",
        "http_headers": "TEXT",
        "http_body": "TEXT",
    })
    # Retention deletes by checked_at alone — without this it is a full scan.
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_checks_checked_at ON checks(checked_at)")


async def m3_drop_dead_columns(db):
    # sites.name always equalled url and heartbeats.ping_count was never read.
    await _drop_column(db, "sites", "name")
    await _drop_column(db, "heartbeats", "ping_count")


MIGRATIONS = [m1_base_schema, m2_site_dials_and_indexes, m3_drop_dead_columns]


async def migrate(db) -> int:
    """Apply pending migrations; returns the number applied."""
    cursor = await db.execute("PRAGMA user_version")
    version = (await cursor.fetchone())[0]
    applied = 0
    for number, step in enumerate(MIGRATIONS, start=1):
        if number <= version:
            continue
        logger.info("DB migration %d: %s", number, step.__name__)
        await step(db)
        await db.execute(f"PRAGMA user_version = {number}")
        applied += 1
    return applied
