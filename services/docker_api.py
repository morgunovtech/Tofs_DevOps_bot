"""
Minimal Docker Engine API client over the unix socket.

Used for bot-host remediation: restarting unhealthy containers and pruning
unused images/containers when the disk fills up. Requires the socket to be
mounted into the container (see docker-compose.yml); every function degrades
to a no-op/None when the socket is absent, so the feature is safely optional.
"""

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
        logger.warning(f"Docker API list_containers failed: {e}")
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
        logger.warning(f"Docker API restart {name} failed: {e}")
        return False


async def prune() -> int | None:
    """Prune stopped containers and unused images. Returns bytes reclaimed."""
    if not docker_available():
        return None
    reclaimed = 0
    try:
        async with _session() as s:
            async with s.post("http://localhost/containers/prune") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reclaimed += data.get("SpaceReclaimed") or 0
            # filters={"dangling":["false"]} = remove ALL unused images,
            # not just dangling layers.
            async with s.post(
                "http://localhost/images/prune",
                params={"filters": '{"dangling":["false"]}'},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reclaimed += data.get("SpaceReclaimed") or 0
        return reclaimed
    except Exception as e:
        logger.warning(f"Docker API prune failed: {e}")
        return None
