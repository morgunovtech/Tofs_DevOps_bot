"""Dependency self-awareness.

The bot cannot safely upgrade its own packages inside an immutable container
(the change would vanish at the next restart and nothing would test it).
What it CAN do:

  * on startup, notice that its installed packages or git commit changed
    since the last boot and say so («🔄 Бот обновился: aiohttp 3.10 → 3.14»);
  * once a week, compare installed versions with PyPI and the OSV
    vulnerability database and report what is outdated or vulnerable.

The actual upgrades arrive as Dependabot pull requests (.github/dependabot.yml)
that CI tests and auto-merges; the hosting platform redeploys from main.
"""

import asyncio
import json
import logging
import os
import platform
import re
from datetime import UTC, datetime
from importlib import metadata

import aiohttp

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
PYPI_SIMPLE = "https://pypi.org/simple/{name}/"
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


def _version_key(v: str) -> tuple[int, ...] | None:
    """Sort key for stable versions only; pre-releases and oddities → None."""
    parts = v.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


# ── Startup: "what changed since last boot?" ─────────────────────────────────

async def report_startup_changes():
    current = {
        "commit": config.app_commit,
        "python": platform.python_version(),
        "packages": installed_versions(),
    }
    prev_raw = await get_state("deps:snapshot")
    await set_state("deps:snapshot", json.dumps(current))
    if not prev_raw:
        return
    try:
        prev = json.loads(prev_raw)
    except ValueError:
        return
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


# ── Weekly: PyPI + OSV ───────────────────────────────────────────────────────

async def _latest(session: aiohttp.ClientSession, name: str) -> str | None:
    try:
        async with session.get(PYPI_SIMPLE.format(name=name), headers={
            "Accept": "application/vnd.pypi.simple.v1+json"}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception as e:
        logger.debug("PyPI lookup for %s failed: %s", name, e)
        return None
    stable = [(k, v) for v in data.get("versions", []) if (k := _version_key(v))]
    return max(stable)[1] if stable else None


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


async def open_major_prs(session: aiohttp.ClientSession) -> list[dict]:
    """Dependabot PRs that CI does not auto-merge (major bumps) and that
    are waiting for a human — public repositories only, no token needed."""
    slug = repo_slug()
    if not slug:
        return []
    try:
        async with session.get(GITHUB_PULLS.format(slug=slug), params={"state": "open", "per_page": "50"},
                               headers={"Accept": "application/vnd.github+json",
                                        "User-Agent": "TofsDevOps"}) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
    except Exception as e:
        logger.debug("open PRs of %s: %s", slug, e)
        return []
    out = []
    for pr in data if isinstance(data, list) else []:
        login = str(((pr or {}).get("user") or {}).get("login") or "")
        title = str((pr or {}).get("title") or "")
        if login.startswith("dependabot") and is_major_bump(title):
            out.append({"number": pr.get("number"), "title": title, "url": pr.get("html_url") or ""})
    return out


async def check_dependencies() -> dict:
    """{"outdated": [[name, installed, latest], …], "vulns": {name: [ids]},
    "major_prs": [{number, title, url}], "checked_at": iso} — network
    errors degrade to empty sections."""
    versions = installed_versions()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        latest = dict(zip(versions, await asyncio.gather(
            *(_latest(session, n) for n in versions)), strict=True))
        vulns = await _vulnerabilities(session, versions)
        major_prs = await open_major_prs(session)
    outdated = [
        [name, ver, latest[name]] for name, ver in versions.items()
        if latest.get(name) and _version_key(ver) and _version_key(latest[name])
        and _version_key(latest[name]) > _version_key(ver)
    ]
    return {"outdated": outdated, "vulns": vulns, "major_prs": major_prs,
            "checked_at": datetime.now(UTC).isoformat(timespec="minutes")}


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


def summary_lines(result: dict | None) -> list[str]:
    """Lines for the weekly report from a stored check_dependencies() result."""
    if not result:
        return []
    lines: list[str] = []
    if result.get("vulns"):
        names = ", ".join(sorted(result["vulns"]))
        lines.append(f"🛡 Уязвимые пакеты: {esc(names)} — жду PR от Dependabot")
    if result.get("outdated"):
        items = ", ".join(f"{n} {cur}→{new}" for n, cur, new in result["outdated"][:6])
        lines.append(f"📦 Есть обновления: {esc(items)}")
    if result.get("major_prs"):
        prs = result["major_prs"]
        items = ", ".join(f'<a href="{esc(p.get("url") or "")}">{esc(short_bump(p.get("title") or ""))}</a>'
                          for p in prs[:4])
        lines.append(f"🔀 Ждут твоего решения: {len(prs)} PR с крупным обновлением — {items}. "
                     f"Такие сами не вливаются: открой, прочитай «что поменялось» и нажми Merge.")
    if not lines:
        lines.append("📦 Зависимости актуальны, уязвимостей нет")
    return lines
