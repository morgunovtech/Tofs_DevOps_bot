"""Disk cleanup through the docker socket: only dangling images and old
build cache, never stopped containers or tagged images; at most once in
12 hours; every cleanup is said, with what it removed. A mock Docker Engine
API on a unix socket stands in for the real one."""

import itertools
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta

from aiohttp import web

from db.database import get_state, set_state
from monitors import host_checker
from reports import scheduler
from services import docker_api

REPO = pathlib.Path(__file__).resolve().parent.parent


class Engine:
    """What the bot asked the Docker API for, and the answers it got."""

    def __init__(self, image_bytes: int = 1_000_000_000, cache_bytes: int = 400_000_000):
        self.calls: list[tuple[str, dict]] = []
        self.image_bytes, self.cache_bytes = image_bytes, cache_bytes

    async def start(self) -> str:
        self.dir = tempfile.mkdtemp(prefix="dk", dir="/tmp")    # short: unix socket paths are limited
        app = web.Application()
        app.router.add_post("/images/prune", self._images)
        app.router.add_post("/build/prune", self._build)
        app.router.add_post("/containers/prune", self._containers)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        path = f"{self.dir}/docker.sock"
        await web.UnixSite(self.runner, path).start()
        return path

    async def stop(self):
        await self.runner.cleanup()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _note(self, request) -> dict:
        filters = json.loads(request.query.get("filters", "{}"))
        self.calls.append((request.path, filters))
        return filters

    async def _images(self, request):
        self._note(request)
        return web.json_response({"ImagesDeleted": [{"Deleted": "sha256:aa"}, {"Deleted": "sha256:bb"}],
                                  "SpaceReclaimed": self.image_bytes})

    async def _build(self, request):
        self._note(request)
        return web.json_response({"CachesDeleted": ["c1", "c2", "c3"], "SpaceReclaimed": self.cache_bytes})

    async def _containers(self, request):
        self._note(request)
        return web.json_response({"ContainersDeleted": ["myapp"], "SpaceReclaimed": 1})


def _usage(*readings):
    """A disk_usage_pct stand-in: the given readings in order, then the last
    one forever — a repeated prune must fail an assert, not exhaust an iterator."""
    seq = itertools.chain(readings, itertools.repeat(readings[-1]))
    return lambda path="/": next(seq)


async def test_prune_removes_only_dangling_images_and_old_build_cache(db, cfg):
    engine = Engine()
    cfg(docker_socket=await engine.start())
    try:
        cleaned = await docker_api.prune()
    finally:
        await engine.stop()
    assert engine.calls == [("/images/prune", {"dangling": ["true"]}), ("/build/prune", {"until": ["24h"]})]
    assert cleaned == {"bytes": 1_400_000_000, "layers": 2, "caches": 3}


async def test_cleanup_is_said_and_not_repeated_within_12_hours(bot, db, cfg, monkeypatch):
    engine = Engine()
    cfg(docker_socket=await engine.start(), auto_cleanup=True, disk_alert_pct=85)
    monkeypatch.setattr(host_checker, "disk_usage_pct",
                        _usage((92, 92.0, 100.0), (70, 70.0, 100.0), (92, 92.0, 100.0)))
    try:
        await scheduler.run_host_checks()          # 92% → prune → 70%
        await scheduler.run_host_checks()          # 92% again, 30 min later: no second prune
    finally:
        await engine.stop()
    assert len(engine.calls) == 2                  # one prune: images + build cache
    assert [c[0] for c in engine.calls] == ["/images/prune", "/build/prune"]
    said = bot.sent[0]["text"]
    assert "−1.4 GB" in said and "сейчас 70%" in said and "Остановленные контейнеры" in said
    assert "заполнен на 92%" in bot.sent[-1]["text"]           # the full disk itself is still alerted


async def test_alert_after_a_partial_cleanup_shows_numbers_measured_together(bot, db, cfg, monkeypatch):
    engine = Engine()
    cfg(docker_socket=await engine.start(), auto_cleanup=True, disk_alert_pct=85)
    monkeypatch.setattr(host_checker, "disk_usage_pct", _usage((92, 92.0, 100.0), (88, 88.0, 100.0)))
    try:
        await scheduler.run_host_checks()          # 92% → prune → 88%, still over the threshold
    finally:
        await engine.stop()
    assert "сейчас 88%" in bot.sent[0]["text"]
    assert "заполнен на 88% (88.0 из 100.0 GB)" in bot.sent[-1]["text"]   # percent and GB from one measurement


async def test_a_prune_that_freed_nothing_is_not_repeated_in_the_same_episode(bot, db, cfg, monkeypatch):
    engine = Engine(image_bytes=0, cache_bytes=0)
    cfg(docker_socket=await engine.start(), auto_cleanup=True, disk_alert_pct=85)
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (92, 92.0, 100.0))
    try:
        await scheduler.run_host_checks()
        assert "Почистить docker было нечего" in bot.sent[-1]["text"]
        await set_state("disk_pruned_at", (datetime.now(UTC) - timedelta(hours=13)).isoformat())
        await scheduler.run_host_checks()                                   # 13 h later, still full
        assert len(engine.calls) == 2                                      # one prune: images + build cache
        monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (60, 60.0, 100.0))
        await scheduler.run_host_checks()                                   # the disk got better
        assert await get_state("disk_prune_useless") is None
    finally:
        await engine.stop()


async def test_cleanup_is_off_unless_asked_for(bot, db, cfg, monkeypatch):
    engine = Engine()
    cfg(docker_socket=await engine.start(), auto_cleanup=False)
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (92, 92.0, 100.0))
    try:
        await scheduler.run_host_checks()
    finally:
        await engine.stop()
    assert engine.calls == []


def test_auto_cleanup_is_off_by_default(tmp_path):
    """A fresh interpreter, no AUTO_CLEANUP in the environment and no .env in
    the cwd: the config must come up with cleanup off."""
    env = {k: v for k, v in os.environ.items() if k != "AUTO_CLEANUP"}
    env["PYTHONPATH"] = str(REPO)
    out = subprocess.run(
        [sys.executable, "-c", "from config import config; print(config.auto_cleanup)"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=60)
    assert out.stdout.strip() == "False"
