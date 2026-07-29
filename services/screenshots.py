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
import logging
from urllib.parse import quote

import aiohttp

from config import config

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)  # rendering can be slow
MAX_IMAGE_BYTES = 10 * 1024 * 1024
# Direct-image providers return a spinner/placeholder while the real render
# happens in the background. Placeholders are animated GIFs (thum.io) or
# near-empty stubs; a small PNG can be a perfectly legitimate render of a
# minimal page, so size alone must not disqualify an image.
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
    # NB: resp.read(), not resp.content.read(n) — the latter returns only
    # the first available chunk (~8KB) and silently truncates the image.
    data = await resp.read()
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None
    return data


async def _fetch_once(session: aiohttp.ClientSession, api: str) -> bytes | None:
    async with session.get(api) as resp:
        ctype = resp.content_type or ""
        if ctype.startswith("image/"):
            return await _read_image(resp)
        if resp.status == 200 and "json" in ctype:
            payload = await resp.json()
            shot = (payload.get("data") or {}).get("screenshot") or {}
            img_url = shot.get("url")
            if img_url:
                async with session.get(img_url) as img_resp:
                    return await _read_image(img_resp)
        else:
            logger.warning(f"Screenshot service HTTP {resp.status} ({ctype})")
    return None


async def fetch_screenshot(url: str) -> bytes | None:
    api = config.screenshot_template.format(url=quote(url, safe=":/"))
    image: bytes | None = None
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        for attempt in range(RETRIES):
            try:
                image = await _fetch_once(session, api)
            except Exception as e:
                # First hit often times out while the provider renders the
                # page; the result is cached server-side, so retry.
                logger.info(f"Screenshot attempt {attempt + 1} for {url}: {e}")
                image = None
            # A GIF or tiny image is the provider's "still rendering"
            # placeholder; give it time and ask again.
            if image and not _looks_like_placeholder(image):
                return image
            if attempt < RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY_SEC)
    if image and _looks_like_placeholder(image):
        return None  # a spinner is worse than an honest error
    return image
