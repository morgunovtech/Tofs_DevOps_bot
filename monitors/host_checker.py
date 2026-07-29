"""
Bot-host monitoring: disk usage with auto-cleanup, and watching/auto-restarting
docker containers on the same host.

The monitored *sites* live on Cloudflare Pages — this module cares about the
server the bot itself runs on (which is also where any future self-hosted
services would live). Container features silently disable themselves when
/var/run/docker.sock isn't mounted.
"""

import logging
import shutil
from datetime import datetime, timezone

from config import config
from db.database import get_state, set_state
from services import docker_api

logger = logging.getLogger(__name__)


def disk_usage_pct(path: str = "/") -> tuple[int, float, float]:
    """(percent_used, used_gb, total_gb)."""
    usage = shutil.disk_usage(path)
    pct = round(usage.used / usage.total * 100)
    return pct, usage.used / 1e9, usage.total / 1e9


async def check_disk(auto_cleanup: bool | None = None) -> dict:
    """Check disk usage; over the threshold, try to reclaim space via docker
    prune (when the socket is available)."""
    if auto_cleanup is None:
        auto_cleanup = config.auto_cleanup
    pct, used_gb, total_gb = disk_usage_pct()
    result = {
        "pct": pct,
        "used_gb": round(used_gb, 1),
        "total_gb": round(total_gb, 1),
        "over_threshold": pct >= config.disk_alert_pct,
        "cleaned_bytes": None,
        "pct_after": pct,
    }
    if result["over_threshold"] and auto_cleanup:
        reclaimed = await docker_api.prune()
        if reclaimed is not None:
            result["cleaned_bytes"] = reclaimed
            result["pct_after"] = disk_usage_pct()[0]
    return result


async def watch_containers() -> list[dict]:
    """Restart configured containers that are exited or unhealthy.

    Returns events: [{name, problem, restarted, ok_after}]. Restart attempts
    are throttled to one per 10 minutes per container so a crash-looping
    service doesn't get hammered.
    """
    if not config.autorestart_containers:
        return []
    containers = await docker_api.list_containers()
    if containers is None:
        return []
    by_name = {c["name"]: c for c in containers}
    events: list[dict] = []
    now = datetime.now(timezone.utc)

    for name in config.autorestart_containers:
        c = by_name.get(name)
        if not c:
            continue
        unhealthy = "unhealthy" in c["status"].lower()
        stopped = c["state"] != "running"
        if not (unhealthy or stopped):
            # Healthy again — clear the throttle so a future failure
            # restarts immediately.
            await set_state(f"restarted:{name}", None)
            continue

        last = await get_state(f"restarted:{name}")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() < 600:
                    continue
            except ValueError:
                pass

        problem = "unhealthy" if unhealthy else f"не запущен ({c['state']})"
        restarted = await docker_api.restart_container(name)
        await set_state(f"restarted:{name}", now.isoformat())

        ok_after = False
        if restarted:
            import asyncio
            await asyncio.sleep(10)
            refreshed = await docker_api.list_containers() or []
            for rc in refreshed:
                if rc["name"] == name:
                    ok_after = (rc["state"] == "running"
                                and "unhealthy" not in rc["status"].lower())
                    break
        events.append({
            "name": name,
            "problem": problem,
            "restarted": restarted,
            "ok_after": ok_after,
        })
    return events
