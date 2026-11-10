"""Bot-host monitoring: disk usage with auto-cleanup, and watching /
auto-restarting docker containers on the same host. Both act on the host
only through services.automation: the owner's switches, the pause for
everything automatic, the cleanup budget and the journal live there. With an empty
AUTORESTART_CONTAINERS the container features are off; with a non-empty one
and a Docker that does not answer (no socket, or a socket nobody listens on)
the watch does not go quiet — it reports that (the `api_down` event), and a
disk alert says when the cleanup it promised could not happen.

One watch run waits for the verdicts of the containers it restarted for at
most RUN_BUDGET_SEC in all: the job runs every 10 minutes and must be over
before the next run starts."""

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime, timedelta

from config import config
from db.database import db_file, get_state, set_state
from services import automation, docker_api
from utils.text import parse_iso_utc

logger = logging.getLogger(__name__)


# From here on the disk «rings»: the next steps are writes that fail.
DISK_CRITICAL_PCT = 95


def disk_usage_pct(path: str = "/") -> tuple[int, float, float]:
    """(percent_used, used_gb, total_gb) as df counts them: the blocks
    reserved for root are not free for the bot's user."""
    usage = shutil.disk_usage(path)
    usable = usage.used + usage.free
    pct = round(usage.used / usable * 100) if usable else 0
    return pct, usage.used / 1e9, usable / 1e9


def _disks() -> dict[str, str]:
    """Where to look: the container's own disk and the one holding the
    database — on Railway a separate small volume that fills up unnoticed."""
    db_dir = os.path.dirname(os.path.abspath(db_file())) or "/"
    return {"/": "Диск на сервере бота", db_dir: "Диск с базой бота"} if db_dir != "/" else {
        "/": "Диск на сервере бота"}


def _measure() -> dict:
    """Every watched disk measured once; the fullest one is the verdict,
    the root's own percent decides whether a docker prune can help at all."""
    measured = []
    for path, label in _disks().items():
        try:
            measured.append((path, label, disk_usage_pct(path)))
        except OSError as e:
            logger.warning("Disk usage of %s unknown: %s", path, e)
    path, label, (pct, used_gb, total_gb) = max(measured, key=lambda m: m[2][0])
    root_pct = next((m[2][0] for m in measured if m[0] == "/"), pct)
    return {"path": path, "label": label, "pct": pct, "used_gb": used_gb, "total_gb": total_gb,
            "root_pct": root_pct}


PRUNE_EVERY = timedelta(hours=12)


async def check_disk(auto_cleanup: bool | None = None) -> dict:
    """Disk usage; over the threshold, a docker prune (services.docker_api
    .prune) when the gate allows it (automation.claim: the «🧹» switch —
    AUTO_CLEANUP or the chat —, the pause, at most once in PRUNE_EVERY), and
    not again in the same full-disk episode once a prune freed nothing.
    auto_cleanup=False — look only (the morning report)."""
    look_only = auto_cleanup is False
    m = _measure()
    pct, used_gb, total_gb = m["pct"], m["used_gb"], m["total_gb"]
    result = {"path": m["path"], "label": m["label"],
              "pct": pct, "used_gb": round(used_gb, 1), "total_gb": round(total_gb, 1),
              "over_threshold": pct >= config.disk_alert_pct,
              "cleaned": None, "cleaned_bytes": None, "cleanup_failed": False,
              "pct_after": pct, "used_gb_after": round(used_gb, 1)}
    if not result["over_threshold"]:
        if not look_only and await get_state("disk_prune_useless"):
            await set_state("disk_prune_useless", None)   # a new full-disk episode may prune again
        return result
    # A prune frees the root disk only: a full database volume gains nothing from it.
    if look_only or m["root_pct"] < config.disk_alert_pct or await get_state("disk_prune_useless"):
        return result
    now = datetime.now(UTC)
    last = parse_iso_utc(await get_state("disk_pruned_at"))
    if last and now - last < PRUNE_EVERY:
        return result
    row, _why = await automation.claim("cleanup", "docker", f"диск заполнен на {pct}%")
    if row is None:
        return result                         # off, paused or done lately: the gate said why in the log
    await set_state("disk_pruned_at", now.isoformat())
    cleaned = await docker_api.prune()
    await automation.finish(row, "Docker не ответил" if cleaned is None
                            else f"освободил {cleaned['bytes'] / 1e9:.1f} GB")
    if cleaned is not None:
        result["cleaned"], result["cleaned_bytes"] = cleaned, cleaned["bytes"]
        # Measure everything again, so the alert's percent and gigabytes agree.
        after = _measure()
        result["path"], result["label"] = after["path"], after["label"]
        result["pct_after"], result["used_gb_after"] = after["pct"], round(after["used_gb"], 1)
        result["total_gb"] = round(after["total_gb"], 1)
        if not cleaned["bytes"]:
            await set_state("disk_prune_useless", "1")
    else:
        result["cleanup_failed"] = True       # the alert says so: «почищу сам» did not happen
    return result


RESTART_BUDGET = 3                       # restarts without a healthy half hour in between
HEALTHY_RESET = timedelta(minutes=30)    # healthy this long → a new episode: budget and alert re-armed
BACKOFF = timedelta(minutes=10)          # after the 1st restart; doubles after each next one
POLL_SEC = 5                             # how often a restarted container is looked at
# Verdict waiting per watch run, all containers together: the job runs every
# 10 minutes, and a container's own window (verdict_window) may be as long.
RUN_BUDGET_SEC = 480

# Docker's state words → what the owner reads after «Сервис «x» на сервере бота …»
STATE_WORDS = {"exited": "остановлен", "restarting": "перезапускается снова и снова",
               "dead": "не запускается (dead)", "created": "создан, но не запущен", "paused": "на паузе"}
UNHEALTHY_WORDS = "не проходит проверку здоровья (healthcheck)"


def _now() -> datetime:
    return datetime.now(UTC)


async def watch_containers() -> list[dict]:
    """Look at every container in AUTORESTART_CONTAINERS and restart the broken
    ones — at most RESTART_BUDGET times per episode, with growing gaps. One
    event per configured container:
      {"api_down": True, "mounted"}                   Docker does not answer (the only event)
      {"name", "missing": True}                       no such container
      {"name", "healthy": True, "healthy_for"}        running, healthcheck passing (or none)
      {"name", "waiting": True}                       starting up / within the gap: no verdict
      {"name", "problem", "gave_up": True}            broken, the budget is spent
      {"name", "problem", "skipped": why, …}          broken, not restarted: the «🔁» switch is
                                                      off or everything automatic is paused
      {"name", "problem", "restarted", "ok_after"}    broken: restarted (or the restart failed);
                                                      with "unverified": True the run's budget
                                                      ran out before the verdict"""
    if not config.autorestart_containers:
        return []
    containers = await docker_api.list_containers()
    if containers is None:
        return [{"api_down": True, "mounted": docker_api.docker_available()}]
    by_name = {c["name"]: c for c in containers}
    own = docker_api.own_container_id()
    now = _now()
    run_deadline = time.monotonic() + RUN_BUDGET_SEC
    events: list[dict] = []
    for name in config.autorestart_containers:
        c = by_name.get(name)
        if not c:
            logger.warning("AUTORESTART_CONTAINERS: no container named %r (docker ps -a)", name)
            events.append({"name": name, "missing": True})
            continue
        if own and c["id"].startswith(own):
            logger.warning("AUTORESTART_CONTAINERS: %r is the bot's own container — never restarted", name)
            continue
        status_l = c["status"].lower()
        unhealthy, stopped = "unhealthy" in status_l, c["state"] != "running"
        if not (unhealthy or stopped or "starting" in status_l):
            events.append(await _healthy(name, now))
            continue
        await set_state(f"container_healthy_since:{name}", None)
        if not (unhealthy or stopped):
            events.append({"name": name, "waiting": True})      # «health: starting»: no verdict yet
            continue
        problem = UNHEALTHY_WORDS if unhealthy else STATE_WORDS.get(c["state"], f"не запущен ({c['state']})")
        history = _history(await get_state(f"restarts:{name}"))
        if len(history) >= RESTART_BUDGET:
            events.append({"name": name, "problem": problem, "gave_up": True})
            continue
        if history and now - history[-1] < BACKOFF * 2 ** (len(history) - 1):
            events.append({"name": name, "waiting": True})
            continue
        # The gate: the «🔁» switch, the pause for everything automatic, the
        # journal. The budget per episode is the one above — not an hourly one.
        row, why = await automation.claim("restart", name, problem)
        if row is None:
            events.append({"name": name, "problem": problem, "restarted": False, "ok_after": False,
                           "skipped": why})
            continue
        # Written before the call: if the restart ever takes this process down,
        # the next start still sees it.
        history.append(now)
        await set_state(f"restarts:{name}", json.dumps([t.isoformat() for t in history]))
        restarted = await docker_api.restart_container(name)
        verdict = await _comes_up(name, run_deadline) if restarted else False
        await automation.finish(row, "перезапустил, работает" if verdict
                                else "перезапустил, вердикта пока нет" if verdict is None
                                else "перезапустил, всё ещё нездоров" if restarted
                                else "Docker не принял команду")
        event = {"name": name, "problem": problem, "restarted": restarted, "ok_after": bool(verdict)}
        if verdict is None:
            event["unverified"] = True
        events.append(event)
    return events


def _history(raw: str | None) -> list[datetime]:
    """Times of this episode's restarts, oldest first."""
    try:
        values = json.loads(raw or "[]")
    except ValueError:
        return []
    return [t for t in (parse_iso_utc(v) for v in values if isinstance(v, str)) if t]


async def _healthy(name: str, now: datetime) -> dict:
    """A healthy look: after HEALTHY_RESET of them in a row the episode is over
    and the restart budget is full again."""
    since = parse_iso_utc(await get_state(f"container_healthy_since:{name}"))
    if since is None:
        since = now
        await set_state(f"container_healthy_since:{name}", now.isoformat())
    if now - since >= HEALTHY_RESET:
        await set_state(f"restarts:{name}", None)
    return {"name": name, "healthy": True, "healthy_for": now - since}


async def _comes_up(name: str, run_deadline: float) -> bool | None:
    """After a restart: healthy — or just running, without a healthcheck —
    within the container's own healthcheck window («health: starting» is
    waited out, not judged). None when the run's budget (run_deadline, a
    time.monotonic() value) ran out before the container's window did: no
    verdict either way."""
    await asyncio.sleep(POLL_SEC)
    info = await docker_api.inspect(name)
    own_deadline = time.monotonic() + (docker_api.verdict_window(info) if info else 0)
    deadline = min(own_deadline, run_deadline)
    while info and docker_api.health_of(info) == "starting" and time.monotonic() < deadline:
        await asyncio.sleep(POLL_SEC)
        info = await docker_api.inspect(name)
    if info and docker_api.health_of(info) == "starting" and time.monotonic() < own_deadline:
        return None
    return bool(info) and docker_api.health_of(info) in ("healthy", "running")
