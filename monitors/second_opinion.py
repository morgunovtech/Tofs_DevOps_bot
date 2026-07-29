"""
Second-opinion availability check via check-host.net (free, keyless).

Before paging about "site down" we ask external nodes to fetch the URL.
Three-valued result:
    True  — at least one external node reached the site (problem is likely
            on the bot's side of the network, don't page)
    False — external nodes can't reach it either (confirmed down)
    None  — the verdict service itself was unreachable (fail open: alert,
            but mark as unconfirmed)
"""

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

API = "https://check-host.net"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


async def second_opinion_up(url: str) -> bool | None:
    try:
        async with aiohttp.ClientSession(
            timeout=_TIMEOUT, headers={"Accept": "application/json"},
        ) as session:
            async with session.get(
                f"{API}/check-http", params={"host": url, "max_nodes": 3},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            request_id = data.get("request_id")
            if not request_id:
                return None

            # Nodes report asynchronously; poll a few times.
            for _ in range(4):
                await asyncio.sleep(4)
                async with session.get(f"{API}/check-result/{request_id}") as resp:
                    if resp.status != 200:
                        continue
                    results = await resp.json()
                verdicts = []
                for node_result in results.values():
                    if node_result is None:
                        continue  # node still working
                    # Success shape: [[1, 0.12, "OK", "200", "1.2.3.4"]]
                    try:
                        first = node_result[0]
                        verdicts.append(bool(first and first[0] == 1))
                    except (TypeError, IndexError, KeyError):
                        continue
                if verdicts:
                    return any(verdicts)
            return None
    except Exception as e:
        logger.warning(f"Second opinion for {url} failed: {e}")
        return None
