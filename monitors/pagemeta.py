"""Small look at a page when a site is added: a phrase worth watching
(title or h1) and whether the www / non-www twin behaves."""

import aiohttp
from bs4 import BeautifulSoup

from utils.urls import USER_AGENT, host_of, registrable_domain

TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)
MAX_BYTES = 512 * 1024


async def suggest_keyword(url: str) -> str | None:
    """h1 if it is a sensible phrase, else the title — 3..60 chars — or None."""
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as s:
            async with s.get(url, allow_redirects=True) as resp:
                if resp.status != 200 or "html" not in (resp.content_type or ""):
                    return None
                raw = await resp.content.read(MAX_BYTES)
    except Exception:
        return None
    soup = BeautifulSoup(raw.decode("utf-8", errors="replace"), "html.parser")
    candidates = []
    h1 = soup.find("h1")
    if h1:
        candidates.append(" ".join(h1.get_text(" ", strip=True).split()))
    if soup.title:
        candidates.append(" ".join(soup.title.get_text(" ", strip=True).split()))
    for text in candidates:
        if 3 <= len(text) <= 60 and not text.lower().startswith(("http", "index", "untitled", "document")):
            return text
    return None


async def alt_host_note(url: str) -> str | None:
    """One line about the www twin, or None when everything is as expected."""
    host = host_of(url)
    if not host or host.count(".") == 0:
        return None
    alt = host[4:] if host.startswith("www.") else f"www.{host}"
    if registrable_domain(alt) != registrable_domain(host) or alt.count(".") < 1:
        return None
    alt_url = f"https://{alt}/"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as s:
            async with s.get(alt_url, allow_redirects=False) as resp:
                if 300 <= resp.status < 400:
                    return None                       # redirects to the main version — fine
                if resp.status == 200:
                    return (f"ℹ️ {alt} открывается как отдельный сайт, а не перенаправляет на {host}. "
                            f"Поисковики могут считать это двумя сайтами; обычно настраивают редирект.")
    except Exception:
        return None                                   # no twin — nothing to say
    return None
