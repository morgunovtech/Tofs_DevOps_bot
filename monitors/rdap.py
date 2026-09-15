"""Domain registration data over RDAP (the protocol that replaced WHOIS for
gTLDs in January 2025), with a tiny async WHOIS fallback for the Russian
zones (.ru/.su/.рф) that still have no RDAP server.

Bootstrap: IANA publishes tld → RDAP base URLs (dns.json); it is cached for
a day. rdap.org acts as a redirector when the bootstrap has nothing.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

import aiohttp

from monitors.base import DomainInfo
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)

IANA_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"
RDAP_ORG = "https://rdap.org/domain/{domain}"
WHOIS_FALLBACK = {
    "ru": "whois.tcinet.ru", "su": "whois.tcinet.ru", "xn--p1ai": "whois.tcinet.ru",
}
_TIMEOUT = aiohttp.ClientTimeout(total=25)
_BOOTSTRAP_TTL = 24 * 3600
_bootstrap: dict[str, list[str]] = {}
_bootstrap_at = 0.0


class DomainLookupError(Exception):
    """Transient failure (network, rate limit) — not evidence about the domain."""


class RdapUnsupported(DomainLookupError):
    """The zone publishes neither RDAP nor a WHOIS we know how to read."""


def ascii_tld(domain: str) -> str:
    tld = domain.rsplit(".", 1)[-1].lower()
    try:
        return tld.encode("idna").decode("ascii")
    except UnicodeError:
        return tld


async def _bootstrap_servers(session: aiohttp.ClientSession) -> dict[str, list[str]]:
    global _bootstrap, _bootstrap_at
    if _bootstrap and time.monotonic() - _bootstrap_at < _BOOTSTRAP_TTL:
        return _bootstrap
    try:
        async with session.get(IANA_BOOTSTRAP) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                table: dict[str, list[str]] = {}
                for tlds, urls in data.get("services", []):
                    for tld in tlds:
                        table[tld.lower()] = [u if u.endswith("/") else u + "/" for u in urls]
                if table:
                    _bootstrap, _bootstrap_at = table, time.monotonic()
    except Exception as e:
        logger.debug("IANA RDAP bootstrap fetch failed: %s", e)
    return _bootstrap


def _parse_date(value) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _registrar_name(entities: list) -> str:
    for ent in entities or []:
        roles = [r.lower() for r in ent.get("roles", [])]
        if "registrar" in roles:
            vcard = ent.get("vcardArray") or []
            for item in (vcard[1] if len(vcard) > 1 else []):
                if item and item[0] == "fn" and len(item) > 3 and item[3]:
                    return str(item[3])
            for pid in ent.get("publicIds", []):
                if pid.get("identifier"):
                    return f"IANA #{pid['identifier']}"
        nested = _registrar_name(ent.get("entities") or [])
        if nested:
            return nested
    return ""


def parse_rdap(data: dict, domain: str) -> DomainInfo:
    """Pure: RDAP domain object → DomainInfo."""
    expiration = creation = None
    for ev in data.get("events", []):
        action = str(ev.get("eventAction", "")).lower()
        if action == "expiration":
            expiration = _parse_date(ev.get("eventDate"))
        elif action == "registration":
            creation = _parse_date(ev.get("eventDate"))
    now = datetime.now(UTC)
    return DomainInfo(
        domain=domain,
        registrar=_registrar_name(data.get("entities") or []) or "Unknown",
        creation_date=creation.isoformat() if creation else None,
        expiration_date=expiration.isoformat() if expiration else None,
        days_left=(expiration - now).days if expiration else None,
        name_servers=sorted(
            str(ns.get("ldhName", "")).lower().rstrip(".")
            for ns in data.get("nameservers", []) if ns.get("ldhName")),
        source="rdap",
    )


async def _rdap_get(session: aiohttp.ClientSession, url: str) -> dict | None:
    """RDAP document, None for 404 (not found here), raise on other failures."""
    async with session.get(url, headers={"Accept": "application/rdap+json",
                                         "User-Agent": USER_AGENT}) as resp:
        if resp.status == 404:
            return None
        if resp.status == 429:
            raise DomainLookupError("RDAP rate limit (429)")
        if resp.status != 200:
            raise DomainLookupError(f"RDAP HTTP {resp.status}")
        return await resp.json(content_type=None)


async def rdap_lookup(domain: str, session: aiohttp.ClientSession) -> DomainInfo:
    tld = ascii_tld(domain)
    servers = (await _bootstrap_servers(session)).get(tld, [])
    urls = [f"{base}domain/{domain}" for base in servers] + [RDAP_ORG.format(domain=domain)]
    last_error: Exception | None = None
    for url in urls:
        try:
            data = await _rdap_get(session, url)
        except DomainLookupError as e:
            last_error = e
            continue
        except Exception as e:
            last_error = DomainLookupError(str(e) or type(e).__name__)
            continue
        if data is not None:
            return parse_rdap(data, domain)
    if not servers and last_error is None:
        raise RdapUnsupported(f"зона .{tld} без RDAP")
    raise last_error or DomainLookupError("RDAP: домен не найден")


# ── WHOIS fallback for zones without RDAP ────────────────────────────────────

def parse_whois(text: str, domain: str) -> DomainInfo:
    """Pure: TCI-style key: value WHOIS text → DomainInfo."""
    fields: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith("%"):
            fields.setdefault(key.strip().lower(), []).append(value.strip())

    def first(*names):
        for n in names:
            if fields.get(n):
                return fields[n][0]
        return None

    def date(raw):
        if not raw:
            return None
        raw = raw.replace(".", "-", 2) if raw[:4].isdigit() and "." in raw[:10] else raw
        return _parse_date(raw)

    expiration = date(first("paid-till", "expiration date", "expiry date", "registry expiry date"))
    creation = date(first("created", "creation date"))
    now = datetime.now(UTC)
    return DomainInfo(
        domain=domain,
        registrar=first("registrar") or "Unknown",
        creation_date=creation.isoformat() if creation else None,
        expiration_date=expiration.isoformat() if expiration else None,
        days_left=(expiration - now).days if expiration else None,
        name_servers=sorted(v.split()[0].lower().rstrip(".") for v in fields.get("nserver", []) if v),
        source="whois",
    )


async def whois_lookup(domain: str, server: str) -> DomainInfo:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(server, 43), timeout=10)
        writer.write(f"{domain}\r\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(64 * 1024), timeout=15)
        writer.close()
    except (TimeoutError, OSError) as e:
        raise DomainLookupError(f"WHOIS {server}: {e}") from e
    return parse_whois(raw.decode("utf-8", errors="replace"), domain)


async def lookup(domain: str) -> DomainInfo:
    """RDAP, then the WHOIS fallback for zones that have none."""
    tld = ascii_tld(domain)
    if tld in WHOIS_FALLBACK:
        return await whois_lookup(domain.encode("idna").decode("ascii"), WHOIS_FALLBACK[tld])
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        return await rdap_lookup(domain, session)
