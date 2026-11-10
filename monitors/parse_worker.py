"""HTML parsing outside the event loop.

BeautifulSoup on a 2 MB page is about a second of pure CPU; in the event
loop it stops everything — polling, the availability tick, /health. A
thread does not help (html.parser holds the GIL), and multiprocessing
re-imports main.py — the whole bot — in every worker. So: one child
process, `python -m monitors.parse_child`, fed over a pipe one call at a
time, killed when a call overruns and started again on the next one;
after IDLE_SEC without work it is stopped and its memory goes back.

Frames both ways: 4-byte big-endian length + UTF-8 JSON. A request is
{"op", "args", "kwargs"}; the answer is {"ok": result} or {"err": text}.
"""

import asyncio
import json
import struct
import sys
from pathlib import Path

from monitors.base import SeoProblem

TIMEOUT_SEC = 30.0          # one parse; a 2 MB page takes ~1–3 s
IDLE_SEC = 60.0             # a minute without work: the child exits, its memory goes back
_ROOT = Path(__file__).resolve().parent.parent
_FRAME = struct.Struct(">I")


class ParseError(Exception):
    """The child failed, overran or died: treat the page as not read."""


class _Child:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.lock = asyncio.Lock()
        self.idle: asyncio.TimerHandle | None = None

    async def call(self, op: str, *args, **kwargs):
        loop = asyncio.get_running_loop()
        if loop is not self.loop:            # a new loop (tests): the old pipe is dead
            self.proc, self.loop, self.lock, self.idle = None, loop, asyncio.Lock(), None
        async with self.lock:
            if self.idle is not None:
                self.idle.cancel()
                self.idle = None
            request = json.dumps({"op": op, "args": args, "kwargs": kwargs}, ensure_ascii=False).encode()
            try:
                proc = await self._running()
                async with asyncio.timeout(TIMEOUT_SEC):
                    proc.stdin.write(_FRAME.pack(len(request)) + request)
                    await proc.stdin.drain()
                    (size,) = _FRAME.unpack(await proc.stdout.readexactly(_FRAME.size))
                    reply = json.loads(await proc.stdout.readexactly(size))
            except (TimeoutError, OSError, asyncio.IncompleteReadError, ValueError) as e:
                await self.close()
                raise ParseError(f"{op}: {type(e).__name__}") from e
            except asyncio.CancelledError:
                # A check cancelled mid-call (the bot stopping) leaves half a
                # frame in the pipe: the next call would read the wrong answer.
                await self.close()
                raise
            self.idle = loop.call_later(IDLE_SEC, self._retire)
        if "err" in reply:
            raise ParseError(f"{op}: {reply['err']}")
        return reply["ok"]

    async def _running(self) -> asyncio.subprocess.Process:
        if self.proc is None or self.proc.returncode is not None:
            self.proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "monitors.parse_child", cwd=_ROOT,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        return self.proc

    def _retire(self) -> None:
        """Called by the idle timer, never during a call (a call cancels it)."""
        proc, self.proc, self.idle = self.proc, None, None
        if proc is not None and proc.returncode is None:
            proc.kill()

    async def close(self) -> None:
        if self.idle is not None:
            self.idle.cancel()
        proc, self.proc, self.idle = self.proc, None, None
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()


_child = _Child()


async def seo_page(*args, **kwargs) -> tuple[list[SeoProblem], int]:
    """monitors.seo_checker.analyze_page(*args, **kwargs), in the child."""
    problems, text_len = await _child.call("seo_page", *args, **kwargs)
    return [SeoProblem(**p) for p in problems], text_len


async def links(*args) -> list[str]:
    """monitors.links_checker.extract_links(*args), in the child."""
    return await _child.call("links", *args)


async def phrase(html: str) -> str | None:
    """monitors.pagemeta.pick_phrase, in the child."""
    return await _child.call("phrase", html)


async def close() -> None:
    """Stop the child (bot shutdown, end of a test)."""
    await _child.close()
