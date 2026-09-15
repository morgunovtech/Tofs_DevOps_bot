"""
Google Search Console API client — real indexing status, no google-* SDKs.

Auth: service-account JWT (RS256 via `cryptography`, already a dependency)
exchanged for an OAuth token. Setup:
  1. Google Cloud Console → create a service account, download the JSON key;
  2. enable "Google Search Console API" for the project;
  3. GSC → property settings → Users → add the service account e-mail
     (Restricted/Full);
  4. .env: GSC_SERVICE_ACCOUNT_FILE=/app/gsc-key.json,
     GSC_PROPERTY=sc-domain:example.com (a domain property covers all
     subdomains; https://-prefix properties work too).

Everything degrades to None when unconfigured — callers just skip sections.
"""

import base64
import json
import logging
import os
import time
from urllib.parse import quote

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from config import config

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{prop}/searchAnalytics/query"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_token_cache: dict = {"token": None, "exp": 0.0}


def available() -> bool:
    return bool(config.gsc_property and config.gsc_service_account_file
                and os.path.exists(config.gsc_service_account_file))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_assertion(creds: dict, now: float | None = None) -> str:
    """Build the signed JWT for the OAuth token exchange (pure, testable)."""
    now = now or time.time()
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": creds["client_email"],
        "scope": SCOPE,
        "aud": TOKEN_URL,
        "iat": int(now),
        "exp": int(now) + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = serialization.load_pem_private_key(
        creds["private_key"].encode(), password=None)
    signature = key.sign(
        signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


async def _access_token(session: aiohttp.ClientSession) -> str | None:
    if _token_cache["token"] and _token_cache["exp"] - 60 > time.time():
        return _token_cache["token"]
    try:
        with open(config.gsc_service_account_file, encoding="utf-8") as f:
            creds = json.load(f)
        assertion = make_assertion(creds)
        async with session.post(TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }) as resp:
            data = await resp.json()
            if resp.status != 200:
                logger.warning("GSC token exchange failed: %s", data)
                return None
        _token_cache["token"] = data["access_token"]
        _token_cache["exp"] = time.time() + data.get("expires_in", 3600)
        return _token_cache["token"]
    except Exception as e:
        logger.warning("GSC auth failed: %s", e)
        return None


async def search_totals(start: str, end: str,
                        page_prefix: str | None = None) -> dict | None:
    """Aggregate clicks/impressions for a date range (YYYY-MM-DD), optionally
    limited to pages containing `page_prefix` (per-site slice of a domain
    property)."""
    if not available():
        return None
    body: dict = {"startDate": start, "endDate": end}
    if page_prefix:
        body["dimensionFilterGroups"] = [{"filters": [{
            "dimension": "page", "operator": "contains",
            "expression": page_prefix,
        }]}]
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            token = await _access_token(session)
            if not token:
                return None
            url = ANALYTICS_URL.format(prop=quote(config.gsc_property, safe=""))
            async with session.post(
                url, json=body,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.warning("GSC analytics HTTP %s: %s", resp.status, data)
                    return None
        row = (data.get("rows") or [{}])[0]
        return {
            "clicks": round(row.get("clicks", 0)),
            "impressions": round(row.get("impressions", 0)),
            "position": round(row.get("position", 0), 1),
        }
    except Exception as e:
        logger.warning("GSC analytics failed: %s", e)
        return None


async def inspect_url(page_url: str) -> dict | None:
    """URL Inspection: is the page actually in Google's index?
    Returns {verdict, coverage} — verdict PASS means indexed/indexable."""
    if not available():
        return None
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            token = await _access_token(session)
            if not token:
                return None
            async with session.post(
                INSPECT_URL,
                json={"inspectionUrl": page_url, "siteUrl": config.gsc_property},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.warning("GSC inspect HTTP %s: %s", resp.status, data)
                    return None
        idx = (data.get("inspectionResult") or {}).get("indexStatusResult") or {}
        return {
            "verdict": idx.get("verdict", "UNKNOWN"),
            "coverage": idx.get("coverageState", "?"),
            "last_crawl": idx.get("lastCrawlTime"),
        }
    except Exception as e:
        logger.warning("GSC inspect failed: %s", e)
        return None
