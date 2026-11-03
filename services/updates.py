"""Dependency self-awareness.

The bot cannot safely upgrade its own packages inside an immutable container
(the change would vanish at the next restart and nothing would test it).
What it CAN do:

  * on startup, notice that its installed packages or git commit changed
    since the last boot and say so («🔄 Бот обновился: aiohttp 3.10 → 3.14»);
  * every hour, notice that the newest commit on main failed to deploy while
    the bot still runs an older build — then every next update fails too;
  * once a week, ask OSV about known vulnerabilities and list the Dependabot
    PRs that wait for a human.

The actual upgrades arrive as Dependabot pull requests (.github/dependabot.yml)
that CI tests and auto-merges; the hosting platform redeploys from main. A
newer version on PyPI is therefore not news: it installs itself on Saturday.
Only what that pipeline cannot do alone is reported.
"""

import json
import logging
import os
import platform
import re
from datetime import UTC, datetime, timedelta
from importlib import metadata

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from db.database import get_state, set_state
from services import notifier
from services.notifier import Priority
from utils.text import esc, plural

logger = logging.getLogger(__name__)

PACKAGES = (
    "aiogram", "aiohttp", "aiosqlite", "apscheduler", "beautifulsoup4",
    "python-dotenv", "cryptography", "dnspython",
)
GITHUB_PULLS = "https://api.github.com/repos/{slug}/pulls"
GITHUB_COMMIT = "https://api.github.com/repos/{slug}/commits/{ref}"
GITHUB_STATUS = "https://api.github.com/repos/{slug}/commits/{ref}/status"
_GH_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "TofsDevOps"}
# A minor/patch PR still open after this long did not auto-merge: CI is red.
STUCK_AFTER = timedelta(days=2)
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def installed_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    return out


# ── Startup: "what changed since last boot?" ─────────────────────────────────

async def report_startup_changes() -> bool:
    """True when the build, Python or a package changed since the last boot."""
    current = {
        "commit": config.app_commit,
        "python": platform.python_version(),
        "packages": installed_versions(),
    }
    prev_raw = await get_state("deps:snapshot")
    await set_state("deps:snapshot", json.dumps(current))
    if not prev_raw:
        return False
    try:
        prev = json.loads(prev_raw)
    except ValueError:
        return False
    lines = []
    if prev.get("commit") != current["commit"] and current["commit"]:
        lines.append(f"  • сборка {esc(prev.get('commit') or '?')} → {esc(current['commit'])}")
    if prev.get("python") != current["python"]:
        lines.append(f"  • Python {esc(prev.get('python'))} → {esc(current['python'])}")
    old_pkgs = prev.get("packages") or {}
    for name, ver in current["packages"].items():
        if old_pkgs.get(name) and old_pkgs[name] != ver:
            lines.append(f"  • {name} {esc(old_pkgs[name])} → {esc(ver)}")
    if lines:
        await notifier.send("🔄 Бот обновился:\n" + "\n".join(lines), Priority.NORMAL)
    return bool(lines)


# ── Weekly: OSV + PRs waiting for a human ───────────────────────────────────

async def _vulnerabilities(session: aiohttp.ClientSession,
                           versions: dict[str, str]) -> dict[str, list[str]]:
    names = list(versions)
    body = {"queries": [{"package": {"name": n, "ecosystem": "PyPI"},
                         "version": versions[n]} for n in names]}
    try:
        async with session.post(OSV_BATCH, json=body) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
    except Exception as e:
        logger.debug("OSV query failed: %s", e)
        return {}
    out: dict[str, list[str]] = {}
    for name, result in zip(names, data.get("results", []), strict=False):
        ids = sorted(v["id"] for v in (result or {}).get("vulns", []) if v.get("id"))
        if ids:
            out[name] = ids
    return out


def repo_slug() -> str | None:
    """owner/name of the repository this bot was deployed from — Railway
    injects it, GitHub Actions too; otherwise unknown."""
    slug = os.getenv("GITHUB_REPOSITORY", "").strip()
    if not slug:
        owner = os.getenv("RAILWAY_GIT_REPO_OWNER", "").strip()
        name = os.getenv("RAILWAY_GIT_REPO_NAME", "").strip()
        slug = f"{owner}/{name}" if owner and name else ""
    return slug or None


_BUMP = re.compile(r"bump (\S+) from (\d+)(\S*) to (\d+)(\S*)", re.I)


def is_major_bump(title: str) -> bool:
    """Dependabot title «Bump aiogram from 3.31.0 to 4.0.0» → major changed."""
    m = _BUMP.search(title)
    return bool(m) and m.group(2) != m.group(4)


def short_bump(title: str) -> str:
    m = _BUMP.search(title)
    return f"{m.group(1)} {m.group(2)}{m.group(3)}→{m.group(4)}{m.group(5)}" if m else title[:60]


async def _gh_json(session: aiohttp.ClientSession, url: str, **params):
    """GitHub REST without a token (public repositories): JSON or None."""
    try:
        async with session.get(url, params=params or None, headers=_GH_HEADERS) as resp:
            return await resp.json() if resp.status == 200 else None
    except Exception as e:
        logger.debug("GitHub %s: %s", url, e)
        return None


async def waiting_prs(session: aiohttp.ClientSession) -> list[dict]:
    """Dependabot PRs that wait for a human: major bumps (never auto-merged)
    and minor ones still open after STUCK_AFTER (auto-merge waits for a
    green CI, so the tests failed)."""
    slug = repo_slug()
    if not slug:
        return []
    data = await _gh_json(session, GITHUB_PULLS.format(slug=slug), state="open", per_page="50")
    now = datetime.now(UTC)
    out = []
    for pr in data if isinstance(data, list) else []:
        login = str(((pr or {}).get("user") or {}).get("login") or "")
        title = str((pr or {}).get("title") or "")
        if not login.startswith("dependabot"):
            continue
        major = is_major_bump(title)
        try:
            opened = datetime.fromisoformat(str(pr.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            opened = now
        if major or now - opened > STUCK_AFTER:
            out.append({"number": pr.get("number"), "title": title, "url": pr.get("html_url") or "",
                        "major": major})
    return out


async def check_dependencies() -> dict:
    """{"vulns": {name: [ids]}, "waiting_prs": [{number, title, url, major}],
    "checked_at": iso} — network errors degrade to empty sections."""
    versions = installed_versions()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        vulns = await _vulnerabilities(session, versions)
        prs = await waiting_prs(session)
    return {"vulns": vulns, "waiting_prs": prs,
            "checked_at": datetime.now(UTC).isoformat(timespec="minutes")}


# ── Hourly: did the newest version deploy? ───────────────────────────────────

async def failed_deploy(session: aiohttp.ClientSession) -> dict | None:
    """{sha, title, url, log} of the newest commit on main when the hosting
    reported a failed deploy for it and this bot still runs an older build."""
    slug, running = repo_slug(), config.app_commit
    if not slug or not running:
        return None
    head = await _gh_json(session, GITHUB_COMMIT.format(slug=slug, ref="main"))
    sha = str((head or {}).get("sha") or "")
    if not sha or sha.startswith(running):
        return None
    status = await _gh_json(session, GITHUB_STATUS.format(slug=slug, ref=sha))
    failed = [st for st in (status or {}).get("statuses") or [] if st.get("state") in ("failure", "error")]
    if not failed:
        return None   # deployed, or still deploying
    title = str((head.get("commit") or {}).get("message") or "").splitlines()[0][:80] if head else ""
    return {"sha": sha[:7], "title": title, "url": head.get("html_url") or "",
            "log": failed[0].get("target_url") or ""}


async def run_deploy_watch():
    """Say once per commit that the newest version did not deploy: the bot
    keeps working on the old build, so nothing else would ever show it —
    and every next update fails the same way."""
    if not config.dependency_watch:
        return
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        failed = await failed_deploy(session)
    await set_state("deploy:failed", json.dumps(failed) if failed else None)
    if not failed or failed["sha"] == await get_state("deploy:failed_alerted"):
        return
    rows = [[InlineKeyboardButton(text="📄 Лог сборки", url=failed["log"])]] if failed["log"].startswith("https://") else []
    if failed["url"].startswith("https://"):
        rows.append([InlineKeyboardButton(text="🔗 Что в этой версии", url=failed["url"])])
    sent = await notifier.send(
        f"🟠 Новая версия бота не запустилась\n"
        f"Хостинг не смог собрать «{esc(failed['title'])}» ({esc(failed['sha'])}). Я работаю на прежней "
        f"версии ({esc(config.app_commit[:7])}), но и следующие обновления не встанут, пока сборка падает.\n\n"
        f"Что делать: открой лог сборки — обычно там видно, какой пакет не поставился. "
        f"Быстрый выход — откатить этот коммит в GitHub (Revert).",
        notifier.priority_for("warning"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)
    if sent:
        await set_state("deploy:failed_alerted", failed["sha"])


async def deploy_problem() -> dict | None:
    """The last failed deploy seen by run_deploy_watch, for the diagnostics."""
    raw = await get_state("deploy:failed")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


async def run_dependency_watch():
    """Weekly job: alert once when vulnerable packages appear (and once when
    they are gone); keep the last report for the weekly digest."""
    if not config.dependency_watch:
        return
    result = await check_dependencies()
    await set_state("deps:last_check", json.dumps(result))
    vuln_ids = sorted(i for ids in result["vulns"].values() for i in ids)
    signature = ",".join(vuln_ids)
    prev = await get_state("deps:vulns_alerted") or ""
    if signature == prev:
        return
    if vuln_ids:
        lines = [
            f"  • {esc(name)} {esc(installed_versions().get(name, '?'))}: "
            f"{len(ids)} {plural(len(ids), 'уязвимость', 'уязвимости', 'уязвимостей')} "
            f"({esc(', '.join(ids[:3]))}{'…' if len(ids) > 3 else ''})"
            for name, ids in result["vulns"].items()
        ]
        sent = await notifier.send(
            "🛡 В зависимостях бота нашлись известные уязвимости:\n"
            + "\n".join(lines)
            + "\n\nDependabot откроет PR с обновлением; после мержа и "
              "редеплоя я сообщу, что обновился.", Priority.NORMAL)
    else:
        sent = await notifier.send(
            "✅ Уязвимых зависимостей больше нет.", Priority.NORMAL)
    if sent:
        await set_state("deps:vulns_alerted", signature)


async def last_check() -> dict | None:
    raw = await get_state("deps:last_check")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _pr_links(prs: list[dict]) -> str:
    return ", ".join(f'<a href="{esc(p.get("url") or "")}">{esc(short_bump(p.get("title") or ""))}</a>'
                     for p in prs[:4])


def summary_lines(result: dict | None) -> list[str]:
    """Lines for the weekly report from a stored check_dependencies() result:
    only what needs a human. Updates that install themselves are not news."""
    if not result:
        return []
    lines: list[str] = []
    if result.get("vulns"):
        names = ", ".join(sorted(result["vulns"]))
        lines.append(f"🛡 Уязвимые пакеты: {esc(names)} — исправление придёт с Dependabot и встанет само")
    prs = result.get("waiting_prs") or [{**p, "major": True} for p in result.get("major_prs") or []]
    stuck = [p for p in prs if not p.get("major")]
    major = [p for p in prs if p.get("major")]
    if stuck:
        lines.append(f"🟠 Обновление не встало само — тесты не прошли: {_pr_links(stuck)}. "
                     f"Открой PR и посмотри, что упало.")
    if major:
        lines.append(f"🔀 Ждут твоего решения: {len(major)} PR с крупным обновлением — {_pr_links(major)}. "
                     f"Такие сами не вливаются: открой, прочитай «что поменялось» и нажми Merge.")
    return lines
