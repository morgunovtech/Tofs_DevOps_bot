# TofsDevOps — your personal DevOps in Telegram

> Monitors your sites, fixes what it can, checks that search engines **and AI agents** can see you — and stays silent while everything is fine.

🇷🇺 [Читать по-русски](README.ru.md) · [Зачем это вам (RU)](docs/WHY.md) · [Full feature list (RU)](docs/FEATURES.md)

Built around one principle: **minimal involvement**. You shouldn't babysit your infrastructure — the bot tells you when something needs attention, tries to fix it first, and proves everything is OK with one short digest a day.

## What it does

### 📡 Monitoring
- **Availability** every 5 minutes — with a twist: before paging you, it re-checks from external nodes (check-host.net). If the site is up for the rest of the world, it won't cry wolf.
- **SSL certificates** — an alert ladder (14 → 7 → 3 → 1 → 0 days) instead of daily nagging, plus a *renewal watch*: it tracks the certificate serial and warns when auto-renewal (Cloudflare/certbot) silently stops working.
- **Domain expiry** via RDAP (the protocol that replaced WHOIS for gTLDs in 2025; IANA bootstrap + rdap.org, a tiny async WHOIS fallback for .ru/.su/.рф), with its own alert ladder.
- **DNS changes** — snapshots A/AAAA/CNAME/NS/MX records hourly; a changed NS record (that's what a hijack looks like) is a critical alert.
- **Broken links** — crawls your pages, separates *your* broken links (actionable) from dead external ones (noise).
- **Deep 5xx probe** — samples pages from `sitemap.xml` hourly, catching "homepage is fine but half the site is erroring".
- **Keyword / stop-phrase check** — per site: alert when a phrase disappears from the page, or when a bad one appears («Fatal error»). Catches the classic "HTTP 200 with a white screen".
- **Custom HTTP requests** — per site: method (GET/HEAD/POST…), headers (`Authorization: Bearer …`) and a request body, so an API behind a token or a POST-only health endpoint is a first-class monitor.
- **TCP and ping monitors** — `tcp://mail.example.com:25`, `ping://10.0.0.1`: non-HTTP services (mail, SSH, databases) get the same incident pipeline, anti-flap and external second opinion.
- **Bot-host health** — disk usage and docker containers on the server the bot runs on.

### 🔍 SEO / GEO (AI visibility)
- **noindex detector** — the classic silent killer: one bad deploy ships `<meta name="robots" content="noindex">` to production and you find out a month later from your traffic graph. The bot finds out the same day and bypasses all mutes.
- **robots.txt** — blocking Googlebot/YandexBot is critical; blocking GPTBot/ClaudeBot/PerplexityBot is a warning (you *want* AI agents to see you).
- **AI user-agent probes** — requests your pages as GPTBot/ClaudeBot/PerplexityBot and alerts on 403s (hello, accidentally enabled Cloudflare "block AI bots" toggle).
- **No-JS content measurement** — AI crawlers don't execute JavaScript. If your page is an SPA with 60 chars of text before JS runs, AI agents see a blank page — the bot will tell you.
- Title/description/canonical/h1/OpenGraph/JSON-LD checks, soft-404 detection, `llms.txt` presence.
- **Real index status** (optional): Google Search Console API (homepage dropped out of the index → critical alert; weekly clicks/impressions) and Yandex.Webmaster API (searchable pages, SQI, site problems).

### 🔧 Self-healing (Cloudflare Pages friendly)
- Alerts come with **one-tap action buttons**: re-check, trigger a Pages deploy hook, purge the Cloudflare cache, grab a live page screenshot.
- **Auto-redeploy**: optionally fires the deploy hook itself when a site goes down (once per incident) and reports what it did.
- **Container auto-restart** and **disk auto-cleanup** (docker prune) on the bot's host — off by default, enabled by mounting the docker socket.

### 📱 Managed entirely from the chat
- **Zero-config onboarding** — the first user to `/start` becomes the admin; an empty bot walks you through adding your first site and checks it immediately.
- **Sites** — add/remove from the menu («🌍 Сайт детально»), with instant first-check feedback. The DB is the source of truth; `.env` is just an optional first-run seed.
- **⚙️ Settings** — morning report hour, evening report on/off, weekly report hour, quiet-hours presets: changed from the chat, applied to the running scheduler on the fly.
- **⚙️ Per-site overrides** — check interval, consecutive-failure threshold, accepted HTTP codes (e.g. `200-399,401`), keyword, "slow" threshold, HTTP method/headers/body — each site gets its own dials.
- **📤 Export / 📥 import** — the site list with all per-site settings as one JSON file: move between instances or keep a copy.
- **👀 Acknowledge** — «Видел, не напоминать 2 ч» on an escalating incident: snoozes the re-alerts without closing the incident.
- **🔧 Maintenance windows** — recurring schedules («weekdays 02:00–04:00», overnight windows welcome) per site or for everything: alerts and escalation stay silent, checks and stats keep running.
- **💓 Heartbeat jobs** — added from the chat with a ready-to-paste `curl` line for your cron.
- **⏸ Per-site pause** — deploying something big? Pause alerts for 1h or until morning; checks keep running silently.
- **🌐 Public status page** — one toggle serves an Uptime-Kuma-style page at `/status` (current state, 24h/7d/30d/90d uptime, anonymised incident history, maintenance banner), a `/status.json` twin for your own dashboards, and SVG uptime badges for your README. Off by default; an optional secret slug makes the URL private-by-obscurity.
- **🩺 Diagnostics** — one tap self-check: DB, web server, docker socket, Google/Yandex tokens (live probes), screenshot provider.
- **🔔 Test alert** — see what a critical alert looks like and trust the pipeline before you need it.

### 💓 Dead-man switch
Your backup cron can't tell you it *didn't* run. Add a job in the chat («📋 Ещё» → «💓 Heartbeats») — the bot hands you the exact line for your cron:

```bash
curl -fsS https://your-server:8080/api/heartbeat/<secret>/backup
```

…and the bot alerts you when the job goes silent longer than its interval. Recovery is announced automatically.

### 📊 Reports that respect your attention
- **Morning digest** — one message: per-site status, 7-day uptime, upcoming SSL/domain expirations, heartbeat status, disk, SEO summary. Reads in 10 seconds.
- **Weekly report** (Sundays) — honest 7/30/90-day uptime (raw checks + nightly rollups), incidents, an ASCII response-time chart (monospace, zero image libraries), Google/Yandex search metrics week-over-week, and the state of the bot's own dependencies.
- **Quiet hours** — non-critical alerts queue up overnight and arrive as one morning digest. "Site down" always gets through.
- **Silent delivery** — informational messages arrive without a sound; only critical alerts ring. Silence-by-default, literally.
- **Escalation** — a site that is still down re-alerts every 30 minutes and ignores mute. A dead site must not be forgettable (deliberately availability-only: half-hourly pages about an expiring cert would train you to ignore alerts).
- **Post-incident summaries** — recovery messages include duration and cause: *"✅ Recovered · down 12 min (14:03–14:15) · cause: HTTP 502"*.

### 🧹 It maintains itself
- Old raw checks roll up into daily aggregates nightly — the SQLite DB stays small forever.
- Nightly DB backups with rotation + a weekly off-host copy sent straight to your Telegram chat.
- Self-heartbeat to an external watchdog (healthchecks.io) — who watches the watchman.
- **Keeps its dependencies fresh without you**: Dependabot opens a weekly PR, CI (ruff + pytest + pip-audit) tests it, the auto-merge workflow merges minor/patch bumps, the host redeploys — and the bot announces «🔄 Бот обновился: aiohttp 3.10 → 3.14» on its next start. A weekly check against PyPI and the OSV vulnerability database reports anything outdated or vulnerable.
- Secrets take care of themselves: an empty or placeholder `WEBHOOK_SECRET`/`HEARTBEAT_SECRET` is replaced by a generated one stored in the DB. The two are always different — the webhook secret is public by design (it ships in the widget), the heartbeat one is not.

### 📩 Bonus: feedback widget
A tiny embeddable JS widget for your sites — messages land in your Telegram (rate limiting and size caps server-side) and stay readable in the bot («📋 Ещё» → «📩 Обратная связь»). Two modes: a floating "⚠️ Проблема?" button, or `button: false` plus `data-devops-feedback` on any link — the classic footer "нашли опечатку?" opens the form with the visitor's selected text pre-filled. Setup snippet and the CSP notes: [docs/SETUP.md](docs/SETUP.md#виджет-обратной-связи-нашли-опечатку).

## What it looks like

```
🌅 Доброе утро · 29.07 09:00

✅ example.com — 96ms
✅ app.example.com — 137ms

Всё спокойно 🐕

📈 Uptime 7д: example.com 100% · app.example.com 99.98%
💓 backup ✅ 7ч назад
💾 Диск: ✅ 41% (16.4/40.0 GB)
🔍 SEO: ✅ все сайты
```

```
🚨 САЙТ НЕДОСТУПЕН
https://example.com
Ошибка: HTTP 502
🌐 Подтверждено извне: сайт недоступен и со второй точки

🤖 Автодействие: 🚀 Передеплой запущен — Cloudflare Pages собирает сайт

[🔍 Перепроверить] [📸 Скрин]
[🚀 Передеплой] [🧹 Сброс кэша CF]
```

<!-- Add screenshots here: docs/screenshots/{menu,report,alert}.png -->

## Quick start

### Railway (no server needed)

Fork this repo → [Railway](https://railway.com) → **New Project → Deploy from GitHub repo**. Railway picks up the `Dockerfile`; then:

1. **Variables**: `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `DB_PATH=/app/data/bot.db`. Secrets are generated on first start.
2. **Attach Volume** at mount path `/app/data` — the SQLite DB must survive redeploys.
3. **Settings → Networking → Generate Domain** (port **8080**), then set `PUBLIC_BASE_URL=https://<your-app>.up.railway.app`.

Full click-by-click walkthrough in plain language (RU): [docs/SETUP.md](docs/SETUP.md).
Note: the docker-socket features (container auto-restart, disk auto-cleanup) only apply to self-hosting and silently disable themselves on Railway.

### Self-hosted (Docker Compose)

Requirements: Docker + a Telegram bot token from [@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/morgunovtech/Tofs_DevOps_bot.git
cd Tofs_DevOps_bot
cp .env.example .env
# Fill in ONE value: TELEGRAM_BOT_TOKEN (from @BotFather)
docker compose up -d --build
```

Send `/start` to your bot — the first user becomes the admin, and the bot
walks you through adding your first site. Sites, report hours, quiet hours
and heartbeat jobs are all managed from the chat; `.env` is only needed for
the optional integrations below (documented in [.env.example](.env.example)):

| Unlocks | Variables |
|---|---|
| Redeploy button + auto-redeploy | `DEPLOY_HOOKS`, `AUTO_REDEPLOY` |
| Cloudflare cache purge button | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID` |
| Dead-man switch | `HEARTBEAT_JOBS` |
| Google index status | `GSC_SERVICE_ACCOUNT_FILE`, `GSC_PROPERTY` |
| Yandex index status | `YANDEX_WEBMASTER_TOKEN` |
| Container restart / disk cleanup | `AUTORESTART_CONTAINERS` + docker.sock mount |
| External watchdog | `SELF_HEARTBEAT_URL` |

## Architecture

```
main.py                 entry point: DB → services → web server → scheduler → polling
├── monitors/           availability, ssl, domain (rdap), dns, links, deep 5xx,
│                       seo/geo, host — typed results, shared alert ladder
├── services/           notifier (the one door for every message: priorities,
│                       mute, quiet hours, pauses), settings, maintenance,
│                       secrets, runtime (admin), updates (dependency watch),
│                       cloudflare actions, docker API, screenshots, GSC, Yandex
├── reports/            scheduler (19 jobs), formatters, ASCII weekly report
├── handlers/           one router per screen behind a single AdminFilter
├── web/                aiohttp: feedback API, heartbeats, status page + JSON
│                       + badges, the widget; HTML template in web/templates
├── db/                 aiosqlite, WAL, PRAGMA user_version migrations,
│                       retention rollups, backups
├── utils/              url/text/time helpers shared by everything above
└── tests/              pytest: pure functions, DB, monitors against a local
                        HTTP server, notifier rules, web endpoints, bot screens
```

Stack: Python 3.12+, [aiogram 3](https://github.com/aiogram/aiogram), aiohttp, APScheduler, SQLite (WAL), stdlib `zoneinfo`. No external monitoring dependencies — the bot *is* the monitoring. No image libraries either: charts are monospace text.

A note on the feedback widget's security model: the widget's "secret" ships to every site visitor, so the endpoint is treated as public — protection is rate limiting and strict size caps, not the token. That is exactly why the heartbeat endpoint uses a *different* secret. A deliberate, documented trade-off for a personal-scale tool.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check .          # lint + import order
pytest -q             # ~70 tests, no network, no Telegram
pip-audit -r requirements.txt
```

CI runs the same three on every push and PR (Python 3.12 and 3.13). Dependabot files a grouped PR every Saturday; `.github/workflows/dependabot-automerge.yml` auto-merges minor/patch updates once CI is green — enable **Allow auto-merge** in the repository settings and protect `main` with the «lint · test · audit» check required, otherwise the merge does not wait for tests. The container runs as an unprivileged user; `entrypoint.sh` fixes the data volume's ownership first.

## Status

Personal project, built for my own sites and shared as-is: no SLA, no roadmap promises. Issues and PRs are welcome — especially new monitors and smarter auto-remediation.

Named after Tofs — an Irish Terrier who takes uptime personally.

Built in pair with [Claude](https://claude.com/claude-code).

## License

[MIT](LICENSE) © Semyon Morgunov
