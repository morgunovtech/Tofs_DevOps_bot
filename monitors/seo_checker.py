"""
SEO/GEO monitor: is the site visible to search engines and AI agents?

Checks (no API keys required):
  * robots.txt — not blocking Googlebot/YandexBot (critical) or AI crawlers
    (GPTBot, ClaudeBot, PerplexityBot — warning: the owner wants to be seen);
  * sitemap.xml exists and yields pages;
  * sampled pages: <title>/description/canonical/h1/og:*, JSON-LD parses,
    and — the classic silent killer — meta robots noindex / X-Robots-Tag
    (critical: one bad deploy hides the site from every index);
  * AI user-agent probes: a 403 for GPTBot/ClaudeBot usually means a
    Cloudflare "block AI bots" toggle — the site vanishes for AI agents;
  * content available WITHOUT JavaScript (AI crawlers mostly don't run JS);
  * http→https redirect, soft-404 (missing pages must return 404, not 200);
  * llms.txt presence (informational).

Pure analysis helpers (analyze_robots / analyze_page) are separated from
fetching so they can be unit-tested without a network.
"""

import asyncio
import json
import logging
from urllib import robotparser
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup

from config import config
from db.database import (
    get_or_create_site, save_check, save_incident, resolve_incident,
)
from monitors.deep_checker import _sitemap_urls, _pick_sample

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 DevOpsBot/1.0"
)

SEARCH_BOTS = ("Googlebot", "YandexBot")
AI_BOTS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; GPTBot/1.2; +https://openai.com/gptbot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                 "compatible; ClaudeBot/1.0; +claudebot@anthropic.com",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                     "compatible; PerplexityBot/1.0; "
                     "+https://perplexity.ai/perplexitybot",
}

# HTTP codes that mean "deliberately turned away" for a bot probe.
BLOCKED_CODES = {401, 403, 451}


def _problem(severity: str, message: str) -> dict:
    return {"severity": severity, "message": message}


# ── Pure analysis (unit-testable) ────────────────────────────────────────────

def analyze_robots(text: str, base_url: str) -> tuple[list[dict], list[str]]:
    """Check robots.txt rules for search and AI crawlers.
    Returns (problems, sitemap_urls)."""
    problems: list[dict] = []
    rp = robotparser.RobotFileParser()
    rp.parse(text.splitlines())

    probe = base_url.rstrip("/") + "/"
    for agent in SEARCH_BOTS:
        if not rp.can_fetch(agent, probe):
            problems.append(_problem(
                "critical", f"robots.txt запрещает {agent} — сайт закрыт от поиска!"))
    for agent in AI_BOTS:
        if not rp.can_fetch(agent, probe):
            problems.append(_problem(
                "warning", f"robots.txt запрещает {agent} — AI-агенты не увидят сайт"))

    return problems, list(rp.site_maps() or [])


def analyze_page(html: str, page_url: str,
                 headers: dict | None = None) -> tuple[list[dict], list[str], int]:
    """Inspect one page's raw (no-JS) HTML.
    Returns (problems, infos, visible_text_chars)."""
    problems: list[dict] = []
    infos: list[str] = []
    path = urlparse(page_url).path or "/"
    headers = {k.lower(): v for k, v in (headers or {}).items()}

    # X-Robots-Tag: noindex hides the page at the HTTP layer.
    xrobots = headers.get("x-robots-tag", "")
    if "noindex" in xrobots.lower():
        problems.append(_problem(
            "critical", f"{path}: заголовок X-Robots-Tag noindex — страница скрыта от индексации!"))

    soup = BeautifulSoup(html, "html.parser")

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta and "noindex" in (robots_meta.get("content") or "").lower():
        problems.append(_problem(
            "critical", f"{path}: meta robots noindex — страница скрыта от индексации!"))

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        problems.append(_problem("warning", f"{path}: нет <title>"))
    elif not (10 <= len(title) <= 70):
        infos.append(f"{path}: длина title {len(title)} (рекомендуется 10–70)")

    desc = soup.find("meta", attrs={"name": "description"})
    desc_text = (desc.get("content") or "").strip() if desc else ""
    if not desc_text:
        problems.append(_problem("warning", f"{path}: нет meta description"))
    elif not (50 <= len(desc_text) <= 170):
        infos.append(f"{path}: длина description {len(desc_text)} (рекомендуется 50–170)")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical:
        href = (canonical.get("href") or "").strip()
        c, p = urlparse(href), urlparse(page_url)
        if not href.startswith("https://"):
            problems.append(_problem("warning", f"{path}: canonical не https ({href[:60]})"))
        elif c.hostname != p.hostname:
            problems.append(_problem(
                "warning", f"{path}: canonical указывает на другой хост ({c.hostname})"))
    else:
        infos.append(f"{path}: нет canonical")

    if not soup.find("h1"):
        infos.append(f"{path}: нет <h1>")

    if path == "/":
        if not soup.find("meta", attrs={"property": "og:title"}):
            infos.append("нет og:title (превью в соцсетях/мессенджерах)")
        if not soup.find("meta", attrs={"property": "og:image"}):
            infos.append("нет og:image (превью в соцсетях/мессенджерах)")
        html_tag = soup.find("html")
        if html_tag and not html_tag.get("lang"):
            infos.append("нет атрибута lang у <html>")

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            json.loads(script.string or "")
        except (ValueError, TypeError):
            problems.append(_problem("warning", f"{path}: JSON-LD не парсится"))
            break

    # Visible text without JS — what AI crawlers actually see.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_len = len(" ".join(soup.get_text(separator=" ").split()))

    return problems, infos, text_len


# ── Fetch + orchestrate ──────────────────────────────────────────────────────

async def _fetch(session: aiohttp.ClientSession, url: str,
                 ua: str = BROWSER_UA) -> tuple[int | None, str, dict]:
    """(status, body_text, headers) — status None on network error."""
    try:
        async with session.get(
            url, timeout=TIMEOUT, allow_redirects=True,
            headers={"User-Agent": ua},
        ) as resp:
            body = await resp.text(errors="replace")
            return resp.status, body, dict(resp.headers)
    except Exception as e:
        logger.debug(f"seo: fetch {url} failed: {e}")
        return None, "", {}


async def check_seo(url: str) -> dict:
    site_id = await get_or_create_site(url)
    base = url.rstrip("/")
    host = urlparse(url).hostname or ""

    result = {
        "url": url,
        "status": "ok",
        "problems": [],      # [{severity, message}] — alertable
        "infos": [],         # observations, shown only in the on-demand audit
        "pages_checked": 0,
        "no_js_chars": None,
        "incident_new": False,
        "recovered": False,
    }
    problems: list[dict] = result["problems"]
    infos: list[str] = result["infos"]

    async with aiohttp.ClientSession() as session:
        # robots.txt
        status, robots_text, _ = await _fetch(session, f"{base}/robots.txt")
        if status == 200:
            p, _sitemaps = analyze_robots(robots_text, base)
            problems.extend(p)
        elif status == 404:
            infos.append("robots.txt отсутствует (всё разрешено — не критично)")
        elif status is not None:
            problems.append(_problem("warning", f"robots.txt отвечает HTTP {status}"))

        # sitemap + page sample
        pages = await _sitemap_urls(session, base)
        if not pages:
            problems.append(_problem("warning", "sitemap.xml не найден или пуст"))
        sample = [base + "/"]
        sample += _pick_sample(
            [p for p in pages if p.rstrip("/") != base],
            max(0, config.seo_pages_sample - 1),
        )
        for page in sample:
            status, html, headers = await _fetch(session, page)
            if status != 200 or not html:
                continue  # availability problems are another monitor's job
            result["pages_checked"] += 1
            p, i, text_len = analyze_page(html, page, headers)
            problems.extend(p)
            infos.extend(i)
            if page == sample[0]:
                result["no_js_chars"] = text_len
                if text_len < config.seo_min_text_chars:
                    problems.append(_problem(
                        "warning",
                        f"без JavaScript на главной всего {text_len} символов "
                        f"текста — AI-краулеры и часть скрейперов видят почти "
                        f"пустую страницу"))

        # AI user-agent probes: blocked = invisible to AI agents.
        for bot, ua in AI_BOTS.items():
            status, _, _ = await _fetch(session, base + "/", ua=ua)
            if status in BLOCKED_CODES:
                problems.append(_problem(
                    "warning",
                    f"{bot} получает HTTP {status} — похоже, включена "
                    f"блокировка AI-ботов (Cloudflare?)"))
            await asyncio.sleep(0.3)

        # Soft-404: a missing page must answer 404, or indexes fill with junk.
        status, _, _ = await _fetch(session, f"{base}/__devopsbot-404-probe")
        if status == 200:
            problems.append(_problem(
                "warning", "несуществующие страницы отдают HTTP 200 (soft-404)"))

        # http → https
        try:
            async with session.get(
                f"http://{host}/", timeout=TIMEOUT, allow_redirects=False,
                headers={"User-Agent": BROWSER_UA},
            ) as resp:
                loc = resp.headers.get("Location", "")
                if not (300 <= resp.status < 400 and loc.startswith("https://")):
                    problems.append(_problem(
                        "warning", f"http:// не редиректит на https (HTTP {resp.status})"))
        except Exception:
            infos.append("http://-версия недоступна (порт 80 закрыт — ок)")

        # llms.txt — emerging convention for LLM crawlers.
        status, _, _ = await _fetch(session, f"{base}/llms.txt")
        infos.append("llms.txt: есть ✅" if status == 200
                     else "llms.txt: нет (опционально, помогает AI-агентам)")

    has_critical = any(p["severity"] == "critical" for p in problems)
    if problems:
        result["status"] = "critical" if has_critical else "warning"

    summary = (f"{len(problems)} проблем: "
               + "; ".join(p["message"] for p in problems[:3])
               if problems else
               f"OK, {result['pages_checked']} страниц проверено")
    await save_check(site_id, "seo", result["status"], details=summary[:500])

    if problems:
        _, is_new = await save_incident(
            site_id, "seo",
            f"SEO: {len(problems)} проблем(ы), напр.: {problems[0]['message']}"[:300],
            severity="critical" if has_critical else "warning",
        )
        result["incident_new"] = is_new
    else:
        if await resolve_incident(site_id, "seo"):
            result["recovered"] = True

    return result


async def check_all_seo(urls: list[str]) -> list[dict]:
    return await asyncio.gather(*[check_seo(u) for u in urls])
