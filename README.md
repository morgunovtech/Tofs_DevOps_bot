# TofsDevOps — your personal DevOps in Telegram

> Watches your sites, fixes what it can, checks that search engines **and AI assistants** can see you — and stays quiet while everything is fine.

🇷🇺 [По-русски](README.ru.md) · [Everything it does](docs/FEATURES.en.md) · [Why (RU)](docs/WHY.md) · [Setup click by click (RU)](docs/SETUP.md)

**For Russian-speaking site owners.** The bot itself speaks Russian — every screen, alert and report. What your visitors see — the status page, its badges and the feedback widget — can be in English (`STATUS_PAGE_LANG=en`; the widget follows the language of the page).

Made for people who have never heard of DNS or a 502. Three buttons: **is everything OK**, **one site in detail**, **settings**. Every alert says what happened, what it means for visitors and what to do — with a button per step, written for your hosting (Cloudflare, Vercel, Netlify, GitHub Pages or a plain server).

## What you get

- **Your site is down — you know first.** A check every 5 minutes, anti-flap and a second opinion from outside before waking you, an alert ladder instead of daily nagging, downtime in minutes rather than percent.
- **Certificate, domain, DNS.** A warning when certificate auto-renewal stops working, domain expiry reminders, an alert when the NS records change — that is what a hijack looks like.
- **Broken links and deep 5xx** — the homepage opens while half the pages answer 500; a link leads to a dead end.
- **Search & AI visibility** — noindex, robots.txt, how much text is there without JavaScript, soft-404, sitemap, and a refusal to a request signed as GPTBot, ClaudeBot or PerplexityBot. Each finding is a numbered item with «what it costs you» and steps for your hosting. What cannot be seen from outside, the bot says so: Cloudflare recognises the real AI crawlers by their address, so for a site on Cloudflare the bot does not promise «AI can see you» — it shows where to check in the panel.
- **Reports that respect your attention** — one morning line, a Sunday digest, quiet hours, «I'm fixing it» silence. One level dictionary everywhere: 🔴 act now (rings), 🟠 act but not today (silent), and what is fine is a caption, never a message.

### For advanced users

- **API checks** — watch what the site actually does (`POST /api/v1/analyze` must answer with `scoreLabel`): method, headers, body; an API check gets status, phrase and timing only, no SEO noise. TCP ports and ping too.
- **Self-healing** — Cloudflare Pages redeploy and cache purge; on your own server, container restart and optional disk cleanup (dangling images and build cache), with a report of what was already done.
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

The bot keeps Moscow time until you pick yours: «⚙️ Настройки → 🕐 Часовой пояс» or `TIMEZONE=Europe/Berlin` in `.env` — reports and quiet hours follow it.

`docker-compose.yml`:

```yaml
services:
  bot:
    image: ghcr.io/morgunovtech/tofs_devops_bot:latest
    restart: always
    init: true
    stop_grace_period: 20s
    env_file: .env
    volumes:
      - bot_data:/app/data
    sysctls:
      - net.ipv4.ping_group_range=10001 10001   # ping:// monitors
volumes:
  bot_data:
```

Run `docker compose up -d` and send `/start` — the first user becomes the admin and adds the first site right in the chat. To update: `docker compose pull && docker compose up -d`; the bot says «🔄 Бот обновился» by itself. Every other setting is a line in the same `.env` (the full list is in [.env.example](.env.example)); the feedback widget, task links and the status page need a public address — see [Public address on your own server](#public-address-on-your-own-server).

Building from source — your own changes or a fork: `git clone`, `cp .env.example .env`, put your `TELEGRAM_BOT_TOKEN` into `.env`, `APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`; the repository's `docker-compose.yml` builds the image (the commit is what «🩺 Диагностика» shows as the build and what the «new version did not start» check compares with `main`).

**A server in Russia.** api.telegram.org is often unreachable from there directly, and the bot cannot talk to Telegram. Set a proxy in `.env`: `TELEGRAM_PROXY=socks5://user:password@host:1080` (`socks4://` and `http://host:3128` work too). Only the traffic to Telegram goes through it; the sites are checked directly.

Every `.env` integration, the architecture and the development loop: [docs/FEATURES.en.md](docs/FEATURES.en.md#quick-start).

#### Public address on your own server

The feedback widget, the links for your cron jobs and the status page need the bot's web server to be reachable from the internet over **HTTPS**. The bot itself speaks plain HTTP on port 8080, so put a reverse proxy with TLS in front of it and keep the port closed to everyone else:

1. In `docker-compose.yml` publish the port on this machine only:
   ```yaml
       ports:
         - "127.0.0.1:8080:8080"
   ```
   Not `"8080:8080"`: a port published by Docker bypasses ufw, the heartbeat secret then travels in clear text, and anyone can fake the visitor address the rate limit relies on.
2. Run a reverse proxy with TLS on the same machine — for example Caddy, which gets the certificate by itself. `/etc/caddy/Caddyfile`:
   ```
   bot.example.com {
       reverse_proxy 127.0.0.1:8080
   }
   ```
   nginx with a certificate or a Cloudflare Tunnel works too.
3. In `.env`: `PUBLIC_BASE_URL=https://bot.example.com` and `TRUST_PROXY=1`, then `docker compose up -d`.

Without TLS the widget does not load on https sites (browsers block it) and the heartbeat secret is visible to anyone on the way.

**The server clock.** The bot lives by the system time. On a home server or a NAS Docker may start before the time is synchronised. The bot notices a clock step and re-arms its schedule by itself, but starting Docker after the sync is safer. On systemd with timesyncd: `sudo systemctl enable systemd-time-wait-sync` and `sudo systemctl edit docker` → in `[Unit]` the line `After=time-sync.target` (with chrony use `chrony-wait.service` instead of time-wait-sync — check your distribution's documentation).

### Easier: Railway in one click — 5 minutes

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/lVRuwW)

1. Press **Deploy on Railway** and paste the token — the only field. The volume for the database, the public domain and the rest are already in the template.
2. Send `/start` — the first user becomes the admin and adds the first site right in the chat. The bot then offers a test alert and the external watchdog; «⚙️ Настройки → 🔌 Подключения» covers the rest.

**What it costs.** For the first 30 days Railway gives $5 and asks for no card. After that the account drops to the Free plan with $1 a month, and the bot needs about $2: without the Hobby plan it stops and nobody is told, and 30 days later Railway deletes the database volume. Hobby is $5 a month and includes $5 of usage — enough for the bot. Paying needs a card; Russian cards are not accepted abroad. The bot reminds you itself: after the first site and around day 25. Railway's terms: [its docs](https://docs.railway.com/pricing/free-trial).

Prefer your own fork? Railway → **Deploy from GitHub repo** → variable `TELEGRAM_BOT_TOKEN`, a volume at `/app/data`, a generated domain on port 8080.

### Rolling back a version

Every image is also published as `:sha-<7 chars>` — the commit it was built from. Before a version changes the database schema the bot copies the database to `data/backups/schema-v<N>-<date>.db` (N is the schema the copy holds; the last 3 are kept). When there is no room for the copy the bot still migrates and says so in Telegram. An old version refuses to start on a newer database and logs «База от более новой версии бота…».

Own server with Docker:
1. `docker compose stop bot`.
2. In `docker-compose.yml` replace `:latest` with the previous tag, e.g. `ghcr.io/morgunovtech/tofs_devops_bot:sha-1a2b3c4` (tags are on the package page on GitHub), and `docker compose up -d`. Building from source: `git checkout <sha> && docker compose up -d --build`.
3. If `docker compose logs bot` shows «База от более новой версии бота», the schema had already changed: `docker compose stop bot`, keep the current database and put back the copy that fits the old version (its schema number is in that log line):
   `docker compose run --rm --no-deps --entrypoint sh bot -c 'cp /app/data/bot.db /app/data/bot.db.before-rollback && cp /app/data/backups/schema-vNNN-DATE.db /app/data/bot.db && rm -f /app/data/bot.db-wal /app/data/bot.db-shm'`, then `docker compose up -d`. Everything the bot wrote after the update is lost.
4. Back to updates: `:latest` again and `docker compose pull && docker compose up -d`.

Railway: roll the deployment back in the Railway panel (Deployments → previous deployment → Redeploy). The copies live on the volume in `/app/data/backups`, reachable through the CLI — `railway volume` (check Railway's volume documentation). Without access to the volume, roll forward only: bring the newer version back.

## Status

Personal project, shared as-is: Python 3.12+, aiogram 3, aiohttp, APScheduler, SQLite. Named after Tofs — an Irish Terrier who takes uptime personally. Built in pair with [Claude](https://claude.com/claude-code). [MIT](LICENSE) © Semyon Morgunov.
