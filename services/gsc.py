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
import hashlib
import importlib
import json
import logging
import time
from urllib.parse import quote

import aiohttp

from services import integrations
from utils.i18n import language, t
from utils.text import err_text
from utils.urls import host_of

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ANALYTICS_URL = "https://www.googleapis.com/webmasters/v3/sites/{prop}/searchAnalytics/query"
INSPECT_URL = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"

_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10)
# A refused or unanswered token exchange is not retried for this long: the
# daily index check asks once per site, not once per site per minute.
FAIL_PAUSE = 300
# What URL Inspection may say about the index; anything else is «no verdict».
VERDICTS = ("PASS", "FAIL", "NEUTRAL")
# Severity of a structured-data remark (RichResultsIssue.severity); anything
# else Google may send (SEVERITY_UNSPECIFIED) is kept as the milder one.
SEVERITIES = {"ERROR": "error", "WARNING": "warning"}
# Google translates issueMessage into the language asked for (languageCode,
# BCP-47; en-US when not given).
LANGUAGE_CODES = {"ru": "ru-RU"}
# who: the key the token was issued for — a new key gets a new token.
_token_cache: dict = {"token": None, "exp": 0.0, "who": None, "fail_until": 0.0}


def available() -> bool:
    return integrations.gsc_configured()


def reset_token_cache():
    """Forget the cached OAuth token (after the key or property changed)."""
    _token_cache.update(token=None, exp=0.0, who=None, fail_until=0.0)


def covers(prop: str, url: str) -> bool:
    """Does the Search Console property see this site at all?"""
    host = host_of(url)
    if prop.startswith("sc-domain:"):
        domain = prop.removeprefix("sc-domain:").lower()
        return host == domain or host.endswith("." + domain)
    return url.lower().rstrip("/").startswith(prop.lower().rstrip("/"))


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
    # GSC is optional: cryptography (cffi + its own OpenSSL, +7–10 MiB RSS)
    # loads on the first signature, not on every start of every bot.
    serialization = importlib.import_module("cryptography.hazmat.primitives.serialization")
    hashes = importlib.import_module("cryptography.hazmat.primitives.hashes")
    padding = importlib.import_module("cryptography.hazmat.primitives.asymmetric.padding")
    key = serialization.load_pem_private_key(
        creds["private_key"].encode(), password=None)
    signature = key.sign(
        signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


async def _access_token(session: aiohttp.ClientSession) -> str | None:
    creds = integrations.gsc_credentials()
    if not creds:
        return None
    who = (creds.get("client_email"), hashlib.sha256(str(creds.get("private_key")).encode()).hexdigest())
    if _token_cache["who"] != who:
        reset_token_cache()
        _token_cache["who"] = who
    if _token_cache["token"] and _token_cache["exp"] - 60 > time.time():
        return _token_cache["token"]
    if _token_cache["fail_until"] > time.time():
        return None                       # refused or silent minutes ago: not again yet
    try:
        assertion = make_assertion(creds)
    except (ValueError, TypeError) as e:
        _token_cache["fail_until"] = time.time() + FAIL_PAUSE
        logger.warning("GSC key cannot sign: %s", err_text(e))
        await integrations.record_result("gsc", False, t("ключ не подходит ({err_text})").format(err_text=err_text(e)), refused=True)
        return None
    try:
        async with session.post(TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or not isinstance(data, dict) or not data.get("access_token"):
                _token_cache["fail_until"] = time.time() + FAIL_PAUSE
                logger.warning("GSC token exchange HTTP %s: %s", resp.status, data)
                why = (data.get("error_description") or data.get("error")) if isinstance(data, dict) else None
                await integrations.record_result("gsc", False, t("Google не принял ключ ({v})").format(v=why or f'HTTP {resp.status}'),
                                                 refused=resp.status in (400, 401, 403))
                return None
    except Exception as e:
        _token_cache["fail_until"] = time.time() + FAIL_PAUSE
        logger.warning("GSC auth failed: %s", err_text(e))
        await integrations.record_result("gsc", False, t("Google не ответил ({err_text})").format(err_text=err_text(e)))
        return None
    _token_cache["token"] = data["access_token"]
    _token_cache["exp"] = time.time() + data.get("expires_in", 3600)
    return _token_cache["token"]


async def search_totals(start: str, end: str,
                        page_prefix: str | None = None) -> dict | None:
    """Aggregate clicks/impressions for a date range (YYYY-MM-DD), optionally
    limited to pages containing `page_prefix` (per-site slice of a domain
    property). A site the property does not cover is not asked about: the
    answer would be zeros, not its traffic."""
    if not available():
        return None
    prop = integrations.gsc_property()
    if page_prefix and not covers(prop, page_prefix):
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
            url = ANALYTICS_URL.format(prop=quote(prop, safe=""))
            async with session.post(
                url, json=body,
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    logger.warning("GSC analytics HTTP %s: %s", resp.status, data)
                    await integrations.record_result("gsc", False, t("Search Console ответил HTTP {status}").format(status=resp.status),
                                                     refused=resp.status in (401, 403))
                    return None
        await integrations.record_result("gsc", True)
        row = ((data.get("rows") if isinstance(data, dict) else None) or [{}])[0]
        return {
            "clicks": round(row.get("clicks", 0)),
            "impressions": round(row.get("impressions", 0)),
            "position": round(row.get("position", 0), 1),
        }
    except Exception as e:
        logger.warning("GSC analytics failed: %s", err_text(e))
        await integrations.record_result("gsc", False, t("Google не ответил ({err_text})").format(err_text=err_text(e)))
        return None


def rich_issues(inspection: dict | None) -> list[dict]:
    """Google's remarks on the page's structured data, from the same URL
    Inspection answer: inspectionResult.richResultsResult.detectedItems[]
    {richResultType, items[] {name, issues[] {issueMessage, severity}}}.
    → [{type, message, severity: «error» | «warning»}], one per remark
    however many items carry it, errors first. The section is absent when
    Google found no rich results on the page — that is «no remarks»."""
    rich = (inspection or {}).get("richResultsResult")
    found: dict[tuple, dict] = {}
    for group in (rich.get("detectedItems") if isinstance(rich, dict) else None) or []:
        if not isinstance(group, dict):
            continue
        kind = str(group.get("richResultType") or "?")
        for item in group.get("items") or []:
            for issue in (item.get("issues") if isinstance(item, dict) else None) or []:
                message = str(issue.get("issueMessage") or "").strip() if isinstance(issue, dict) else ""
                if not message:
                    continue
                severity = SEVERITIES.get(str(issue.get("severity")), "warning")
                found.setdefault((severity, kind, message), {"type": kind, "message": message, "severity": severity})
    return [found[k] for k in sorted(found)]


async def inspect_url(page_url: str) -> dict | None:
    """URL Inspection: is the page actually in Google's index?
    Returns {verdict, coverage, last_crawl, …, rich_issues} — verdict PASS
    means indexed/indexable; None when there is no verdict (an answer
    without one is not «removed from the index»). rich_issues: what Google
    says about the page's structured data (see rich_issues()) — the same
    call, no second request."""
    if not available():
        return None
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            token = await _access_token(session)
            if not token:
                return None
            async with session.post(
                INSPECT_URL,
                json={"inspectionUrl": page_url, "siteUrl": integrations.gsc_property(),
                      "languageCode": LANGUAGE_CODES.get(language(), "en-US")},
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    logger.warning("GSC inspect HTTP %s: %s", resp.status, data)
                    await integrations.record_result("gsc", False, t("Search Console ответил HTTP {status}").format(status=resp.status),
                                                     refused=resp.status in (401, 403))
                    return None
        await integrations.record_result("gsc", True)
        result = (data.get("inspectionResult") if isinstance(data, dict) else None) or {}
        idx = result.get("indexStatusResult") or {}
        verdict = idx.get("verdict")
        if verdict not in VERDICTS:
            logger.warning("GSC inspect of %s: no verdict (%s)", page_url, verdict)
            return None
        return {
            "verdict": verdict,
            "coverage": idx.get("coverageState", "?"),
            "last_crawl": idx.get("lastCrawlTime"),
            # The page the owner names as the main copy, and the one Google chose.
            "user_canonical": idx.get("userCanonical"),
            "google_canonical": idx.get("googleCanonical"),
            "rich_issues": rich_issues(result),
        }
    except Exception as e:
        logger.warning("GSC inspect failed: %s", err_text(e))
        await integrations.record_result("gsc", False, t("Google не ответил ({err_text})").format(err_text=err_text(e)))
        return None
