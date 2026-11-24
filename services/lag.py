"""Code that is merged but not deployed — for the owner's API checks and
Workers (the bot checks its own build in services.updates).

Once a day:

* an API check whose answer is JSON with a top-level «version» is compared
  with the latest release of the GitHub repository whose «homepage» is the
  check's host. Behind it for more than LAG since that release: one 🟠
  «API … отстаёт от релиза 2.2.2 уже 3 дн.: отвечает 2.2.1», with the
  release a tap away; ✅ when it catches up;
* a Worker routed on a host (Zone → Workers Routes → Read) is compared with
  the same repository: its last deployment (Account → Workers Scripts →
  Read, «modified_on») against the newest commit under WORKER_DIR. A commit
  that has waited for its deployment longer than LAG: one 🟠 «Worker …
  отстаёт от кода»; ✅ when it is deployed.

Silent while everything is deployed, while nothing can be compared (no
version in the answer, no repository with that homepage, no release, no
commit under WORKER_DIR) and when GitHub or Cloudflare do not answer.
"""

import json
import logging
import re
from datetime import UTC, datetime, timedelta

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_all_sites, get_state, set_state
from monitors.availability import answer_text
from services import cloudflare, github, notifier
from services.cloudflare import CloudflareError
from services.github import GitHubError
from services.notifier import Priority
from utils.clock import to_local
from utils.i18n import t
from utils.text import esc
from utils.urls import host_of, is_api_check, site_label

logger = logging.getLogger(__name__)

LAG = timedelta(days=1)
WORKER_DIR = "worker"
TOLD = "lag:told"      # {«api:<site id>» / «worker:<script>»: its name} — said, until it catches up


def version_of(text: str | None) -> str | None:
    """«2.2.1» from an answer like {"status": "ok", "version": "v2.2.1"}."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return None
    v = data.get("version") if isinstance(data, dict) else None
    return str(v).strip().removeprefix("v") if isinstance(v, (str, int, float)) and str(v).strip() else None


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:4])


def behind(deployed: str, released: str) -> bool:
    """Is the deployed version older than the release? Unreadable — no."""
    a, b = _parts(deployed), _parts(released.removeprefix("v"))
    return bool(a) and bool(b) and a < b


def _days(since: datetime) -> int:
    return max(1, (datetime.now(UTC) - since).days)


def _when(dt: datetime) -> str:
    return to_local(dt).strftime("%d.%m")


def _repo_by_host(repos: list[dict]) -> dict[str, dict]:
    """host → the repository whose homepage it is; a host two repositories
    claim is nobody's (no guessing)."""
    out: dict[str, dict | None] = {}
    for r in repos:
        host = host_of(r["homepage"])
        out[host] = None if host in out else r
    return {h: r for h, r in out.items() if r}


def _release_kb(repo: dict, tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=t("🔗 Релиз {tag}").format(tag=tag), url=f"{repo['url']}/releases/tag/{tag}")]])


Lag = tuple[str, InlineKeyboardMarkup | None, str]   # the 🟠 text, its keyboard, what to name in ✅


async def _api_lags(repos: dict[str, dict]) -> tuple[dict[str, Lag], set[str]]:
    """The API checks behind their release, and every check that got a
    verdict this time (behind or not): only those may say ✅."""
    out: dict[str, Lag] = {}
    judged: set[str] = set()
    for site in await get_all_sites():
        url = site["url"]
        repo = repos.get(host_of(url))
        if not repo or not repo.get("release") or not is_api_check(url, site.get("http_method")):
            continue
        key = f"api:{site['id']}"
        deployed = version_of(await answer_text(site))
        released_at = cloudflare.parse_time(repo.get("released_at"))
        if deployed is None or released_at is None:
            continue                                       # down, or no version: no verdict
        judged.add(key)
        if not behind(deployed, repo["release"]) or datetime.now(UTC) - released_at < LAG:
            continue
        tag, label = repo["release"], esc(site_label(url))
        out[key] = (
            "\n".join([
                t("🟠 API {label} отстаёт от релиза {tag} уже {days} дн.: отвечает версией {deployed}").format(
                    label=label, tag=esc(tag), days=_days(released_at), deployed=esc(deployed)),
                t("Код релиза влит, но не задеплоен: те, кто зовёт API, получают старую версию."),
                "", t("Что делать: посмотри последний деплой (Cloudflare → Workers или Pages → Deployments) и "
                      "запусти его снова, если он упал."),
                "", f"<i>{esc(repo['name'])} · {esc(tag)} · {_when(released_at)}</i>"]),
            _release_kb(repo, tag), t("API {label}").format(label=label))
    return out, judged


async def _worker_lags(repos: dict[str, dict]) -> tuple[dict[str, Lag], set[str]]:
    """The Workers deployed before the newest commit under WORKER_DIR of
    their host's repository, and every Worker that got a verdict."""
    if not cloudflare.configured() or not repos:
        return {}, set()
    async with aiohttp.ClientSession(timeout=cloudflare.TIMEOUT) as s:
        routes = await cloudflare.worker_routes(s)
        scripts = {str(w.get("id") or ""): w for w in await cloudflare.worker_scripts(s)}
    out: dict[str, Lag] = {}
    judged: set[str] = set()
    for route in routes:
        script = str(route.get("script") or "")
        host = str(route.get("pattern") or "").split("/", 1)[0].lower()
        repo = repos.get(host)
        key = f"worker:{script}"
        if not script or key in judged or script not in scripts or not repo:
            continue
        deployed_at = cloudflare.parse_time(scripts[script].get("modified_on"))
        committed_at = await github.last_commit_at(repo["name"], WORKER_DIR)
        if deployed_at is None or committed_at is None:
            continue
        judged.add(key)
        if committed_at <= deployed_at or datetime.now(UTC) - committed_at < LAG:
            continue                                       # deployed, or a deployment may still come today
        out[key] = (
            "\n".join([
                t("🟠 Worker {script} отстаёт от кода: последний коммит в {dir}/ от {committed}, задеплоен {deployed}").format(
                    script=esc(script), dir=WORKER_DIR, committed=_when(committed_at), deployed=_when(deployed_at)),
                t("Изменения в {dir}/ влиты, но на {host} работает прежняя версия.").format(dir=WORKER_DIR, host=esc(host)),
                "", t("Что делать: задеплой Worker (wrangler deploy или кнопка в Cloudflare → Workers) или проверь, "
                      "почему упал автоматический деплой."),
                "", f"<i>{esc(repo['name'])}/{WORKER_DIR} · {esc(script)}</i>"]),
            None, t("Worker {script}").format(script=esc(script)))
    return out, judged


async def run():
    """The daily comparison: a lag is said once; ✅ when a verdict says it
    is gone (a check that is down, or GitHub that did not answer, is no
    verdict)."""
    if not github.available():
        return
    try:
        repos = _repo_by_host(await github.published_repos())
    except GitHubError as e:
        logger.info("Deployed vs released: GitHub said %s", e)
        return
    lags, judged = await _api_lags(repos)
    try:
        worker_lags, worker_judged = await _worker_lags(repos)
        lags.update(worker_lags)
        judged |= worker_judged
    except CloudflareError as e:
        logger.info("Deployed vs released: Cloudflare said %s", e)
    try:
        told = json.loads(await get_state(TOLD) or "{}")
    except ValueError:
        told = {}
    told = told if isinstance(told, dict) else {}
    for key, (text, kb, name) in lags.items():
        if key not in told and await notifier.send(text, Priority.NORMAL, reply_markup=kb):
            told[key] = name
    for key in [k for k in told if k in judged and k not in lags]:
        if await notifier.send(t("✅ {what}: задеплоено то, что влито, — больше не отстаёт.").format(what=told[key]),
                               Priority.NORMAL):
            told.pop(key)
    await set_state(TOLD, json.dumps(told, ensure_ascii=False))
