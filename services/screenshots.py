"""
Page screenshots via an external rendering service — lets the admin eyeball
a page straight from Telegram without bundling a headless browser.

The provider is a URL template (SCREENSHOT_TEMPLATE). Two response styles
are supported:
  * a JSON API à la microlink.io (default) — {"data": {"screenshot": {"url": …}}},
    the image is fetched from that URL;
  * a direct image (thum.io etc.). These often return a tiny "rendering…"
    placeholder on the first hit, so suspiciously small images are retried.
"""

import asyncio
import hashlib
import hmac
import logging
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import aiohttp

from config import config
from services import public_urls, secrets, sitestatus

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


QUOTA_REASON = "лимит бесплатных снимков на сегодня исчерпан (около 50 в день)"


class QuotaExceeded(Exception):
    pass


async def _fetch_once(session: aiohttp.ClientSession, api: str) -> bytes | None:
    async with session.get(api) as resp:
        ctype = resp.content_type or ""
        if resp.status == 429:
            raise QuotaExceeded
        if ctype.startswith("image/"):
            return await _read_image(resp)
        if resp.status == 200 and "json" in ctype:
            payload = await resp.json()
            if payload.get("status") == "fail" and "rate" in str(payload.get("message", "")).lower():
                raise QuotaExceeded
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
    api = config.screenshot_template.format(url=quote(url, safe=":/"))
    image: bytes | None = None
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for attempt in range(RETRIES):
            try:
                image = await _fetch_once(session, api)
            except QuotaExceeded:
                return None, QUOTA_REASON
            except Exception as e:
                # First hit often times out while the provider renders the
                # page; the result is cached server-side, so retry.
                logger.info("Screenshot attempt %s for %s: %s", attempt + 1, url, e)
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
# address (no domain) there is simply no picture.

REFRESH_AFTER = timedelta(hours=20)


def _shots_dir() -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "shots")


def shot_path(site_id: int) -> str:
    return os.path.join(_shots_dir(), f"{site_id}.img")


def _sig(site_id: int) -> str:
    """Signed, so the address does not enumerate what the bot watches."""
    key = secrets.heartbeat_secret().encode()
    return hmac.new(key, f"shot:{site_id}".encode(), hashlib.sha256).hexdigest()[:16]


def sig_ok(site_id: int, sig: str) -> bool:
    return hmac.compare_digest(_sig(site_id), sig)


def image_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


async def refresh(site_id: int, url: str) -> bool:
    """Take a fresh picture of the page and keep it for the card."""
    image, reason = await fetch_screenshot(url)
    if not image:
        logger.info("No card picture for %s: %s", url, reason)
        return False
    os.makedirs(_shots_dir(), exist_ok=True)
    with open(shot_path(site_id), "wb") as f:
        f.write(image)
    await sitestatus.update(site_id, "shot", ok=True)
    return True


async def is_due(site_id: int) -> bool:
    shot = (await sitestatus.get(site_id)).get("shot") or {}
    age = sitestatus.age_minutes(shot)
    return age is None or age > REFRESH_AFTER.total_seconds() / 60 or not os.path.exists(shot_path(site_id))


async def card_picture_url(site_id: int) -> str | None:
    """The address Telegram previews above the card, or None. The day in
    the query makes Telegram take a fresh picture after a refresh."""
    if not public_urls.is_public() or not os.path.exists(shot_path(site_id)):
        return None
    shot = (await sitestatus.get(site_id)).get("shot") or {}
    version = str(shot.get("at") or datetime.now(UTC).date())[:16].replace(":", "").replace("-", "")
    return f"{public_urls.base_url().rstrip('/')}/shot/{_sig(site_id)}/{site_id}?v={version}"
