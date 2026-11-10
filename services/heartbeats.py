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
    icon: str            # ✅ on time · 🟠 late · ⏳ not in touch yet · ⚠️ still not, after the alert
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
            icon, state = "✅", ("на связи, только что" if ago < 1
                                 else f"на связи, последний раз {fmt_duration(ago)} назад")
        elif await get_state(f"hb_alerted:{name}"):
            icon, state = humanize.UNCHECKED, "так и не выходит на связь — подключали?"
        else:
            icon, state = "⏳", "пока не выходит на связь"
        out.append(Job(name, icon, state, interval, ago))
    return out


def digest_line(job: Job) -> tuple[str, bool]:
    """(line, fine) for the morning report, worded like a site's line: the
    expected rhythm next to the last mark says why «1 дн назад» is fine."""
    name, rhythm = esc(job.name), every(job.interval)
    if job.icon == "✅":
        return f"✅ {name} — {job.state} (выходит на связь {rhythm})", True
    if job.icon == "🟠":
        return (f"🟠 {name} — молчит {fmt_duration(job.ago or 0)}, а выходит на связь {rhythm}: "
                f"проверь, запускается ли"), False
    if job.icon == "⏳":
        return f"⏳ {name} — пока не выходит на связь (жду {rhythm})", True
    return f"{job.icon} {name} — так и не выходит на связь: подключи или удали, кнопки ниже", False


# ── Status that a bot sends with its mark ────────────────────────────────────
#
# A bot that does more than «I ran» — SocialTracker watching channels and
# followers — POSTs its verdict with the mark: one line per thing it
# watches, so «всё в порядке» is one screen for sites, bots and what they
# see. The bot keeps its own alerts and deep screens; here it is shown only.
#
#   POST /api/heartbeat/<secret>/<job>      Content-Type: application/json
#   {"title": "📣 Соцсети",                       ≤ 40 chars
#    "items": [{"name": "Telegram-канал",         ≤ 10 items, name ≤ 40
#               "level": "ok",                    ok | warning | critical | unknown
#               "text": "12 340, +18 за сутки"}], ≤ 120
#    "open_url": "https://t.me/some_bot",         optional, https
#    "heartbeat": true}                           false: status only, not a mark

STATUS_ICONS = {"ok": "✅", "warning": "🟠", "critical": "🔴", "unknown": humanize.UNCHECKED}
MAX_STATUS_ITEMS = 10


class StatusError(ValueError):
    """The payload is not a status; the message says what is wrong."""


def parse_status(data) -> dict:
    """Validated and trimmed status, or StatusError."""
    if not isinstance(data, dict):
        raise StatusError("body must be a JSON object")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise StatusError("items: a non-empty list is required")
    if len(items) > MAX_STATUS_ITEMS:
        raise StatusError(f"items: at most {MAX_STATUS_ITEMS}")
    out_items = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise StatusError(f"items[{i}]: an object is required")
        name, level, text = item.get("name"), item.get("level", "ok"), item.get("text", "")
        if not isinstance(name, str) or not name.strip():
            raise StatusError(f"items[{i}].name is required")
        if level not in STATUS_ICONS:
            raise StatusError(f"items[{i}].level: one of {', '.join(STATUS_ICONS)}")
        if not isinstance(text, str):
            raise StatusError(f"items[{i}].text: a string is required")
        out_items.append({"name": name.strip()[:40], "level": level, "text": text.strip()[:120]})
    title = data.get("title")
    open_url = data.get("open_url")
    if open_url is not None and not (isinstance(open_url, str) and open_url.startswith("https://")
                                     and len(open_url) <= 200):
        raise StatusError("open_url: an https link up to 200 characters")
    return {"title": title.strip()[:40] if isinstance(title, str) and title.strip() else None,
            "items": out_items, "open_url": open_url}


async def save_status(job: str, status: dict):
    await set_state(f"hb_status:{job}", json.dumps({**status, "at": datetime.now(UTC).isoformat()},
                                                   ensure_ascii=False))


async def job_status(job: str) -> dict | None:
    try:
        status = json.loads(await get_state(f"hb_status:{job}") or "null")
    except ValueError:
        return None
    return status if isinstance(status, dict) and status.get("items") else None


async def forget_status(job: str):
    await set_state(f"hb_status:{job}", None)


def status_title(job: Job, status: dict) -> str:
    return status.get("title") or f"⏰ Что видит {job.name}"


def status_items(job: Job, status: dict) -> list[tuple[str, str, str]]:
    """(icon, name, text) for each line — only while the job marks on time:
    a status from a bot that went silent says nothing about now."""
    if job.icon != "✅":
        return [(humanize.UNCHECKED, job.name, "молчит — не знаю, что сейчас")]
    return [(STATUS_ICONS[i["level"]], i["name"], i["text"]) for i in status["items"]]


# ── The bot's own watchdog ───────────────────────────────────────────────────

class Watchdog(NamedTuple):
    icon: str     # ✅ in touch · 🟠 not for a while · ⏳ not yet
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
    except TimeoutError:
        return "не ответил за 10 с"
    except aiohttp.ClientConnectorError:
        return "не удаётся подключиться (сеть или DNS на моей стороне)"
    except Exception as e:
        # Never the exception text: it may carry the whole address, and the address is a secret.
        logger.warning("Watchdog ping to %s failed: %s", watchdog_name(url), type(e).__name__)
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
        return Watchdog("🟠", name, f"не на связи {fmt_duration(failing)} ({rec.get('error')})")
    if rec.get("ok_at"):
        return Watchdog("✅", name, "на связи")
    return Watchdog("⏳", name, "пока не на связи")


def watchdog_digest_line(w: Watchdog | None) -> tuple[str, bool]:
    """(line, fine): what happens if the bot itself goes down."""
    if w is None:
        return ("🛡 Внешнего сторожа нет — если я упаду, никто не напишет. Подключить: "
                "⚙️ Настройки → 🔌 Подключения"), True
    head = f"{w.icon} Сторож {esc(w.name)} — {esc(w.state)}"
    if w.icon == "✅":
        return f"{head}: если я упаду, он сообщит", True
    if w.icon == "🟠":
        return f"{head}: если я упаду, никто не узнает", False
    return head, True
