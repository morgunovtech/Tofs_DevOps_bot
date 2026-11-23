# CLAUDE.md — TofsDevOps

General rules (branches and PRs, worktrees, no twins, repos in `~`, secrets) live in `~/CLAUDE.md`; the principles shared with the owner's other bots are in `~/BOT_PRINCIPLES.md` and summarised in `docs/DEVELOPMENT.md`. This file holds only what is specific to this repository.

## What it is

A Telegram bot that watches the owner's sites (uptime, SSL, DNS, mail, links, SEO/GEO and AI-crawler visibility), heals what it can on Cloudflare Pages and Docker, and stays silent while everything is fine. The bot speaks Russian (the default) or English to its owner — `BOT_LANG`, `utils/i18n.py`, `locales/en/`; the visitor-facing status page and widget have their own switch (`STATUS_PAGE_LANG`). Python 3.12–3.14, aiogram 3, aiohttp, APScheduler, SQLite.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pip install --require-hashes --no-deps -r requirements.txt   # exactly what the image gets
ruff check .
pytest -q                                   # no network: tests/conftest.py closes sockets
pip-audit -r requirements.txt --strict
```

Changing a pin: edit `requirements.in`, regenerate the hashed lock (`docs/DEVELOPMENT.md`, «Зависимости»); `test_the_lock_is_compiled_from_requirements_in` fails otherwise.

## Deploy

- `main` is production: Railway redeploys from it (template `lVRuwW`, volume at `/app/data`, one required variable `TELEGRAM_BOT_TOKEN`); a green `main` also publishes `ghcr.io/morgunovtech/tofs_devops_bot` (`:latest`, `:sha-…`, amd64 + arm64).
- Dependabot's weekly PR auto-merges only patch and minor Python updates after every «lint · test · audit» check; the base image, Actions and majors wait for the owner.
- Live check: `https://devops-bot-production.up.railway.app/health` → `{"status": "ok"}`.
- Releases: `CHANGELOG.md` entry `## x.y.z — YYYY-MM-DD` + `version` in `pyproject.toml` (+ the `TofsDevOps/x.y` User-Agent in `utils/urls.py` for a major) in the PR; the annotated tag `vX.Y.Z` on the squash commit and the GitHub Release with English notes after the merge.

## Conventions

- Layout: `monitors/` (checkers) · `services/` (notifier, humanize, Cloudflare, Docker, GSC, Yandex, screenshots) · `reports/` (scheduler, digests) · `web/` (webhooks, status page, widget) · `handlers/` (chat) · `db/` · `utils/` (`redact.py`, `net.py`, `urls.py`, `text.py`).
- User-facing text is Russian in the code and doubles as the lookup key; English lives in `locales/en/` (`BOT_LANG`, `utils/i18n.py`). Wrap a new string in `t()` (`.format()` for placeholders, `plural()` and `fmt_duration()` from `utils.i18n`), add the English entry, and extend `tests/test_i18n.py` so the screen renders with `BOT_LANG=en` — it fails on the first Cyrillic letter. Module-level constants stay Russian and are translated where shown. The status page and widget keep their own `STATUS_PAGE_LANG`.
- Every proactive message goes through `services/notifier.py` (CRITICAL rings, NORMAL waits for quiet hours and mute, DIGEST waits only for mute); alert words come from `services/humanize.py` with tests. Icons: 🔴 act now, 🟠 act later, ⚠️ could not check — one dictionary.
- Untrusted text (page titles, widget messages, names) is escaped with `esc`/`esc_attr`; links are never clickable unless the bot built them; query strings are never shown.
- `.env.example` documents every variable with its default, and `cp .env.example .env` must change nothing but the token (`tests/test_docs.py`). The chat wins over `.env` for anything the owner changes more than once a year.
- Docs name screens and buttons exactly as the code does; `tests/test_docs.py` keeps README.md, README.ru.md and `docs/*.md` honest (links, stale screen names, token-shaped strings). `docs/FEATURES.md` is the Russian source of truth, `docs/FEATURES.en.md` its translation by meaning.

## Outside the repo

The README table «What it creates outside the repo» lists the bot at @BotFather, the Railway service and volume, the ghcr.io package, the external watchdog and the integration tokens. Keep it current whenever a new bot, key, server or hosted project appears.
