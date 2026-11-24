"""⏰ Tasks and bots that mark in with the bot, and the bot's own external
watchdog (healthchecks.io): one verdict for the main screen, the task
card, the morning digest and the alerts.

A task is late 25% past its interval — slack for a late cron. The
watchdog counts as working only when it says it took the mark:
healthchecks.io answers a check that does not exist with HTTP 200 and
«OK (not found)», so the status code alone proves nothing."""

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from urllib.parse import urlsplit

import aiohttp

from db.database import get_heartbeats, get_state, set_state
from services import health, humanize, integrations, settings
from utils.i18n import fmt_duration, t
from utils.text import esc, parse_iso_utc, parse_sqlite_utc

logger = logging.getLogger(__name__)

INTERVALS = [("раз в час", 60), ("раз в 6 часов", 360), ("раз в сутки", 1440), ("раз в неделю", 10080)]
LATE = 1.25
# The watchdog is pinged every 5 minutes: one lost ping is a blip, three
# in a row turn its line 🟠, and half an hour of them is an alert.
WATCHDOG_FAIL_MIN = 15
WATCHDOG_ALERT_MIN = 30
_WATCHDOG_KEY = "watchdog"
# The bot hears nothing while it is not running. Its last such gap —
# [the last «alive» mark, this start] — is kept here (note_downtime) and
# taken out of every job's silence. Only the latest one: an earlier gap was
# already allowed for when the jobs' silence was last judged.
_DOWN_KEY = "bot_down"


def every(interval: int) -> str:
    """«раз в сутки» for a preset interval, «каждые 3 ч» for any other."""
    return next((t(label) for label, mins in INTERVALS if mins == interval),
                t("каждые {duration}").format(duration=fmt_duration(interval)))


def is_late(interval: int, ago: float) -> bool:
    return ago > interval * LATE


async def note_downtime(down_from: datetime | None, down_until: datetime):
    """Remember the bot's last downtime, once at startup: from the last
    «alive» mark (reports.scheduler.run_alive) to this start."""
    if down_from is None or down_from >= down_until:
        return
    await set_state(_DOWN_KEY, json.dumps([down_from.isoformat(timespec="seconds"),
                                           down_until.isoformat(timespec="seconds")]))


async def last_downtime() -> tuple[datetime, datetime] | None:
    try:
        raw = json.loads(await get_state(_DOWN_KEY) or "null")
    except ValueError:
        return None
    if not isinstance(raw, list) or len(raw) != 2 or not all(isinstance(v, str) for v in raw):
        return None
    start, end = parse_iso_utc(raw[0]), parse_iso_utc(raw[1])
    return (start, end) if start and end and start < end else None


async def silence_since(name: str, last: datetime | None) -> datetime:
    """Where a job's silence is counted from: its last mark or when it was
    added — the later. A job is not to blame for the time it did not exist.
    A job the bot sees for the first time without either (one from
    HEARTBEAT_JOBS on the server) is taken as added now — or at its last
    mark, when it has one: it existed at least since then."""
    added = parse_iso_utc(await get_state(f"hb_added:{name}"))
    if added is None:
        added = last or datetime.now(UTC)
        # A read path (the watch, the main screen): a database that refuses
        # the mark must not take the screen or the «молчит» alert with it.
        try:
            await set_state(f"hb_added:{name}", added.isoformat(timespec="seconds"))
        except sqlite3.OperationalError as e:
            health.db_write_failed(e)
    return max(t for t in (last, added) if t)


async def silent_minutes(name: str, last: datetime | None, now: datetime | None = None) -> float:
    """How long nothing has been heard from the job, less the time the bot
    itself was down to hear it (the last such gap, note_downtime). The one
    rule for the card, the main screen, the digest and the watch — so a
    restart neither forgives a dead job nor makes a live one «молчит»."""
    now = now or datetime.now(UTC)
    since = await silence_since(name, last)
    silent = now - since
    down = await last_downtime()
    if down:
        overlap = min(now, down[1]) - max(since, down[0])
        if overlap > timedelta(0):
            silent -= overlap
    return max(silent, timedelta(0)).total_seconds() / 60


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
        # Late by the silence the bot could hear; the words still say how long since the mark.
        silent = await silent_minutes(name, last, now)
        if ago is not None and is_late(interval, silent):
            icon, state = "🟠", t("молчит {duration}").format(duration=fmt_duration(ago))
        elif ago is not None:
            icon, state = "✅", (t("на связи, только что") if ago < 1
                                 else t("на связи, последний раз {duration} назад").format(duration=fmt_duration(ago)))
        elif await get_state(f"hb_alerted:{name}"):
            icon, state = humanize.UNCHECKED, t("так и не выходит на связь — подключали?")
        else:
            icon, state = "⏳", t("пока не выходит на связь")
        out.append(Job(name, icon, state, interval, ago))
    return out


def digest_line(job: Job) -> tuple[str, bool]:
    """(line, fine) for the morning report, worded like a site's line: the
    expected rhythm next to the last mark says why «1 дн назад» is fine."""
    name, rhythm = esc(job.name), every(job.interval)
    if job.icon == "✅":
        return t("✅ {name} — {state} (выходит на связь {rhythm})").format(name=name, state=job.state, rhythm=rhythm), True
    if job.icon == "🟠":
        return t("🟠 {name} — молчит {duration}, а выходит на связь {rhythm}: проверь, запускается ли").format(
            name=name, duration=fmt_duration(job.ago or 0), rhythm=rhythm), False
    if job.icon == "⏳":
        return t("⏳ {name} — пока не выходит на связь (жду {rhythm})").format(name=name, rhythm=rhythm), True
    return t("{icon} {name} — так и не выходит на связь: подключи или удали, кнопки ниже").format(
        icon=job.icon, name=name), False


# ── Status that a bot sends with its mark ────────────────────────────────────
#
# A bot that does more than «I ran» — SocialTracker watching channels and
# followers — POSTs its verdict with the mark: one line per thing it
# watches, so «всё в порядке» is one screen for sites, bots and what they
# see. The bot keeps its own alerts and deep screens; here it is shown only.
#
#   POST /api/heartbeat/<job's token>/<job>  Content-Type: application/json
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
        if not isinstance(level, str) or level not in STATUS_ICONS:
            raise StatusError(f"items[{i}].level: one of {', '.join(STATUS_ICONS)}")
        if not isinstance(text, str):
            raise StatusError(f"items[{i}].text: a string is required")
        out_items.append({"name": name.strip()[:40], "level": level, "text": text.strip()[:120]})
    title = data.get("title")
    open_url = data.get("open_url")
    if open_url is not None and not _https_link(open_url):
        raise StatusError("open_url: an https link up to 200 characters")
    return {"title": title.strip()[:40] if isinstance(title, str) and title.strip() else None,
            "items": out_items, "open_url": open_url}


def _https_link(value) -> bool:
    """An absolute https URL with a host, no spaces or control characters —
    what Telegram takes for a URL button."""
    if not isinstance(value, str) or len(value) > 200 or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        p = urlsplit(value)
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.hostname)


def link_host(url: str) -> str:
    """The host to name on a button: the owner sees where it leads before tapping."""
    return urlsplit(url).hostname or url


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
    return status.get("title") or t("⏰ Что видит {name}").format(name=job.name)


def status_items(job: Job, status: dict) -> list[tuple[str, str, str]]:
    """(icon, name, text) for each line — only while the job marks on time
    and the lines themselves are fresh: a status from a bot that went
    silent, or one it last sent an interval ago, says nothing about now."""
    if job.icon != "✅":
        return [(humanize.UNCHECKED, job.name, t("молчит — не знаю, что сейчас"))]
    at = parse_iso_utc(status.get("at"))
    age = (datetime.now(UTC) - at).total_seconds() / 60 if at else None
    if age is None or is_late(job.interval, age):
        when = t(" {duration} назад").format(duration=fmt_duration(age)) if age is not None else ""
        return [(humanize.UNCHECKED, job.name, t("что видит, присылал последний раз{when} — строки устарели").format(when=when))]
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
        return t("не ответил за 10 с")
    except aiohttp.ClientConnectorError:
        return t("не удаётся подключиться (сеть или DNS на моей стороне)")
    except Exception as e:
        # Never the exception text: it may carry the whole address, and the address is a secret.
        logger.warning("Watchdog ping to %s failed: %s", watchdog_name(url), type(e).__name__)
        return t("не отвечает")
    if status >= 400:
        return t("отвечает ошибкой {status}").format(status=status)
    if "not found" in body.lower():
        return t("check не найден")
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
        return Watchdog("🟠", name, t("не на связи {duration} ({why})").format(duration=fmt_duration(failing),
                                                                                why=rec.get("error")))
    if rec.get("ok_at"):
        return Watchdog("✅", name, t("на связи"))
    return Watchdog("⏳", name, t("пока не на связи"))


def watchdog_digest_line(w: Watchdog | None) -> tuple[str, bool]:
    """(line, fine): what happens if the bot itself goes down."""
    if w is None:
        return t("🛡 Внешнего сторожа нет — если я упаду, никто не напишет. Подключить: "
                 "⚙️ Настройки → 🔌 Подключения"), True
    head = t("{icon} Сторож {name} — {state}").format(icon=w.icon, name=esc(w.name), state=esc(w.state))
    if w.icon == "✅":
        return t("{head}: если я упаду, он сообщит").format(head=head), True
    if w.icon == "🟠":
        return t("{head}: если я упаду, никто не узнает").format(head=head), False
    return head, True
