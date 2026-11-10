"""Typed results shared by every monitor, plus the gather(), read_capped()
and decode_body() helpers.

`manage=False` on a check means read-only: the check row is still saved
(it feeds uptime and sparklines) but incidents and alert state are NOT
touched, so a menu tap can never consume an alert that the scheduled
monitor should fire.
"""

import asyncio
import html
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import TypedDict

import aiohttp

from utils.urls import site_label

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    url: str
    site_id: int
    status: str = "ok"                 # ok | warning | critical | error; availability also «unknown»:
                                       # the check says nothing about the site, the cause is on the bot's side
    error: str | None = None
    incident_new: bool = False
    incident_id: int | None = None
    recovered: bool = False
    resolved_incident: dict | None = None
    # True when the failure is on OUR side (network, verdict service) and
    # must not touch incidents or alert ladders.
    transient: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def severity(self) -> str:
        return "critical" if self.status in ("error", "critical") else "warning"


@dataclass
class AvailabilityResult(CheckResult):
    status_code: int | None = None
    response_time_ms: int | None = None
    # Second-opinion verdict: True = up from most external nodes, False =
    # confirmed down, None = not checked / no verdict (service unreachable,
    # the nodes split, or the audience's node could not open it).
    external_ok: bool | None = None
    external_ups: int = 0        # external nodes that opened it
    external_total: int = 0      # external nodes asked
    # The node in the audience's country (check-host country code) could not
    # open the site while others did: the visitors see the same.
    external_region: str | None = None
    # HTTP succeeded but the keyword/stop-phrase check failed.
    keyword_failed: bool = False
    # The server answered with a certificate a browser will not accept: every
    # visitor sees the warning page — no second opinion, no network check.
    cert_failed: bool = False
    # Hosting guessed from response headers (services.hosting key).
    hosting: str | None = None
    # An API check (utils.urls.is_api_check): «отвечает», not «открывается».
    api: bool = False
    # The bot's own server had no network: the tick says it once for every site.
    own_outage: bool = False


@dataclass
class SslInfo:
    common_name: str
    issuer: str
    not_before: str
    not_after: str
    days_left: int
    serial_number: str


@dataclass
class SslResult(CheckResult):
    ssl_info: SslInfo | None = None
    # True the first check after the cert was replaced (serial changed).
    renewed: bool = False
    # Set when expiry is close AND the serial hasn't changed.
    renewal_note: str | None = None
    threshold_crossed: int | None = None


@dataclass
class DomainInfo:
    domain: str
    registrar: str
    creation_date: str | None
    expiration_date: str | None
    days_left: int | None
    name_servers: list[str]
    source: str = "rdap"          # rdap | whois


@dataclass
class DomainResult(CheckResult):
    domain: str = ""
    domain_info: DomainInfo | None = None
    threshold_crossed: int | None = None
    # The zone has no RDAP/WHOIS we can read — expiry is not tracked.
    unsupported: bool = False


class DnsChange(TypedDict):
    host: str
    rtype: str
    old: str
    new: str
    critical: bool


@dataclass
class LinkCheck:
    url: str
    status_code: int | None
    ok: bool
    error: str | None = None

    @property
    def reason(self) -> str:
        return str(self.status_code or self.error or "N/A")


@dataclass
class LinksResult(CheckResult):
    total_links: int = 0
    broken: list[LinkCheck] = field(default_factory=list)    # a problem: own or other site alike
    pending: list[LinkCheck] = field(default_factory=list)   # failed once (timeout/5xx), rechecking


@dataclass
class DeepResult(CheckResult):
    sampled: int = 0
    errors: list[tuple[str, int]] = field(default_factory=list)  # (url, code)


@dataclass
class SeoProblem:
    code: str          # key into services.seo_fixes — headline, cost, steps
    severity: str      # critical (the site drops out of search) | warning (act, not today)
    message: str       # the finding with its specifics (page, count), human words

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message": self.message}


@dataclass
class SeoResult(CheckResult):
    # Every entry needs an action; facts of normal operation are not listed.
    problems: list[SeoProblem] = field(default_factory=list)
    pages_checked: int = 0
    no_js_chars: int | None = None

    @property
    def has_critical(self) -> bool:
        return any(p.severity == "critical" for p in self.problems)


@dataclass
class Finding:
    """A problem the owner fixes by hand — mail records, security settings:
    what, why it matters, what to do, the exact thing to paste."""
    code: str
    title: str        # «Две записи SPF вместо одной»
    why: str          # what it does to the owner's visitors or letters
    fix: str          # what to do, leading to the record
    record: str = ""  # the exact value to paste, when there is one
    note: str = ""    # said after the record

    def as_dict(self) -> dict:
        return asdict(self)


_META_CHARSET_RE = re.compile(rb'charset=["\']?([a-zA-Z0-9_-]{2,20})')


def decode_body(raw: bytes, header_charset: str | None) -> str:
    """Header charset first; <meta charset=…> sniffed from the first bytes
    (classic windows-1251 sites); utf-8 as the last resort."""
    enc = header_charset
    if not enc:
        m = _META_CHARSET_RE.search(raw[:4096])
        if m:
            enc = m.group(1).decode("ascii", errors="replace")
    if enc:
        try:
            return raw.decode(enc, errors="replace")
        except (LookupError, ValueError):
            pass
    return raw.decode("utf-8", errors="replace")


# [^<>] and not [^>]: on a body of «<» without «>» the latter rescans to the
# end from every «<» — quadratic, minutes of a stalled event loop for 1 MB.
_TAG_RE = re.compile(r"<[^<>]*>")
_WS_RE = re.compile(r"\s+")          # a str pattern: \s covers U+00A0 (&nbsp;) too


def visible_text(text: str) -> str:
    """What a person reads and types: tags dropped, entities decoded,
    every run of whitespace (non-breaking included) folded to one space."""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip().casefold()


def phrase_found(phrase: str, body: str) -> bool:
    """The phrase as written in the source OR as a visitor sees it; a raw
    match (a stop phrase inside <script>) still counts. A phrase that is
    nothing but markup («<html>», «&nbsp;») has no visible text and is
    looked for in the source only: «'' in anything» would be a silent yes."""
    if phrase.casefold() in body.casefold():
        return True
    visible = visible_text(phrase)
    return bool(visible) and visible in visible_text(body)


async def read_capped(resp: aiohttp.ClientResponse, limit: int) -> bytes:
    """The body up to `limit` bytes. StreamReader.read(n) returns whatever
    is buffered, NOT n bytes: one call hands back a random prefix of the
    page (a missing h1, an empty sitemap) — accumulate explicitly."""
    chunks: list[bytes] = []
    remaining = limit
    while remaining > 0:
        chunk = await resp.content.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


async def gather_checks(urls, check, label: str, **kwargs) -> list:
    """Run `check(url, **kwargs)` for every url concurrently. One site's
    unexpected exception is logged with the site and the traceback and
    dropped instead of killing the whole batch; a shutdown is not swallowed."""
    urls = list(urls)
    results = await asyncio.gather(*(check(u, **kwargs) for u in urls), return_exceptions=True)
    out = []
    for url, r in zip(urls, results, strict=True):
        if isinstance(r, asyncio.CancelledError):
            raise r
        if isinstance(r, BaseException):
            logger.error("%s check of %s crashed", label, site_label(url), exc_info=r)
        else:
            out.append(r)
    return out
