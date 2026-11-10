"""Weekly GitHub hygiene against a fake GraphQL endpoint: only problems are
reported, a missing permission or a dead network never breaks the report."""

import json
from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeMessage, FakeState

from db.database import get_state, set_state
from handlers import setup
from reports import scheduler
from reports.weekly import build_weekly_report
from services import github, integrations
from services.integrations import Probe

OLD =(datetime.now(UTC) - timedelta(days=12)).isoformat().replace("+00:00", "Z")
FRESH = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")


def repo(name, *, archived=False, fork=False, auto_delete=True, ci="SUCCESS", branches=("main",), prs=()):
    full = f"me/{name}"
    return {
        "nameWithOwner": full, "url": f"https://github.com/{full}", "isArchived": archived, "isFork": fork,
        "isTemplate": False, "deleteBranchOnMerge": auto_delete,
        "defaultBranchRef": {"name": "main", "target": {"oid": "abc", "statusCheckRollup": {"state": ci} if ci else None}},
        "pullRequests": {"totalCount": len(prs), "nodes": [
            {"number": n, "title": f"PR {n}", "url": f"https://github.com/{full}/pull/{n}", "createdAt": at,
             "isDraft": False, "headRefName": head, "author": {"login": "me"}, "headRepository": {"nameWithOwner": full}}
            for n, at, head in prs]},
        "refs": {"totalCount": len(branches), "nodes": [{"name": b} for b in branches]},
    }


TIDY = [repo("bot"), repo("site", branches=("main", "gh-pages", "feature"), prs=((3, FRESH, "feature"),))]
MESSY = [
    repo("bot", ci="FAILURE", prs=((7, OLD, "old-work"),), branches=("main", "old-work", "stale-1", "stale-2")),
    repo("site", auto_delete=False),
    repo("archive", archived=True, auto_delete=False, ci="FAILURE", branches=("main", "x")),
    repo("someones-fork", fork=True, auto_delete=False, branches=("main", "patch-1")),
]


async def _github(state: dict):
    """Fake api.github.com/graphql: state["repos"] are the owner's repos,
    state["status"] the HTTP status, state["alerts"] what Dependabot says."""
    async def graphql(request: web.Request):
        state.setdefault("auth", []).append(request.headers.get("Authorization"))
        if state.get("status", 200) != 200:
            return web.json_response({"message": "Bad credentials"}, status=state["status"])
        query = (await request.json())["query"]
        if "viewer { login }" in query:
            return web.json_response({"data": {"viewer": {"login": "me"}}})
        if "vulnerabilityAlerts" in query:
            if state.get("alerts") is None:
                return web.json_response({"data": None, "errors": [
                    {"type": "FORBIDDEN", "message": "Resource not accessible by personal access token"}]})
            nodes = [{"nameWithOwner": r["nameWithOwner"],
                      "vulnerabilityAlerts": {"totalCount": state["alerts"].get(r["nameWithOwner"], 0)}}
                     for r in state["repos"]]
        else:
            nodes = state["repos"]
        root = "repositoryOwner" if "repositoryOwner" in query else "viewer"
        return web.json_response({"data": {root: {"login": "me", "repositories": {
            "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}})
    app = web.Application()
    app.router.add_post("/graphql", graphql)
    return TestServer(app)


async def test_off_without_a_token(db):
    await github.run_watch()
    assert await github.last_report() is None and github.summary_lines(None) == []


def test_analyze_names_only_problems():
    repos = [github._repo(n) for n in MESSY]
    report = github.analyze(repos, ["me/bot", "me/site", "me/gone"],
                            skip_pr_urls=frozenset())
    assert report["gone"] == ["me/gone"]
    assert [r["repo"] for r in report["red_ci"]] == ["me/bot"]                  # archived is skipped
    assert [(p["repo"], p["number"]) for p in report["stale_prs"]] == [("me/bot", 7)]
    # old-work is the head of an open PR, main is the default — two are litter.
    assert report["extra_branches"] == [{"repo": "me/bot", "url": "https://github.com/me/bot", "count": 2,
                                         "names": ["stale-1", "stale-2"]}]
    assert [r["repo"] for r in report["no_auto_delete"]] == ["me/site"]          # forks and archives too
    tidy = github.analyze([github._repo(n) for n in TIDY], None)
    assert not any(tidy[k] for k in ("gone", "red_ci", "stale_prs", "extra_branches", "no_auto_delete"))
    # The bot's own Dependabot PR is already a line of the dependency section.
    skip = github.analyze(repos, None, skip_pr_urls=frozenset({"https://github.com/me/bot/pull/7"}))
    assert skip["stale_prs"] == []


async def test_weekly_watch_end_to_end(bot, db, monkeypatch):
    state = {"repos": TIDY + [repo("old-project")], "alerts": None}
    async with await _github(state) as server:
        monkeypatch.setattr(github, "GRAPHQL", f"http://127.0.0.1:{server.port}/graphql")
        await integrations.set_value("github_token", "github_pat_" + "a" * 40)

        await github.run_watch()                       # first run: remembers the list, all tidy
        assert github.summary_lines(await github.last_report()) == []      # silent when healthy
        assert json.loads(await get_state("gh:repos:me")) == ["me/bot", "me/old-project", "me/site"]
        assert state["auth"][-1] == "bearer github_pat_" + "a" * 40

        state.update(repos=MESSY, alerts={"me/site": 2})
        await github.run_watch()
        lines = github.summary_lines(await github.last_report())
        text = "\n".join(lines)
        assert lines[0] == "🐙 GitHub · me:"
        assert "Пропали с прошлой недели: old-project" in text                # archive/fork are still there
        assert 'CI упал на основной ветке: <a href="https://github.com/me/bot/actions">bot (main)</a>' in text
        assert "PR висят дольше недели" in text and "bot#7</a> (12 дн.)" in text
        assert "2 лишние ветки" in text and "stale-1, stale-2" in text
        assert "Ветки не удаляются после мержа" in text and "Automatically delete head branches" in text
        assert "Dependabot видит уязвимости" in text and "site (2)</a>" in text
        assert all(line.startswith(("🐙", "🟠")) for line in lines)           # one level, one line each

        weekly, _ = await build_weekly_report()
        assert "🐙 GitHub · me:" in weekly

        state["alerts"] = None                         # no Dependabot permission: the line just goes
        await github.run_watch()
        assert "Dependabot" not in "\n".join(github.summary_lines(await github.last_report()))


async def test_a_hole_in_the_answer_is_not_a_gone_repository(bot, db, monkeypatch):
    state = {"repos": [repo("bot"), repo("site")], "alerts": None}
    async with await _github(state) as server:
        monkeypatch.setattr(github, "GRAPHQL", f"http://127.0.0.1:{server.port}/graphql")
        await integrations.set_value("github_token", "github_pat_" + "c" * 40)
        await github.run_watch()
        state["repos"] = [repo("bot"), None]           # GitHub could not return one node
        await github.run_watch()
    assert (await github.last_report())["gone"] == []
    assert json.loads(await get_state("gh:repos:me")) == ["me/bot", "me/site"]   # baseline kept


async def test_errors_never_break_the_weekly_report(bot, db, monkeypatch, cfg, tmp_path):
    cfg(dependency_watch=False, db_path=str(tmp_path / "bot.db"))   # the weekly DB copy stays out of the repo
    state = {"repos": TIDY, "status": 401}
    async with await _github(state) as server:
        monkeypatch.setattr(github, "GRAPHQL", f"http://127.0.0.1:{server.port}/graphql")
        await integrations.set_value("github_token", "github_pat_" + "b" * 40)
        await github.run_watch()
    assert github.summary_lines(await github.last_report()) == [
        "⚠️ GitHub: не смог проверить — токен не принят: истёк, отозван или скопирован не целиком"]

    monkeypatch.setattr(github, "GRAPHQL", "http://127.0.0.1:9/graphql")      # nothing listens there
    await github.run_watch()
    assert (await github.last_report())["error"] == "GitHub не ответил"

    async def boom():
        raise RuntimeError("unexpected")
    monkeypatch.setattr(github, "run_watch", boom)
    await set_state("gh:last_report", "not json")
    await scheduler.send_weekly_report()
    assert any("Итоги недели" in (m.get("text") or "") for m in bot.sent)


async def test_setup_wizard_checks_the_token_live(bot, db, monkeypatch):
    async def login(token=None):
        return "me" if (token or integrations.github_token()).startswith("github_pat_good") else None
    monkeypatch.setattr(github, "viewer_login", login)

    async def check(token=None):
        name = await login(token)
        return (Probe.OK, name) if name else (Probe.REJECTED, None)
    monkeypatch.setattr(github, "check_token", check)
    msg = FakeMessage("short")
    await setup.msg_gh_token(msg, FakeState())
    assert "Не похоже" in msg.texts[-1]
    msg = FakeMessage("github_pat_bad" + "x" * 30)
    await setup.msg_gh_token(msg, FakeState())
    assert "не принял" in msg.texts[-1] and not github.available()
    msg = FakeMessage("github_pat_good" + "y" * 30)
    await setup.msg_gh_token(msg, FakeState())
    assert "аккаунт me" in msg.texts[-1] and github.available() and integrations.source("github_token") == "bot"
    msg = FakeMessage("@morgunovtech")
    await setup.msg_gh_owner(msg, FakeState())
    assert integrations.github_owner() == "morgunovtech"
    items = {i.key: i for i in await setup.build_items()}
    assert items["github"].status == "ok"
