"""
Minimal Docker Engine API client over the unix socket.

Used for bot-host remediation: restarting unhealthy containers and, when
the owner turned it on (AUTO_CLEANUP=1), removing dangling images and old
build cache when the disk fills up. Requires the socket to be mounted into
the container (see docker-compose.yml); every function degrades to a
no-op/None when the socket is absent, so the feature is safely optional.
"""

import json
import logging
import os

import aiohttp

from config import config

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)


def docker_available() -> bool:
    return os.path.exists(config.docker_socket)


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.UnixConnector(path=config.docker_socket),
        timeout=_TIMEOUT,
    )


async def list_containers() -> list[dict] | None:
    """All containers (incl. stopped): [{name, state, status}]. None on error."""
    if not docker_available():
        return None
    try:
        async with _session() as s:
            async with s.get("http://localhost/containers/json?all=1") as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        return [
            {
                "name": (c.get("Names") or ["/?"])[0].lstrip("/"),
                "state": c.get("State", ""),        # running / exited / ...
                "status": c.get("Status", ""),      # "Up 2 hours (unhealthy)"
            }
            for c in data
        ]
    except Exception as e:
        logger.warning("Docker API list_containers failed: %s", e)
        return None


async def restart_container(name: str) -> bool:
    if not docker_available():
        return False
    try:
        async with _session() as s:
            async with s.post(
                f"http://localhost/containers/{name}/restart?t=10"
            ) as resp:
                return resp.status == 204
    except Exception as e:
        logger.warning("Docker API restart %s failed: %s", name, e)
        return False


async def prune() -> dict | None:
    """Free disk space without touching anything that may still be needed:
    dangling images (untagged layers no container uses) and build cache
    unused for a day. Stopped containers and tagged images stay — a stopped
    container may be a service waiting for its restart, with data in its
    layer; a tagged image may be the way back. Returns what was removed:
    {"bytes", "layers", "caches"}; None when the socket is absent or failed."""
    if not docker_available():
        return None
    out = {"bytes": 0, "layers": 0, "caches": 0}
    try:
        async with _session() as s:
            async with s.post(
                "http://localhost/images/prune",
                params={"filters": json.dumps({"dangling": ["true"]})},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    out["bytes"] += data.get("SpaceReclaimed") or 0
                    out["layers"] += sum(1 for d in data.get("ImagesDeleted") or [] if d.get("Deleted"))
            # Older daemons have no /build/prune: a 404 there is not an error.
            async with s.post(
                "http://localhost/build/prune",
                params={"filters": json.dumps({"until": ["24h"]})},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    out["bytes"] += data.get("SpaceReclaimed") or 0
                    out["caches"] += len(data.get("CachesDeleted") or [])
        logger.info("Docker prune: %s", out)
        return out
    except Exception as e:
        logger.warning("Docker API prune failed: %s", e)
        return None
