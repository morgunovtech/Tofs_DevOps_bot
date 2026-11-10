"""Bot-host monitoring: disk usage with auto-cleanup, and watching /
auto-restarting docker containers on the same host. Container features
silently disable themselves when /var/run/docker.sock isn't mounted."""

import asyncio
import logging
import os
import shutil
from datetime import UTC, datetime, timedelta

from config import config
from db.database import db_file, get_state, set_state
from services import docker_api
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
    """Disk usage; over the threshold and with AUTO_CLEANUP=1, a docker
    prune (services.docker_api.prune) — at most once in PRUNE_EVERY, and
    not again in the same full-disk episode once a prune freed nothing."""
    if auto_cleanup is None:
        auto_cleanup = config.auto_cleanup
    m = _measure()
    pct, used_gb, total_gb = m["pct"], m["used_gb"], m["total_gb"]
    result = {"path": m["path"], "label": m["label"],
              "pct": pct, "used_gb": round(used_gb, 1), "total_gb": round(total_gb, 1),
              "over_threshold": pct >= config.disk_alert_pct,
              "cleaned": None, "cleaned_bytes": None,
              "pct_after": pct, "used_gb_after": round(used_gb, 1)}
    if not result["over_threshold"]:
        if auto_cleanup and await get_state("disk_prune_useless"):
            await set_state("disk_prune_useless", None)   # a new full-disk episode may prune again
        return result
    # A prune frees the root disk only: a full database volume gains nothing from it.
    if not auto_cleanup or m["root_pct"] < config.disk_alert_pct or await get_state("disk_prune_useless"):
        return result
    now = datetime.now(UTC)
    last = parse_iso_utc(await get_state("disk_pruned_at"))
    if last and now - last < PRUNE_EVERY:
        return result
    await set_state("disk_pruned_at", now.isoformat())
    cleaned = await docker_api.prune()
    if cleaned is not None:
        result["cleaned"], result["cleaned_bytes"] = cleaned, cleaned["bytes"]
        # Measure everything again, so the alert's percent and gigabytes agree.
        after = _measure()
        result["path"], result["label"] = after["path"], after["label"]
        result["pct_after"], result["used_gb_after"] = after["pct"], round(after["used_gb"], 1)
        result["total_gb"] = round(after["total_gb"], 1)
        if not cleaned["bytes"]:
            await set_state("disk_prune_useless", "1")
    return result


async def _find(name: str) -> dict | None:
    for c in await docker_api.list_containers() or []:
        if c["name"] == name:
            return c
    return None


async def watch_containers() -> list[dict]:
    """Restart configured containers that are exited or unhealthy.
    Returns events: [{name, problem, restarted, ok_after}]. Restarts are
    throttled to one per 10 minutes per container."""
    if not config.autorestart_containers:
        return []
    containers = await docker_api.list_containers()
    if containers is None:
        return []
    by_name = {c["name"]: c for c in containers}
    events: list[dict] = []
    now = datetime.now(UTC)

    for name in config.autorestart_containers:
        c = by_name.get(name)
        if not c:
            continue
        unhealthy = "unhealthy" in c["status"].lower()
        stopped = c["state"] != "running"
        if not (unhealthy or stopped):
            await set_state(f"restarted:{name}", None)  # healthy — clear throttle
            continue
        last = parse_iso_utc(await get_state(f"restarted:{name}"))
        if last and (now - last).total_seconds() < 600:
            continue
        problem = "unhealthy" if unhealthy else f"не запущен ({c['state']})"
        restarted = await docker_api.restart_container(name)
        await set_state(f"restarted:{name}", now.isoformat())
        ok_after = False
        if restarted:
            await asyncio.sleep(10)
            for attempt in range(2):
                found = await _find(name)
                status_l = found["status"].lower() if found else ""
                # "health: starting" is neither healthy nor failed — give
                # the healthcheck one more window before judging.
                if found and "starting" in status_l and attempt == 0:
                    await asyncio.sleep(10)
                    continue
                ok_after = bool(found and found["state"] == "running"
                                and "unhealthy" not in status_l and "starting" not in status_l)
                break
        events.append({"name": name, "problem": problem, "restarted": restarted,
                       "ok_after": ok_after})
    return events
