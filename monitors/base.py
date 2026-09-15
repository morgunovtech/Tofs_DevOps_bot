"""Typed results shared by every monitor, plus the one gather() helper.

`manage=False` on a check means read-only: the check row is still saved
(it feeds uptime and sparklines) but incidents and alert state are NOT
touched, so a menu tap can never consume an alert that the scheduled
monitor should fire.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TypedDict

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    url: str
    site_id: int
    status: str = "ok"                 # ok | warning | critical | error
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
    # Second-opinion verdict: True = up externally, False = confirmed down,
    # None = not checked / verdict service unreachable.
    external_ok: bool | None = None
    # HTTP succeeded but the keyword/stop-phrase check failed.
    keyword_failed: bool = False
    # Hosting guessed from response headers (services.hosting key).
    hosting: str | None = None


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
    skipped: bool = False

    @property
    def reason(self) -> str:
        return str(self.status_code or self.error or "N/A")


@dataclass
class LinksResult(CheckResult):
    total_links: int = 0
    broken_internal: list[LinkCheck] = field(default_factory=list)
    broken_external: list[LinkCheck] = field(default_factory=list)


@dataclass
class DeepResult(CheckResult):
    sampled: int = 0
    errors: list[tuple[str, int]] = field(default_factory=list)  # (url, code)
    skipped: bool = False


@dataclass
class SeoProblem:
    severity: str      # critical | warning
    message: str
    hint: str = ""     # what it means / what to do, in plain words


@dataclass
class SeoResult(CheckResult):
    problems: list[SeoProblem] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    pages_checked: int = 0
    no_js_chars: int | None = None

    @property
    def has_critical(self) -> bool:
        return any(p.severity == "critical" for p in self.problems)


async def gather_checks(coros, label: str) -> list:
    """Run checks concurrently; one site's unexpected exception is logged
    and dropped instead of killing the whole batch."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, BaseException):
            logger.error("%s check failed: %r", label, r)
        else:
            out.append(r)
    return out
