"""The in-process watchdog notices a wall-clock step and tells the scheduler;
the health marks are monotonic, so the step itself is not a false alarm."""

import asyncio
import time

from services import health


async def test_the_watchdog_notices_a_clock_step_and_stays_calm(monkeypatch):
    health.reset()
    offset = [0.0]
    monkeypatch.setattr(health, "_wall", lambda: time.time() + offset[0])
    steps: list[float] = []
    task = asyncio.create_task(health.watchdog(every=0.01, on_clock_step=steps.append))
    await asyncio.sleep(0.05)
    offset[0] = -3600                                        # NTP sets the clock an hour back
    await asyncio.sleep(0.05)
    assert steps and -3700 < steps[0] < -3500
    assert not task.done() and health.fatal() is None        # a step is not a stall: monotonic marks
    task.cancel()
    health.reset()
