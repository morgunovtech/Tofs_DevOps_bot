"""Weekly GitHub hygiene (optional) — a section of the Sunday report.

Off until a token is set (GITHUB_TOKEN or «⚙️ Настройки → 🔌 Подключения → 🐙 GitHub»).
One GraphQL query reads the owner's repositories (GITHUB_OWNER, by default
the token's own account) and the report names only what needs a hand —
silent when healthy:

  * repositories that were there last week and are gone now (the list is
    kept in bot_state per owner; the first run only remembers it);
  * open pull requests older than PR_STALE_AFTER;
  * branches beyond the default one and the heads of open PRs;
  * «Automatically delete head branches» switched off;
  * the default branch's latest commit with a red CI (checks + statuses);
  * open Dependabot alerts — when the token may read them; without that
    permission the line is simply absent.

Archived repositories and forks are only watched for disappearance: there
is nothing to merge or switch on in them. A failed request never breaks
the weekly report — the section turns into one «⚠️ не смог проверить».

Token: a fine-grained personal access token, read-only — Metadata, Contents,
Pull requests, Commit statuses, optionally Dependabot alerts (docs/SETUP.md).
"""

import json
import logging
from datetime import UTC, datetime, timedelta

import aiohttp

from db.database import get_state, set_state
from services import integrations, updates
from utils.text import esc, plural

logger = logging.getLogger(__name__)

# Module-level so tests can point it at a fake server.
GRAPHQL = "https://api.github.com/graphql"
_TIMEOUT = aiohttp.ClientTimeout(total=40)
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=15)   # the diagnostics screen waits for it
PR_STALE_AFTER = timedelta(days=7)
# Long-lived branches that are part of how a repository works, not litter.
KEEP_BRANCHES = frozenset({"gh-pages"})
MAX_PAGES = 5          # 500 repositories — far beyond a personal account
LIST_LIMIT = 5         # items per report line before «и ещё N»

_REPO_FIELDS = """
nameWithOwner url isArchived isFork isTemplate deleteBranchOnMerge
defaultBranchRef { name target { ... on Commit { oid statusCheckRollup { state } } } }
pullRequests(states: OPEN, first: 50) {
  totalCount
  nodes { number title url createdAt isDraft headRefName author { login } headRepository { nameWithOwner } }
}
refs(refPrefix: "refs/heads/", first: 100) { totalCount nodes { name } }
"""
_ALERT_FIELDS = "nameWithOwner vulnerabilityAlerts(states: OPEN, first: 1) { totalCount }"
_VIEWER = "query { viewer { login } }"


class GitHubError(Exception):
    """GitHub could not be asked; the message is a short human reason."""


def available() -> bool:
    return bool(integrations.github_token())


def _query(fields: str, owner: str) -> str:
    root = "repositoryOwner(login: $login)" if owner else "viewer"
    params = "$login: String!, $after: String" if owner else "$after: String"
    return (f"query({params}) {{ {root} {{ login repositories(first: 100, after: $after, "
            f"ownerAffiliations: OWNER, orderBy: {{field: NAME, direction: ASC}}) "
            f"{{ pageInfo {{ hasNextPage endCursor }} nodes {{ {fields} }} }} }} }}")


async def _graphql(session: aiohttp.ClientSession, query: str, variables: dict | None = None,
                   token: str | None = None) -> dict:
    """data of a GraphQL answer (possibly partial: a field the token may not
    read comes back null). Raises GitHubError when there is no data at all."""
    headers = {"Authorization": f"bearer {token or integrations.github_token()}",
               "User-Agent": "TofsDevOps", "Accept": "application/json"}
    try:
        async with session.post(GRAPHQL, json={"query": query, "variables": variables or {}},
                                headers=headers) as resp:
            if resp.status == 401:
                raise GitHubError("токен не принят: истёк, отозван или скопирован не целиком")
            if resp.status == 403:
                raise GitHubError("GitHub отказал в доступе или лимит запросов исчерпан")
            if resp.status != 200:
                raise GitHubError(f"GitHub ответил ошибкой {resp.status}")
            body = await resp.json(content_type=None)
    except GitHubError:
        raise
    except (aiohttp.ClientError, TimeoutError, ValueError) as e:
        logger.warning("GitHub GraphQL failed: %s", e)
        raise GitHubError("GitHub не ответил") from e
    data = body.get("data") if isinstance(body, dict) else None
    if not data:
        errors = body.get("errors") if isinstance(body, dict) else None
        first = errors[0].get("message") if errors and isinstance(errors[0], dict) else None
        raise GitHubError(f"GitHub вернул ошибку: {first}" if first else "пустой ответ GitHub")
    return data


async def viewer_login(token: str | None = None) -> str | None:
    """The token's account — the live probe for the setup wizard; None
    when the token is not accepted or GitHub does not answer."""
    try:
        async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT) as session:
            data = await _graphql(session, _VIEWER, token=token)
    except GitHubError:
        return None
    return ((data.get("viewer") or {}).get("login")) or None


async def _repositories(session: aiohttp.ClientSession, fields: str, owner: str) -> tuple[str, list[dict], bool]:
    """(owner login, repository nodes, complete?) across pages. A page with a
    hole (a repository node GitHub could not return) is not complete: a
    repository missing from it must not read as «gone»."""
    nodes: list[dict] = []
    after, login, complete = None, owner, True
    for _ in range(MAX_PAGES):
        variables = {"after": after, **({"login": owner} if owner else {})}
        data = await _graphql(session, _query(fields, owner), variables)
        root = data.get("repositoryOwner" if owner else "viewer")
        if not root:
            raise GitHubError(f"владелец «{owner}» не найден или токену не виден")
        login = root.get("login") or login
        conn = root.get("repositories") or {}
        raw = conn.get("nodes") or []
        complete &= all(isinstance(n, dict) and n.get("nameWithOwner") for n in raw)
        nodes += [n for n in raw if isinstance(n, dict) and n.get("nameWithOwner")]
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return login, nodes, complete
        after = page.get("endCursor")
    return login, nodes, False


def _repo(node: dict) -> dict:
    """GraphQL repository node → the plain dict analyze() works with."""
    ref = node.get("defaultBranchRef") or {}
    rollup = ((ref.get("target") or {}).get("statusCheckRollup") or {})
    prs = node.get("pullRequests") or {}
    refs = node.get("refs") or {}
    return {
        "name": node["nameWithOwner"], "url": node.get("url") or f"https://github.com/{node['nameWithOwner']}",
        "archived": bool(node.get("isArchived")), "fork": bool(node.get("isFork")),
        "delete_branch_on_merge": bool(node.get("deleteBranchOnMerge")),
        "default": ref.get("name"), "ci": rollup.get("state"),
        "prs": [{"number": p.get("number"), "title": p.get("title") or "", "url": p.get("url") or "",
                 "created_at": p.get("createdAt"), "head": p.get("headRefName"),
                 "head_repo": (p.get("headRepository") or {}).get("nameWithOwner")}
                for p in prs.get("nodes") or [] if isinstance(p, dict)],
        "branches": [b.get("name") for b in refs.get("nodes") or [] if isinstance(b, dict) and b.get("name")],
        "branch_total": refs.get("totalCount") or 0,
    }


def _stale_days(iso: str | None, now: datetime) -> int | None:
    """Age in days of a PR older than PR_STALE_AFTER, else None."""
    try:
        created = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - created).days if now - created > PR_STALE_AFTER else None


def analyze(repos: list[dict], previous: list[str] | None, *, now: datetime | None = None,
            skip_pr_urls: frozenset[str] = frozenset()) -> dict:
    """Problems only. previous=None is the first run: nothing can be gone yet.
    skip_pr_urls: PRs another section of the weekly report already names
    (the bot's own Dependabot PRs) — one problem, one line."""
    now = now or datetime.now(UTC)
    names = {r["name"] for r in repos}
    report: dict = {"repos": len(repos),
                    "gone": sorted(set(previous) - names) if previous is not None else [],
                    "red_ci": [], "stale_prs": [], "extra_branches": [], "no_auto_delete": []}
    for r in sorted(repos, key=lambda x: x["name"].lower()):
        if r["archived"] or r["fork"] or not r["default"]:
            continue   # read-only, someone else's history, or empty: nothing to tidy
        if r["ci"] in ("FAILURE", "ERROR"):
            report["red_ci"].append({"repo": r["name"], "branch": r["default"], "url": r["url"]})
        for p in r["prs"]:
            days = _stale_days(p["created_at"], now)
            if days is not None and p["url"] not in skip_pr_urls:
                report["stale_prs"].append({"repo": r["name"], "number": p["number"], "title": p["title"],
                                            "url": p["url"], "days": days})
        heads = {p["head"] for p in r["prs"] if p["head_repo"] == r["name"]}
        extra = [b for b in r["branches"] if b != r["default"] and b not in heads and b not in KEEP_BRANCHES]
        unseen = max(0, r["branch_total"] - len(r["branches"]))   # beyond the first page
        if extra or unseen:
            report["extra_branches"].append({"repo": r["name"], "url": r["url"],
                                             "count": len(extra) + unseen, "names": extra[:3]})
        if not r["delete_branch_on_merge"]:
            report["no_auto_delete"].append({"repo": r["name"], "url": r["url"]})
    return report


async def _alerts(session: aiohttp.ClientSession, owner: str) -> dict[str, int] | None:
    """{repo: open Dependabot alerts} for repositories that have any; None
    when the token may not read them (no permission is not «zero»)."""
    try:
        _, nodes, _ = await _repositories(session, _ALERT_FIELDS, owner)
    except GitHubError as e:
        logger.info("Dependabot alerts unavailable: %s", e)
        return None
    readable = [n for n in nodes if n.get("vulnerabilityAlerts") is not None]
    if not readable:
        return None
    return {n["nameWithOwner"]: n["vulnerabilityAlerts"].get("totalCount") or 0
            for n in readable if (n["vulnerabilityAlerts"].get("totalCount") or 0) > 0}


async def check() -> dict:
    """One weekly look. Raises GitHubError when the repositories could not
    be read; the stored list of repositories moves on only after a complete
    read."""
    owner = integrations.github_owner()
    async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
        login, nodes, complete = await _repositories(session, _REPO_FIELDS, owner)
        vulns = await _alerts(session, owner)
    repos = [_repo(n) for n in nodes]
    key = f"gh:repos:{login.lower()}"
    raw = await get_state(key)
    try:
        previous = json.loads(raw) if raw else None
    except ValueError:
        previous = None
    deps = await updates.last_check() or {}
    skip = frozenset(p.get("url") or "" for p in deps.get("waiting_prs") or [])
    report = analyze(repos, previous if complete else None, skip_pr_urls=skip)
    report.update(owner=login, vulns=vulns or {},
                  checked_at=datetime.now(UTC).isoformat(timespec="minutes"))
    if complete:
        await set_state(key, json.dumps(sorted(r["name"] for r in repos)))
    return report


async def run_watch():
    """Weekly job, right before the Sunday report is built: the result is
    stored for summary_lines(); nothing is sent from here."""
    if not available():
        await set_state("gh:last_report", None)
        return
    try:
        report = await check()
    except GitHubError as e:
        report = {"error": str(e), "checked_at": datetime.now(UTC).isoformat(timespec="minutes")}
    await set_state("gh:last_report", json.dumps(report, ensure_ascii=False))


async def last_report() -> dict | None:
    raw = await get_state("gh:last_report")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# ── Weekly report lines ──────────────────────────────────────────────────────

def _short(full: str, owner: str) -> str:
    """morgunovtech/SocialBot → SocialBot when it is the owner's own."""
    prefix = f"{owner}/" if owner else ""
    return full[len(prefix):] if prefix and full.lower().startswith(prefix.lower()) else full


def _link(url: str, text: str) -> str:
    return f'<a href="{esc(url)}">{esc(text)}</a>' if url.startswith("https://") else esc(text)


def _listing(items: list[str]) -> str:
    more = f" и ещё {len(items) - LIST_LIMIT}" if len(items) > LIST_LIMIT else ""
    return ", ".join(items[:LIST_LIMIT]) + more


def summary_lines(report: dict | None) -> list[str]:
    """The GitHub section of the weekly report: one 🟠 line per kind of
    problem, each saying what to do. Nothing at all when all is tidy."""
    if not report:
        return []
    if report.get("error"):
        return [f"⚠️ GitHub: не смог проверить — {esc(report['error'])}"]
    owner = report.get("owner") or ""
    lines: list[str] = []
    gone = report.get("gone") or []
    if gone:
        lines.append(f"🟠 Пропали с прошлой недели: {_listing([esc(_short(g, owner)) for g in gone])} — удалены, "
                     f"переименованы или токен их больше не видит. Если это не ты — проверь доступы аккаунта.")
    red = report.get("red_ci") or []
    if red:
        items = [_link(f"{r['url']}/actions", f"{_short(r['repo'], owner)} ({r['branch']})") for r in red]
        lines.append(f"🟠 CI упал на основной ветке: {_listing(items)} — последний коммит не прошёл проверки, "
                     f"открой и посмотри, что красное.")
    stale = report.get("stale_prs") or []
    if stale:
        items = [_link(p["url"], f"{_short(p['repo'], owner)}#{p['number']}") + f" ({p['days']} дн.)" for p in stale]
        lines.append(f"🟠 PR висят дольше недели: {_listing(items)} — влей или закрой.")
    extra = report.get("extra_branches") or []
    if extra:
        items = [_link(f"{b['url']}/branches", f"{_short(b['repo'], owner)}: {b['count']}")
                 + (f" ({esc(', '.join(b['names']))}{'…' if b['count'] > len(b['names']) else ''})"
                    if b.get("names") else "") for b in extra]
        n = sum(b["count"] for b in extra)
        lines.append(f"🟠 {n} {plural(n, 'лишняя ветка', 'лишние ветки', 'лишних веток')} (не основная "
                     f"и без открытого PR): {_listing(items)}. Смёрженные можно удалить.")
    no_delete = report.get("no_auto_delete") or []
    if no_delete:
        items = [_link(f"{r['url']}/settings", _short(r["repo"], owner)) for r in no_delete]
        lines.append(f"🟠 Ветки не удаляются после мержа: {_listing(items)} — Settings → General → "
                     f"«Automatically delete head branches».")
    vulns = report.get("vulns") or {}
    if vulns:
        items = [_link(f"https://github.com/{repo}/security/dependabot", f"{_short(repo, owner)} ({n})")
                 for repo, n in sorted(vulns.items())]
        lines.append(f"🟠 Dependabot видит уязвимости: {_listing(items)} — вкладка Security, обычно "
                     f"лечится обновлением пакета.")
    if lines:
        lines.insert(0, f"🐙 GitHub{f' · {esc(owner)}' if owner else ''}:")
    return lines
