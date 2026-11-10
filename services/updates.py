"""Dependency self-awareness.

The bot cannot safely upgrade its own packages inside an immutable container
(the change would vanish at the next restart and nothing would test it).
What it CAN do:

  * on startup, notice that Python or an installed package changed since
    the last boot and say so («🔄 Бот обновился: aiohttp 3.10 → 3.14»); a
    new build with the same versions is a log line, not a message;
  * every hour, notice that the newest commit on main failed to deploy while
    the bot still runs an older build — then every next update fails too;
  * once a week, ask OSV about known vulnerabilities in every installed
    package and list the Dependabot PRs that wait for a human.

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
from services import integrations, notifier
from services.notifier import Priority
from utils.text import err_text, esc, esc_attr, fmt_date, plural

logger = logging.getLogger(__name__)

# The direct dependencies (requirements.in): named one by one in «🔄 Бот
# обновился». Everything installed is compared and asked about in OSV —
# yarl, multidict, certifi… carry every request too. A test keeps this
# list equal to the requirements file (idna is pinned there: the bot imports
# it directly).
PACKAGES = (
    "aiogram", "aiohttp", "aiohttp-socks", "aiosqlite", "apscheduler", "beautifulsoup4",
    "python-dotenv", "cryptography", "dnspython", "idna",
)
_NOT_THE_BOTS = {"pip", "setuptools", "wheel"}   # the installer, not the bot
GITHUB_PULLS = "https://api.github.com/repos/{slug}/pulls"
GITHUB_COMMIT = "https://api.github.com/repos/{slug}/commits/{ref}"
GITHUB_STATUS = "https://api.github.com/repos/{slug}/commits/{ref}/status"
_GH_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "TofsDevOps"}
# A minor/patch PR still open after this long did not auto-merge: CI is red.
STUCK_AFTER = timedelta(days=2)
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _canonical(name: str) -> str:
    """PEP 503 form: «APScheduler», «python_dotenv» → «apscheduler», «python-dotenv»."""
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_versions() -> dict[str, str]:
    """{name: version} of every installed distribution (the image holds only
    the bot's packages), the installer itself left out. A name found twice on
    sys.path keeps the first — the one `import` would load."""
    out: dict[str, str] = {}
    for dist in metadata.distributions():
        name = _canonical(dist.metadata["Name"] or "")
        if name and name not in _NOT_THE_BOTS:
            out.setdefault(name, dist.version)
    return dict(sorted(out.items()))


# ── Startup: "what changed since last boot?" ─────────────────────────────────

async def report_startup_changes() -> bool:
    """Tell the owner when Python or a package changed since the last boot:
    the direct dependencies by name, the rest as one «и ещё N» line. A new
    build with the same versions (a commit that touched only the code or
    requirements-dev.txt) is a log line — the build number stays visible in
    «🩺 Диагностика». True when anything changed, build included: main.py
    re-runs the SEO audit on that."""
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
    new_build = bool(current["commit"]) and prev.get("commit") != current["commit"]
    lines = []
    if prev.get("python") != current["python"]:
        lines.append(f"  • Python {esc(prev.get('python'))} → {esc(current['python'])}")
    old_pkgs = {_canonical(k): v for k, v in (prev.get("packages") or {}).items()}
    changed = {name: (old_pkgs[name], ver) for name, ver in current["packages"].items()
               if old_pkgs.get(name) and old_pkgs[name] != ver}
    for name in PACKAGES:
        if name in changed:
            lines.append(f"  • {name} {esc(changed[name][0])} → {esc(changed[name][1])}")
    others = sorted(set(changed) - set(PACKAGES))
    if others:
        n = len(others)
        lines.append(f"  • и ещё {n} {plural(n, 'вспомогательный пакет', 'вспомогательных пакета', 'вспомогательных пакетов')}: "
                     f"{esc(', '.join(others[:5]))}{'…' if n > 5 else ''}")
    if lines:
        await notifier.send("🔄 Бот обновился:\n" + "\n".join(lines), Priority.NORMAL)
    elif new_build:
        logger.info("New build %s → %s, same Python and packages: nothing to tell the owner",
                    prev.get("commit"), current["commit"])
    return bool(lines) or new_build


# ── Weekly: OSV + PRs waiting for a human ───────────────────────────────────

async def _vulnerabilities(session: aiohttp.ClientSession,
                           versions: dict[str, str]) -> dict[str, list[str]] | None:
    """{name: [ids]} — None when OSV could not be asked: «unknown» must not
    read as «no vulnerabilities» (that would send a false all-clear)."""
    names = list(versions)
    body = {"queries": [{"package": {"name": n, "ecosystem": "PyPI"},
                         "version": versions[n]} for n in names]}
    try:
        async with session.post(OSV_BATCH, json=body) as resp:
            if resp.status != 200:
                logger.warning("OSV answered HTTP %s", resp.status)
                await integrations.record_result("osv", False, f"OSV ответил HTTP {resp.status}")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning("OSV query failed: %s", err_text(e))
        await integrations.record_result("osv", False, f"OSV не ответил ({err_text(e)})")
        return None
    await integrations.record_result("osv", True)
    if not isinstance(data, dict):
        return None
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
# A group PR («Bump the python-deps group with 2 updates») names each update
# in its text: «Updates `aiogram` from 3.31.0 to 4.0.0».
_UPDATES = re.compile(r"Updates `([^`]+)` from (\d+)(\S*) to (\d+)(\S*)")
_MAJOR_MARK = re.compile(r"update-type:\s*version-update:semver-major")


def _bumps(title: str, body: str = "") -> list[tuple[str, str, str, bool]]:
    """[(name, old, new, major)] from a Dependabot title and its text."""
    found = [(m.group(1), m.group(2) + m.group(3), m.group(4) + m.group(5), m.group(2) != m.group(4))
             for m in (*_BUMP.finditer(title), *_UPDATES.finditer(body or ""))]
    return list(dict.fromkeys(found))


def is_major_bump(title: str, body: str = "") -> bool:
    """«Bump aiogram from 3.31.0 to 4.0.0», or a group PR with such an
    update inside → the major version changed: auto-merge will not take it."""
    return any(b[3] for b in _bumps(title, body)) or bool(_MAJOR_MARK.search(body or ""))


def short_bump(title: str, body: str = "") -> str:
    """«aiogram 3.31.0→4.0.0»; a group PR lists its updates, majors first."""
    bumps = sorted(_bumps(title, body), key=lambda b: not b[3])   # majors first
    if not bumps:
        return title[:60]
    return ", ".join(f"{name} {old}→{new}" for name, old, new, _ in bumps[:2]) + ("…" if len(bumps) > 2 else "")


_no_repo_said = False


def _say_no_repo() -> None:
    """Once per start: without the repository the deploy check and the list of
    PRs that wait for a human are off — say why instead of being silent."""
    global _no_repo_said
    if not _no_repo_said:
        _no_repo_said = True
        logger.info("Deploy check and waiting Dependabot PRs are off: set GITHUB_REPOSITORY=owner/name "
                    "(Railway and GitHub Actions set it themselves)")


class _Unknown(Exception):
    """GitHub did not answer (rate limit on a shared IP, network)."""


def _gh_headers() -> dict:
    """With the bot's GitHub token when there is one: the unauthenticated
    limit (60 requests an hour per address) is shared with everyone on the
    hosting's address."""
    token = integrations.github_token()
    return {**_GH_HEADERS, "Authorization": f"Bearer {token}"} if token else dict(_GH_HEADERS)


async def _gh_json(session: aiohttp.ClientSession, url: str, **params):
    """GitHub REST (public repositories; the token, if any): JSON or None."""
    try:
        async with session.get(url, params=params or None, headers=_gh_headers()) as resp:
            if resp.status != 200:
                logger.warning("GitHub %s: HTTP %s", url, resp.status)
                return None
            return await resp.json(content_type=None)
    except Exception as e:
        logger.warning("GitHub %s: %s", url, err_text(e))
        return None


async def waiting_prs(session: aiohttp.ClientSession) -> list[dict] | None:
    """Dependabot PRs that wait for a human: major bumps and base-image bumps
    (never auto-merged) and pip ones still open after STUCK_AFTER (auto-merge
    waits for a green CI, so the tests failed). None when GitHub did not
    answer — «unknown» is not «none waiting»."""
    slug = repo_slug()
    if not slug:
        _say_no_repo()
        return []
    data = await _gh_json(session, GITHUB_PULLS.format(slug=slug), state="open", per_page="50")
    if not isinstance(data, list):
        return None
    now = datetime.now(UTC)
    out = []
    for pr in data:
        login = str(((pr or {}).get("user") or {}).get("login") or "")
        title = str((pr or {}).get("title") or "")
        body = str((pr or {}).get("body") or "")
        if not login.startswith("dependabot"):
            continue
        # The base image (a new Python) and the Actions are merged by a human,
        # whatever their CI says (.github/workflows/dependabot-automerge.yml);
        # a major bump too — alone or inside the weekly group PR.
        branch = str(((pr or {}).get("head") or {}).get("ref") or "")
        manual = is_major_bump(title, body) or branch.startswith(("dependabot/docker/", "dependabot/github_actions/"))
        try:
            opened = datetime.fromisoformat(str(pr.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            opened = now
        if manual or now - opened > STUCK_AFTER:
            out.append({"number": pr.get("number"), "title": title, "label": short_bump(title, body),
                        "url": pr.get("html_url") or "", "major": manual})
    return out


async def check_dependencies() -> dict:
    """{"vulns": {name: [ids]} or None when OSV did not answer,
    "waiting_prs": [{number, title, label, url, major}] or None when GitHub
    did not answer, "checked_at": iso}. Every installed package goes to OSV:
    a hole in yarl or certifi is as real as one in aiohttp."""
    versions = installed_versions()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        vulns = await _vulnerabilities(session, versions)
        prs = await waiting_prs(session)
    return {"vulns": vulns, "waiting_prs": prs,
            "checked_at": datetime.now(UTC).isoformat(timespec="minutes")}


# ── Hourly: did the newest version deploy? ───────────────────────────────────

async def failed_deploy(session: aiohttp.ClientSession) -> dict | None:
    """{sha, title, url, log} of the newest commit on main when the hosting
    reported a failed deploy for it and this bot still runs an older build.
    Raises _Unknown when GitHub did not answer."""
    slug, running = repo_slug(), config.app_commit
    if not slug:
        _say_no_repo()
        return None
    if not running:
        return None
    head = await _gh_json(session, GITHUB_COMMIT.format(slug=slug, ref="main"))
    if not isinstance(head, dict):
        raise _Unknown
    sha = str(head.get("sha") or "")
    if not sha or sha.startswith(running):
        return None
    status = await _gh_json(session, GITHUB_STATUS.format(slug=slug, ref=sha))
    if not isinstance(status, dict):
        raise _Unknown
    # Only a deploy status counts: a red status from some other integration
    # on the same commit is not «the bot did not start».
    failed = [st for st in status.get("statuses") or [] if st.get("state") in ("failure", "error")
              and "deploy" in f"{st.get('description')} {st.get('context')}".lower()]
    if not failed:
        return None   # deployed, or still deploying
    message = str((head.get("commit") or {}).get("message") or "")
    title = (message.splitlines() or [sha[:7]])[0][:80]    # an empty commit message is not a crash
    return {"sha": sha[:7], "title": title, "url": head.get("html_url") or "",
            "log": failed[0].get("target_url") or "", "running": running}


async def run_deploy_watch():
    """Say once per commit that the newest version did not deploy: the bot
    keeps working on the old build, so nothing else would ever show it —
    and every next update fails the same way."""
    if not config.dependency_watch:
        return
    stale = await deploy_problem()
    if stale and stale.get("running") and stale["running"] != config.app_commit:
        await set_state("deploy:failed", None)   # a newer build is running: that failure is history
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            failed = await failed_deploy(session)
    except _Unknown:
        return   # could not check — the last known state stays as it was
    except Exception:
        logger.exception("Deploy watch failed")
        return
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
    last = await last_check() or {}
    if result["waiting_prs"] is None:
        # GitHub did not answer: last week's list, marked as such.
        result["waiting_prs"] = last.get("waiting_prs") or []
        result["prs_unknown"] = True
    if result["vulns"] is None:
        # OSV did not answer: keep last week's verdict, say nothing new.
        result["vulns"] = last.get("vulns") or {}
        result["vulns_unknown"] = True
        result["vulns_checked_at"] = last.get("vulns_checked_at")
        await set_state("deps:last_check", json.dumps(result))
        return
    result["vulns_checked_at"] = result["checked_at"]
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
    # A result stored before «label» existed has only the title.
    return ", ".join(f'<a href="{esc_attr(p.get("url") or "")}">{esc(p.get("label") or short_bump(p.get("title") or ""))}</a>'
                     for p in prs[:4])


def summary_lines(result: dict | None) -> list[str]:
    """Lines for the weekly report from a stored check_dependencies() result:
    only what needs a human. Updates that install themselves are not news."""
    if not result:
        return []
    lines: list[str] = []
    if result.get("vulns_unknown"):
        when = f", последняя проверка {fmt_date(result['vulns_checked_at'])}" if result.get("vulns_checked_at") else ""
        lines.append(f"⚠️ Уязвимости: база OSV не ответила{when} — повторю через неделю")
    if result.get("prs_unknown"):
        lines.append("⚠️ PR Dependabot: GitHub не ответил — список прошлой недели")
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
        lines.append(f"🔀 Ждут твоего решения: {len(major)} PR — {_pr_links(major)}. "
                     f"Такие сами не вливаются (крупная версия или базовый образ): "
                     f"открой, прочитай «что поменялось» и нажми Merge.")
    return lines
