"""Container auto-restart against a fake Docker Engine API on a unix socket:
the verdict after a restart, the alert flag, a missing container, a silent
Docker, the restart budget, the run's waiting budget and the bot's own
container."""

import shutil
import socket
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiohttp import web

from db.database import get_state
from monitors import host_checker
from reports import scheduler
from services import docker_api

HEALTHY = "Up 3 hours (healthy)"
UNHEALTHY = "Up 3 hours (unhealthy)"


class FakeDocker:
    """Just enough of the Engine API: /_ping, /containers/json, restart, inspect.
    `after_restart` is the list of health states inspect returns after a restart."""

    def __init__(self):
        self.containers: dict[str, dict] = {}
        self.restarts: list[str] = []
        self.restart_ok = True
        self.after_restart: list[str] = ["healthy"]
        self.queue: list[str] = ["healthy"]

    def add(self, name, status=HEALTHY, state="running", cid=None):
        self.containers[name] = {"Id": cid or name.encode().hex().ljust(64, "0"), "Names": [f"/{name}"],
                                 "State": state, "Status": status}

    def app(self) -> web.Application:
        async def ping(_):
            return web.Response(text="OK")

        async def listing(_):
            return web.json_response(list(self.containers.values()))

        async def restart(request):
            name = request.match_info["name"]
            self.restarts.append(name)
            if not self.restart_ok:
                return web.json_response({"message": "boom"}, status=500)
            self.queue = list(self.after_restart)
            return web.Response(status=204)

        async def inspect(request):
            health = self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]
            return web.json_response({"State": {"Status": "running", "Health": {"Status": health}},
                                      "Config": {"Healthcheck": {"Interval": 1_000_000_000, "Retries": 2}}})

        app = web.Application()
        app.router.add_get("/_ping", ping)
        app.router.add_get("/containers/json", listing)
        app.router.add_post("/containers/{name}/restart", restart)
        app.router.add_get("/containers/{name}/json", inspect)
        return app


@pytest.fixture
async def docker(cfg, monkeypatch):
    short = Path(tempfile.mkdtemp(prefix="dk"))          # unix socket paths are limited to ~100 bytes
    fake = FakeDocker()
    runner = web.AppRunner(fake.app())
    await runner.setup()
    await web.UnixSite(runner, str(short / "d.sock")).start()
    fake.socket_path = str(short / "d.sock")
    cfg(docker_socket=fake.socket_path, autorestart_containers=("web",))
    monkeypatch.setattr(host_checker, "POLL_SEC", 0)
    monkeypatch.setattr(docker_api, "MOUNTINFO", str(short / "no-mountinfo"))
    monkeypatch.delenv("HOSTNAME", raising=False)
    clock = {"now": datetime(2026, 11, 9, 1, 0, tzinfo=UTC)}
    monkeypatch.setattr(host_checker, "_now", lambda: clock["now"])
    fake.clock = clock
    yield fake
    await runner.cleanup()
    shutil.rmtree(short, ignore_errors=True)


async def _tick(fake, minutes=10):
    await scheduler.run_container_watch()
    fake.clock["now"] += timedelta(minutes=minutes)


def _dead_socket() -> Path:
    """A socket file nobody listens on: mounted, but Docker is not there."""
    dead = Path(tempfile.mkdtemp(prefix="dk")) / "dead.sock"
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(dead))
    sock.close()
    return dead


async def test_starting_after_a_restart_is_waited_out(bot, db, docker):
    docker.add("web", UNHEALTHY)
    docker.after_restart = ["starting", "starting", "healthy"]
    await _tick(docker)
    assert docker.restarts == ["web"]
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("✅ Сервис «web»")
    assert "unhealthy" not in bot.sent[0]["text"]                  # human words, not Docker's code
    assert await get_state("container_alerted:web") is None


async def test_a_container_fixed_by_hand_rearms_the_alert(bot, db, docker):
    docker.add("web", "Exited (1) 2 minutes ago", state="exited")
    docker.restart_ok = False
    await _tick(docker)
    assert bot.sent[-1]["text"].startswith("🔴") and bot.sent[-1]["silent"] is False
    assert "<code>docker logs web</code>" in bot.sent[-1]["text"]   # ready to copy, not «<имя>»
    docker.add("web", HEALTHY)                                      # the owner fixed it by hand
    for _ in range(4):                                              # 40 minutes of health
        await _tick(docker)
    assert bot.sent[-1]["text"].startswith("✅ Сервис «web» на сервере бота снова работает")
    docker.add("web", "Exited (1) 1 minute ago", state="exited")    # a week later it breaks again
    await _tick(docker)
    assert bot.sent[-1]["text"].startswith("🔴")                   # and the owner hears it again


@pytest.mark.parametrize("after_restart", [["healthy"], ["unhealthy"]])
async def test_a_flapping_container_rings_once_in_eight_hours(bot, db, docker, after_restart):
    docker.after_restart = after_restart
    for i in range(48):                                             # 8 hours, a tick every 10 minutes
        docker.add("web", UNHEALTHY if i % 2 == 0 else HEALTHY)
        await _tick(docker)
    rings = [m for m in bot.sent if not m["silent"]]
    assert len(rings) == 1
    assert len(docker.restarts) == host_checker.RESTART_BUDGET     # then it is left to the owner


async def test_a_missing_container_is_said_once(bot, db, docker, cfg):
    cfg(autorestart_containers=("web-app",))
    docker.add("web_app")
    for _ in range(3):
        await _tick(docker)
    assert len(bot.sent) == 1 and "не найден" in bot.sent[0]["text"]


async def test_a_silent_docker_is_said_once_and_its_return_too(bot, db, docker, cfg):
    dead = _dead_socket()
    try:
        cfg(docker_socket=str(dead))
        assert docker_api.docker_available() and not await docker_api.ping()
        for _ in range(3):
            await _tick(docker)
        assert len(bot.sent) == 1 and "Не могу достучаться до Docker" in bot.sent[0]["text"]
    finally:
        shutil.rmtree(dead.parent, ignore_errors=True)
    cfg(docker_socket=docker.socket_path)                         # Docker answers again
    docker.add("web")
    await _tick(docker)
    assert len(bot.sent) == 2 and "снова отвечает" in bot.sent[1]["text"]


async def test_a_root_group_socket_names_the_fix_not_a_restart(bot, db, docker, cfg):
    """entrypoint.sh refused group 0 (DOCKER_SOCKET_ROOT_GROUP=1): the socket
    is mounted and silent for the bot, but `docker compose up -d` is no fix."""
    dead = _dead_socket()
    try:
        cfg(docker_socket=str(dead), docker_socket_root_group=True)
        await _tick(docker)
    finally:
        shutil.rmtree(dead.parent, ignore_errors=True)
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "группе root" in text and "ALLOW_ROOT_GROUP_SOCKET=1" in text and "socket-proxy" in text
    assert "docker compose up -d" not in text


async def test_the_bots_own_container_is_never_restarted(bot, db, docker, monkeypatch, tmp_path):
    own = "ab" * 32
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(f"512 500 0:50 /var/lib/docker/containers/{own}/hostname /etc/hostname rw\n")
    monkeypatch.setattr(docker_api, "MOUNTINFO", str(mountinfo))
    docker.add("web", UNHEALTHY, cid=own)
    await _tick(docker)
    assert docker.restarts == [] and bot.sent == []


async def test_a_run_out_of_waiting_budget_says_so_and_judges_next_time(bot, db, docker, monkeypatch):
    """The whole run may wait RUN_BUDGET_SEC for verdicts: a container still
    «starting» when that is spent is neither healthy nor broken — the owner
    hears that the verdict is pending, and gets it at the next look."""
    monkeypatch.setattr(host_checker, "RUN_BUDGET_SEC", 0)
    docker.add("web", UNHEALTHY)
    docker.after_restart = ["starting"]                             # would be «starting» for 32 s
    await _tick(docker)
    assert docker.restarts == ["web"]
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🟠") and "не успел" in bot.sent[0]["text"]
    assert "всё ещё нездоров" not in bot.sent[0]["text"]           # no verdict is not «still broken»
    assert await get_state("container_alerted:web") is None
    docker.add("web", HEALTHY)                                      # it did come up
    await _tick(docker)
    assert len(bot.sent) == 2 and "заработал после перезапуска" in bot.sent[1]["text"]
    assert await get_state("container_unverified:web") is None
    await _tick(docker)
    assert len(bot.sent) == 2                                       # said once


def test_a_directory_is_not_a_docker_socket(cfg, tmp_path):
    cfg(docker_socket=str(tmp_path))                                # what Docker leaves for a missing source
    assert not docker_api.docker_available()


async def test_the_disk_alert_says_when_docker_could_not_clean(bot, db, docker, cfg, monkeypatch):
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (96, 38.4, 40.0))
    dead = _dead_socket()
    try:
        cfg(docker_socket=str(dead), auto_cleanup=True)
        await scheduler.run_host_checks()
    finally:
        shutil.rmtree(dead.parent, ignore_errors=True)
    assert "Почистить docker сам не смог" in bot.sent[-1]["text"]
