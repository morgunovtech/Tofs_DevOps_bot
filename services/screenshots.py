"""
Page screenshots via an external rendering service — lets the admin eyeball
a page straight from Telegram without bundling a headless browser.

The provider is a URL template (SCREENSHOT_TEMPLATE; «off» — no
screenshots at all). Two response styles are supported:
  * a JSON API à la microlink.io (default) — {"data": {"screenshot": {"url": …}}},
    the image is fetched from that URL;
  * a direct image (thum.io etc.). These often return a tiny "rendering…"
    placeholder on the first hit, so suspiciously small images are retried.

The page address goes to that third party, so an internal address
(utils.net.is_private_target) never does: the service could not open it
anyway, and the owner's LAN is not its business.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import aiohttp

from config import TEMPLATE_OFF, config
from services import public_urls, secrets, sitestatus
from utils.net import is_private_target, owner_connector
from utils.text import err_text

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)  # rendering can be slow
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Direct-image providers return a spinner/placeholder while the real render
# happens in the background. GIFs are always placeholders (real renders are
# PNG/JPEG); anything under ~2KB is treated as an error stub — a real page
# render below that size has not been observed in practice.
PLACEHOLDER_MAX_BYTES = 2 * 1024
RETRIES = 4
RETRY_DELAY_SEC = 8


def _looks_like_placeholder(data: bytes) -> bool:
    if data.startswith(b"GIF8"):  # thum.io's animated "rendering…" spinner
        return True
    return len(data) <= PLACEHOLDER_MAX_BYTES


async def _read_image(resp: aiohttp.ClientResponse) -> bytes | None:
    if resp.status != 200 or not (resp.content_type or "").startswith("image/"):
        return None
    if resp.content_length and resp.content_length > MAX_IMAGE_BYTES:
        return None
    # Stream with a hard cap: without Content-Length a hostile/broken
    # provider could otherwise feed us an unbounded body.
    chunks, total = [], 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            return None
        chunks.append(chunk)
    data = b"".join(chunks)
    return data or None


QUOTA_REASON = "лимит бесплатных снимков на сегодня исчерпан"
OFF_REASON = "снимки выключены (SCREENSHOT_TEMPLATE=off или в нём нет {url})"
PRIVATE_REASON = "это внутренний адрес: сервис снимков его не откроет, и наружу я его не отправляю"


class QuotaExceeded(Exception):
    pass


class ProviderRefused(Exception):
    """A definite «no» from the service (HTTP 4xx, status: fail) — the same
    request would get the same answer, so it is not repeated."""


def _template() -> str | None:
    """SCREENSHOT_TEMPLATE, or None when screenshots are off: empty, «off»,
    or a template without {url}."""
    t = config.screenshot_template.strip()
    if not t or t.lower() in TEMPLATE_OFF:
        return None
    return t if "{url}" in t else None


def enabled() -> bool:
    return _template() is not None


async def _fetch_once(session: aiohttp.ClientSession, api: str) -> bytes | None:
    async with session.get(api) as resp:
        ctype = resp.content_type or ""
        if resp.status == 429:
            raise QuotaExceeded
        if 400 <= resp.status < 500:
            raise ProviderRefused(f"HTTP {resp.status}")
        if ctype.startswith("image/"):
            return await _read_image(resp)
        if resp.status == 200 and "json" in ctype:
            payload = await resp.json()
            if payload.get("status") == "fail":
                message = str(payload.get("message", ""))
                if "rate" in message.lower():
                    raise QuotaExceeded
                raise ProviderRefused(message[:200] or "status: fail")
            shot = (payload.get("data") or {}).get("screenshot") or {}
            img_url = shot.get("url")
            if img_url:
                async with session.get(img_url) as img_resp:
                    return await _read_image(img_resp)
        else:
            logger.warning("Screenshot service HTTP %s (%s)", resp.status, ctype)
    return None


async def fetch_screenshot(url: str) -> tuple[bytes | None, str | None]:
    """(image, reason) — reason explains a None image in plain words."""
    template = _template()
    if template is None:
        return None, OFF_REASON
    if is_private_target(url):
        return None, PRIVATE_REASON
    # replace, not str.format: other braces of a provider's template stay as they are
    api = template.replace("{url}", quote(url, safe=":/"))
    image: bytes | None = None
    # The service's host (SCREENSHOT_TEMPLATE) is the owner's choice — a
    # renderer on localhost keeps working; the image URL in its answer is
    # someone else's and must be public.
    async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=owner_connector(api)) as session:
        for attempt in range(RETRIES):
            try:
                image = await _fetch_once(session, api)
            except QuotaExceeded:
                return None, QUOTA_REASON
            except ProviderRefused as e:
                return None, f"сервис снимков не смог открыть страницу ({err_text(e)})"
            except Exception as e:
                # First hit often times out while the provider renders the
                # page; the result is cached server-side, so retry.
                logger.info("Screenshot attempt %s for %s: %s", attempt + 1, url, err_text(e))
                image = None
            # A GIF or tiny image is the provider's "still rendering"
            # placeholder; give it time and ask again.
            if image and not _looks_like_placeholder(image):
                return image, None
            if attempt < RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY_SEC)
    return None, "сервис снимков не ответил, попробуй через минуту"


# ── The site card's picture ──────────────────────────────────────────────────
#
# The card shows the page above its text instead of behind a «📸» button:
# a screenshot is taken once a day (with the search audit) and kept on the
# bot's disk; the card links to it on the bot's own public address, and
# Telegram draws that link as a picture above the text. Off a public
# address (no domain) there is simply no picture — and then no screenshot
# is taken either: the free quota is not spent on pictures nobody sees.

REFRESH_AFTER = timedelta(hours=20)
# A failed attempt is not repeated at the next look at the card: six hours
# (a parameter — chosen high for the small free quota).
FAILED_RETRY_AFTER = timedelta(hours=6)
# Sites whose picture is being taken right now: one attempt per site at a time.
_inflight: set[int] = set()


def _shots_dir() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "shots")


def shot_path(site_id: int) -> str:
    return os.path.join(_shots_dir(), f"{site_id}.img")


def _sig(site_id: int) -> str:
    """Signed, so the address does not enumerate what the bot watches."""
    key = secrets.shots_key().encode()
    return hmac.new(key, f"shot:{site_id}".encode(), hashlib.sha256).hexdigest()[:16]


_SIG_RE = re.compile(r"[0-9a-f]{16}")


def sig_ok(site_id: int, sig: str) -> bool:
    """Format first, then bytes: a non-ASCII «signature» is a 404, not a 500."""
    return bool(_SIG_RE.fullmatch(sig)) and hmac.compare_digest(_sig(site_id).encode(), sig.encode())


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


async def refresh(site_id: int, url: str) -> bool:
    """Take a fresh picture of the page and keep it for the card. One
    attempt per site at a time; a failure is remembered (is_due)."""
    if site_id in _inflight:
        return False
    _inflight.add(site_id)
    try:
        before = (await sitestatus.get(site_id)).get("shot") or {}
        pic_at = before.get("pic_at") or (before.get("at") if before.get("ok") else None)
        image, reason = await fetch_screenshot(url)
        if not image:
            logger.info("No card picture for %s: %s", url, reason)
            await sitestatus.update(site_id, "shot", ok=False, reason=reason, pic_at=pic_at)
            return False
        os.makedirs(_shots_dir(), exist_ok=True)
        with open(shot_path(site_id), "wb") as f:
            f.write(image)
        await sitestatus.update(site_id, "shot", ok=True, pic_at=sitestatus.now_iso())
        logger.info("Card picture for %s: %d KB", url, len(image) // 1024)
        return True
    finally:
        _inflight.discard(site_id)


async def is_due(site_id: int, url: str) -> bool:
    """Time for a new picture: only when the card can show it (the bot has
    a public address), the address may go to the service (not an internal
    one) and screenshots are on."""
    if not enabled() or not public_urls.is_public() or is_private_target(url):
        return False
    shot = (await sitestatus.get(site_id)).get("shot") or {}
    age = sitestatus.age_minutes(shot)
    if age is None:
        return True
    if shot.get("ok") is False:
        return age > FAILED_RETRY_AFTER.total_seconds() / 60
    return age > REFRESH_AFTER.total_seconds() / 60 or not os.path.exists(shot_path(site_id))


async def card_picture_url(site_id: int) -> str | None:
    """The address Telegram previews above the card, or None. The day in
    the query makes Telegram take a fresh picture after a refresh."""
    if not public_urls.is_public() or not os.path.exists(shot_path(site_id)):
        return None
    shot = (await sitestatus.get(site_id)).get("shot") or {}
    taken = shot.get("pic_at") or shot.get("at")          # older records have no pic_at
    version = str(taken or datetime.now(UTC).date())[:16].replace(":", "").replace("-", "")
    return f"{public_urls.base_url().rstrip('/')}/shot/{_sig(site_id)}/{site_id}?v={version}"
