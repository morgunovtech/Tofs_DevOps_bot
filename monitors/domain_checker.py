import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import whois

from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
    get_last_alert_threshold, set_last_alert_threshold,
)

logger = logging.getLogger(__name__)

# Domain expiry warnings should start earlier than SSL — registrars are slower.
# Sorted ascending so _current_threshold picks the smallest band days_left fits in.
DOMAIN_THRESHOLDS = [0, 1, 3, 7, 14, 30]


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


def _current_threshold(days_left: int) -> int | None:
    for t in DOMAIN_THRESHOLDS:
        if days_left <= t:
            return t
    return None


async def check_domain(url: str, manage: bool = True) -> dict:
    """Check domain registration expiry. manage=False = read-only."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        # A malformed URL must not crash the whole gathered domain job
        # (and the morning report with it).
        return {"url": url, "domain": url, "status": "warning",
                "error": "Некорректный URL", "domain_info": None,
                "incident_new": False, "recovered": False,
                "threshold_crossed": None}
    # NOTE: naive "last two labels" registrable-domain heuristic — correct for
    # domains like example.com, wrong for multi-part public suffixes
    # (example.co.uk → co.uk). Switch to tldextract if such sites are added.
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
        "incident_new": False,
        "recovered": False,
        "threshold_crossed": None,
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

    if result["domain_info"]:
        details = result["error"] or (
            f"OK, {result['domain_info']['days_left']} days left"
        )
    else:
        details = result["error"]

    await save_check(
        site_id=site_id,
        check_type="domain",
        status=result["status"],
        details=details,
    )

    if not manage:
        return result

    # WHOIS failures are flaky — don't manage incidents based on them, just log.
    # NB: the days_left=None path below also returns without touching
    # incidents — deliberately: auto-closing a real expiry incident because
    # WHOIS went flaky would be worse than leaving it open.
    if result["status"] == "warning" and result["error"] and result["error"].startswith("WHOIS"):
        return result

    last_threshold = await get_last_alert_threshold(site_id, "domain")

    if result["status"] in ("error", "critical", "warning") and result["error"]:
        days = result["domain_info"]["days_left"] if result["domain_info"] else -999
        if days is None:
            return result
        current = _current_threshold(days)

        if last_threshold is None or (current is not None and current < last_threshold):
            await save_incident(
                site_id, "domain", result["error"],
                severity="critical" if result["status"] in ("error", "critical") else "warning",
            )
            result["incident_new"] = True
            result["threshold_crossed"] = current
            await set_last_alert_threshold(site_id, "domain", current)
        else:
            await save_incident(
                site_id, "domain", result["error"],
                severity="critical" if result["status"] in ("error", "critical") else "warning",
            )
    else:
        resolved = await resolve_incident(site_id, "domain")
        if resolved:
            result["recovered"] = True
        if last_threshold is not None:
            await set_last_alert_threshold(site_id, "domain", None)

    return result


async def check_all_domains(urls: list[str], manage: bool = True) -> list[dict]:
    # Deduplicate by registrable domain to avoid multiple WHOIS queries.
    seen_domains = set()
    unique_urls = []
    for url in urls:
        hostname = urlparse(url).hostname
        if not hostname:
            continue
        parts = hostname.split(".")
        domain = ".".join(parts[-2:]) if len(parts) > 2 else hostname
        if domain not in seen_domains:
            seen_domains.add(domain)
            unique_urls.append(url)

    tasks = [check_domain(url, manage=manage) for url in unique_urls]
    return await asyncio.gather(*tasks, return_exceptions=False)
