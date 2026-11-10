"""Incident bookkeeping shared by monitors: the alert ladder of SSL and
domain expiry (notify once per threshold crossing, 14 → 7 → 3 → 1 → 0
days, never daily about the same band) and the plain open / keep / close
of mail and security findings — where a finding the owner has not heard
of yet, inside an incident already open, is said again (told_before,
told_after, apply_findings)."""

from db.database import (
    get_last_alert_threshold,
    mark_incident_alerted,
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
        if last == 0 and days_left is not None and days_left > 0:
            # The bottom (0) was an «invalid/expired» episode; the new
            # certificate is valid but expiring — start its own ladder, or
            # 7/3/1 would never be said.
            last = None
        result.incident_id, created = await save_incident(result.site_id, check_type, result.error, result.severity)
        if result.incident_id is None:
            return          # the site was removed while it was being checked
        if last is None or (current is not None and current < last):
            # Force "new" even if the incident was already open from a prior
            # threshold, so the scheduler sends the worse-threshold alert.
            result.incident_new = True
            result.threshold_crossed = current
            await set_last_alert_threshold(result.site_id, check_type, current)
        elif created:
            # A fresh row for a step already said (the previous one was closed
            # by an older version, which kept the step; «✓ Закрыть» now starts
            # the ladder over): nothing is owed, or the re-send would call it
            # a lost alert.
            await mark_incident_alerted(result.incident_id)
        return
    closed = await resolve_incident(result.site_id, check_type)
    if closed:
        result.recovered, result.resolved_incident = True, closed
    if last is not None:
        await set_last_alert_threshold(result.site_id, check_type, None)


async def apply_incident(r: CheckResult, check_type: str, settled: bool = True, open_new: bool = True):
    """Open, keep or close r's incident. settled=False — the check had no
    verdict (its service is down): an open incident stays as it is. A check
    from a screen (open_new=False) may close what got fixed — the owner is
    looking at it — but never opens: the alert belongs to the scheduled check."""
    if not r.ok and r.error:
        if open_new:
            r.incident_id, r.incident_new = await save_incident(r.site_id, check_type, r.error, r.severity)
    elif settled:
        closed = await resolve_incident(r.site_id, check_type)
        r.recovered, r.resolved_incident = closed is not None, closed


def told_before(previous: dict) -> set[str]:
    """Finding codes the scheduled check has already alerted. A snapshot
    written before «told» existed counts its findings as said: an upgrade
    must not repeat every open alert."""
    if "told" in previous:
        return set(previous.get("told") or [])
    return {f["code"] for f in previous.get("findings") or []}


def told_after(told: set[str], codes: set[str], manage: bool) -> list[str]:
    """What to remember as said. A check from a screen adds nothing: the
    scheduled check must still alert what the screen saw first."""
    return sorted(codes if manage else told & codes)


async def apply_findings(r: CheckResult, check_type: str, codes: set[str], told: set[str],
                         settled: bool = True, open_new: bool = True):
    """apply_incident for an incident that holds several findings: a finding
    the scheduled check has not alerted yet sends the incident's alert again
    (it lists all of them, the new one among them)."""
    await apply_incident(r, check_type, settled=settled, open_new=open_new)
    if open_new and r.incident_id and codes - told:
        r.incident_new = True
