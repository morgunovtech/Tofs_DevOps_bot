"""Alert ladder shared by SSL and domain expiry: notify once per threshold
crossing (14 → 7 → 3 → 1 → 0 days), never daily about the same band."""

from db.database import (
    get_last_alert_threshold,
    resolve_incident,
    save_incident,
    set_last_alert_threshold,
)
from monitors.base import CheckResult


def current_threshold(days_left: int, thresholds: list[int]) -> int | None:
    """Lowest threshold days_left has crossed, or None if all clear.
    thresholds must be sorted ascending."""
    for t in thresholds:
        if days_left <= t:
            return t
    return None


async def apply_ladder(result: CheckResult, check_type: str,
                       days_left: int | None, thresholds: list[int]):
    """Open/keep/resolve the incident for result and mark it incident_new
    only when a NEW (lower) threshold was crossed. days_left=None with a
    non-ok status means "invalid/expired" — the bottom of the ladder."""
    last = await get_last_alert_threshold(result.site_id, check_type)
    if not result.ok and result.error:
        current = current_threshold(days_left, thresholds) if days_left is not None else 0
        await save_incident(result.site_id, check_type, result.error, result.severity)
        if last is None or (current is not None and current < last):
            # Force "new" even if the incident was already open from a prior
            # threshold, so the scheduler sends the worse-threshold alert.
            result.incident_new = True
            result.threshold_crossed = current
            await set_last_alert_threshold(result.site_id, check_type, current)
        return
    if await resolve_incident(result.site_id, check_type):
        result.recovered = True
    if last is not None:
        await set_last_alert_threshold(result.site_id, check_type, None)
