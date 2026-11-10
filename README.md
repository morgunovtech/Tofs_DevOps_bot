# TofsDevOps — your personal DevOps in Telegram

> Watches your sites, fixes what it can, checks that search engines **and AI assistants** can see you — and stays quiet while everything is fine.

🇷🇺 [По-русски](README.ru.md) · [Everything it does](docs/FEATURES.en.md) · [Why (RU)](docs/WHY.md) · [Setup click by click (RU)](docs/SETUP.md)

**For Russian-speaking site owners.** The bot itself speaks Russian — every screen, alert and report. What your visitors see — the status page, its badges and the feedback widget — can be in English (`STATUS_PAGE_LANG=en`; the widget follows the language of the page).

Made for people who have never heard of DNS or a 502. Three buttons under the input field: **🏠 Главная** (the main screen answers «is everything OK?»), **📖 Что я умею**, **❓ Значки**; every site has its own card. Every alert says what happened, what it means for visitors and what to do — with a button per step, written for your hosting (Cloudflare, Vercel, Netlify, GitHub Pages or a plain server).

## What you get

- **Your site is down — you know first.** A check every 5 minutes, anti-flap and a second opinion from outside before waking you, an alert ladder instead of daily nagging, downtime in minutes rather than percent.
- **Certificate, domain, DNS.** A warning when the certificate has not been replaced `SSL_RENEW_WARN_DAYS` (10) days before expiry — auto-renewal looks broken; domain expiry reminders, an alert when the NS records change — that is what a hijack looks like.
- **Broken links and deep 5xx** — the homepage opens while half the pages answer 500 (pages from the sitemap are sampled hourly); a link leads to a dead end (the links are crawled every 6 hours; a 503 or a timeout counts only when it persists for three hours, a bot block never).
- **Search & AI visibility** — noindex, robots.txt, how much text is there without JavaScript, soft-404, sitemap, and a refusal to a request signed as GPTBot, ClaudeBot or PerplexityBot. Each finding is a numbered item with «what it costs you» and steps for your hosting. What cannot be seen from outside, the bot says so: Cloudflare recognises the real AI crawlers by their address, so for a site on Cloudflare the bot does not promise «AI can see you» — it shows where to check in the panel.
- **Reports that respect your attention** — one morning message, a Sunday digest, quiet hours (off until you set them), «I'm fixing it» silence. One level dictionary everywhere: 🔴 act now (rings), 🟠 act but not today (silent), and what is fine is a caption, never a message. Facts of normal operation do come as messages too: «✅ … снова открывается» (it rings, unless «🔔 Будить: только «сайт лёг»» is on), «🔄 Бот обновился», «⏸ Меня не было», reports of what the bot fixed itself.

### For advanced users

- **API checks** — watch what the site actually does (`POST /api/v1/analyze` must answer with `scoreLabel`): method, headers, body; an API check gets status, phrase and timing only, no SEO noise. TCP ports and ping too.
- **Self-healing** — Cloudflare Pages redeploy and cache purge; on your own server — only with docker.sock mounted, see [Security](#security) — container restart and optional disk cleanup (`AUTO_CLEANUP=1`: dangling images and build cache unused for a day), with a report of what was already done.
- **Scheduled jobs and other bots** — a dead-man switch: one `curl` line at the end of a cron job, and the bot notices when it goes silent.
- **Google Search Console and Yandex.Webmaster** — whether the site is actually in the index.
- **GitHub hygiene** in the Sunday digest (read-only token): stale PRs, extra branches, red CI, repositories that vanished.
- **Helpers, status page, feedback widget**, a copy of the database (on Sundays to your private chat, without tokens) and restore right from the chat.

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

Run `docker compose up -d` and send `/start` in a private chat with the bot — the first `/start` becomes the owner (add `TELEGRAM_ADMIN_USER_ID=<your id>` to `.env` to pin the seat, see [Security](#security)) and adds the first site right in the chat. To update: `docker compose pull && docker compose up -d`; the bot says «🔄 Бот обновился» by itself after the restart, but it does not tell you that a new image exists. Every other setting is a line in the same `.env` (the full list is in [.env.example](.env.example)); the feedback widget, task links and the status page need a public address — see [Public address on your own server](#public-address-on-your-own-server).

Building from source — your own changes or a fork: `git clone`, `cp .env.example .env`, put your `TELEGRAM_BOT_TOKEN` into `.env`, `APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build`; the repository's `docker-compose.yml` builds the image (the commit is what «⚙️ Настройки → 🔌 Подключения» shows as the build and what the «new version did not start» check compares with `main`).

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

Without TLS the widget does not load on https sites (browsers block mixed content), and the heartbeat secret (it is in the URL path) and the status page's secret slug travel in clear text. The line for your cron is then `curl -fsS https://bot.example.com/api/heartbeat/<job token>/backup` — the exact one is on the job's card («🔗 Как подключена»).

**The server clock.** The bot lives by the system time. On a home server or a NAS Docker may start before the time is synchronised. The bot notices a clock step and re-arms its schedule by itself, but starting Docker after the sync is safer. On systemd with timesyncd: `sudo systemctl enable systemd-time-wait-sync` and `sudo systemctl edit docker` → in `[Unit]` the line `After=time-sync.target` (with chrony use `chrony-wait.service` instead of time-wait-sync — check your distribution's documentation).

### Easier: Railway in one click — 5 minutes

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/lVRuwW)

1. Press **Deploy on Railway** and paste the token — the only field. The volume for the database, the public domain and the rest are already in the template.
2. Send `/start` in a private chat with the bot — the first `/start` becomes the owner and adds the first site right in the chat. The bot then offers a test alert and the external watchdog; «⚙️ Настройки → 🔌 Подключения» covers the rest.

**What it costs.** For the first 30 days Railway gives $5 and asks for no card. After that the account drops to the Free plan with $1 a month, and the bot needs about $2: without the Hobby plan it stops and nobody is told, and 30 days later Railway deletes the database volume. Hobby is $5 a month and includes $5 of usage — enough for the bot. Paying needs a card; Russian cards are not accepted abroad. The bot reminds you itself: after the first site and around day 25. Railway's terms: [its docs](https://docs.railway.com/pricing/free-trial).

Prefer your own fork? Railway → **Deploy from GitHub repo** → variables `TELEGRAM_BOT_TOKEN`, `DB_PATH=/app/data/bot.db`, `TRUST_PROXY=1` (Railway is a proxy in front of the bot), a volume at `/app/data`, Generate Domain on port 8080 — the bot picks the address up itself.

### Rolling back a version

**Updates.** On Railway updates install themselves: the template follows `main`. On your own server run `docker compose pull && docker compose up -d` — the bot does not announce that a new image exists; it says «🔄 Бот обновился» only after the new one has started.

Every image is also published as `:sha-<7 chars>` — the commit it was built from. Before a version changes the database schema the bot copies the database to `data/backups/schema-v<N>-<date>.db` (N is the schema the copy holds; the last 3 are kept). When there is no room for the copy the bot still migrates and says so in Telegram. An old version refuses to start on a newer database and logs «База от более новой версии бота…».

Own server with Docker:
1. `docker compose stop bot`.
2. In `docker-compose.yml` replace `:latest` with the previous tag, e.g. `ghcr.io/morgunovtech/tofs_devops_bot:sha-1a2b3c4` (tags are on the package page on GitHub), and `docker compose up -d`. Building from source: `git checkout <sha> && docker compose up -d --build`.
3. If `docker compose logs bot` shows «База от более новой версии бота», the schema had already changed: `docker compose stop bot`, keep the current database and put back the copy that fits the old version (its schema number is in that log line):
   `docker compose run --rm --no-deps --entrypoint sh bot -c 'cp /app/data/bot.db /app/data/bot.db.before-rollback && cp /app/data/backups/schema-vNNN-DATE.db /app/data/bot.db && rm -f /app/data/bot.db-wal /app/data/bot.db-shm'`, then `docker compose up -d`. Everything the bot wrote after the update is lost.
4. Back to updates: `:latest` again and `docker compose pull && docker compose up -d`.

Railway: roll the deployment back in the Railway panel (Deployments → previous deployment → Redeploy). The copies live on the volume in `/app/data/backups`, reachable through the CLI — `railway volume` (check Railway's volume documentation). Without access to the volume, roll forward only: bring the newer version back.

## Security

- **Who owns the bot.** Set `TELEGRAM_ADMIN_USER_ID` (your numeric id; @userinfobot shows it) before the first start: then only this person owns the bot, whatever the database says. Without it the seat goes to the first `/start` in a private chat with the bot — including one that waited in Telegram's queue while the bot was down. Someone else took it? Set `TELEGRAM_ADMIN_USER_ID` (for a group also `TELEGRAM_ADMIN_CHAT_ID`) and restart: the environment wins over the database. Deleting the database does not help — a fresh one opens the seat to the first `/start` again; a container without a volume for `data/` loses it on every recreate, with the same effect.
- **What the owner gets:** every alert and report, the «🚀 rebuild» and «🧹 purge cache» buttons, container restarts (with the socket), «💾 Скачать копию базы».
- **The database copy.** On Sundays the bot sends a copy to the owner's private chat — never to a group — without connection tokens, secrets, API-check headers and bodies and visitors' IPs; «🔔 Когда писать» switches it off. The nightly copies in `data/backups/` are complete and stay on the server (mode 0600): keep the volume like a password store.
- **docker.sock is root on the host.** Mount it only on a machine where everything is yours. With it the bot restarts the containers listed in `AUTORESTART_CONTAINERS` and, only with `AUTO_CLEANUP=1` (off by default), removes dangling images and build cache unused for a day on the whole host — stopped containers and tagged images are never touched. A filtered socket instead: [docs/SETUP.md, «Свой сервер: доступ к Docker»](docs/SETUP.md#свой-сервер-доступ-к-docker-по-желанию).
- **Secrets.** The widget secret is public: it is in the code of every page with the widget. The heartbeat secret is always different — a `HEARTBEAT_SECRET` equal to `WEBHOOK_SECRET` is ignored and a generated one is used («🔌 Подключения» says so). Every job gets an address of its own, derived from it; «🔑» in «🔌 Подключения» rotates them all.
- **Updates and the supply chain.** The Railway template and `:latest` follow `main`. Dependabot's weekly PR merges itself only for patch and minor updates of the Python libraries, after a 7-day cooldown and a green CI; the base image (a new Python), the Actions and major versions wait for a human.

## What leaves your server

Addresses of your sites go to a few free public services; each line can be switched off. Internal addresses — 10.x, 192.168.x, 127.x, CGNAT, `.local`, `.lan`, `.home`, `.internal`, `.corp`, single-label names, and a public-looking name that resolves to an internal address on the bot's host — never go to any of them (`utils/net.py`, `is_private_target`): such a site gets no second opinion, no picture, no CrUX data and no registration date.

| Where | What | When | Off |
|---|---|---|---|
| check-host.net | the address of a site that stopped answering (never one with a query or a password in it; only GET/HEAD monitors without headers of their own; `tcp://host:port` and `ping://host` as they are); port 25 of your MX hosts; with `REGION_SPEED=1` the origin (`scheme://host/`) of every site | once per incident, when the fail threshold is reached; mail hourly; the timing every 6 h | `SECOND_OPINION=0` or «⚙️ Настройки → 🔌 Подключения → 🌐 Вторая точка» (the chat wins); the timing is off by default |
| the screenshot service — `SCREENSHOT_TEMPLATE`, microlink.io by default | the URL of the page | the card picture: daily, 70 min before the morning digest, and 2 min after a start — only while the bot has a public address (`PUBLIC_BASE_URL` or Railway); «📸 Как выглядит сайт» whenever you press it | `SCREENSHOT_TEMPLATE=off` |
| Google Chrome UX Report | the origin of every site | daily, only with a key | «🗑 Отключить» in «⚡ Скорость у посетителей Chrome» |
| data.iana.org, the zone's RDAP registry, rdap.org (only when the IANA list could not be fetched), whois.tcinet.ru:43 for .ru/.su/.рф | the registrable domain of every site | daily | no switch |
| api.osv.dev | the names and versions of every installed Python package | Sundays | `DEPENDENCY_WATCH=0` |
| api.github.com (the bot's token when there is one) | the repository slug: the newest `main` commit and its statuses, the open PRs | hourly and on Sundays, only with `GITHUB_REPOSITORY` (Railway and GitHub Actions set it) | `DEPENDENCY_WATCH=0`, or leave `GITHUB_REPOSITORY` empty on your own server |
| GitHub GraphQL, Google Search Console, Yandex.Webmaster, Cloudflare | your repositories (two paginated queries, Sundays); the sites of the property; the deploy hook and the cache purge | only when connected | disconnect in «🔌 Подключения» |
| the external watchdog (`SELF_HEARTBEAT_URL`, e.g. healthchecks.io) | an empty GET | every 5 min, while the checks run and Telegram answers | remove the link |
| Telegram | every message; visitors' widget messages with their IP; the pictures; the Sunday copy of the database | | the copy: «🔔 Когда писать» |
| your sites, and every link on their home pages | the bot's address and User-Agent (`… TofsDevOps/2.0`); the home page asked again with the GPTBot, ClaudeBot and PerplexityBot signatures; probes of `/.git/HEAD`, `/.env` and `/__devopsbot-404-probe` | every check; the signatures and the 404 probe daily; `.git`/`.env` every 6 h; the links every 6 h | `LINKS_CHECK_INTERVAL_HOURS=0` stops the crawl |
| the host's DNS resolver | the names of your hosts, NS and MX of your domains, `_dmarc` and the DKIM selectors of known mail providers | hourly | — |

## Where to keep the database

- Copy `data/backups/` (the nightly copies) or use «💾 Скачать копию базы». Copy `bot.db` itself only with the bot stopped, and together with `bot.db-wal` and `bot.db-shm`: a copy of the live file can miss the rows still in the WAL. A filesystem snapshot of the volume is fine.
- Not on a network share (NFS, SMB): SQLite's locking does not survive it.
- Btrfs: `chattr +C` on the empty data directory before the first start (no copy-on-write for the database; the price is no checksums and no compression for it).
- Raspberry Pi: a USB SSD rather than an SD card — every check is a synchronous write.
- A home server or a NAS: start Docker after the clock is synchronised — «The server clock» in [Public address on your own server](#public-address-on-your-own-server). The bot notices a clock step and re-arms its schedule, but a bot that started on a wrong clock has already stamped its checks with it.
- `ping://` monitors work only where the container may open an ICMP socket: the `sysctls` line in the compose file above opens `net.ipv4.ping_group_range` for the bot's group. In rootless Docker, Podman or with userns-remap use `tcp://host:port` instead; the bot says «cannot check» once instead of ringing.

## Status

Personal project, shared as-is: Python 3.12–3.14 (the upper bound comes from the pinned aiogram's Requires-Python, see `requirements.in`), aiogram 3, aiohttp, APScheduler, SQLite. Named after Tofs — an Irish Terrier who takes uptime personally. Built in pair with [Claude](https://claude.com/claude-code). [MIT](LICENSE) © Semyon Morgunov.
