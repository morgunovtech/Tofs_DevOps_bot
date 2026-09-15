"""SQLite access layer.

One shared aiosqlite connection for the whole process — aiosqlite serialises
statements internally, so per-call connect/close churn buys nothing.
`_write_lock` groups multi-statement write sequences so concurrent tasks
can't interleave statements into each other's transactions.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import aiosqlite

from config import config
from db.migrations import migrate
from utils.urls import is_http_url

_conn: aiosqlite.Connection | None = None
_conn_lock = asyncio.Lock()
_write_lock = asyncio.Lock()
_db_path: str = config.db_path


def configure(path: str):
    """Point the module at another file (tests, tools). Call before get_db()."""
    global _db_path
    _db_path = path


async def get_db() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        async with _conn_lock:
            if _conn is None:
                os.makedirs(os.path.dirname(_db_path) or ".", exist_ok=True)
                conn = await aiosqlite.connect(_db_path)
                conn.row_factory = aiosqlite.Row
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute("PRAGMA foreign_keys=ON")
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
        await migrate(db)
        await db.commit()


# ── Sites ────────────────────────────────────────────────────────────────────

async def get_or_create_site(url: str) -> int:
    db = await get_db()
    async with _write_lock:
        # Upsert-style insert: two concurrent callers can't race into an
        # IntegrityError on the UNIQUE url.
        await db.execute(
            "INSERT INTO sites (url) VALUES (?) ON CONFLICT(url) DO NOTHING", (url,))
        await db.commit()
    cursor = await db.execute("SELECT id FROM sites WHERE url = ?", (url,))
    return (await cursor.fetchone())[0]


async def get_site(site_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_site_by_url(url: str) -> dict:
    """Full site row (incl. per-site settings), creating the row if needed."""
    return await get_site(await get_or_create_site(url))


async def get_all_sites() -> list[dict]:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM sites WHERE active = 1 ORDER BY url")
    return [dict(r) for r in await cursor.fetchall()]


async def get_active_site_urls() -> list[str]:
    db = await get_db()
    cursor = await db.execute("SELECT url FROM sites WHERE active = 1 ORDER BY id")
    return [r[0] for r in await cursor.fetchall()]


async def get_active_http_site_urls() -> list[str]:
    """Active sites that are real web pages — the input for every monitor
    that only makes sense over HTTP (SSL, domains, DNS, links, SEO, deep)."""
    return [u for u in await get_active_site_urls() if is_http_url(u)]


async def count_sites_total() -> int:
    """All site rows, active or not — used to decide whether to seed from env."""
    db = await get_db()
    cursor = await db.execute("SELECT COUNT(*) FROM sites")
    return (await cursor.fetchone())[0]


async def activate_or_create_site(url: str) -> int:
    """Add a site from the UI (or re-activate a previously removed one)."""
    site_id = await get_or_create_site(url)
    db = await get_db()
    async with _write_lock:
        await db.execute("UPDATE sites SET active = 1 WHERE id = ?", (site_id,))
        await db.commit()
    return site_id


async def deactivate_site(site_id: int) -> bool:
    """Soft-remove: checks/incidents history stays, monitoring stops."""
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            "UPDATE sites SET active = 0 WHERE id = ? AND active = 1", (site_id,))
        # Close its open incidents so they don't haunt the incidents list.
        await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE site_id = ? AND resolved = 0""", (site_id,))
        await db.commit()
        return cursor.rowcount > 0


# Whitelist for update_site_settings — the only columns the UI may touch.
SITE_SETTING_COLS = frozenset({
    "check_interval_min", "fail_threshold", "accepted_codes",
    "keyword", "keyword_mode", "slow_ms",
    "http_method", "http_headers", "http_body",
})


async def update_site_settings(site_id: int, **fields):
    """Set per-site monitoring overrides; value None clears an override."""
    unknown = set(fields) - SITE_SETTING_COLS
    if unknown:
        raise ValueError(f"Unknown site setting(s): {unknown}")
    if not fields:
        return
    db = await get_db()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    async with _write_lock:
        await db.execute(
            f"UPDATE sites SET {assignments} WHERE id = ?",
            (*fields.values(), site_id))
        await db.commit()


# ── Checks ───────────────────────────────────────────────────────────────────

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
            (site_id, check_type, status, response_time_ms, status_code, details))
        await db.commit()


async def get_last_check(site_id: int, check_type: str) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT * FROM checks WHERE site_id = ? AND check_type = ?
           ORDER BY id DESC LIMIT 1""", (site_id, check_type))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_recent_check_statuses(site_id: int, check_type: str,
                                    limit: int = 5) -> list[str]:
    """Most recent N statuses for a site/type, newest first."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT status FROM checks WHERE site_id = ? AND check_type = ?
           ORDER BY id DESC LIMIT ?""", (site_id, check_type, limit))
    return [r[0] for r in await cursor.fetchall()]


async def get_recent_response_times(site_id: int, limit: int = 12) -> list[int]:
    """Last N successful availability response times, oldest first."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT response_time_ms FROM checks
           WHERE site_id = ? AND check_type = 'availability'
             AND status = 'ok' AND response_time_ms IS NOT NULL
           ORDER BY id DESC LIMIT ?""", (site_id, limit))
    return [r[0] for r in reversed(await cursor.fetchall())]


async def get_uptime_stats(site_id: int, hours: int = 24) -> dict:
    """Uptime over the last N hours from raw checks (retention keeps far
    more than the 24h/7d windows this serves)."""
    db = await get_db()
    cursor = await db.execute(
        """SELECT COUNT(*), SUM(status = 'ok'), AVG(response_time_ms)
           FROM checks
           WHERE site_id = ? AND check_type = 'availability'
             AND checked_at >= datetime('now', ?)""",
        (site_id, f"-{hours} hours"))
    total, ok, avg = await cursor.fetchone()
    return _uptime_dict(total or 0, ok or 0, avg)


def _uptime_dict(total: int, ok: int, avg_ms) -> dict:
    return {
        "total_checks": total,
        "ok_checks": ok,
        "uptime_pct": round(ok / total * 100, 2) if total else 0,
        "avg_response_ms": round(avg_ms) if avg_ms else 0,
    }


# Raw checks for the recent window UNION the nightly rollups for anything
# older — the only way a 30/90-day number stays honest past RETENTION_DAYS.
_DAILY_UNION = """
    SELECT day, SUM(total) AS total, SUM(ok) AS ok, SUM(sum_ms) AS sum_ms FROM (
        SELECT date(checked_at) AS day, COUNT(*) AS total,
               SUM(status = 'ok') AS ok,
               COALESCE(SUM(response_time_ms), 0) AS sum_ms
        FROM checks
        WHERE site_id = ? AND check_type = 'availability'
          AND checked_at >= datetime('now', ?)
        GROUP BY date(checked_at)
        UNION ALL
        SELECT day, total, ok, sum_ms FROM checks_daily
        WHERE site_id = ? AND check_type = 'availability'
          AND day >= date('now', ?)
    ) GROUP BY day ORDER BY day
"""


async def get_daily_availability(site_id: int, days: int = 7) -> list[dict]:
    """Per-day availability for the last N days: [{day, total, ok, avg_ms}]."""
    db = await get_db()
    window = f"-{days} days"
    cursor = await db.execute(_DAILY_UNION, (site_id, window, site_id, window))
    return [
        {"day": r["day"], "total": r["total"], "ok": r["ok"],
         "avg_ms": round(r["sum_ms"] / r["total"]) if r["total"] else None}
        for r in await cursor.fetchall()
    ]


async def get_uptime_over_days(site_id: int, days: int) -> dict:
    """Uptime over the last N days across raw checks and daily rollups."""
    rows = await get_daily_availability(site_id, days)
    total = sum(r["total"] for r in rows)
    ok = sum(r["ok"] for r in rows)
    avg = (sum((r["avg_ms"] or 0) * r["total"] for r in rows) / total) if total else None
    return _uptime_dict(total, ok, avg)


# ── Incidents ────────────────────────────────────────────────────────────────

async def save_incident(site_id: int, check_type: str, message: str,
                        severity: str = "warning") -> tuple[int, bool]:
    """Insert or reuse an open incident. Returns (incident_id, is_new)."""
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """SELECT id FROM incidents
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type))
        existing = await cursor.fetchone()
        if existing:
            return existing[0], False
        cursor = await db.execute(
            """INSERT INTO incidents (site_id, check_type, message, severity)
               VALUES (?, ?, ?, ?)""", (site_id, check_type, message, severity))
        await db.commit()
        return cursor.lastrowid, True


async def resolve_incident(site_id: int, check_type: str) -> dict | None:
    """Resolve the open incident for site/type. Returns the row as it was
    (incl. created_at) for post-incident summaries, or None if nothing was open."""
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """SELECT * FROM incidents
               WHERE site_id = ? AND check_type = ? AND resolved = 0""",
            (site_id, check_type))
        row = await cursor.fetchone()
        if not row:
            return None
        await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE id = ?""", (row["id"],))
        await db.commit()
        return dict(row)


async def resolve_incident_by_id(incident_id: int) -> bool:
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE id = ? AND resolved = 0""", (incident_id,))
        await db.commit()
        return cursor.rowcount > 0


async def resolve_all_incidents() -> int:
    """Mark every open incident as resolved and reset the alert ladders."""
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """UPDATE incidents SET resolved = 1, resolved_at = datetime('now')
               WHERE resolved = 0""")
        n = cursor.rowcount
        await db.execute("DELETE FROM alert_state")
        await db.commit()
        return n


async def get_incident(incident_id: int) -> dict | None:
    db = await get_db()
    cursor = await db.execute(
        """SELECT i.*, s.url FROM incidents i JOIN sites s ON i.site_id = s.id
           WHERE i.id = ?""", (incident_id,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_active_incidents() -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT i.*, s.url FROM incidents i JOIN sites s ON i.site_id = s.id
           WHERE i.resolved = 0 ORDER BY i.created_at DESC""")
    return [dict(r) for r in await cursor.fetchall()]


async def get_incidents_since(days: int = 7) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        """SELECT i.*, s.url FROM incidents i JOIN sites s ON i.site_id = s.id
           WHERE i.created_at >= datetime('now', ?) ORDER BY i.created_at""",
        (f"-{days} days",))
    return [dict(r) for r in await cursor.fetchall()]


# ── Alert ladder state ───────────────────────────────────────────────────────

async def get_last_alert_threshold(site_id: int, check_type: str) -> int | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT last_threshold FROM alert_state WHERE site_id = ? AND check_type = ?",
        (site_id, check_type))
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
            (site_id, check_type, threshold))
        await db.commit()


# ── bot_state KV ─────────────────────────────────────────────────────────────

async def get_state(key: str) -> str | None:
    db = await get_db()
    cursor = await db.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
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
                     value = excluded.value, updated_at = datetime('now')""",
                (key, value))
        await db.commit()


async def get_state_keys(prefix: str) -> dict[str, str]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT key, value FROM bot_state WHERE key LIKE ?", (prefix + "%",))
    return {r[0]: r[1] for r in await cursor.fetchall()}


# ── Heartbeats (dead-man switch) ─────────────────────────────────────────────

async def heartbeat_ping(job: str):
    db = await get_db()
    async with _write_lock:
        await db.execute(
            """INSERT INTO heartbeats (job, last_ping) VALUES (?, datetime('now'))
               ON CONFLICT(job) DO UPDATE SET last_ping = datetime('now')""",
            (job,))
        await db.commit()


async def get_heartbeats() -> dict[str, str | None]:
    """{job: last_ping (sqlite UTC string)}."""
    db = await get_db()
    cursor = await db.execute("SELECT job, last_ping FROM heartbeats")
    return {r["job"]: r["last_ping"] for r in await cursor.fetchall()}


# ── DNS snapshots ────────────────────────────────────────────────────────────

async def get_dns_state(host: str, rtype: str) -> str | None:
    db = await get_db()
    cursor = await db.execute(
        "SELECT value FROM dns_state WHERE host = ? AND rtype = ?", (host, rtype))
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_dns_state(host: str, rtype: str, value: str):
    db = await get_db()
    async with _write_lock:
        await db.execute(
            """INSERT INTO dns_state (host, rtype, value, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(host, rtype) DO UPDATE SET
                 value = excluded.value, updated_at = datetime('now')""",
            (host, rtype, value))
        await db.commit()


# ── Deferred notifications (quiet hours / mute) ──────────────────────────────

async def queue_notification(text: str):
    db = await get_db()
    async with _write_lock:
        await db.execute("INSERT INTO pending_notifications (text) VALUES (?)", (text,))
        await db.commit()


async def peek_notifications() -> list[dict]:
    """Queued notifications (oldest first) WITHOUT removing them — delete
    explicitly after a successful send, or a failed send loses the digest."""
    db = await get_db()
    cursor = await db.execute("SELECT id, text FROM pending_notifications ORDER BY id")
    return [dict(r) for r in await cursor.fetchall()]


async def delete_notifications(ids: list[int]):
    if not ids:
        return
    db = await get_db()
    async with _write_lock:
        await db.executemany(
            "DELETE FROM pending_notifications WHERE id = ?", [(i,) for i in ids])
        await db.commit()


# ── Feedback from the site widget ────────────────────────────────────────────

async def save_feedback(site_url: str, page_url: str, message: str,
                        user_agent: str = "", ip_address: str = "") -> int:
    db = await get_db()
    async with _write_lock:
        cursor = await db.execute(
            """INSERT INTO feedback (site_url, page_url, message, user_agent, ip_address)
               VALUES (?, ?, ?, ?, ?)""",
            (site_url, page_url, message, user_agent, ip_address))
        await db.commit()
        return cursor.lastrowid


async def list_feedback(limit: int = 10, offset: int = 0) -> list[dict]:
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM feedback ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return [dict(r) for r in await cursor.fetchall()]


async def count_feedback() -> int:
    db = await get_db()
    cursor = await db.execute("SELECT COUNT(*) FROM feedback")
    return (await cursor.fetchone())[0]


# ── Retention & backup ───────────────────────────────────────────────────────

async def rollup_old_checks(retention_days: int) -> int:
    """Aggregate raw checks older than N days into checks_daily, then delete
    them. Returns the number of raw rows rolled up."""
    db = await get_db()
    # One FIXED cutoff for all statements: re-evaluating datetime('now') per
    # statement lets boundary-second rows slip between aggregate and delete.
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)
              ).strftime("%Y-%m-%d %H:%M:%S")
    async with _write_lock:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM checks WHERE checked_at < ?", (cutoff,))
        count = (await cursor.fetchone())[0]
        if not count:
            return 0
        await db.execute(
            """INSERT INTO checks_daily (site_id, check_type, day, total, ok, sum_ms)
               SELECT site_id, check_type, date(checked_at), COUNT(*),
                      SUM(status = 'ok'), COALESCE(SUM(response_time_ms), 0)
               FROM checks WHERE checked_at < ?
               GROUP BY site_id, check_type, date(checked_at)
               ON CONFLICT(site_id, check_type, day) DO UPDATE SET
                 total = total + excluded.total,
                 ok = ok + excluded.ok,
                 sum_ms = sum_ms + excluded.sum_ms""", (cutoff,))
        await db.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
        # Escalation/ack markers for long-resolved incidents are dead weight.
        for prefix in ("escalated:", "ack:"):
            await db.execute(
                """DELETE FROM bot_state WHERE key LIKE ? AND key NOT IN (
                       SELECT ? || id FROM incidents WHERE resolved = 0)""",
                (prefix + "%", prefix))
        await db.commit()
        return count


async def backup_db(dest_path: str):
    """Write a compact, consistent copy of the DB via VACUUM INTO."""
    db = await get_db()
    async with _write_lock:
        if os.path.exists(dest_path):
            os.remove(dest_path)
        await db.execute("VACUUM INTO ?", (dest_path,))
