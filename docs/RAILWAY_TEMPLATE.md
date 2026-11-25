<!-- The listing of the Railway template tofsdevops (railway.com/deploy/tofsdevops?referralCode=7hTMqE).
     Railway keeps its own copy: after editing this file, paste the part below
     the line into Workspace → Templates → TofsDevOps → Marketplace → Overview.
     Category: Bots. Description (25–75 characters):
     Site monitoring in Telegram: speaks only when something breaks. -->

---

# Deploy and Host TofsDevOps on Railway

TofsDevOps is a self-hosted Telegram bot that watches your websites: uptime, SSL, domain and DNS, mail records, broken links, and how search engines and AI assistants see your pages. It fixes what it can, tells you in plain words what to do about the rest, and stays quiet while everything is fine.

## About Hosting TofsDevOps

The template deploys one service built from the open-source repository, with a volume at `/app/data` for its SQLite database and a public domain for the status page, heartbeats and the feedback widget. You fill in one field: the bot token from @BotFather. Then you send the bot `/start` in Telegram — the first person to do so becomes its owner — and add your first site right in the chat. Every other connection (Cloudflare, GitHub, Search Console, Bing, Yandex, an external watchdog) is optional and is set up in the chat, not in Railway variables. The bot speaks English or Russian and picks the language from your Telegram at the first `/start`. It uses about $2 of resources a month, which the Hobby plan covers.

## Common Use Cases

- Know within minutes that a site, an API endpoint or a TCP port is down — and get one alert, not a flood, with the likely cause and a recovery note with the downtime
- Get warned before an SSL certificate or a domain expires, when DNS or mail records change, or when a link on your pages dies
- A morning digest and a weekly report on search and AI-assistant visibility: indexing, sitemap, robots, AI crawler access, visitor speed
- A public status page and a feedback widget for your visitors, served by the bot itself
- Self-healing for Cloudflare Pages sites: a redeploy and a cache purge when a site goes down

## Dependencies for TofsDevOps Hosting

- A Telegram bot token from [@BotFather](https://t.me/BotFather) — the only required variable
- A Railway volume at `/app/data` (already in the template) — the database, its weekly copies and the settings you change in the chat live there

### Deployment Dependencies

- Source code, changelog and security notes: [github.com/morgunovtech/Tofs_DevOps_bot](https://github.com/morgunovtech/Tofs_DevOps_bot)
- Step-by-step setup: [README](https://github.com/morgunovtech/Tofs_DevOps_bot#install--two-ways)
- Prefer your own server? The same bot runs from a published Docker image: `ghcr.io/morgunovtech/tofs_devops_bot`

### Implementation Details

Updates: when a new version is merged, Railway offers the update inside your project — accept it and the bot redeploys, then tells you in the chat what changed. Removing it: delete the Railway project (the volume goes with it) and the bot at @BotFather.

## Why Deploy TofsDevOps on Railway?

<!-- Recommended: Keep this section as shown below -->
Railway is a singular platform to deploy your infrastructure stack. Railway will host your infrastructure so you don't have to deal with configuration, while allowing you to vertically and horizontally scale it.

By deploying TofsDevOps on Railway, you are one step closer to supporting a complete full-stack application with minimal burden. Host your servers, databases, AI agents, and more on Railway.
<!-- End recommended section -->
