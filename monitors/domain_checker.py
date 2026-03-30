import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import whois

from db.database import get_or_create_site, save_check, save_incident, resolve_incident

logger = logging.getLogger(__name__)


def _get_domain_info(domain: str) -> dict:
    """Synchronous WHOIS lookup (run in executor)."""
    w = whois.whois(domain)

    expiration = w.expiration_date
    if isinstance(expiration, list):
        expiration = expiration[0]

    creation = w.creation_date
    if isinstance(creation, list):
        creation = creation[0]

    registrar = w.registrar or "Unknown"
    now = datetime.now(timezone.utc)

    if expiration:
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        days_left = (expiration - now).days
    else:
        days_left = None

    return {
        "domain": domain,
        "registrar": registrar,
        "creation_date": creation.isoformat() if creation else None,
        "expiration_date": expiration.isoformat() if expiration else None,
        "days_left": days_left,
        "name_servers": w.name_servers if w.name_servers else [],
    }


async def check_domain(url: str) -> dict:
    """Check domain registration expiry."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    # Extract registrable domain (e.g. morgunov.tech from s.morgunov.tech)
    parts = hostname.split(".")
    if len(parts) > 2:
        domain = ".".join(parts[-2:])
    else:
        domain = hostname

    site_id = await get_or_create_site(url)

    result = {
        "url": url,
        "domain": domain,
        "status": "ok",
        "error": None,
        "domain_info": None,
    }

    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _get_domain_info, domain)
        result["domain_info"] = info

        days_left = info["days_left"]
        if days_left is None:
            result["status"] = "warning"
            result["error"] = "Could not determine domain expiry date"
        elif days_left < 0:
            result["status"] = "error"
            result["error"] = f"Domain expired {abs(days_left)} days ago!"
        elif days_left <= 7:
            result["status"] = "critical"
            result["error"] = f"Domain expires in {days_left} days!"
        elif days_left <= 14:
            result["status"] = "warning"
            result["error"] = f"Domain expires in {days_left} days"
        elif days_left <= 30:
            result["status"] = "warning"
            result["error"] = f"Domain expires in {days_left} days"

    except Exception as e:
        result["status"] = "warning"
        result["error"] = f"WHOIS lookup failed: {e}"

    await save_check(
        site_id=site_id,
        check_type="domain",
        status=result["status"],
        details=result["error"] or f"OK, {result['domain_info']['days_left']} days left" if result["domain_info"] else None,
    )

    if result["status"] in ("error", "critical", "warning") and result["error"]:
        await save_incident(
            site_id, "domain", result["error"],
            severity="critical" if result["status"] in ("error", "critical") else "warning"
        )
    else:
        await resolve_incident(site_id, "domain")

    return result


async def check_all_domains(urls: list[str]) -> list[dict]:
    # Deduplicate by registrable domain to avoid multiple WHOIS queries
    seen_domains = set()
    unique_urls = []
    for url in urls:
        parsed = urlparse(url)
        parts = parsed.hostname.split(".")
        domain = ".".join(parts[-2:]) if len(parts) > 2 else parsed.hostname
        if domain not in seen_domains:
            seen_domains.add(domain)
            unique_urls.append(url)

    tasks = [check_domain(url) for url in unique_urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
