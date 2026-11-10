"""SSL certificate expiry with an alert ladder and a renewal watch (the
serial number is tracked between checks: a cert that is about to expire
and has NOT changed means auto-renewal is failing)."""

import asyncio
import contextlib
import logging
import ssl
from datetime import UTC, datetime
from urllib.parse import urlparse

from config import config
from db.database import get_or_create_site, get_state, save_check, set_state
from monitors.base import SslInfo, SslResult, gather_checks
from monitors.ladder import apply_ladder
from services import sitestatus

logger = logging.getLogger(__name__)

SSL_THRESHOLDS = [0, 1, 3, 7, 14]
HANDSHAKE_TIMEOUT = 10   # s: a host that takes TCP but never finishes TLS
CONNECT_BUDGET = 20      # s: the whole fetch, the name lookup included


async def _fetch_cert(hostname: str, port: int) -> SslInfo:
    """The certificate via asyncio's own TLS — no thread is held while a
    stalled host keeps the handshake hanging (that pool resolves the names
    of every HTTP check)."""
    ctx = ssl.create_default_context()
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(hostname, port, ssl=ctx, server_hostname=hostname,
                                ssl_handshake_timeout=HANDSHAKE_TIMEOUT),
        timeout=CONNECT_BUDGET)
    try:
        cert = writer.get_extra_info("peercert")
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    return _parse_cert(cert)


def _parse_cert(cert: dict) -> SslInfo:
    fmt = "%b %d %H:%M:%S %Y %Z"
    not_after = datetime.strptime(cert["notAfter"], fmt).replace(tzinfo=UTC)
    not_before = datetime.strptime(cert["notBefore"], fmt).replace(tzinfo=UTC)
    subject = dict(x[0] for x in cert.get("subject", ()))
    issuer = dict(x[0] for x in cert.get("issuer", ()))
    return SslInfo(
        common_name=subject.get("commonName", ""),
        issuer=issuer.get("organizationName", ""),
        not_before=not_before.isoformat(),
        not_after=not_after.isoformat(),
        days_left=(not_after - datetime.now(UTC)).days,
        serial_number=cert.get("serialNumber", ""),
    )


async def check_ssl(url: str, manage: bool = True) -> SslResult:
    parsed = urlparse(url)
    hostname, port = parsed.hostname or "", parsed.port or 443
    site_id = await get_or_create_site(url)
    r = SslResult(url=url, site_id=site_id)
    try:
        info = await _fetch_cert(hostname, port)
        r.ssl_info = info
        # Renewal watch. Read-only checks must not update the serial — that
        # would consume the "renewed" transition before the scheduler sees it.
        prev_serial = await get_state(f"ssl_serial:{site_id}")
        if info.serial_number:
            if prev_serial and prev_serial != info.serial_number:
                r.renewed = True
            if manage:
                await set_state(f"ssl_serial:{site_id}", info.serial_number)
        days = info.days_left
        if 0 <= days <= config.ssl_renew_warn_days and prev_serial == info.serial_number:
            r.renewal_note = (f"Автопродление, похоже, не сработало: до истечения "
                              f"{days} дн., а сертификат не менялся.")
        if days < 0:
            r.status, r.error = "error", f"SSL expired {abs(days)} days ago"
        elif days <= 3:
            r.status, r.error = "critical", f"SSL expires in {days} days!"
        elif days <= 14:
            r.status, r.error = "warning", f"SSL expires in {days} days"
    except ssl.SSLCertVerificationError as e:
        # An expired/invalid cert fails the handshake before we can read
        # its dates — produce a human-readable message.
        reason = e.verify_message or str(e)
        r.status = "error"
        r.error = ("SSL certificate has expired" if "expired" in reason.lower()
                   else f"SSL certificate invalid: {reason}")
    except Exception as e:
        # Network-level failure — NOT a certificate problem. Must not touch
        # the ladder: pinning last_threshold at 0 would suppress alerts.
        r.status, r.error, r.transient = "error", f"SSL check failed: {e}", True

    details = r.error or (f"OK, {r.ssl_info.days_left} days left" if r.ssl_info else None)
    await save_check(site_id, "ssl", r.status, details=details)
    # A renewal is a fact of normal operation: no message, just a date the
    # weekly report can mention. Kept across snapshots until the next one.
    renewed_at = ((await sitestatus.get(site_id)).get("ssl") or {}).get("renewed_at")
    if r.renewed and manage:
        renewed_at = datetime.now(UTC).isoformat(timespec="seconds")
    await sitestatus.update(site_id, "ssl", days_left=r.ssl_info.days_left if r.ssl_info else None,
                            issuer=r.ssl_info.issuer if r.ssl_info else None,
                            not_after=r.ssl_info.not_after if r.ssl_info else None,
                            error=None if r.ssl_info else r.error, transient=r.transient,
                            renewed_at=renewed_at)
    if manage and not r.transient:
        await apply_ladder(r, "ssl", r.ssl_info.days_left if r.ssl_info else None,
                           SSL_THRESHOLDS)
    return r


async def check_all_ssl(urls: list[str], manage: bool = True) -> list[SslResult]:
    """https:// sites only: on an http:// one port 443 answers with the
    hosting's default certificate (a false «недействителен»), and on
    http://host:8080 there is no TLS at all."""
    https = [u for u in urls if u.lower().startswith("https://")]
    return await gather_checks(https, check_ssl, "ssl", manage=manage)
