"""⏰ Tasks and bots that mark in with the bot, and the bot's own external
watchdog (healthchecks.io): one verdict for the main screen, the task
card, the morning digest and the alerts.

A task is late 25% past its interval — slack for a late cron. The
watchdog counts as working only when it says it took the mark:
healthchecks.io answers a check that does not exist with HTTP 200 and
«OK (not found)», so the status code alone proves nothing."""

import json
import logging
from datetime import UTC, datetime
from typing import NamedTuple
from urllib.parse import urlsplit

import aiohttp

from db.database import get_heartbeats, get_state, set_state
from services import humanize, integrations, settings
from utils.text import esc, fmt_duration, parse_iso_utc, parse_sqlite_utc

logger = logging.getLogger(__name__)

INTERVALS = [("раз в час", 60), ("раз в 6 часов", 360), ("раз в сутки", 1440), ("раз в неделю", 10080)]
LATE = 1.25
# The watchdog is pinged every 5 minutes: one lost ping is a blip, three
# in a row turn its line 🟠, and half an hour of them is an alert.
WATCHDOG_FAIL_MIN = 15
WATCHDOG_ALERT_MIN = 30
_WATCHDOG_KEY = "watchdog"


def every(interval: int) -> str:
    """«раз в сутки» for a preset interval, «каждые 3 ч» for any other."""
    return next((label for label, mins in INTERVALS if mins == interval), f"каждые {fmt_duration(interval)}")


def is_late(interval: int, ago: float) -> bool:
    return ago > interval * LATE


class Job(NamedTuple):
    name: str
    icon: str            # ✅ on time · 🟠 late · ⏳ waits for its first mark · ⚠️ none even after the alert
    state: str           # short words, as on the main screen
    interval: int        # minutes
    ago: float | None    # minutes since the last mark; None — never marked

    @property
    def needs_you(self) -> bool:
        return self.icon != "✅"


async def job_states() -> list[Job]:
    """Every task under watch, from the stored marks — no network. Never
    marked is ⏳ until the watch has said so, then ⚠️ like its alert."""
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return []
    beats = await get_heartbeats()
    now = datetime.now(UTC)
    out = []
    for name, interval in jobs.items():
        last = parse_sqlite_utc(beats.get(name))
        ago = (now - last).total_seconds() / 60 if last else None
        if ago is not None and is_late(interval, ago):
            icon, state = "🟠", f"молчит {fmt_duration(ago)}"
        elif ago is not None:
            icon, state = "✅", "отметка только что" if ago < 1 else f"отметка {fmt_duration(ago)} назад"
        elif await get_state(f"hb_alerted:{name}"):
            icon, state = humanize.UNCHECKED, "ни одной отметки — не подключили?"
        else:
            icon, state = "⏳", "ждёт первой отметки"
        out.append(Job(name, icon, state, interval, ago))
    return out


def digest_line(job: Job) -> tuple[str, bool]:
    """(line, fine) for the morning report, worded like a site's line: the
    expected rhythm next to the last mark says why «1 дн назад» is fine."""
    name, rhythm = esc(job.name), every(job.interval)
    if job.icon == "✅":
        return f"✅ {name} — работает, {job.state} (жду {rhythm})", True
    if job.icon == "🟠":
        return f"🟠 {name} — молчит {fmt_duration(job.ago or 0)}, а жду отметку {rhythm}: проверь, запускается ли", False
    if job.icon == "⏳":
        return f"⏳ {name} — отметок ещё не было, жду {rhythm}", True
    return f"{job.icon} {name} — ни одной отметки: подключи или удали, кнопки ниже", False


# ── The bot's own watchdog ───────────────────────────────────────────────────

class Watchdog(NamedTuple):
    icon: str     # ✅ takes the marks · 🟠 has not for a while · ⏳ no answer yet
    name: str     # «healthchecks.io» or the host of another service
    state: str


def watchdog_name(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return "healthchecks.io" if host.endswith(("hc-ping.com", "healthchecks.io")) else host


async def ping_watchdog(url: str) -> str | None:
    """Mark in with the watchdog: None when it took the mark, else why not,
    in a few words."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url) as resp:
                status, body = resp.status, (await resp.content.read(512)).decode(errors="replace")
    except Exception as e:
        logger.warning("Watchdog ping failed: %s", e)
        return "не отвечает"
    if status >= 400:
        return f"отвечает ошибкой {status}"
    if "not found" in body.lower():
        return "check не найден"
    return None


async def _watchdog_record() -> dict:
    try:
        return json.loads(await get_state(_WATCHDOG_KEY) or "{}")
    except ValueError:
        return {}


async def record_watchdog(error: str | None) -> dict:
    """Store the outcome of a ping; the first failure in a row keeps its
    time, so the silence is measured from it."""
    rec = await _watchdog_record()
    now = datetime.now(UTC).isoformat()
    if error is None:
        rec.update(ok_at=now, fail_since=None, error=None)
    else:
        rec["fail_since"] = rec.get("fail_since") or now
        rec["error"] = error
    await set_state(_WATCHDOG_KEY, json.dumps(rec))
    return rec


async def forget_watchdog():
    await set_state(_WATCHDOG_KEY, None)
    await set_state("watchdog_alerted", None)


def failing_minutes(rec: dict) -> float:
    since = parse_iso_utc(rec.get("fail_since"))
    return (datetime.now(UTC) - since).total_seconds() / 60 if since else 0.0


async def watchdog() -> Watchdog | None:
    """None when no watchdog is connected."""
    url = integrations.self_heartbeat_url()
    if not url:
        return None
    rec, name = await _watchdog_record(), watchdog_name(url)
    failing = failing_minutes(rec)
    if failing >= WATCHDOG_FAIL_MIN:
        return Watchdog("🟠", name, f"не принимает отметки {fmt_duration(failing)} ({rec.get('error')})")
    if rec.get("ok_at"):
        return Watchdog("✅", name, "на связи")
    return Watchdog("⏳", name, "жду первого ответа")


def watchdog_digest_line(w: Watchdog | None) -> tuple[str, bool]:
    """(line, fine): what happens if the bot itself goes down."""
    if w is None:
        return ("🛡 Внешнего сторожа нет — если я упаду, никто не напишет. Подключить: "
                "⚙️ Настройки → 🩺 Диагностика и подключения"), True
    head = f"{w.icon} Сторож {esc(w.name)} — {esc(w.state)}"
    if w.icon == "✅":
        return f"{head}: если я упаду, он сообщит", True
    if w.icon == "🟠":
        return f"{head}: если я упаду, никто не узнает", False
    return head, True
