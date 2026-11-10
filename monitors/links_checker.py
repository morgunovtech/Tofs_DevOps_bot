"""Broken-link crawl of one page. A link that does not open is a dead end
for the visitor wherever it points, and the owner can always fix it (a new
address, an archive copy, or remove it) — so own and other sites' links are
one list and one 🟠 problem.

What decides is how sure the answer is, not the domain:
  * definite — a 4xx (page gone), a domain that does not resolve, a bad
    certificate: a problem at once;
  * maybe temporary — a timeout, a 5xx, a refused connection: a problem
    only when the same link still fails CONFIRM_AFTER after its first
    failure. Time, not «the next crawl»: every bot start runs a crawl, and
    on 09.11.2026 six deploys in twenty minutes turned a GitHub incident
    (504 for anonymous visitors) into two confirmed «broken» links;
  * a bot block (401/403/429…, ALWAYS_OK_HOSTS) is not an answer at all.

The page is written by its author and the far ends belong to strangers, so
a crawl is bounded: a window of MAX_LINKS links per crawl (the next crawl
takes the next window), PER_HOST requests to a foreign host at a time and
LINK_SLOTS foreign requests in flight over all crawls, names resolved in
the crawl's own thread pool. Links once found broken are rechecked in every
crawl on top of the window, and the problem closes only when the windows
have gone a full round without one.
"""

import asyncio
import contextlib
import json
import logging
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from db.database import get_or_create_site, get_state, resolve_incident, save_check, save_incident, set_state
from monitors.base import LinkCheck, LinksResult, decode_body, gather_checks, read_capped
from services import sitestatus
from utils.net import CrawlResolver, owner_connector, refused_private
from utils.text import parse_iso_utc
from utils.urls import USER_AGENT, host_of, same_site

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
# A maybe-temporary failure must outlive a typical outage on the far end
# before it is called broken. Shorter than the 6-hour crawl interval, so a
# scheduled crawl always confirms; longer than any burst of restarts.
CONFIRM_AFTER = timedelta(hours=3)
MAX_PAGE_BYTES = 2 * 1024 * 1024
# One crawl checks at most MAX_LINKS links (own site first) on at most
# MAX_HOSTS foreign hosts; the next crawl takes the next window, so a long
# page is covered in turns. The site's own links go OWN_SLOTS at a time, as
# before; a foreign host sees PER_HOST requests at once over all crawls
# together, and all crawls together keep LINK_SLOTS foreign requests in
# flight.
MAX_LINKS = 300
MAX_HOSTS = 50
PER_HOST = 2
LINK_SLOTS = 16
OWN_SLOTS = 8
# getaddrinfo errors that mean «no such name»; EAI_AGAIN is «could not ask».
_NAME_GONE = {socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME)}
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru,en;q=0.9",
}
# Codes that look like errors but mean "bot blocked / method not allowed /
# range not satisfiable" rather than "the resource is broken". 503 is not
# one of them: a fallen application answers it too (a bot-check page says
# so in a header — see _challenge).
BENIGN_STATUS_CODES = {401, 403, 405, 416, 429}
# Hosts known to block automated requests — assume they work.
ALWAYS_OK_HOSTS = {
    "linkedin.com", "www.linkedin.com", "facebook.com", "www.facebook.com",
    "m.facebook.com", "twitter.com", "x.com", "www.twitter.com",
    "instagram.com", "www.instagram.com", "t.me", "telegram.me",
    "youtube.com", "www.youtube.com", "youtu.be",
}
# Tracking / analytics — skip entirely.
SKIP_HOSTS = {
    "google-analytics.com", "www.google-analytics.com",
    "googletagmanager.com", "www.googletagmanager.com", "doubleclick.net",
    "mc.yandex.ru", "facebook.net", "connect.facebook.net",
}
# /cdn-cgi/ is Cloudflare's internal namespace: these intentionally 404 on
# direct requests and can't be fixed by the site owner anyway.
SKIP_PATH_PREFIXES = ("/cdn-cgi/",)


class _HostSlots:
    """At most PER_HOST requests to one foreign host at a time, over all
    crawls; a host's semaphore lives only while someone holds or waits for it."""

    def __init__(self) -> None:
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._users: dict[str, int] = {}

    @contextlib.asynccontextmanager
    async def hold(self, host: str):
        self._users[host] = self._users.get(host, 0) + 1
        sem = self._sems.setdefault(host, asyncio.Semaphore(PER_HOST))
        try:
            async with sem:
                yield
        finally:
            self._users[host] -= 1
            if not self._users[host]:
                del self._users[host], self._sems[host]


_loop_state: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore, _HostSlots, dict[int, asyncio.Lock]] | None = None


def _shared() -> tuple[asyncio.Semaphore, _HostSlots, dict[int, asyncio.Lock]]:
    """The foreign-link slots for all crawls, the per-host slots and one
    lock per site — per event loop (every test runs its own)."""
    global _loop_state
    loop = asyncio.get_running_loop()
    if _loop_state is None or _loop_state[0] is not loop:
        _loop_state = (loop, asyncio.Semaphore(LINK_SLOTS), _HostSlots(), {})
    return _loop_state[1], _loop_state[2], _loop_state[3]


def pick_window(links: list[str], site_url: str, offset: int) -> tuple[list[str], int]:
    """This crawl's share: at most MAX_LINKS links on at most MAX_HOSTS
    foreign hosts, own-site links first, starting at `offset`. Returns
    (links, offset for the next crawl; 0 — everything fits in one crawl)."""
    ordered = [u for u in links if same_site(u, site_url)] + [u for u in links if not same_site(u, site_url)]
    if not ordered:
        return [], 0
    start = offset % len(ordered)
    picked: list[str] = []
    hosts: set[str] = set()
    next_at: int | None = None
    for i, u in enumerate(ordered[start:] + ordered[:start]):
        if len(picked) >= MAX_LINKS:
            next_at = i if next_at is None else next_at
            break
        if not same_site(u, site_url):
            host = host_of(u)
            if host not in hosts and len(hosts) >= MAX_HOSTS:
                next_at = i if next_at is None else next_at
                continue
            hosts.add(host)
        picked.append(u)
    return picked, 0 if next_at is None else start + next_at


async def _fetch_page(session: aiohttp.ClientSession, url: str) -> tuple[str, str] | None:
    """(html, the page's final address after redirects): relative links are
    resolved from where the page actually is, as a browser does."""
    try:
        async with session.get(url, timeout=TIMEOUT, allow_redirects=True,
                               headers=DEFAULT_HEADERS) as resp:
            if resp.status == 200 and "text/html" in (resp.content_type or ""):
                return decode_body(await read_capped(resp, MAX_PAGE_BYTES), resp.charset), str(resp.url)
    except Exception as e:
        logger.debug("Could not fetch %s: %s", url, e)
    return None


def _challenge(resp: aiohttp.ClientResponse) -> bool:
    """A bot-check page (Cloudflare marks it with this header, whatever the
    status code): the link is there, it is the bot that is not let in."""
    return "challenge" in resp.headers.get("cf-mitigated", "").lower()


async def _check_link(session: aiohttp.ClientSession, url: str) -> LinkCheck:
    """GET with Range: bytes=0-0 (cheap but real GET behaviour), one retry
    on timeout/5xx. The certificate is verified on every link: a visitor's
    browser stops at a bad one wherever the link points."""
    host = host_of(url)
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return LinkCheck(url=url, status_code=None, ok=False, error=MALFORMED, definite=True)
    if (host in SKIP_HOSTS or host in ALWAYS_OK_HOSTS
            or any(path.startswith(p) for p in SKIP_PATH_PREFIXES)):
        return LinkCheck(url=url, status_code=None, ok=True)

    headers = {**DEFAULT_HEADERS, "Range": "bytes=0-0"}

    async def attempt() -> LinkCheck:
        try:
            async with session.get(url, timeout=TIMEOUT, allow_redirects=True, headers=headers) as resp:
                ok = resp.status < 400 or resp.status in BENIGN_STATUS_CODES or _challenge(resp)
                return LinkCheck(url=url, status_code=resp.status, ok=ok)
        except TimeoutError:
            return LinkCheck(url=url, status_code=None, ok=False, error="timeout")
        except aiohttp.ClientConnectorCertificateError as e:
            return LinkCheck(url=url, status_code=None, ok=False, error=str(e), definite=True)
        except aiohttp.ClientConnectorDNSError as e:
            if refused_private(e):
                # A link into the bot's own network is not checked — and not broken.
                return LinkCheck(url=url, status_code=None, ok=True, error="internal")
            # By the code, not the words: EAI_NONAME is «no such domain»,
            # EAI_AGAIN «the DNS did not answer» — that one may pass.
            code = getattr(e.os_error, "errno", None)
            return LinkCheck(url=url, status_code=None, ok=False, error=str(e) or type(e).__name__,
                             definite=code in _NAME_GONE)
        except Exception as e:
            if refused_private(e):
                return LinkCheck(url=url, status_code=None, ok=True, error="internal")
            return LinkCheck(url=url, status_code=None, ok=False, error=str(e) or type(e).__name__)

    result = await attempt()
    transient = not result.ok and (result.status_code is None or result.status_code >= 500)
    if transient:
        await asyncio.sleep(0.8)
        result = await attempt()
    return result


MALFORMED = "адрес записан с ошибкой"


def extract_links(html: str, base_url: str) -> list[str]:
    """Anchor and resource links (a/link/script/img) resolved to absolute
    URLs the way a browser does: against the page's own address and its
    <base href>, spaces around the href trimmed, #fragments dropped."""
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    if base_tag:
        with contextlib.suppress(ValueError):
            base_url = urljoin(base_url, str(base_tag["href"]).strip())
    links = set()
    for tag in soup.find_all(["a", "link", "script", "img"]):
        # preconnect / dns-prefetch name an origin, not a page: its root may
        # well answer 404 and is not a link anyone follows.
        if tag.name == "link" and {"preconnect", "dns-prefetch"} & set(tag.get("rel") or []):
            continue
        href = str(tag.get("href") or tag.get("src") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        try:
            full = urldefrag(urljoin(base_url, href)).url
            scheme = urlparse(full).scheme
        except ValueError:
            links.add(href[:300])          # «https://example.com]/post»: a dead end for a visitor too
            continue
        if scheme in ("http", "https"):
            links.add(full)
    return sorted(links)


# The words of a «no such name» error for a check that kept no error code;
# «temporary failure in name resolution» is not among them: the DNS did
# not answer, which is not the domain's disappearance.
_DNS_GONE = ("name or service not known", "nodename nor servname", "no address associated",
             "name does not resolve")


def is_definite(c: LinkCheck) -> bool:
    """The far end answered for sure: the page or the domain is gone, or
    its certificate is bad. Timeouts and 5xx may pass by themselves."""
    if c.definite is not None:
        return c.definite
    if c.status_code is not None:
        return 400 <= c.status_code < 500
    if c.error == MALFORMED:
        return True
    err = (c.error or "").lower()
    return "certificate" in err or any(s in err for s in _DNS_GONE)


async def check_links(url: str, manage: bool = True) -> LinksResult:
    site_id = await get_or_create_site(url)
    slots, hosts, locks = _shared()
    async with locks.setdefault(site_id, asyncio.Lock()):    # one crawl of a site at a time
        return await _crawl(url, site_id, manage, slots, hosts)


async def _crawl(url: str, site_id: int, manage: bool, slots: asyncio.Semaphore, hosts: _HostSlots) -> LinksResult:
    r = LinksResult(url=url, site_id=site_id)
    connector = owner_connector(url, limit=OWN_SLOTS + LINK_SLOTS, resolver=CrawlResolver())
    async with aiohttp.ClientSession(connector=connector) as session:
        fetched = await _fetch_page(session, url)
        if not fetched:
            r.status, r.error, r.transient = "error", "Could not fetch page", True
            await save_check(site_id, "links", "error", details=r.error)
            await sitestatus.update(site_id, "links", status="error", broken=0, pending=0)
            return r
        html, final_url = fetched
        links = extract_links(html, final_url)
        on_page = set(links)
        r.total_links = len(links)
        offset = _count(await get_state(f"links_offset:{site_id}"))
        picked, next_offset = pick_window(links, url, offset)
        # Links found broken before are rechecked in every crawl, on top of
        # the window, as long as the page still has them: the open problem
        # is about them, and a fix must be seen, not assumed.
        known, in_window = _known_broken(await get_state(f"links_broken:{site_id}")), set(picked)
        checked = picked + [u for u in known if u in on_page and u not in in_window]
        r.checked = len(checked)
        own = asyncio.Semaphore(OWN_SLOTS)

        async def guarded(link: str) -> LinkCheck:
            if same_site(link, url):
                async with own:
                    return await _check_link(session, link)
            async with hosts.hold(host_of(link)), slots:
                return await _check_link(session, link)

        failed = [c for c in await asyncio.gather(*(guarded(link) for link in checked)) if not c.ok]

    # A maybe-temporary failure becomes a problem when it still fails
    # CONFIRM_AFTER after the first time; {url: first failure} is alert
    # state, kept by the scheduler only.
    key = f"links_suspect:{site_id}"
    now = datetime.now(UTC)
    suspects = _suspects(await get_state(key), now)
    flaky = [c for c in failed if not is_definite(c)]
    for c in failed:
        since = parse_iso_utc(suspects.get(c.url))
        confirmed = since is not None and now - since >= CONFIRM_AFTER
        (r.broken if is_definite(c) or confirmed else r.pending).append(c)
    # The windows have gone a full round without a broken link — or the
    # whole page fits in one crawl: only then is «the links are fine» known
    # about every link, not about this window. Scheduled crawls move the
    # window and keep the count; a tap checks the window the next crawl
    # would, so it counts toward the round without being written down.
    covered = 0 if r.broken else _count(await get_state(f"links_clean:{site_id}"))
    advance = next_offset - offset % r.total_links if next_offset else r.total_links
    full_round = not next_offset or covered + advance >= r.total_links
    if manage:
        # Links outside this window keep the time of their first failure.
        was_checked = set(checked)
        state = {u: t for u, t in suspects.items() if u in on_page and u not in was_checked}
        state |= {c.url: suspects.get(c.url) or now.isoformat() for c in flaky}
        await set_state(key, json.dumps(state, sort_keys=True) if state else None)
        await set_state(f"links_offset:{site_id}", str(next_offset) if next_offset else None)
        await set_state(f"links_broken:{site_id}",
                        json.dumps(sorted(c.url for c in r.broken)) if r.broken else None)
        await set_state(f"links_clean:{site_id}",
                        None if r.broken or full_round else str(covered + advance))

    if r.broken:
        r.status = "warning"
        preview = "\n".join(f"  - {b.url} ({b.reason})" for b in r.broken[:10])
        r.error = f"{len(r.broken)} broken link(s):\n{preview}"
    details = r.error or (f"All {r.checked} of {r.total_links} links OK"
                          + (f" ({len(r.pending)} failed once, rechecking)" if r.pending else ""))
    await save_check(site_id, "links", r.status, details=details)
    await sitestatus.update(site_id, "links", status=r.status, broken=len(r.broken),
                            pending=len(r.pending), total=r.total_links,
                            # the card lists them — no separate «🔗 Ссылки» screen to tap
                            items=[{"url": b.url, "code": b.status_code, "error": b.error} for b in r.broken[:5]])
    if not manage:
        # Read-only, with one exception (as for search): a clean check — no
        # dead link, nothing waiting for a recheck — closes the open links
        # problem. The screen says «все открываются»; the list must agree.
        if r.status == "ok" and not r.pending and full_round:
            await resolve_incident(site_id, "links")
        return r
    if r.broken:
        r.incident_id, r.incident_new = await save_incident(
            site_id, "links", f"{len(r.broken)} broken link(s) found on {url}", severity="warning")
    elif full_round:
        closed = await resolve_incident(site_id, "links")
        r.recovered, r.resolved_incident = closed is not None, closed
    return r


def _count(raw: str | None) -> int:
    try:
        return max(0, int(raw or 0))
    except ValueError:
        return 0


def _known_broken(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    return [u for u in data if isinstance(u, str)] if isinstance(data, list) else []


def _suspects(raw: str | None, now: datetime) -> dict[str, str]:
    """{url: first failure, ISO}. The list kept before 11.2026 had no times:
    its links start the clock now rather than count as confirmed."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return {}
    if isinstance(data, list):
        return {u: now.isoformat() for u in data if isinstance(u, str)}
    return {u: t for u, t in data.items() if isinstance(u, str) and isinstance(t, str)} \
        if isinstance(data, dict) else {}


async def check_all_links(urls: list[str], manage: bool = True) -> list[LinksResult]:
    return await gather_checks(urls, check_links, "links", manage=manage)
