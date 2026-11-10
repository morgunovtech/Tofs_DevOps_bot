"""Schema migrations, tracked with PRAGMA user_version.

Every function takes an open aiosqlite connection and is applied once, in
order; each step runs in its own transaction together with its user_version,
so a failure leaves the database exactly as it was before the step. The
caller (init_db) holds the write lock. Existing databases created before
versioning are at user_version 0 and get the whole list — migration 1 is
idempotent (IF NOT EXISTS / ADD COLUMN guards) precisely so that legacy
files upgrade in place. A released migration is never edited: fix it with
the next one. Inside a step use db.execute or _script, never executescript
(it commits whatever is open first).
"""

import logging
import sqlite3

logger = logging.getLogger(__name__)


class StartRefused(RuntimeError):
    """The database cannot be used by this build as it is: said in one
    sentence, and the start stops before anything is changed."""


class SchemaTooNew(StartRefused):
    def __init__(self, found: int, known: int):
        super().__init__(
            f"База от более новой версии бота (схема v{found}, эта версия знает v{known}). "
            f"Верни версию поновее или восстанови копию data/backups/schema-v{known:03d}-….db, "
            f"сделанную перед обновлением, — раздел «Откат версии» в README.")
        self.found, self.known = found, known


async def schema_version(db) -> int:
    cursor = await db.execute("PRAGMA user_version")
    return (await cursor.fetchone())[0]


async def _script(db, script: str):
    """executescript() commits whatever is open first — inside a migration's
    transaction it must not: the statements go one by one."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            await db.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError(f"incomplete SQL in a migration: {statement.strip()[:60]}")


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
    await _script(db, """
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


async def m4_queued_buttons(db):
    # A message deferred by mute / quiet hours keeps its buttons: its text
    # often says «нажми «Подключить»», and without them it cannot be acted on.
    await _add_columns(db, "pending_notifications", {"reply_markup": "TEXT"})


async def m5_outbox(db):
    # Messages Telegram did not take (network, 5xx, flood limit): kept and
    # retried every minute — a lost «сайт лёг» would leave the silence lying.
    await _script(db, """
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            reply_markup TEXT,
            priority TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)


async def m6_visitor_speed(db):
    # Created rum_samples for the own speed measurement, since dropped (m7).
    pass


async def m7_drop_visitor_samples(db):
    # The own measurement on sites is gone: real visitors' speed comes from
    # Google's Chrome data only, so its page views are not kept anywhere.
    await _script(db, """
        DROP INDEX IF EXISTS idx_rum_site_at;
        DROP TABLE IF EXISTS rum_samples;
    """)


async def m8_incident_alerted_at(db):
    # When the incident's alert went out (delivered, queued or kept in the
    # outbox); NULL — still owed, reports.scheduler.deliver_missed_alerts
    # sends it. Incidents opened before this version count as alerted:
    # re-sending all of them after an update would be a storm of stale alerts.
    await _add_columns(db, "incidents", {"alerted_at": "TEXT"})
    await db.execute("UPDATE incidents SET alerted_at = COALESCE(created_at, datetime('now')) "
                     "WHERE alerted_at IS NULL")
    # The mark used to live in bot_state (announced:<id>); the column replaces it.
    await db.execute("DELETE FROM bot_state WHERE key LIKE 'announced:%'")


async def m9_checks_scheduled(db):
    # Which availability rows came from the schedule: a «🔄 Проверить», a
    # digest snapshot or a follow-up also writes a row, and the threshold
    # «N сбоев подряд» must count planned checks only. Old rows count as planned.
    await _add_columns(db, "checks", {"scheduled": "INTEGER NOT NULL DEFAULT 1"})


MIGRATIONS = [m1_base_schema, m2_site_dials_and_indexes, m3_drop_dead_columns, m4_queued_buttons, m5_outbox,
              m6_visitor_speed, m7_drop_visitor_samples, m8_incident_alerted_at, m9_checks_scheduled]


async def migrate(db, upto: int | None = None) -> int:
    """Apply pending migrations; returns the number applied. A database newer
    than this build is refused, not used: old code on a new schema breaks in
    ways nobody sees (a dropped column, a table it does not know). `upto`
    stops after that step: a restore builds the copy's own schema version
    first, moves the rows in, and only then catches up — so migrations that
    change data reach the restored rows too (db.database.restore_from_file).
    Each step is one transaction with its user_version: all or nothing."""
    version = await schema_version(db)
    if version > len(MIGRATIONS):
        raise SchemaTooNew(version, len(MIGRATIONS))
    applied = 0
    for number, step in enumerate(MIGRATIONS, start=1):
        if number <= version or (upto is not None and number > upto):
            continue
        logger.info("DB migration %d: %s", number, step.__name__)
        if db.in_transaction:
            await db.commit()
        await db.execute("BEGIN IMMEDIATE")
        try:
            await step(db)
            await db.execute(f"PRAGMA user_version = {number}")
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        applied += 1
    return applied
