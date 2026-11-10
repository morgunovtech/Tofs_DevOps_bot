"""Bot-host monitoring: disk usage with auto-cleanup, and watching /
auto-restarting docker containers on the same host. Container features
silently disable themselves when /var/run/docker.sock isn't mounted."""

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta

from config import config
from db.database import get_state, set_state
from services import docker_api
from utils.text import parse_iso_utc

logger = logging.getLogger(__name__)


def disk_usage_pct(path: str = "/") -> tuple[int, float, float]:
    """(percent_used, used_gb, total_gb)."""
    usage = shutil.disk_usage(path)
    return round(usage.used / usage.total * 100), usage.used / 1e9, usage.total / 1e9


PRUNE_EVERY = timedelta(hours=12)


async def check_disk(auto_cleanup: bool | None = None) -> dict:
    """Disk usage; over the threshold and with AUTO_CLEANUP=1, a docker
    prune (services.docker_api.prune) — at most once in PRUNE_EVERY, and
    not again in the same full-disk episode once a prune freed nothing."""
    if auto_cleanup is None:
        auto_cleanup = config.auto_cleanup
    pct, used_gb, total_gb = disk_usage_pct()
    result = {"pct": pct, "used_gb": round(used_gb, 1), "total_gb": round(total_gb, 1),
              "over_threshold": pct >= config.disk_alert_pct,
              "cleaned": None, "cleaned_bytes": None,
              "pct_after": pct, "used_gb_after": round(used_gb, 1)}
    if not result["over_threshold"]:
        if auto_cleanup and await get_state("disk_prune_useless"):
            await set_state("disk_prune_useless", None)   # a new full-disk episode may prune again
        return result
    if not auto_cleanup or await get_state("disk_prune_useless"):
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
        pct_after, used_after, total_after = disk_usage_pct()
        result["pct_after"], result["used_gb_after"] = pct_after, round(used_after, 1)
        result["total_gb"] = round(total_after, 1)
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
