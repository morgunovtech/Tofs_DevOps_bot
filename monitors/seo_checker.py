"""SEO/GEO monitor: is the site visible to search engines and AI agents?

Checks (no API keys required):
  * robots.txt — not blocking Googlebot/YandexBot (critical) or AI crawlers
    (GPTBot, ClaudeBot, PerplexityBot — warning);
  * sitemap.xml exists and yields pages;
  * sampled pages: <title>/description/canonical/h1/og:*, JSON-LD parses,
    and meta robots noindex / X-Robots-Tag (critical);
  * AI user-agent probes: a 403 for GPTBot/ClaudeBot usually means a
    Cloudflare "block AI bots" toggle;
  * content available WITHOUT JavaScript (AI crawlers mostly don't run JS);
  * http→https redirect, soft-404, llms.txt presence (informational).

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
from db.database import get_or_create_site, resolve_incident, save_check, save_incident
from monitors.base import SeoProblem, SeoResult, gather_checks
from monitors.sitemap import pick_sample, sitemap_urls
from services import sitestatus
from utils.text import plural
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
MAX_PAGE_BYTES = 2 * 1024 * 1024
SEARCH_BOTS = ("Googlebot", "YandexBot")
AI_BOTS = {
    "GPTBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
              "compatible; GPTBot/1.2; +https://openai.com/gptbot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                 "compatible; ClaudeBot/1.0; +claudebot@anthropic.com",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); "
                     "compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot",
}
BLOCKED_CODES = {401, 403, 451}


def _p(severity: str, message: str, hint: str = "") -> SeoProblem:
    return SeoProblem(severity, message, hint)


HINT_ROBOTS_SEARCH = ("В robots.txt стоит запрет для поискового робота — сайт пропадёт из поиска. "
                      "Убери строку Disallow: / в секции этого робота (или во всех).")
HINT_ROBOTS_AI = ("Сайт закрыт от ИИ-ассистентов (ChatGPT, Claude, Perplexity). Если хочешь, чтобы они "
                  "знали о сайте и советовали его, убери запрет в robots.txt.")
HINT_NOINDEX_HEADER = ("Сервер отдаёт заголовок X-Robots-Tag: noindex — поисковики выкинут страницу. "
                       "Убери его в настройках хостинга или сервера.")
HINT_NOINDEX_META = ("В коде страницы стоит meta robots noindex — поисковики выкинут страницу. Обычно это "
                     "настройка тестовой версии, случайно попавшая в прод. Убери тег.")
HINT_TITLE = "У страницы нет заголовка title — в поиске она будет без подписи. Добавь <title> в код."
HINT_DESC = ("Нет описания страницы (meta description) — текста под заголовком в результатах поиска. "
             "Добавь 1–2 предложения о странице.")
HINT_CANONICAL = ("Тег canonical говорит поисковикам «главная копия этой страницы вон там». Он должен "
                  "вести на https-адрес этой же страницы на этом же сайте.")
HINT_JSONLD = ("Разметка JSON-LD (структурированные данные для поиска) с ошибкой синтаксиса — "
               "поисковики её проигнорируют. Проверь блок валидатором schema.org.")
HINT_ROBOTS_HTTP = "Файл robots.txt отвечает ошибкой — поисковики могут считать сайт закрытым. Проверь, что файл отдаётся."
HINT_SITEMAP = ("Нет карты сайта (sitemap.xml) — без неё поисковики находят новые страницы медленнее. "
                "Большинство движков умеют её генерировать.")
HINT_NO_JS = ("Текст сайта появляется только после выполнения JavaScript. ИИ-ассистенты и часть поисковых "
              "роботов JS не запускают и видят пустую страницу. Нужен серверный рендеринг или предрендер.")
HINT_AI_BLOCKED = ("Сайт отвечает ИИ-ассистентам ошибкой доступа — обычно это включённый в Cloudflare "
                   "переключатель «Block AI bots». Выключи его, если хочешь, чтобы ИИ знали о сайте.")
HINT_SOFT404 = ("Несуществующие адреса отвечают «всё хорошо» вместо «страница не найдена». Поисковики "
                "заполняют индекс мусором. Настрой ответ 404 для несуществующих страниц.")
HINT_HTTPS = "Адрес с http:// не перенаправляется на https:// — часть посетителей попадёт на незащищённую версию. Настрой редирект у хостинга."


# ── Pure analysis (unit-testable) ────────────────────────────────────────────

def analyze_robots(text: str, base_url: str) -> tuple[list[SeoProblem], list[str]]:
    """(problems, sitemap_urls) from robots.txt rules."""
    problems: list[SeoProblem] = []
    rp = robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    probe = base_url.rstrip("/") + "/"
    for agent in SEARCH_BOTS:
        if not rp.can_fetch(agent, probe):
            problems.append(_p("critical", f"robots.txt запрещает {agent} — сайт закрыт от поиска!", HINT_ROBOTS_SEARCH))
    for agent in AI_BOTS:
        if not rp.can_fetch(agent, probe):
            problems.append(_p("warning", f"robots.txt запрещает {agent} — ИИ-ассистенты не увидят сайт", HINT_ROBOTS_AI))
    return problems, list(rp.site_maps() or [])


def analyze_page(html: str, page_url: str,
                 headers: dict | None = None) -> tuple[list[SeoProblem], list[str], int]:
    """Inspect one page's raw (no-JS) HTML → (problems, infos, visible_text_chars)."""
    problems: list[SeoProblem] = []
    infos: list[str] = []
    path = urlparse(page_url).path or "/"
    headers = {k.lower(): v for k, v in (headers or {}).items()}

    if "noindex" in headers.get("x-robots-tag", "").lower():
        problems.append(_p("critical", f"{path}: заголовок X-Robots-Tag noindex — страница скрыта от индексации!", HINT_NOINDEX_HEADER))

    soup = BeautifulSoup(html, "html.parser")
    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta and "noindex" in (robots_meta.get("content") or "").lower():
        problems.append(_p("critical", f"{path}: meta robots noindex — страница скрыта от индексации!", HINT_NOINDEX_META))

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        problems.append(_p("warning", f"{path}: нет <title>", HINT_TITLE))
    elif not 10 <= len(title) <= 70:
        infos.append(f"{path}: длина title {len(title)} (рекомендуется 10–70)")

    desc = soup.find("meta", attrs={"name": "description"})
    desc_text = (desc.get("content") or "").strip() if desc else ""
    if not desc_text:
        problems.append(_p("warning", f"{path}: нет meta description", HINT_DESC))
    elif not 50 <= len(desc_text) <= 170:
        infos.append(f"{path}: длина description {len(desc_text)} (рекомендуется 50–170)")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if canonical:
        href = (canonical.get("href") or "").strip()
        if not href.startswith("https://"):
            problems.append(_p("warning", f"{path}: canonical не https ({href[:60]})", HINT_CANONICAL))
        elif urlparse(href).hostname != urlparse(page_url).hostname:
            problems.append(_p("warning", f"{path}: canonical указывает на другой хост ({urlparse(href).hostname})", HINT_CANONICAL))
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
            problems.append(_p("warning", f"{path}: JSON-LD не парсится", HINT_JSONLD))
            break

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text_len = len(" ".join(soup.get_text(separator=" ").split()))
    return problems, infos, text_len


# ── Fetch + orchestrate ──────────────────────────────────────────────────────

async def _fetch(session: aiohttp.ClientSession, url: str,
                 ua: str = USER_AGENT) -> tuple[int | None, str, dict]:
    """(status, body_text, headers) — status None on network error."""
    try:
        async with session.get(url, timeout=TIMEOUT, allow_redirects=True,
                               headers={"User-Agent": ua}) as resp:
            raw = await resp.content.read(MAX_PAGE_BYTES)
            body = raw.decode(resp.charset or "utf-8", errors="replace")
            # Join duplicate headers (two X-Robots-Tag lines) instead of
            # keeping only the last one.
            headers = {k: ", ".join(resp.headers.getall(k)) for k in set(resp.headers.keys())}
            return resp.status, body, headers
    except Exception as e:
        logger.debug("seo: fetch %s failed: %s", url, e)
        return None, "", {}


async def check_seo(url: str, manage: bool = True) -> SeoResult:
    site_id = await get_or_create_site(url)
    base = url.rstrip("/")
    host = urlparse(url).hostname or ""
    r = SeoResult(url=url, site_id=site_id)
    problems, infos = r.problems, r.infos

    async with aiohttp.ClientSession() as session:
        # Unreachable right now → every check below would produce false
        # findings and a bogus SEO incident. Availability is another
        # monitor's job — bail out as transient.
        probe_status, _, _ = await _fetch(session, base + "/")
        if probe_status is None:
            r.status, r.transient = "error", True
            await save_check(site_id, "seo", "error", details="site unreachable, audit skipped")
            await sitestatus.update(site_id, "seo", status="error", critical=0, improve=0)
            return r

        status, robots_text, _ = await _fetch(session, f"{base}/robots.txt")
        if status == 200:
            problems.extend(analyze_robots(robots_text, base)[0])
        elif status == 404:
            infos.append("robots.txt отсутствует (всё разрешено — не критично)")
        elif status is not None:
            problems.append(_p("warning", f"robots.txt отвечает HTTP {status}", HINT_ROBOTS_HTTP))

        pages = await sitemap_urls(session, base)
        if not pages:
            problems.append(_p("warning", "sitemap.xml не найден или пуст", HINT_SITEMAP))
        sample = [base + "/"] + pick_sample(
            [p for p in pages if p.rstrip("/") != base], max(0, config.seo_pages_sample - 1))
        for page in sample:
            status, html, headers = await _fetch(session, page)
            if status != 200 or not html:
                continue
            r.pages_checked += 1
            p, i, text_len = analyze_page(html, page, headers)
            problems.extend(p)
            infos.extend(i)
            if page == sample[0]:
                r.no_js_chars = text_len
                if text_len < config.seo_min_text_chars:
                    problems.append(_p("warning", (
                        f"без JavaScript на главной всего {text_len} "
                        f"{plural(text_len, 'символ', 'символа', 'символов')} текста — "
                        f"ИИ-ассистенты видят почти пустую страницу"), HINT_NO_JS))

        for bot, ua in AI_BOTS.items():
            status, _, _ = await _fetch(session, base + "/", ua=ua)
            if status in BLOCKED_CODES:
                problems.append(_p("warning", (
                    f"{bot} получает HTTP {status} — похоже, включена блокировка "
                    f"ИИ-ботов (Cloudflare?)"), HINT_AI_BLOCKED))
            await asyncio.sleep(0.3)

        status, _, _ = await _fetch(session, f"{base}/__devopsbot-404-probe")
        if status == 200:
            problems.append(_p("warning", "несуществующие страницы отдают HTTP 200 (soft-404)", HINT_SOFT404))

        try:
            async with session.get(f"http://{host}/", timeout=TIMEOUT, allow_redirects=False,
                                   headers={"User-Agent": USER_AGENT}) as resp:
                loc = resp.headers.get("Location", "")
                if not (300 <= resp.status < 400 and loc.startswith("https://")):
                    problems.append(_p("warning", f"http:// не редиректит на https (HTTP {resp.status})", HINT_HTTPS))
        except Exception:
            infos.append("http://-версия недоступна (порт 80 закрыт — ок)")

        status, _, _ = await _fetch(session, f"{base}/llms.txt")
        infos.append("llms.txt: есть ✅" if status == 200
                     else "llms.txt: нет (опционально, помогает AI-агентам)")

    if problems:
        r.status = "critical" if r.has_critical else "warning"
    n = len(problems)
    summary = (f"{n} {plural(n, 'проблема', 'проблемы', 'проблем')}: "
               + "; ".join(p.message for p in problems[:3])
               if problems else f"OK, проверено страниц: {r.pages_checked}")
    await save_check(site_id, "seo", r.status, details=summary[:500])
    await sitestatus.update(site_id, "seo", status=r.status,
                            critical=sum(p.severity == "critical" for p in problems),
                            improve=sum(p.severity != "critical" for p in problems))
    if not manage:
        return r
    if problems:
        r.error = (f"SEO: {n} {plural(n, 'проблема', 'проблемы', 'проблем')}, "
                   f"напр.: {problems[0].message}")[:300]
        r.incident_id, r.incident_new = await save_incident(site_id, "seo", r.error, severity=r.severity)
    elif await resolve_incident(site_id, "seo"):
        r.recovered = True
    return r


async def check_all_seo(urls: list[str], manage: bool = True) -> list[SeoResult]:
    return await gather_checks((check_seo(u, manage=manage) for u in urls), "seo")
