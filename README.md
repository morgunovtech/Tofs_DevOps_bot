# TofsDevOps — your personal DevOps in Telegram

> Watches your sites, fixes what it can, checks that search engines **and AI assistants** can see you — and stays quiet while everything is fine.

🇷🇺 [По-русски](README.ru.md) · [Everything it does](docs/FEATURES.en.md) · [Why (RU)](docs/WHY.md) · [Setup click by click (RU)](docs/SETUP.md)

Made for people who have never heard of DNS or a 502. Three buttons: **is everything OK**, **one site in detail**, **settings**. Every alert says what happened, what it means for visitors and what to do — with a button per step, written for your hosting (Cloudflare, Vercel, Netlify, GitHub Pages or a plain server). **The bot speaks Russian:** every screen and alert is in Russian.

## What you get

- **Your site is down — you know first.** A check every 5 minutes, anti-flap and a second opinion from outside before waking you, an alert ladder instead of daily nagging, downtime in minutes rather than percent.
- **Certificate, domain, DNS.** A warning when certificate auto-renewal stops working, domain expiry reminders, an alert when the NS records change — that is what a hijack looks like.
- **Broken links and deep 5xx** — the homepage opens while half the pages answer 500; a link leads to a dead end.
- **Search & AI visibility** — noindex, robots.txt, how much text is there without JavaScript, soft-404, sitemap, and a refusal to a request signed as GPTBot, ClaudeBot or PerplexityBot. Each finding is a numbered item with «what it costs you» and steps for your hosting. What cannot be seen from outside, the bot says so: Cloudflare recognises the real AI crawlers by their address, so for a site on Cloudflare the bot does not promise «AI can see you» — it shows where to check in the panel.
- **Reports that respect your attention** — one morning line, a Sunday digest, quiet hours, «I'm fixing it» silence. One level dictionary everywhere: 🔴 act now (rings), 🟠 act but not today (silent), and what is fine is a caption, never a message.

### For advanced users

- **API checks** — watch what the site actually does (`POST /api/v1/analyze` must answer with `scoreLabel`): method, headers, body; an API check gets status, phrase and timing only, no SEO noise. TCP ports and ping too.
- **Self-healing** — Cloudflare Pages redeploy and cache purge; on your own server, container restart and disk cleanup, with a report of what was already done.
- **Scheduled jobs and other bots** — a dead-man switch: one `curl` line at the end of a cron job, and the bot notices when it goes silent.
- **Google Search Console and Yandex.Webmaster** — whether the site is actually in the index.
- **GitHub hygiene** in the Sunday digest (read-only token): stale PRs, extra branches, red CI, repositories that vanished.
- **Helpers, status page, feedback widget**, database copy and restore right from the chat.

## Install — two ways

Either way, first create a bot at [@BotFather](https://t.me/BotFather) and copy the token.

### Free: your own server with Docker

Any machine that runs around the clock will do: a VPS, a home server, a NAS, a Raspberry Pi. A ready image for amd64 and arm64 is published by itself after every change that passed the tests — nothing to clone or build.

Make a folder with two files. `.env` — one line:

```
TELEGRAM_BOT_TOKEN=token-from-BotFather
```

`docker-compose.yml`:

```yaml
services:
  bot:
    image: ghcr.io/morgunovtech/tofs_devops_bot:latest
    restart: always
    env_file: .env
    volumes:
      - bot_data:/app/data
volumes:
  bot_data:
```

Run `docker compose up -d` and send `/start` — the first user becomes the admin and adds the first site right in the chat. To update: `docker compose pull && docker compose up -d`; the bot says «🔄 Бот обновился» by itself. Every other setting is a line in the same `.env` (the full list is in [.env.example](.env.example)); the feedback widget, task links and the status page need a public address — see [docs/FEATURES.en.md](docs/FEATURES.en.md#quick-start).

Building from source — your own changes or a fork: `git clone`, `cp .env.example .env`, `docker compose up -d --build`; the repository's `docker-compose.yml` builds the image.

**A server in Russia.** api.telegram.org is often unreachable from there directly, and the bot cannot talk to Telegram. Set a proxy in `.env`: `TELEGRAM_PROXY=socks5://user:password@host:1080` (`socks4://` and `http://host:3128` work too). Only the traffic to Telegram goes through it; the sites are checked directly.

Every `.env` integration, the architecture and the development loop: [docs/FEATURES.en.md](docs/FEATURES.en.md#quick-start).

### Easier: Railway in one click — 5 minutes

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/lVRuwW)

1. Press **Deploy on Railway** and paste the token — the only field. The volume for the database, the public domain and the rest are already in the template.
2. Send `/start` — the first user becomes the admin and adds the first site right in the chat. The bot then offers a test alert and the external watchdog; «⚙️ Настройки → 🔌 Подключения» covers the rest.

**What it costs.** For the first 30 days Railway gives $5 and asks for no card. After that the account drops to the Free plan with $1 a month, and the bot needs about $2: without the Hobby plan it stops and nobody is told, and 30 days later Railway deletes the database volume. Hobby is $5 a month and includes $5 of usage — enough for the bot. Paying needs a card; Russian cards are not accepted abroad. The bot reminds you itself: after the first site and around day 25. Railway's terms: [its docs](https://docs.railway.com/pricing/free-trial).

Prefer your own fork? Railway → **Deploy from GitHub repo** → variable `TELEGRAM_BOT_TOKEN`, a volume at `/app/data`, a generated domain on port 8080.

## Status

Personal project, shared as-is: Python 3.12+, aiogram 3, aiohttp, APScheduler, SQLite. Named after Tofs — an Irish Terrier who takes uptime personally. Built in pair with [Claude](https://claude.com/claude-code). [MIT](LICENSE) © Semyon Morgunov.
