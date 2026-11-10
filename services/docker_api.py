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
import re
import stat

import aiohttp

from config import config

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)
_PING_TIMEOUT = aiohttp.ClientTimeout(total=3)
# Docker mounts /etc/hostname, /etc/hosts and /etc/resolv.conf from
# /var/lib/docker/containers/<id>/ — the container's own id is in mountinfo.
MOUNTINFO = "/proc/self/mountinfo"
_CONTAINER_ID_RE = re.compile(r"/containers/([0-9a-f]{64})/")
_NS = 1_000_000_000


def docker_available() -> bool:
    """A socket at DOCKER_SOCKET — not a directory Docker created in its place
    when the host had nothing to bind-mount."""
    try:
        return stat.S_ISSOCK(os.stat(config.docker_socket).st_mode)
    except OSError:
        return False


def own_container_id() -> str:
    """The id of the container this process runs in ('' when unknown): from
    mountinfo, else HOSTNAME — Docker's default hostname is the short id."""
    try:
        with open(MOUNTINFO, encoding="utf-8", errors="replace") as f:
            m = _CONTAINER_ID_RE.search(f.read())
    except OSError:
        m = None
    return m.group(1) if m else os.environ.get("HOSTNAME", "")


def _session(timeout: aiohttp.ClientTimeout = _TIMEOUT) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        connector=aiohttp.UnixConnector(path=config.docker_socket),
        timeout=timeout,
    )


async def ping() -> bool:
    """Docker answers on the socket right now (GET /_ping)."""
    if not docker_available():
        return False
    try:
        async with _session(_PING_TIMEOUT) as s, s.get("http://localhost/_ping") as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Docker API ping failed: %s", e or type(e).__name__)
        return False


async def inspect(name: str) -> dict | None:
    """GET /containers/<name>/json; None on error."""
    if not docker_available():
        return None
    try:
        async with _session() as s, s.get(f"http://localhost/containers/{name}/json") as resp:
            return await resp.json() if resp.status == 200 else None
    except Exception as e:
        logger.warning("Docker API inspect %s failed: %s", name, e or type(e).__name__)
        return None


def health_of(info: dict) -> str:
    """'starting' / 'healthy' / 'unhealthy' from the container's healthcheck;
    'running' / 'exited' / … when it has none."""
    state = info.get("State") or {}
    return (state.get("Health") or {}).get("Status") or state.get("Status") or ""


def verdict_window(info: dict) -> float:
    """Seconds a restarted container may stay «health: starting» before that
    is a failure: StartPeriod + Interval × Retries + Timeout of its healthcheck
    (Docker's defaults: 0 + 30 × 3 + 30 = 120 s), at most 10 minutes."""
    hc = (info.get("Config") or {}).get("Healthcheck") or {}
    interval = (hc.get("Interval") or 30 * _NS) / _NS
    timeout = (hc.get("Timeout") or 30 * _NS) / _NS
    start_period = (hc.get("StartPeriod") or 0) / _NS
    return min(start_period + interval * (hc.get("Retries") or 3) + timeout, 600.0)


async def list_containers() -> list[dict] | None:
    """All containers (incl. stopped): [{id, name, state, status}]. None on error."""
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
                "id": c.get("Id", ""),
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
