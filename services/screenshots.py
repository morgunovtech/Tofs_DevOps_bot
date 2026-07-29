"""
Page screenshots via an external rendering service (default: thum.io,
keyless) — lets the admin eyeball a page straight from Telegram without
bundling a headless browser into the image.

The provider is a URL template (SCREENSHOT_TEMPLATE) so it can be swapped
for any service that returns an image by URL.
"""

import logging

import aiohttp

from config import config

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=45)  # rendering can be slow
MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def fetch_screenshot(url: str) -> bytes | None:
    api = config.screenshot_template.format(url=url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(api) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"Screenshot service HTTP {resp.status} for {url}")
                    return None
                if not (resp.content_type or "").startswith("image/"):
                    logger.warning(
                        f"Screenshot service returned {resp.content_type} for {url}")
                    return None
                data = await resp.content.read(MAX_IMAGE_BYTES + 1)
                if len(data) > MAX_IMAGE_BYTES:
                    return None
                return data
    except Exception as e:
        logger.warning(f"Screenshot fetch for {url} failed: {e}")
        return None
