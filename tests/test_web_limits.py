"""The public web server (:8080) survives a flood of silent connections:
they cannot eat the process's file descriptors or lock real requests out."""

import asyncio
from pathlib import Path

import aiohttp

from web import webhook

ROOT = Path(__file__).resolve().parent.parent


async def _start(monkeypatch, **limits):
    for name, value in limits.items():
        monkeypatch.setattr(webhook, name, value)
    runner = await webhook.start_web_server(host="127.0.0.1", port=0)
    return runner, runner.addresses[0][1]


async def _closed_by_server(reader: asyncio.StreamReader) -> bool:
    try:
        return await asyncio.wait_for(reader.read(1), 1) == b""
    except ConnectionError:
        return True
    except TimeoutError:
        return False


async def test_silent_flood_keeps_health_alive_and_fds_bounded(db, monkeypatch):
    runner, port = await _start(monkeypatch, MAX_CONNECTIONS=20)
    socks = []
    try:
        for _ in range(60):                      # 3× the ceiling, none sends a byte
            socks.append(await asyncio.open_connection("127.0.0.1", port))
        await asyncio.sleep(0.3)
        assert len(runner.server.connections) <= 21
        async with aiohttp.ClientSession() as s, s.get(f"http://127.0.0.1:{port}/health") as r:
            assert r.status == 200
        closed = await asyncio.gather(*(_closed_by_server(reader) for reader, _ in socks))
        assert sum(closed) >= 40                 # the oldest ones were dropped
    finally:
        for _, writer in socks:
            writer.close()
        await runner.cleanup()


async def test_silent_connection_is_dropped_after_keepalive(db, monkeypatch):
    runner, port = await _start(monkeypatch, KEEPALIVE_SEC=0.5)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(reader.read(1), 3) == b""
        writer.close()
    finally:
        await runner.cleanup()


async def test_oversized_header_is_refused(db, monkeypatch):
    runner, port = await _start(monkeypatch)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/health", headers={"X-Big": "a" * 5000}) as r:
                assert r.status == 400
            async with s.get(f"http://127.0.0.1:{port}/health") as r:
                assert r.status == 200
    finally:
        await runner.cleanup()


def test_compose_publishes_the_web_port_on_localhost_only():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '- "127.0.0.1:${WEBHOOK_PORT:-8080}:${WEBHOOK_PORT:-8080}"' in compose
    assert '- "8080:8080"' not in compose
