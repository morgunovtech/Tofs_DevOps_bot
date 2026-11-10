"""Everything the bot does to production by itself — rebuilding a site
(Cloudflare deploy hook), restarting a container, docker prune — goes
through here: the owner's switches (⚙️ → 🔌 → 🤖), one pause for all of it,
a budget per target, and a journal (bot_state «auto:journal» + a log line)
of what was done and why. The owner's own «🚀»/«🧹» taps are journaled too;
for them a double-tap guard stands in for the budget.

The journal is a JSON list of the last JOURNAL_CAP actions (kind, target,
trigger, auto, result, at) — no table, no migration: the budgets are
counted on it, the cap is the retention, and a rollback of this code is a
plain git revert."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from db.database import get_state, set_state
from services import integrations, maintenance
from services.actions import deploy_hook_for, trigger_redeploy
from utils.clock import to_local
from utils.text import parse_iso_utc
from utils.urls import host_of

logger = logging.getLogger(__name__)

KINDS = ("redeploy", "restart", "cleanup")
# How often the bot may act by itself, per target: a deploy hook (its
# sha256 prefix — never the secret address), the bot's own host («docker»).
# A restart has no hourly budget here: monitors.host_checker keeps its own
# budget per episode (RESTART_BUDGET restarts, growing gaps, then «gave up»),
# and an hourly one on top would quietly restart a crash-looping container
# forever. The journal still has every restart, and the pause/switch apply.
BUDGETS: dict[str, tuple[int, timedelta]] = {
    "redeploy": (1, timedelta(hours=1)),
    "cleanup": (1, timedelta(hours=12)),
}
_PER = {"redeploy": " на один хук (проект Pages)"}
# Kept in words with monitors.host_checker.RESTART_BUDGET (a test pins it).
RESTART_NOTE = "не больше 3 рестартов за эпизод, потом зову человека"
TAP_GUARD = timedelta(minutes=2)       # a second «Да» on the same thing within it does nothing
CONFIRM_TTL = timedelta(minutes=10)    # an older confirmation is asked again
PAUSE_KEY = "auto:paused_until"
JOURNAL_KEY = "auto:journal"
JOURNAL_CAP = 100
FOREVER = "forever"
_lock = asyncio.Lock()


def limit_text(kind: str) -> str:
    """«не чаще раза в час на один хук (проект Pages)», «не чаще раза в 12 ч»;
    for a restart — the episode budget of the container watch."""
    if kind not in BUDGETS:
        return RESTART_NOTE
    n, window = BUDGETS[kind]
    hours = int(window.total_seconds() // 3600)
    times = "раза" if n == 1 else f"{n} раз"
    return f"не чаще {times} в " + ("час" if hours == 1 else f"{hours} ч") + _PER.get(kind, "")


def redeploy_target(url: str) -> str | None:
    """The budget key of a rebuild: the hook, not the site — two sites served
    by one Pages project (apex and www) share one hook and one budget. A
    short hash: the hook address is the secret and never reaches the journal."""
    hook = deploy_hook_for(url)
    return f"hook:{hashlib.sha256(hook.encode()).hexdigest()[:12]}" if hook else None


# ── Pause ────────────────────────────────────────────────────────────────────

async def paused_until() -> datetime | str | None:
    """None — working; FOREVER; or the moment the pause ends."""
    raw = await get_state(PAUSE_KEY)
    if raw == FOREVER:
        return FOREVER
    until = parse_iso_utc(raw)
    return until if until and until > datetime.now(UTC) else None


async def pause(hours: int | None):
    """Stop everything automatic: for `hours`, or until resume() when None."""
    value = FOREVER if hours is None else (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    await set_state(PAUSE_KEY, value)
    logger.warning("Auto-actions paused %s", "until switched back on" if hours is None else f"for {hours} h")


async def resume():
    await set_state(PAUSE_KEY, None)
    logger.warning("Auto-actions switched back on")


# ── Journal ──────────────────────────────────────────────────────────────────

async def _journal() -> list[dict]:
    """Oldest first, as stored."""
    try:
        entries = json.loads(await get_state(JOURNAL_KEY) or "[]")
    except ValueError:
        entries = []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


async def _save(entries: list[dict]):
    await set_state(JOURNAL_KEY, json.dumps(entries[-JOURNAL_CAP:], ensure_ascii=False))


def _at(entry: dict) -> datetime | None:
    return parse_iso_utc(entry.get("at"))


async def add_action(kind: str, target: str, trigger: str, auto: bool, label: str | None = None) -> int:
    """A new journal row (its id); `label` is what the owner reads for the
    target when the target itself is a key (the hook's hash)."""
    entries = await _journal()
    row_id = max((int(e.get("id") or 0) for e in entries), default=0) + 1
    entries.append({"id": row_id, "kind": kind, "target": target, "label": label or target,
                    "trigger": trigger, "auto": int(auto), "result": None,
                    "at": datetime.now(UTC).isoformat(timespec="seconds")})
    await _save(entries)
    return row_id


async def finish_action(action_id: int, result: str):
    async with _lock:
        entries = await _journal()
        for e in entries:
            if e.get("id") == action_id:
                e["result"] = result
        await _save(entries)


async def actions_since(kind: str, target: str, since: datetime, auto: bool) -> list[dict]:
    """Rows of one kind/target newer than `since`, newest first."""
    rows = [e for e in await _journal()
            if e.get("kind") == kind and e.get("target") == target and bool(e.get("auto")) == auto
            and (_at(e) or datetime.min.replace(tzinfo=UTC)) >= since]
    return rows[::-1]


async def recent_actions(limit: int = 20) -> list[dict]:
    """The last actions, newest first."""
    return (await _journal())[::-1][:limit]


def _hhmm(entry: dict) -> str:
    dt = _at(entry)
    return to_local(dt).strftime("%H:%M") if dt else "?"


# ── The gate ─────────────────────────────────────────────────────────────────

async def _refusal(kind: str, target: str) -> str | None:
    until = await paused_until()
    if until == FOREVER:
        return "все авто-действия остановлены (⚙️ → 🔌 Подключения → 🤖)"
    if until:
        return f"все авто-действия остановлены до {to_local(until).strftime('%H:%M')}"
    if kind not in BUDGETS:
        return None
    limit, window = BUDGETS[kind]
    done = await actions_since(kind, target, datetime.now(UTC) - window, auto=True)
    if len(done) >= limit:
        return f"уже делал это в {_hhmm(done[0])} — {limit_text(kind)}"
    return None


async def claim(kind: str, target: str, trigger: str, label: str | None = None) -> tuple[int | None, str | None]:
    """Ask before acting by itself. (journal row id, None) — go ahead and
    call finish(); (None, why) — not now, `why` in words for the owner."""
    if not integrations.auto_enabled(kind):
        return None, "выключено"
    async with _lock:   # check and record in one step: two callers must not both pass
        why = await _refusal(kind, target)
        if why:
            logger.info("auto-action %s on %s (%s) not done: %s", kind, label or target, trigger, why)
            return None, why
        row = await add_action(kind, target, trigger, auto=True, label=label)
    logger.info("auto-action #%d %s on %s (%s): starting", row, kind, label or target, trigger)
    return row, None


async def tap(kind: str, target: str, label: str | None = None) -> tuple[int | None, str | None]:
    """The owner's confirmed «🚀»/«🧹»: journaled; a second one on the same
    target within TAP_GUARD (a double tap, a second phone) does nothing."""
    async with _lock:
        recent = await actions_since(kind, target, datetime.now(UTC) - TAP_GUARD, auto=False)
        if recent:
            return None, f"уже запускал в {_hhmm(recent[0])} — подожди пару минут"
        row = await add_action(kind, target, "кнопка", auto=False, label=label)
    logger.info("action #%d %s on %s: owner's tap", row, kind, label or target)
    return row, None


async def finish(row: int, result: str):
    await finish_action(row, result)
    logger.info("action #%d done: %s", row, result)


async def last_auto(kind: str, target: str, window: timedelta) -> str | None:
    """HH:MM of the bot's own last action on the target within the window."""
    done = await actions_since(kind, target, datetime.now(UTC) - window, auto=True)
    return _hhmm(done[0]) if done else None


async def auto_redeploy(url: str, site_id: int, incident_id: int | None = None) -> str | None:
    """The bot's own rebuild after a NEW availability incident. Returns the
    line for the alert; None when there is nothing to say (no hook, off)."""
    target = redeploy_target(url)
    if target is None or not integrations.auto_enabled("redeploy"):
        return None
    if await maintenance.is_paused(site_id):
        return "🤖 Сам не пересобирал: сайт на паузе или в плановых работах."
    trigger = f"инцидент #{incident_id}" if incident_id else "сайт упал"
    row, why = await claim("redeploy", target, trigger, label=host_of(url))
    if row is None:
        return f"🤖 Сам не пересобирал: {why}."
    res = await trigger_redeploy(url)
    await finish(row, ("🚀 " if res.ok else "❌ ") + res.text)
    if res.ok:
        return f"🤖 Уже сделал сам: 🚀 {res.text}"
    return f"🤖 Пробовал пересобрать сайт — не вышло. {res.text}"
