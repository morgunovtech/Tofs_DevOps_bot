from db.database import get_active_incidents, get_last_alert_threshold
from monitors.base import SslResult
from monitors.ladder import apply_ladder, current_threshold
from monitors.ssl_checker import SSL_THRESHOLDS


def test_current_threshold():
    assert current_threshold(20, SSL_THRESHOLDS) is None
    assert current_threshold(14, SSL_THRESHOLDS) == 14
    assert current_threshold(5, SSL_THRESHOLDS) == 7
    assert current_threshold(-3, SSL_THRESHOLDS) == 0


async def _step(sid, days, status="warning"):
    r = SslResult(url="https://example.com", site_id=sid, status=status,
                  error=f"expires in {days}")
    await apply_ladder(r, "ssl", days, SSL_THRESHOLDS)
    return r


async def test_ladder_alerts_once_per_threshold(db):
    sid = await db.get_or_create_site("https://example.com")
    assert (await _step(sid, 13)).incident_new            # crossed 14
    assert not (await _step(sid, 12)).incident_new        # same band: silent
    assert not (await _step(sid, 8)).incident_new
    assert (await _step(sid, 6)).incident_new             # crossed 7
    assert (await _step(sid, 2, "critical")).incident_new  # crossed 3
    assert await get_last_alert_threshold(sid, "ssl") == 3
    assert len(await get_active_incidents()) == 1

    ok = SslResult(url="https://example.com", site_id=sid)
    await apply_ladder(ok, "ssl", 90, SSL_THRESHOLDS)
    assert ok.recovered
    assert await get_last_alert_threshold(sid, "ssl") is None
    assert await get_active_incidents() == []


async def test_invalid_cert_is_bottom_of_ladder(db):
    sid = await db.get_or_create_site("https://example.com")
    r = SslResult(url="https://example.com", site_id=sid, status="error", error="invalid")
    await apply_ladder(r, "ssl", None, SSL_THRESHOLDS)
    assert r.incident_new and r.threshold_crossed == 0
    r2 = SslResult(url="https://example.com", site_id=sid, status="error", error="invalid")
    await apply_ladder(r2, "ssl", None, SSL_THRESHOLDS)
    assert not r2.incident_new
