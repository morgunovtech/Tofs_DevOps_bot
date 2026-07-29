import ssl
import socket
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from config import config
from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
    get_last_alert_threshold, set_last_alert_threshold,
    get_state, set_state,
)

logger = logging.getLogger(__name__)

# Alert ladder for SSL — we notify once per threshold crossing, not daily.
# Sorted ascending so _current_threshold picks the smallest band days_left fits in.
SSL_THRESHOLDS = [0, 1, 3, 7, 14]


def _get_ssl_info(hostname: str, port: int = 443) -> dict:
    """Synchronous SSL cert fetch (run in executor)."""
    ctx = ssl.create_default_context()
    with socket.create_connection((hostname, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            cert = ssock.getpeercert()

    not_after = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
    not_after = not_after.replace(tzinfo=timezone.utc)
    not_before = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z")
    not_before = not_before.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days_left = (not_after - now).days

    subject = dict(x[0] for x in cert["subject"])
    issuer = dict(x[0] for x in cert["issuer"])

    return {
        "common_name": subject.get("commonName", ""),
        "issuer": issuer.get("organizationName", ""),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_left": days_left,
        "serial_number": cert.get("serialNumber", ""),
    }


def _current_threshold(days_left: int) -> int | None:
    """Return the lowest threshold days_left has crossed, or None if all clear."""
    for t in SSL_THRESHOLDS:
        if days_left <= t:
            return t
    return None


async def check_ssl(url: str, manage: bool = True) -> dict:
    """Check SSL certificate for a URL. manage=False = read-only (no ladder,
    no incidents, no serial tracking) for interactive checks/reports."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    site_id = await get_or_create_site(url)

    result = {
        "url": url,
        "hostname": hostname,
        "status": "ok",
        "error": None,
        "ssl_info": None,
        "incident_new": False,
        "recovered": False,
        "threshold_crossed": None,
        # True the first check after the cert was replaced (serial changed).
        "renewed": False,
        # Set when expiry is close AND the serial hasn't changed — i.e.
        # auto-renewal (Cloudflare/certbot) appears to be failing.
        "renewal_note": None,
    }

    try:
        loop = asyncio.get_event_loop()
        ssl_info = await loop.run_in_executor(None, _get_ssl_info, hostname)
        result["ssl_info"] = ssl_info

        # Renewal watch: track the cert serial between checks. Read-only
        # checks must not update the serial — that would consume the
        # "renewed" transition before the scheduled job sees it.
        serial = ssl_info.get("serial_number") or ""
        prev_serial = await get_state(f"ssl_serial:{site_id}")
        if serial:
            if prev_serial and prev_serial != serial:
                result["renewed"] = True
            if manage:
                await set_state(f"ssl_serial:{site_id}", serial)

        days_left = ssl_info["days_left"]
        if (0 <= days_left <= config.ssl_renew_warn_days
                and prev_serial == serial):
            result["renewal_note"] = (
                f"Автопродление, похоже, не сработало: до истечения "
                f"{days_left} дн., а сертификат не менялся."
            )
        if days_left < 0:
            result["status"] = "error"
            result["error"] = f"SSL expired {abs(days_left)} days ago"
        elif days_left <= 3:
            result["status"] = "critical"
            result["error"] = f"SSL expires in {days_left} days!"
        elif days_left <= 7:
            result["status"] = "warning"
            result["error"] = f"SSL expires in {days_left} days"
        elif days_left <= 14:
            result["status"] = "warning"
            result["error"] = f"SSL expires in {days_left} days"

    except ssl.SSLCertVerificationError as e:
        # An expired/invalid cert fails the handshake before we can read its
        # dates (so the days_left<0 branch above never fires for expired
        # certs) — produce a human-readable message here instead of the raw
        # SSLCertVerificationError repr.
        reason = e.verify_message or str(e)
        result["status"] = "error"
        if "expired" in reason.lower():
            result["error"] = "SSL certificate has expired"
        else:
            result["error"] = f"SSL certificate invalid: {reason}"
    except Exception as e:
        # Network-level failure (timeout, refused, DNS) — NOT a certificate
        # problem. Must not touch the ladder: pinning last_threshold at 0
        # here would silently suppress all future expiry alerts.
        result["status"] = "error"
        result["error"] = f"SSL check failed: {e}"
        result["transient"] = True

    if result["ssl_info"]:
        details = result["error"] or f"OK, {result['ssl_info']['days_left']} days left"
    else:
        details = result["error"]

    await save_check(
        site_id=site_id,
        check_type="ssl",
        status=result["status"],
        details=details,
    )

    if not manage or result.get("transient"):
        return result

    # Alert ladder: only fire when we cross to a new (lower) threshold,
    # not every daily check.
    last_threshold = await get_last_alert_threshold(site_id, "ssl")

    if result["status"] in ("error", "critical", "warning"):
        days = result["ssl_info"]["days_left"] if result["ssl_info"] else -999
        current = _current_threshold(days) if result["ssl_info"] else 0

        # Re-alert only when threshold dropped (e.g. from 14 → 7 → 3 → expired)
        if last_threshold is None or (current is not None and current < last_threshold):
            _, is_new = await save_incident(
                site_id, "ssl", result["error"],
                severity="critical" if result["status"] in ("error", "critical") else "warning",
            )
            # If incident was already open from a prior threshold, force "new"
            # so the scheduler sends an updated alert for the worse threshold.
            result["incident_new"] = True
            result["threshold_crossed"] = current
            await set_last_alert_threshold(site_id, "ssl", current)
        else:
            # Same threshold or better — keep incident open silently.
            await save_incident(
                site_id, "ssl", result["error"],
                severity="critical" if result["status"] in ("error", "critical") else "warning",
            )
    else:
        # All clear — close incident, reset ladder, fire recovery alert if needed.
        resolved = await resolve_incident(site_id, "ssl")
        if resolved:
            result["recovered"] = True
        if last_threshold is not None:
            await set_last_alert_threshold(site_id, "ssl", None)

    return result


async def check_all_ssl(urls: list[str], manage: bool = True) -> list[dict]:
    tasks = [check_ssl(url, manage=manage) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
