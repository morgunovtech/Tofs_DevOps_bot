"""Is the site actually IN Google's and Yandex's index? Only Search Console
and Webmaster know — the audit itself only proves that nothing blocks the
robots. Their answers are kept in the site snapshot (services.sitestatus,
sections «google» and «yandex») so the «🔎 Поиск и ИИ» screen opens
instantly and can say how old the data is. Refreshed by the daily index
job and by «🔄 Проверить снова»; nothing here alerts. The Google section
also keeps Google's remarks on the page's structured data (rich results)."""

from datetime import datetime

from services import gsc, sitestatus, yandex_webmaster
from utils.clock import to_local
from utils.i18n import plural, t
from utils.urls import short_host

# Older than this and the screen says «по данным на DD.MM» instead of
# asserting the state: the daily job has missed at least one run.
STALE_HOURS = 48

_UNSET = object()     # «the caller did not ask Yandex» — unlike None, «asked, and it did not answer»


async def refresh(site_id: int, url: str, yx_summaries=_UNSET) -> tuple[dict | None, dict | None]:
    """Fetch and store both sections. Returns (google, yandex) as stored;
    None for an integration that is off or answered nothing (the previous
    snapshot is then kept, with its own date). yx_summaries: the daily job
    asks Yandex once for every site and passes the answer — None included,
    so a Webmaster that did not answer is not asked again per site."""
    google = yandex = None
    if gsc.available():
        info = await gsc.inspect_url(url.rstrip("/") + "/")
        if info:
            google = {"verdict": info["verdict"], "coverage": info["coverage"],
                      "last_crawl": info.get("last_crawl"), "user_canonical": info.get("user_canonical"),
                      "google_canonical": info.get("google_canonical"),
                      "rich_issues": info.get("rich_issues") or []}
            await sitestatus.update(site_id, "google", **google)
    if yandex_webmaster.available():
        if yx_summaries is _UNSET:
            yx_summaries = await yandex_webmaster.get_summaries(only={short_host(url)})
        s = (yx_summaries or {}).get(short_host(url))
        if s:
            yandex = {"searchable_pages": s.get("searchable_pages"), "sqi": s.get("sqi"),
                      "alert_problems": s.get("alert_problems") or {}}
            await sitestatus.update(site_id, "yandex", **yandex)
    return google, yandex


# Search Console's coverage states (URL Inspection «coverageState»), in the
# owner's words with what helps. Matched by a lowercase fragment: the API
# spells them in English and varies the punctuation.
COVERAGE = (
    ("crawled - currently not indexed", "просканирована, но не проиндексирована — Google обошёл страницу и пока не "
     "показывает её: помогают уникальный текст, ссылки на неё с других страниц и «Запросить индексирование»"),
    ("discovered - currently not indexed", "обнаружена, но не проиндексирована — Google знает адрес, но ещё не обходил: "
     "помогают ссылки на неё и «Запросить индексирование»"),
    ("unknown to google", "Google не знает этот адрес — добавь sitemap в Search Console и запроси индексирование"),
    ("chose different canonical", "Google выбрал главной копией другой адрес"),
    ("without user-selected canonical", "дубль без canonical — главную копию Google выбрал сам"),
    ("noindex", "закрыта noindex"),
    ("robots.txt", "закрыта в robots.txt"),
    ("soft 404", "Google считает её пустой страницей («soft 404»)"),
    ("not found (404)", "страница не найдена (404)"),
    ("server error", "при обходе сервер ответил ошибкой"),
    ("redirect", "страница с переадресацией — в индексе адрес, на который она ведёт"),
)


def coverage_words(state: str | None) -> str:
    """«Discovered - currently not indexed» → words with what to do; an
    unknown state stays as Google wrote it."""
    low = (state or "").lower()
    return next((t(words) for key, words in COVERAGE if key in low), state or "?")


def canonical_differs(google: dict | None) -> bool:
    """Google chose another main copy than the page names: both known and
    not the same address (scheme, case and a trailing slash aside)."""
    g = google or {}
    a, b = g.get("google_canonical"), g.get("user_canonical")
    if not a or not b:
        return False
    norm = lambda u: str(u).lower().split("://", 1)[-1].rstrip("/")  # noqa: E731
    return norm(a) != norm(b)


def rich_level(issues: list | None) -> str | None:
    """«error» when any remark stops the rich result, «warning» when all of
    them only trim it, None when Google has no remarks."""
    levels = {i.get("severity") for i in issues or [] if isinstance(i, dict)}
    return "error" if "error" in levels else "warning" if levels else None


def rich_types(issues: list | None) -> str:
    """«ProfilePage, FAQ» — the rich result types the remarks are about."""
    return ", ".join(dict.fromkeys(str(i.get("type") or "?") for i in issues or [] if isinstance(i, dict)))


def rich_count_words(issues: list | None) -> str:
    """«1 ошибка и 2 предупреждения» — one wording for the alert and the screen."""
    parts = []
    for level, forms in (("error", ("ошибка", "ошибки", "ошибок")),
                         ("warning", ("предупреждение", "предупреждения", "предупреждений"))):
        n = sum(1 for i in issues or [] if isinstance(i, dict) and i.get("severity") == level)
        if n:
            parts.append(f"{n} {plural(n, *forms)}")
    return t(" и ").join(parts)


def is_stale(section: dict | None) -> bool:
    age = sitestatus.age_minutes(section)
    return age is None or age > STALE_HOURS * 60


def fmt_day(iso: str | None) -> str:
    """'2026-09-20T10:11:12Z' → '20.09'; '' when unparseable."""
    if not iso:
        return ""
    try:
        return to_local(datetime.fromisoformat(iso.replace("Z", "+00:00"))).strftime("%d.%m")
    except ValueError:
        return ""


def yandex_problem_level(alert_problems: dict | None) -> str:
    """FATAL is what Yandex calls «the site is unavailable or banned» —
    that rings; CRITICAL waits for the morning, like every other 🟠."""
    return "critical" if any(str(k).upper() == "FATAL" for k in (alert_problems or {})) else "warning"


_LEVEL_WORDS = {"FATAL": ("критическая проблема", "критические проблемы", "критических проблем"),
                "CRITICAL": ("серьёзная проблема", "серьёзные проблемы", "серьёзных проблем")}


def yandex_problem_count(alert_problems: dict | None) -> int:
    """KEYS are severities, VALUES are counts: {"CRITICAL": 2} is two problems."""
    return sum(int(v) for v in (alert_problems or {}).values() if str(v).isdigit())


def yandex_problems_words(alert_problems: dict | None) -> str:
    """«1 критическая проблема и 2 серьёзные проблемы» — the one wording of
    the alert, the screen and the report."""
    parts = []
    for level, forms in _LEVEL_WORDS.items():
        n = sum(int(v) for k, v in (alert_problems or {}).items() if str(k).upper() == level and str(v).isdigit())
        if n:
            parts.append(f"{n} {plural(n, *forms)}")
    return t(" и ").join(parts)
