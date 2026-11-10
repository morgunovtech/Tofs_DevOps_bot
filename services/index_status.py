"""Is the site actually IN Google's and Yandex's index? Only Search Console
and Webmaster know — the audit itself only proves that nothing blocks the
robots. Their answers are kept in the site snapshot (services.sitestatus,
sections «google» and «yandex») so the «🔎 Поиск и ИИ» screen opens
instantly and can say how old the data is. Refreshed by the daily index
job and by «🔄 Проверить снова»; nothing here alerts."""

from datetime import datetime

from services import gsc, sitestatus, yandex_webmaster
from utils.clock import to_local
from utils.text import plural
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
                      "last_crawl": info.get("last_crawl")}
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
    return " и ".join(parts)
