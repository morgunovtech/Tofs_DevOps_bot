# DevOps Bot — your personal DevOps in Telegram

> Monitors your sites, fixes what it can, checks that search engines **and AI agents** can see you — and stays silent while everything is fine.

🇷🇺 [Читать по-русски](README.ru.md) · [Зачем это вам (RU)](docs/WHY.md) · [Full feature list (RU)](docs/FEATURES.md)

Built around one principle: **minimal involvement**. You shouldn't babysit your infrastructure — the bot tells you when something needs attention, tries to fix it first, and proves everything is OK with one short digest a day.

## What it does

### 📡 Monitoring
- **Availability** every 5 minutes — with a twist: before paging you, it re-checks from external nodes (check-host.net). If the site is up for the rest of the world, it won't cry wolf.
- **SSL certificates** — an alert ladder (14 → 7 → 3 → 1 days) instead of daily nagging, plus a *renewal watch*: it tracks the certificate serial and warns when auto-renewal (Cloudflare/certbot) silently stops working.
- **Domain expiry** via WHOIS, with its own alert ladder.
- **DNS changes** — snapshots A/AAAA/CNAME/NS/MX records hourly; a changed NS record (that's what a hijack looks like) is a critical alert.
- **Broken links** — crawls your pages, separates *your* broken links (actionable) from dead external ones (noise).
- **Deep 5xx probe** — samples pages from `sitemap.xml` hourly, catching "homepage is fine but half the site is erroring".
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

### 💓 Dead-man switch
Your backup cron can't tell you it *didn't* run. Add one line to it:

```bash
curl -fsS https://your-server:8080/api/heartbeat/<secret>/backup
```

…and the bot alerts you when the job goes silent longer than its interval. Recovery is announced automatically.

### 📊 Reports that respect your attention
- **Morning digest** — one message: per-site status, 7-day uptime, upcoming SSL/domain expirations, heartbeat status, disk, SEO summary. Reads in 10 seconds.
- **Weekly report** (Sundays) — uptime, incidents, a response-time chart, Google/Yandex search metrics week-over-week.
- **Quiet hours** — non-critical alerts queue up overnight and arrive as one morning digest. "Site down" always gets through.
- **Escalation** — an unresolved critical incident re-alerts every 30 minutes and ignores mute. A dead site must not be forgettable.
- **Post-incident summaries** — recovery messages include duration and cause: *"✅ Recovered · down 12 min (14:03–14:15) · cause: HTTP 502"*.

### 🧹 It maintains itself
- Old raw checks roll up into daily aggregates nightly — the SQLite DB stays small forever.
- Nightly DB backups with rotation + a weekly off-host copy sent straight to your Telegram chat.
- Self-heartbeat to an external watchdog (healthchecks.io) — who watches the watchman.

### 📩 Bonus: feedback widget
A tiny embeddable JS widget ("Report a problem" button) for your sites — messages land in your Telegram with rate limiting and size caps server-side.

## What it looks like

```
🌅 Доброе утро · 29.07 09:00

✅ example.com — 96ms
✅ app.example.com — 137ms

Всё работает 👌

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

Requirements: Docker + a Telegram bot token from [@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/morgunovtech/devops-bot.git
cd devops-bot
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
main.py                 entry point: DB → webhook server → scheduler → polling
├── monitors/           availability, ssl, domain, dns, links, deep 5xx,
│                       seo/geo, host — each saves checks & manages incidents
├── services/           cloudflare actions, docker API, screenshots,
│                       google search console, yandex webmaster
├── reports/            scheduler (19 jobs), formatters, weekly chart
├── handlers/           telegram menu (a mini-dashboard) & alert buttons
├── web/                aiohttp: feedback API, heartbeat endpoint, widget
└── db/                 aiosqlite, WAL, retention rollups, backups
```

Stack: Python 3.12, [aiogram 3](https://github.com/aiogram/aiogram), aiohttp, APScheduler, SQLite (WAL), matplotlib. No external monitoring dependencies — the bot *is* the monitoring.

A note on the feedback widget's security model: the widget's "secret" ships to every site visitor, so the endpoint is treated as public — protection is rate limiting and strict size caps, not the token. This is a deliberate, documented trade-off for a personal-scale tool.

## Status

Personal project, built for my own sites and shared as-is: no SLA, no roadmap promises. Issues and PRs are welcome — especially new monitors and smarter auto-remediation.

Built in pair with [Claude](https://claude.com/claude-code).

## License

[MIT](LICENSE) © Semyon Morgunov
