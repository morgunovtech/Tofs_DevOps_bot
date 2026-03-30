import ssl
import socket
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from db.database import get_or_create_site, save_check, save_incident, resolve_incident

logger = logging.getLogger(__name__)


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


async def check_ssl(url: str) -> dict:
    """Check SSL certificate for a URL."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    site_id = await get_or_create_site(url)

    result = {
        "url": url,
        "hostname": hostname,
        "status": "ok",
        "error": None,
        "ssl_info": None,
    }

    try:
        loop = asyncio.get_event_loop()
        ssl_info = await loop.run_in_executor(None, _get_ssl_info, hostname)
        result["ssl_info"] = ssl_info

        days_left = ssl_info["days_left"]
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

    except Exception as e:
        result["status"] = "error"
        result["error"] = f"SSL check failed: {e}"

    await save_check(
        site_id=site_id,
        check_type="ssl",
        status=result["status"],
        details=result["error"] or f"OK, {result['ssl_info']['days_left']} days left" if result["ssl_info"] else None,
    )

    if result["status"] in ("error", "critical", "warning"):
        await save_incident(
            site_id, "ssl", result["error"],
            severity="critical" if result["status"] in ("error", "critical") else "warning"
        )
    else:
        await resolve_incident(site_id, "ssl")

    return result


async def check_all_ssl(urls: list[str]) -> list[dict]:
    tasks = [check_ssl(url) for url in urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
