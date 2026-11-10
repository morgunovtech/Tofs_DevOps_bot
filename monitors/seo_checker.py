"""SEO/GEO monitor: is the site open to search engines and AI agents?

Checks (no API keys required):
  * robots.txt — not blocking Googlebot/YandexBot (critical) or AI crawlers
    (GPTBot, ClaudeBot, PerplexityBot — warning);
  * sitemap.xml exists and yields pages;
  * sampled pages: meta robots noindex / X-Robots-Tag (critical);
    <title>/description present and of a sane length, canonical points
    home, h1, og:title/og:image and lang on the homepage, JSON-LD parses;
  * AI user-agent probes: the homepage asked again with the GPTBot /
    ClaudeBot / PerplexityBot signature. A refusal there, after the plain
    request passed, is a block by signature — the real bots send the same
    one. What this probe cannot see is a block by address: Cloudflare and
    others recognise the real crawlers by the IPs they come from, and this
    request does not come from OpenAI's. The wording says «a request with
    the signature», never «what GPTBot sees»;
  * content available WITHOUT JavaScript (AI crawlers mostly don't run JS);
  * http→https redirect, soft-404.

Every finding is something the owner can act on; there is no «info» level.
What the bot measured but found in order (pages checked, text without
JavaScript) is a caption on the screen, not a list entry. Whether the
site is actually IN an index only Search Console / Webmaster know — that
lives in services.index_status, never in this monitor's wording.

Pure analysis helpers (analyze_robots / analyze_page) are separated from
fetching so they can be unit-tested without a network.
"""

import asyncio
import json
import logging
import re
from urllib.parse import unquote, urlparse

import aiohttp
from bs4 import BeautifulSoup

from config import config
from db.database import get_open_incident, get_or_create_site, resolve_incident, save_check, save_incident
from monitors.base import SeoProblem, SeoResult, decode_body, gather_checks, read_capped
from monitors.security_checker import plain_http_opens
from monitors.sitemap import pick_sample, sitemap_urls
from services import seo_fixes, sitestatus
from utils.text import plural
from utils.urls import USER_AGENT, bare_host

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_PAGE_BYTES = 2 * 1024 * 1024
SEARCH_BOTS = ("Googlebot", "YandexBot")
# robots.txt groups a crawler obeys, most specific first: its own group beats
# «*», «YandexBot» beats «Yandex». A name is compared whole, case-insensitively,
# never as a substring.
_GROUP_NAMES = {"Googlebot": ("googlebot",), "YandexBot": ("yandexbot", "yandex")}
AI_BOTS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; GPTBot/1.2; +https://openai.com/gptbot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                 "compatible; ClaudeBot/1.0; +claudebot@anthropic.com",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                     "compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot",
}
BLOCKED_CODES = {401, 403, 451}
# A crawler refused by Cloudflare's pay-per-crawl gets 402 Payment Required —
# for the AI probe that is a refusal too (the homepage probe keeps the set
# above: a 402 to a plain request is not a block by signature).
AI_REFUSED_CODES = BLOCKED_CODES | {402}


def _p(code: str, severity: str, message: str) -> SeoProblem:
    return SeoProblem(code, severity, message)


def _page(path: str, limit: int = 60) -> str:
    """The page as the owner reads it: «/о-нас», not «/%D0%BE-%D0%BD%D0%B0%D1%81»."""
    if path in ("", "/"):
        return "главная"
    text = unquote(path)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _key(url: str) -> tuple[str, str]:
    """One page however a sitemap spells it: the scheme and «www.» do not matter."""
    try:
        p = urlparse(url)
        return bare_host((p.hostname or "").lower()), (p.path.rstrip("/") or "/")
    except ValueError:
        return "", url


# ── Pure analysis (unit-testable) ────────────────────────────────────────────

# noindex in every form it is written: meta robots / googlebot / yandex in any
# case, several of them, «none»; X-Robots-Tag with or without a crawler name.
_NOINDEX = {"noindex", "none"}
_OUR_AGENTS = {"robots", "googlebot", "yandex"}        # whose «noindex» takes the site out of search
_VALUED = {"unavailable_after", "max-snippet", "max-image-preview", "max-video-preview"}
_AGENT_RE = re.compile(r"\s*([A-Za-z][\w.-]*)\s*:(.*)", re.S)   # «googlebot: …» — one word before «:»


def _meta(soup: BeautifulSoup, *names: str) -> list:
    """Every <meta name=…> of these names, the name in any case."""
    want = {n.lower() for n in names}
    return [m for m in soup.find_all("meta") if str(m.get("name") or "").strip().lower() in want]


def _tokens(value: str) -> set[str]:
    return {t.lower() for t in re.split(r"[\s,]+", value) if t}


def noindex_meta(soup: BeautifulSoup) -> bool:
    """Any robots/googlebot/yandex meta says noindex or none — the strictest one wins (Google)."""
    return any(_NOINDEX & _tokens(str(m.get("content") or "")) for m in _meta(soup, *_OUR_AGENTS))


def noindex_header(lines: list[str]) -> bool:
    """X-Robots-Tag, one entry per header line: «googlebot: noindex» is for that
    crawler only, «GPTBot: noindex» is not ours, «noindex, max-snippet:0» has no prefix."""
    for line in lines:
        m = _AGENT_RE.fullmatch(line)
        if m and m.group(1).lower() not in _VALUED:
            if m.group(1).lower() not in _OUR_AGENTS:
                continue
            line = m.group(2)
        if _NOINDEX & _tokens(line):
            return True
    return False


def parse_robots(text: str) -> tuple[dict[str, list[list[tuple[bool, str]]]], list[str]]:
    """({user-agent: [rules of each group it is in, …]}, Sitemap: lines) —
    RFC 9309: consecutive User-agent lines share one group; Crawl-delay, Host
    and the like neither end one. Linear on purpose: a group's rules are ONE
    list that every agent of the group references — the file comes from the
    watched site, and N agents × N rules must not become N² tuples."""
    groups: dict[str, list[list[tuple[bool, str]]]] = {}
    sitemaps: list[str] = []
    rules: list[tuple[bool, str]] | None = None
    in_rules = False
    for raw in text.lstrip("﻿").splitlines():
        key, sep, value = raw.split("#", 1)[0].partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if in_rules or rules is None:
                rules, in_rules = [], False
            groups.setdefault(value.lower(), []).append(rules)
        elif key in ("allow", "disallow"):
            in_rules = True
            if value and rules is not None:               # an empty Disallow allows everything
                rules.append((key == "allow", value))
        elif key == "sitemap":
            sitemaps.append(value)
    return groups, sitemaps


def _rule_matches(pattern: str, path: str) -> bool:
    """«*» — any run of characters, a final «$» — the end of the path, otherwise
    a prefix. No regex is built from the site's text."""
    anchored = pattern.endswith("$")
    parts = (pattern[:-1] if anchored else pattern).split("*")
    if not path.startswith(parts[0]):
        return False
    if len(parts) == 1:
        return not anchored or path == parts[0]
    end, middle = len(path), parts[1:]
    if anchored:
        if not path.endswith(parts[-1]) or len(path) - len(parts[-1]) < len(parts[0]):
            return False
        end, middle = len(path) - len(parts[-1]), parts[1:-1]
    pos = len(parts[0])
    for part in middle:
        found = path.find(part, pos, end)
        if found < 0:
            return False
        pos = found + len(part)
    return True


def robots_allows(groups: dict[str, list[list[tuple[bool, str]]]], names: tuple[str, ...], path: str) -> bool:
    """The crawler's own group (else «*»), the longest matching pattern wins,
    a tie goes to Allow, no match — allowed (Google, Yandex)."""
    lists = next((groups[n] for n in names if n in groups), None)
    if lists is None:
        lists = groups.get("*", [])
    best, allowed = -1, True
    for rules in lists:
        for allow, pattern in rules:
            if _rule_matches(pattern, path) and (len(pattern) > best or (len(pattern) == best and allow)):
                best, allowed = len(pattern), allow
    return allowed


def analyze_robots(text: str, base_url: str) -> tuple[list[SeoProblem], list[str]]:
    """(problems, sitemap_urls) from robots.txt rules — our own reading, so the
    verdict does not change with the patch version of Python."""
    problems: list[SeoProblem] = []
    groups, sitemaps = parse_robots(text)
    path = urlparse(base_url.rstrip("/") + "/").path or "/"
    blocked = [bot for bot in SEARCH_BOTS if not robots_allows(groups, _GROUP_NAMES[bot], path)]
    if blocked:
        problems.append(_p("robots_search", "critical",
                           f"robots.txt запрещает {', '.join(blocked)} — сайт закрыт от поиска"))
    blocked_ai = [bot for bot in AI_BOTS if not robots_allows(groups, (bot.lower(),), path)]
    if blocked_ai:
        problems.append(_p("robots_ai", "warning",
                           f"robots.txt запрещает {', '.join(blocked_ai)} — ИИ-ассистенты не видят сайт"))
    return problems, sitemaps


def analyze_page(html: str, page_url: str, headers: dict | None = None, *,
                 is_home: bool | None = None) -> tuple[list[SeoProblem], int]:
    """Inspect one page's raw (no-JS) HTML → (problems, visible_text_chars).
    Messages are for the owner: which page, what is missing, in words.
    is_home says what was ASKED for: a homepage that answers «/» with a
    redirect to «/en/» is still the homepage, and its noindex is 🔴.
    A missing canonical is deliberately NOT reported: without knowing
    whether the page has duplicate addresses it would be a guess."""
    problems: list[SeoProblem] = []
    path = urlparse(page_url).path or "/"
    home = path == "/" if is_home is None else is_home
    page = _page(path) if not home or path == "/" else f"главная (→ {_page(path)})"
    headers = {k.lower(): v for k, v in (headers or {}).items()}

    # noindex on the homepage takes the site out of search (🔴); on an inner
    # page it is often deliberate (/privacy) — worth a look, not a 3 a.m. call.
    level = "critical" if home else "warning"
    if noindex_header(str(headers.get("x-robots-tag", "")).splitlines()):
        problems.append(_p("noindex_header", level, f"{page}: сервер отдаёт X-Robots-Tag: noindex"))

    soup = BeautifulSoup(html, "html.parser")
    if noindex_meta(soup):
        problems.append(_p("noindex_meta", level, f"{page}: в коде стоит meta robots noindex"))

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        problems.append(_p("no_title", "warning", f"{page}: нет заголовка <title>"))
    elif not 10 <= len(title) <= 70:
        problems.append(_p("title_len", "warning", f"{page}: заголовок {len(title)} {plural(len(title), 'символ', 'символа', 'символов')} (лучше 10–70)"))

    desc = _meta(soup, "description")
    desc_text = (desc[0].get("content") or "").strip() if desc else ""
    if not desc_text:
        problems.append(_p("no_description", "warning", f"{page}: нет описания (meta description)"))
    elif not 50 <= len(desc_text) <= 170:
        problems.append(_p("desc_len", "warning", f"{page}: описание {len(desc_text)} {plural(len(desc_text), 'символ', 'символа', 'символов')} (лучше 50–170)"))

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical:
        href = (canonical.get("href") or "").strip()
        try:
            canon_host = urlparse(href).hostname
        except ValueError:                  # «https://example.com]/» — a typo in the address
            problems.append(_p("canonical_bad", "warning", f"{page}: canonical записан с ошибкой ({href[:60]})"))
        else:
            if not href.startswith("https://"):
                problems.append(_p("canonical_bad", "warning", f"{page}: canonical ведёт не на https ({href[:60]})"))
            elif canon_host != urlparse(page_url).hostname:
                problems.append(_p("canonical_bad", "warning",
                                   f"{page}: canonical ведёт на другой сайт ({canon_host})"))

    if not soup.find("h1"):
        problems.append(_p("no_h1", "warning", f"{page}: нет главного заголовка h1"))

    if home:
        if not soup.find("meta", attrs={"property": "og:title"}):
            problems.append(_p("og_title", "warning", "нет заголовка для превью в мессенджерах (og:title)"))
        if not soup.find("meta", attrs={"property": "og:image"}):
            problems.append(_p("og_image", "warning", "нет картинки для превью в мессенджерах (og:image)"))
        html_tag = soup.find("html")
        if html_tag and not html_tag.get("lang"):
            problems.append(_p("no_lang", "warning", "не указан язык страницы (lang)"))

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            json.loads(script.string or "")
        except (ValueError, TypeError, RecursionError):
            problems.append(_p("jsonld_invalid", "warning", f"{page}: разметка JSON-LD с ошибкой"))
            break

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_len = len(" ".join(soup.get_text(separator=" ").split()))
    return problems, text_len


# ── Fetch + orchestrate ──────────────────────────────────────────────────────

async def _fetch(session: aiohttp.ClientSession, url: str,
                 ua: str = USER_AGENT) -> tuple[int | None, str, dict, str]:
    """(status, body_text, headers, final_url after redirects) — status None
    on network error."""
    try:
        async with session.get(url, timeout=TIMEOUT, allow_redirects=True,
                               headers={"User-Agent": ua}) as resp:
            body = decode_body(await read_capped(resp, MAX_PAGE_BYTES), resp.charset)
            # Join duplicate headers (two X-Robots-Tag lines) one per line
            # instead of keeping only the last one — «googlebot: nofollow»
            # and «otherbot: noindex» must not merge into one entry.
            headers = {k: "\n".join(resp.headers.getall(k)) for k in set(resp.headers.keys())}
            return resp.status, body, headers, str(resp.url)
    except Exception as e:
        logger.info("seo: fetch %s failed: %r", url, e)
        return None, "", {}, url


async def check_seo(url: str, manage: bool = True) -> SeoResult:
    site_id = await get_or_create_site(url)
    base = url.rstrip("/")
    r = SeoResult(url=url, site_id=site_id)
    problems = r.problems

    # The session carries the timeout: plain_http_opens has none of its own.
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        # Unreachable, down (5xx) or blocking this server (401/403/451) →
        # every check below would produce false findings and a bogus SEO
        # incident. Availability is another monitor's job — skip as transient.
        probe_status, _, _, _ = await _fetch(session, base + "/")
        if probe_status is None or probe_status >= 500 or probe_status in BLOCKED_CODES:
            r.status, r.transient = "error", True
            await save_check(site_id, "seo", "error", details=f"homepage {probe_status or 'unreachable'}, audit skipped")
            await sitestatus.update(site_id, "seo", status="error", critical=0, improve=0, problems=[])
            return r

        # A missing robots.txt means «everything allowed» — nothing to act
        # on, so nothing to say. Google reads any 4xx the same way; only a
        # 5xx or a 429 stalls its crawl.
        status, robots_text, _, _ = await _fetch(session, f"{base}/robots.txt")
        declared: list[str] = []
        # An audit that could not read a part of the site (robots.txt or the
        # homepage) has no verdict: it may open or raise an incident on what
        # it did see, but must not lower or close one. 200 and any 4xx are
        # the site's answer; a 5xx, a 429 or silence is not.
        robots_read = status is not None and status < 500 and status != 429
        home_read = False
        if status == 200:
            try:
                robot_problems, declared = analyze_robots(robots_text, base)
                problems.extend(robot_problems)
            except Exception:
                logger.warning("seo: robots.txt of %s could not be parsed", base, exc_info=True)
                robots_read = False
        elif status is not None and (status >= 500 or status == 429):
            problems.append(_p("robots_http_error", "warning", f"robots.txt отвечает ошибкой HTTP {status}"))

        # [] is the site's own answer (404, no pages); None — could not read,
        # which is not a finding.
        pages = await sitemap_urls(session, base)
        if pages == [] and not declared:
            problems.append(_p("sitemap_missing", "warning", "нет карты сайта (sitemap.xml)"))
        home_key = _key(base + "/")
        sample = [base + "/"] + pick_sample(
            [p for p in pages or [] if _key(p) != home_key], max(0, config.seo_pages_sample - 1))
        seen: set[tuple[str, str]] = set()     # final addresses already analysed
        for page in sample:
            is_home = page == sample[0]
            status, html, headers, final = await _fetch(session, page)
            ctype = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
            if status != 200 or not html or (ctype and "html" not in ctype):
                continue   # an XML or a PDF in the sitemap is not a page to audit
            # The homepage that redirects to /en/ is analysed as the homepage
            # once; the sitemap's /en/ is the same page and is not read again.
            if not is_home and _key(final) in seen:
                continue
            try:
                p, text_len = analyze_page(html, final, headers, is_home=is_home)
            except Exception:
                logger.warning("seo: page %s could not be analysed", page, exc_info=True)
                continue
            seen.add(_key(final))
            r.pages_checked += 1
            problems.extend(p)
            if is_home:
                home_read = True
                r.no_js_chars = text_len
                if text_len < config.seo_min_text_chars:
                    problems.append(_p("no_js", "warning", (
                        f"без JavaScript на главной всего {text_len} "
                        f"{plural(text_len, 'символ', 'символа', 'символов')} текста")))

        # The plain request above passed, so a refusal here is a block by
        # signature. A block by address (the real crawlers' IPs) is invisible
        # from here — the screen says so for Cloudflare, where it is the default.
        blocked_bots = []
        for bot, ua in AI_BOTS.items():
            status, _, _, _ = await _fetch(session, base + "/", ua=ua)
            if status in AI_REFUSED_CODES:
                blocked_bots.append(f"{bot} (HTTP {status})")
            await asyncio.sleep(0.3)
        if blocked_bots:
            problems.append(_p("ai_blocked", "warning", "обычный запрос сайт пропустил, а запрос с подписью "
                               + ", ".join(blocked_bots) + " — нет"))

        status, _, _, _ = await _fetch(session, f"{base}/__devopsbot-404-probe")
        if status == 200:
            problems.append(_p("soft404", "warning", "несуществующий адрес ответил «всё хорошо» (HTTP 200)"))

        # A closed port 80 or an http:// that answers 4xx/5xx is not reported:
        # nothing to intercept. Only an http:// that shows the site itself
        # is — the same verdict as the security check (http → http://www →
        # https is fine).
        if await plain_http_opens(session, url):
            problems.append(_p("https_redirect", "warning", "http:// открывает сайт без перевода на https://"))

    # Critical first: the screen, the incident line and the alert all lead
    # with what takes the site out of search.
    problems.sort(key=lambda p: p.severity != "critical")
    complete = robots_read and home_read
    if problems:
        r.status = "critical" if r.has_critical else "warning"
    elif not complete:
        r.status, r.transient = "error", True      # no verdict: never «OK»
    n = len(problems)
    summary = (f"{n} {plural(n, 'проблема', 'проблемы', 'проблем')}: "
               + "; ".join(p.message for p in problems[:3])
               if problems else f"OK, проверено страниц: {r.pages_checked}")
    if not complete:
        summary = f"incomplete (robots.txt read: {robots_read}, homepage read: {home_read}); " + summary
    await save_check(site_id, "seo", r.status, details=summary[:500])
    # What the owner hears about is still open: a snapshot of an incomplete
    # audit never shows a level below the incident it could not close.
    before = await get_open_incident(site_id, "seo")
    rank = {"critical": 2, "warning": 1}
    shown = r.status
    if not complete and before and rank.get(before["severity"], 0) > rank.get(shown, 0):
        shown = before["severity"]
    await sitestatus.update(site_id, "seo", status=shown,
                            critical=sum(p.severity == "critical" for p in problems),
                            improve=sum(p.severity != "critical" for p in problems),
                            problems=[p.as_dict() for p in problems[:12]],
                            pages=r.pages_checked, no_js_chars=r.no_js_chars, incomplete=not complete)
    if not manage:
        return r
    # An incomplete audit may open or raise an incident, never lower it:
    # the 🔴 it could not re-check stays until a full audit says otherwise.
    opens_or_raises = before is None or rank[r.severity] > rank.get(before["severity"], 0)
    if problems and (complete or opens_or_raises):
        r.error = incident_message(problems)
        r.incident_id, r.incident_new = await save_incident(site_id, "seo", r.error, severity=r.severity)
        r.downgraded = bool(before and before["severity"] == "critical" and not r.has_critical)
    elif complete and not problems:
        closed = await resolve_incident(site_id, "seo")
        r.recovered, r.resolved_incident = closed is not None, closed
    return r


def incident_message(problems: list[SeoProblem]) -> str:
    """The incident line, and with it the level: «закрыт от поиска — …» is
    what services.humanize.classify reads as critical."""
    n = len(problems)
    lead = min(problems, key=lambda p: p.severity != "critical")   # first critical, else the first found
    first = seo_fixes.fix(lead.code).title
    if lead.severity == "critical":
        return f"закрыт от поиска — {first}"[:300]
    return (f"{n} {plural(n, 'помеха', 'помехи', 'помех')} в поиске, напр.: {first}")[:300]


async def check_all_seo(urls: list[str], manage: bool = True) -> list[SeoResult]:
    return await gather_checks(urls, check_seo, "seo", manage=manage)
