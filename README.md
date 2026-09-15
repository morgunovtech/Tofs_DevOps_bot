# TofsDevOps — your personal DevOps in Telegram

> Watches your sites, fixes what it can, checks that search engines **and AI assistants** can see you — and stays quiet while everything is fine.

🇷🇺 [По-русски](README.ru.md) · [Everything it does](docs/FEATURES.en.md) · [Why (RU)](docs/WHY.md) · [Setup click by click (RU)](docs/SETUP.md)

Made for people who have never heard of DNS or a 502. Three buttons: **is everything OK**, **one site in detail**, **settings**. Every alert says what happened, what it means for visitors and what to do — with a button per step, written for your hosting (Cloudflare, Vercel, Netlify, GitHub Pages or a plain server).

## What you get

- **Uptime, SSL, domain, DNS, broken links, deep 5xx** — one incident pipeline: anti-flap, a second opinion from outside before waking you, an alert ladder instead of daily nagging, downtime in minutes rather than percent.
- **Search & AI visibility** — noindex, robots.txt, AI-bot blocks, empty no-JS pages, soft-404, sitemap. Each finding is a numbered item with «what it costs you» and steps for your hosting.
- **Self-healing** — Cloudflare Pages redeploy and cache purge, container restart and disk cleanup on your own server, with a report of what was already done.
- **Reports that respect your attention** — one morning line, a Sunday digest with ASCII charts, quiet hours, «I'm fixing it» silence, your timezone, full export/import and database restore from the chat.

## Quick start — 5 minutes, no server

1. Create a bot at [@BotFather](https://t.me/BotFather) and copy the token.
2. Fork this repo → [Railway](https://railway.com) → **Deploy from GitHub repo** → variable `TELEGRAM_BOT_TOKEN`, a volume at `/app/data`, a generated domain.
3. Send `/start` — the first user becomes the admin and adds the first site right in the chat. The bot's own checklist («⚙️ Settings → Diagnostics») walks you through the rest.

Docker Compose, every `.env` integration, architecture and the development loop: [docs/FEATURES.en.md](docs/FEATURES.en.md#quick-start).

## Status

Personal project, shared as-is: Python 3.12+, aiogram 3, aiohttp, APScheduler, SQLite. Named after Tofs — an Irish Terrier who takes uptime personally. Built in pair with [Claude](https://claude.com/claude-code). [MIT](LICENSE) © Semyon Morgunov.
