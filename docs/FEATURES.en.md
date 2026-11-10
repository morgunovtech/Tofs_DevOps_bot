# TofsDevOps — everything it does

The long version of the [README](../README.md): every monitor, every screen, quick start for Railway and Docker Compose, the architecture and the development loop. Russian: [FEATURES.md](FEATURES.md), [SETUP.md](SETUP.md), [WHY.md](WHY.md).

## What it does

### 📡 Monitoring
- **Availability** every 5 minutes — with a twist: before paging you, it re-checks from three external nodes (check-host.net) and from the node in your time zone's country, all within 30 seconds. Up for most of them and from your country — no alarm: one quiet 🟠 «down from my server, up from 2 of 3 external nodes» with a hint that visitors in the bot's region probably see the same (a block, a provider, a CDN node), and a ✅ «opens from my server again» once it does. One lucky node out of three does not cancel the alarm: a split is a 🔴 with the honest «opens only from 1 of 3 external nodes — it seems down in places»; down from your country — «opens from outside, but not from Russia». A certificate error never goes to the second opinion — the server answered, every visitor sees the same warning — and pages right after the threshold. While the incident is open the external nodes are not asked again.
- **SSL certificates** — an alert ladder (14 → 7 → 3 → 1 → 0 days) instead of daily nagging, plus a *renewal watch*: it tracks the certificate serial and warns when auto-renewal (Cloudflare/certbot) silently stops working. A renewal that worked is normal operation: no message, just a line in the Sunday report.
- **Domain expiry** via RDAP (the protocol that replaced WHOIS for gTLDs in 2025; IANA bootstrap + rdap.org, a tiny async WHOIS fallback for .ru/.su/.рф), with its own alert ladder.
- **DNS changes** — snapshots A/AAAA/CNAME/NS/MX records hourly; a changed NS record (that's what a hijack looks like) is a critical alert, and so is every MX disappearing (mail stops arriving).
- **Real visitors' speed** — not the bot's own request: what visitors actually wait for, from Google's Chrome UX Report. Nothing to install on the sites: with a free Google API key the bot asks once a day how soon the page appears (LCP), how soon a tap is answered (INP) and how much the layout jumps (CLS) over 28 days. Small sites often have no data — Google only covers sites with enough Chrome visitors — and the card says so. The card says it in words («страница появляется за 1.8 с, кнопки откликаются за 0.12 с, вёрстка не прыгает · посетители Chrome за 28 дней, данные Google»); slow by Google's yardstick (p75 in the red zone) is one quiet alert with a PageSpeed Insights button.
- **Basic security** — every six hours, what anyone can see from outside: `/.git/HEAD` or `/.env` open to the world (the whole code with its history, or passwords and keys) rings; the content is checked, so a single-page app answering index.html for any path is not a false alarm. A site that still opens over plain `http://`, and the four protective headers any site can have safely (HSTS, X-Content-Type-Options, X-Frame-Options or `frame-ancestors`, Referrer-Policy), come as one quiet message with the ready text for the hosting — a `_headers` file for Cloudflare Pages and Netlify, a `vercel.json` block, plain header lines for the rest. CSP is advice only (a wrong one breaks the site). The public status page never lists these.
- **Your domain's mail** — hourly, for every domain among the sites: who receives the mail (Proton, Google Workspace, Microsoft 365, Yandex 360, Zoho, iCloud, Fastmail, Cloudflare Email Routing…), whether those servers exist and accept connections on port 25 from outside (asked of check-host.net, with a control server so a broken checker is never mistaken for a dead mailbox). Two failed checks ten minutes apart are a critical «your mail does not accept letters». SPF (missing, two records instead of one, `+all`, over ten lookups, the provider left out), DKIM and DMARC problems arrive as one quiet message with the exact record to paste — two SPF records are merged into one for you.
- **Broken links** — a link that does not open is a dead end for your visitor wherever it points, so own and external links are one list. What decides is certainty, not the domain: a 4xx or a vanished domain counts at once, a timeout or 5xx only if the link still fails three hours after the first failure (a crawl after a restart minutes later does not count — a short outage on the far end, like GitHub's, is not a broken link), a bot block (401/403/429) never.
- **Deep 5xx probe** — samples pages from `sitemap.xml` hourly, catching "homepage is fine but half the site is erroring".
- **Keyword / stop-phrase check** — per site: alert when a phrase disappears from the page, or when a bad one appears («Fatal error»). Catches the classic "HTTP 200 with a white screen".
- **Custom HTTP requests** — per site: method (GET/HEAD/POST…), headers (`Authorization: Bearer …`) and a request body (a JSON body goes out as `application/json` unless you set the header), so an API behind a token or a POST-only health endpoint is a first-class monitor.
- **API checks — watch what the site actually does**: `POST https://good-pass.pages.dev/api/v1/analyze` with a JSON body, expecting `scoreLabel` in the answer; `GET …/api/whoami`. One rule (`utils/urls.py`, `is_api_check`): an http(s) monitor is an API check when its method is not GET/HEAD, **or** its path is `/api` or under `/api/`, **or** its host is `api.…`. An API check gets availability only — status code, the phrase in the response, response time, fail threshold and anti-flap — and no SEO audit, link crawl, sitemap 5xx sampling, page meta, AI-crawler probes, screenshot or regional timing (a GET to a POST endpoint is a 405, not an outage). Certificate, domain and DNS are checked once per host: when a regular site lives on the same host, the API check has none of its own (one alert, not two) and its card points to the site's card. The external second opinion (check-host sends a bare GET) is asked only for GET/HEAD requests without custom headers. An `/api/` address keeps its path and query when added; the card says «🔌 API-проверка: POST, …», the status page tags it API and never shows the query.
- **TCP and ping monitors** — `tcp://mail.example.com:25`, `ping://10.0.0.1`: non-HTTP services (mail, SSH, databases) get the same incident pipeline, anti-flap and external second opinion.
- **Bot-host health** — disk usage and docker containers on the server the bot runs on.

### 🔍 SEO / GEO (AI visibility)
- **noindex detector** — the classic silent killer: one bad deploy ships `<meta name="robots" content="noindex">` to production and you find out a month later from your traffic graph. The bot finds out the same day and bypasses all mutes.
- **robots.txt** — blocking Googlebot/YandexBot is critical; blocking GPTBot/ClaudeBot/PerplexityBot is a warning (you *want* AI agents to see you).
- **A request signed as an AI crawler** — the homepage is asked again with the GPTBot, ClaudeBot and PerplexityBot signatures. When the plain request passed and a signed one gets 401/402/403/451, that is a block by signature, and the real crawlers send the same one. What this cannot see is a block by address: Cloudflare recognises the real AI crawlers by their IPs, and since July 2025 blocks them by default on new domains. So the finding says «a request with the signature», never «what GPTBot sees», and a clean Cloudflare site is not called «open to AI assistants» — the search screen says why and offers «💡 Проверить в Cloudflare» (AI Crawl Control → Crawlers); the site card carries a line and a button until opened once.
- **No-JS content measurement** — AI crawlers don't execute JavaScript. If your page is an SPA with 60 chars of text before JS runs, AI agents see a blank page — the bot will tell you.
- Title/description (present, sane length), canonical target, h1, OpenGraph, lang, JSON-LD checks, soft-404 detection. Every finding is an action (🟠) with steps; nothing is listed just to say it is fine (no robots.txt, closed port 80, `llms.txt` present or not).
- **The screen says only what was measured**: «open to search engines and AI assistants» / «open, but N things in the way» / «🔴 closed to search». Whether the site is actually *in* an index only Search Console and Webmaster know — without them the screen says so and offers to connect them; with them it shows «📇 Google: homepage indexed (crawled 20.09)» from the daily index check, and marks data older than two days as «as of DD.MM».
- **Real index status** (optional): Google Search Console API (homepage dropped out of the index → critical alert; weekly clicks/impressions) and Yandex.Webmaster API (searchable pages, SQI, site problems — FATAL rings, CRITICAL waits for the morning).

### 🔧 Self-healing (Cloudflare Pages friendly)
- Alerts come with **one-tap action buttons**: re-check, trigger a Pages deploy hook, purge the Cloudflare cache, grab a live page screenshot.
- **Auto-redeploy**: fires the deploy hook itself when a site goes down (on by default once a hook exists, once per incident) and reports what it did in the alert.
- **Follow-ups without you**: 2 and 5 minutes after a "site down" alert the bot re-checks and edits the same message — "still down" or "already up". A "I'm on it, one hour of silence" button lives in every alert.
- **Knows your hosting**: guesses Cloudflare / Vercel / Netlify / GitHub Pages / Railway / Render / Fly / Heroku from response headers and puts an "Open the panel" link into the alert; domain alerts link to the registrar's panel (Cloudflare, reg.ru, Namecheap, GoDaddy…).
- **Container auto-restart** (`AUTORESTART_CONTAINERS`) and **opt-in disk auto-cleanup** (`AUTO_CLEANUP=1`: dangling images and build cache older than a day, at most every 12 h, with a report of what was removed) — both need the docker socket mounted, which is root-equivalent access to the host. A stopped or unhealthy container is restarted and given its own healthcheck window to come up («health: starting» is waited out, not judged); at most 3 restarts per episode, with 10- and 20-minute gaps, then one «I am leaving it to you» message and the container is yours until it has been healthy for half an hour, which re-arms the alert and the budget (a «✅ back up» comes after a fix by hand, too). The bot never restarts its own container, even when it is listed. A container that is not in Docker (a typo, renamed, removed) and a Docker that stopped answering on the socket are said once (🟠) instead of silently switching the feature off; so is `AUTORESTART_CONTAINERS` set without the socket mounted.

### 📱 Managed entirely from the chat
- **Three buttons under the input field, no ☰ menu**: «🏠 Главная», «📖 Что я умею» (every function as «what it does → what you get», plus what each optional connection gives and who needs it) and «❓ Значки». A tap aborts any pending input.
- **One set of rules for the main screen and the site card**: status icons only (✅ 🟠 🔴 ⚠️ ⏳); one check time, at the top, as a time («проверено в 17:33»); a line is status · what · one number (the answer time «0.31 с», never «быстро»); a problem is said once, on the line of what it belongs to (mail by domain, in a «Почта» block); a block title carries only what its lines do not. The card puts problems first, then blocks by importance — availability (each sitemap page with its own mark), domain, mail and protection, search.
- **Everything watched, on one screen.** The main screen answers "is everything OK?" by itself — one line per site from the last checks, a «⏰ Задачи и боты» block with every heartbeat job (last mark, or how long it has been silent), open problems listed inline with their level — and every line has its own button straight to its card. Below: «🔎 Проверить всё сейчас», «➕ Добавить» (a site; an API, port or ping; a bot or scheduled job) and «⚙️ Настройки». «Проблемы» comes first and only while something is open, with the worst open level as its icon. A site card holds everything about one site at once (availability, certificate, domain, links, search and AI, downtime, its problems with "what to do") plus «🔧 Я чиню», «🔎 Поиск и ИИ», «🔗 Ссылки» and the site's settings. Settings hold only what is not "is it OK": digests and quiet hours, mute, maintenance windows, timezone, diagnostics and connections (with the test alert), status page, feedback, export/import.
- **One level dictionary** (`services/humanize.py`), the same in an alert, on the problems screen, in the menu, on the card and in the digests: 🔴 — act now (rings, bypasses mute and quiet hours); 🟠 — act, but not today (silent, listed in problems, on the card and in the digests); ✅ — back to normal; ⚠️ — could not check; ℹ️ — a fact that is not a problem. Facts of normal operation («3 pages checked», «certificate renewed by itself») are captions and digest lines — never counted, never alerted, never given a «what to do» button. The icon in the text always matches the delivery.
- **Zero-config onboarding** — the first user to `/start` becomes the admin; an empty bot walks you through adding your first site and checks it immediately.
- **Sites** — add from the main screen («➕ Добавить»), remove from the site's card, with instant first-check feedback: the bot also suggests a phrase from the page to watch (catches "HTTP 200 with a blank page") and warns when the www twin behaves like a separate site. The DB is the source of truth; `.env` is just an optional first-run seed.
- **Two-level settings** — how often to check and whether to watch a phrase, in words; the other eight dials are behind «🛠 Для продвинутых».
- **⚙️ Settings** — morning report hour, evening report on/off, weekly report hour, quiet-hours presets: changed from the chat, applied to the running scheduler on the fly.
- **⚙️ Per-site overrides** — check interval, consecutive-failure threshold, accepted HTTP codes (e.g. `200-399,401`), keyword, "slow" threshold, HTTP method/headers/body — each site gets its own dials.
- **💾 Backups under the hood** — a nightly copy of the database on the server; on Sundays and on demand («🤖 Боты» → «💾 Скачать копию базы») a copy without connection tokens, secrets, API-check headers/bodies and visitors' IPs goes to the owner's private chat — never to a group (switch it off in «🔔 Когда писать»). To restore, send that file back: the bot recognises its copy (and older JSON exports) and asks before replacing anything; the tokens and API-check headers the copy lacks stay as they are on the same server, and are typed in again on a new one.
- **👀 Acknowledge** — «Видел, не напоминать 2 ч» on an escalating incident: snoozes the re-alerts without closing the incident.
- **🔧 Maintenance windows** — recurring schedules («weekdays 02:00–04:00», overnight windows welcome) per site or for everything: alerts and escalation stay silent, checks and stats keep running.
- **⏰ Tasks and bots (heartbeats)** — added from the chat («➕ Добавить» → «⏰ Бот или задача по расписанию») with a ready-to-paste `curl` line for your cron, or the same URL as another bot's healthcheck link; each job has an address of its own, shown on its card («🔗 Как подключена»), and each one sits on the main screen next to the sites.
- **⏸ Per-site pause** — deploying something big? Pause alerts for 1h or until morning; checks keep running silently. A problem that outlives the pause is said after it; a «✅ back up» about a fall the bot already reported comes through the pause and lifts «🔧 Я чиню».
- **🌐 Public status page** — one toggle serves an Uptime-Kuma-style page at `/status` (current state, 24h/7d/30d/90d uptime, anonymised incident history, maintenance banner), a `/status.json` twin for your own dashboards, and SVG uptime badges for your README. Off by default; an optional secret slug makes the URL private-by-obscurity.
- **⚙️ Settings in five questions** — when to write (digests, quiet hours, mute, maintenance windows, the test alert), helpers, connections (what is broken first with its fix, then what works and what could be added), what visitors see (status page, feedback widget), timezone.
- **🔔 Test alert** — arrives a minute after the tap (Telegram hushes the phone while you are in the app), rings like a real «site down», and «🔕 No» says what to fix.

### 💓 Dead-man switch
Your backup cron can't tell you it *didn't* run. Add a job in the chat («➕ Добавить» → «⏰ Бот или задача по расписанию») — the bot hands you the exact line for your cron:

```bash
curl -fsS https://your-server:8080/api/heartbeat/<job token>/backup
```

…and the bot alerts you when the job goes silent longer than its interval. Recovery is announced automatically. The token is the job's own (derived from `HEARTBEAT_SECRET`; rotating the secret changes every job's address), so one leaked cron line exposes one job, not all of them. `HEAD` is not a mark (link previews send it) — `405`; a name the bot does not wait for is `404`, so a typo in the cron line fails right there. Old addresses with the shared secret keep working, but the bot asks once per job to move it to its own address. Silence is counted from the last mark or from when the job was added, minus the bot's own downtime: neither a freshly added job nor a redeploy produces a false «молчит».

### 📊 Reports that respect your attention
- **Morning digest** — one message: per-site status, open problems, 7-day downtime (one phrase when the week was clean), upcoming SSL/domain expirations, disk, and right under the sites a line per bot or scheduled task (✅ works, with its expected rhythm; a silent one brings «connect / delete» buttons) and one for the bot's own watchdog — ✅ only while it actually takes the marks, and a search/AI line that never repeats an open problem. Reads in 10 seconds.
- **Weekly report** (Sundays) — downtime in minutes for the week and the month (raw checks + nightly rollups, so it stays honest past retention), everything open right now with its level (however old) and what was solved this week, certificates that renewed by themselves, an ASCII response-time chart (monospace, zero image libraries), Google/Yandex search metrics week-over-week, the state of the bot's own dependencies, GitHub hygiene — and exactly one suggestion with a button, never more. A long report is split into several messages, never cut.
- **GitHub hygiene** (optional: `GITHUB_TOKEN`, or «⚙️ Настройки → 🔌 Подключения → 🐙 GitHub») — a section of the Sunday report. One GraphQL query over the owner's repositories (`GITHUB_OWNER`, defaults to the token's account) and only what needs a hand: repositories gone since last week (the list is kept in the DB), open PRs older than 7 days, extra branches (not the default, not an open PR's head; `gh-pages` is fine), «Automatically delete head branches» switched off, a red CI on the default branch's latest commit, open Dependabot alerts when the token may read them. Archived repos and forks are watched for disappearance only. All tidy — no section at all; GitHub down or the token rejected — one «⚠️ не смог проверить» line, and the report arrives as usual. The bot's own Dependabot PRs are not repeated: the dependency line already names them.
- **Quiet first day** — a freshly added site is audited silently; its first SEO findings arrive in the next morning digest instead of ten separate alerts. Only findings that take the site out of search are ever alerted; the rest waits in «🔎 Поиск и ИИ» with a button per finding.
- **Quiet hours** — non-critical alerts queue up overnight and arrive as one morning digest. "Site down" always gets through.
- **Silent delivery** — informational messages arrive without a sound; only critical alerts ring. Silence-by-default, literally.
- **Escalation** — a site that is still down re-alerts every 30 minutes and ignores mute. A dead site must not be forgettable (deliberately availability-only: half-hourly pages about an expiring cert would train you to ignore alerts).
- **Post-incident summaries** — recovery messages include duration and cause: *"✅ Recovered · down 12 min (14:03–14:15) · cause: HTTP 502"*.

### 🧹 It maintains itself
- Old raw checks roll up into daily aggregates nightly — the SQLite DB stays small forever.
- Nightly DB backups with rotation (written atomically, checked before they replace the previous copy, mode 0600) + a weekly off-host copy without tokens and secrets sent to the owner's private Telegram chat.
- **👥 Helpers** — up to three people who also get the alerts that ring (site down, back up): the owner on holiday or with a dead phone no longer means nobody knows. A one-off invite link that lives a day; a helper gets text only, cannot steer the bot, leaves with /stop.
- **Silence never lies.** The bot is quiet when all is well, so it makes sure quiet means nothing else: a message Telegram did not take is kept and retried every minute with a «⏳ Задержалось с 14:03» (delayed since 14:03) note; if more than 50 messages pile up during an outage, non-critical ones are dropped first and the bot says how many were lost; a failure on the bot's side is never a site's failure: before calling an unanswered site down the bot checks its own network (the `NET_CANARIES` hosts, or any site that just answered) — no network means no alerts, no downtime in the statistics, «no fresh data» on the status page and one summary for the whole episode once it is back, and a `ping://` monitor the bot may not open an ICMP socket for is one quiet «cannot check», not a 🔴 with escalation; after a restart following more than 10 minutes away it says when it was gone and how the sites are now, and sends a morning report that fell into the gap late; after the first site it offers a test alert (a minute later, so Telegram does not hush the phone while you are in the app) and the external watchdog. On Railway it also asks about the plan: the trial lasts 30 days, then the Free plan gives $1 a month while the bot needs about $2 (~190 MB of memory at $10 per GB-month), so without Hobby it would stop without a word. The question comes among the first steps, once more around day 25 after /start, and as a line in «🔌 Подключения» until the trial is over; «✅ Уже на Hobby» ends it. Installs from before this check get no reminder.
- **Telegram through a proxy** — `TELEGRAM_PROXY=socks5://user:password@host:1080` (or `socks4://`, `http://`) sends every call to Telegram through it, with api.telegram.org resolved on the proxy side — for a server that cannot reach Telegram directly (a VPS in Russia). Site checks go direct. A malformed value stops the start with a sentence; the password never reaches a log or the connections screen.
- **The database stays on disk.** On Railway without a volume the database is wiped on every deploy — with weekly auto-updates the bot would forget its sites and owner every week. It checks that the file sits on the volume and says so right after /start, on every start and in the diagnostics. Dependabot PRs merge only after the auto-merge job has seen every test job green — no branch rule needed in a fork.
- Self-heartbeat to an external watchdog (healthchecks.io) — who watches the watchman. The bot reads the answer, not only the status code (healthchecks.io says «OK (not found)» with HTTP 200 for a deleted check), shows the watchdog on the main screen and in the digest, and says once if it has refused the marks for half an hour. Marks are sent only while the checks run and Telegram answers, so the watchdog also catches a stuck bot, a dead Telegram channel or a bot that can no longer write to its owner.
- **Keeps its dependencies fresh without you**: Dependabot opens a weekly PR, CI (ruff + pytest + pip-audit) tests it, the auto-merge workflow merges patch and minor updates of the Python libraries released at least a week ago (a new base image — the Python version — and the Actions are merged by you), the host redeploys — and the bot announces «🔄 Бот обновился: aiohttp 3.10 → 3.14» on its next start. A newer version on PyPI is therefore not news; the bot reports only what needs a human — vulnerable packages (OSV), major PRs, minor PRs stuck for over two days (red CI) — and, hourly, a newest commit that failed to deploy while the bot still runs the old build. CI also builds the Docker image, but base-image bumps are never auto-merged: CI does not run the tests on the new Python. Every green `main` publishes `ghcr.io/morgunovtech/tofs_devops_bot` (`:latest` and `:sha-…`, amd64 and arm64) — nobody builds it by hand. The auto-merge dispatches CI on `main` itself, because a merge made with GITHUB_TOKEN starts no workflow run.
- Secrets take care of themselves: an empty or placeholder `WEBHOOK_SECRET`/`HEARTBEAT_SECRET` is replaced by a generated one stored in the DB. The two are always different — the webhook secret is public by design (it ships in the widget), the heartbeat one is not. A heartbeat is accepted only with the job's own token (derived from the heartbeat secret) or the old shared heartbeat secret, never the webhook one.

### 📩 Bonus: feedback widget
A tiny embeddable JS widget for your sites — messages land in your Telegram (rate limiting and size caps server-side) and stay readable in the bot («🔌 Подключения» → «📩 Обратная связь»). Two modes: a floating "⚠️ Проблема?" button, or `button: false` plus `data-devops-feedback` on any link — the classic footer "нашли опечатку?" opens the form with the visitor's selected text pre-filled. Setup snippet and the CSP notes: [docs/SETUP.md](docs/SETUP.md#виджет-обратной-связи-нашли-опечатку).

## What it looks like

```
🌅 Доброе утро · 29.07 09:00

✅ example.com — открывается за 0.10 с
✅ app.example.com — открывается за 0.14 с

✅ socialbot — на связи, последний раз 7 ч назад (выходит на связь раз в сутки)
✅ Сторож healthchecks.io — на связи: если я упаду, он сообщит

Всё спокойно 🐕

📈 За неделю: все сайты без сбоев
🔎 Поиск и ИИ: помех нет
💾 Диск сервера: ✅ занято 41%
```

```
🔴 example.com не открывается: хостинг отвечает ошибкой 502
Посетители видят страницу с ошибкой вместо сайта. Проверил дважды
подряд и с внешних серверов: не открывается ни у кого.

Что это обычно значит: сбой на стороне хостинга, упавший деплой
или закончившиеся лимиты тарифа.

Что делать:
• Подождать 5 минут: я проверяю каждую минуту и напишу, как только
  сайт поднимется.
• Если не поднимется — открыть панель хостинга, посмотреть последний
  деплой и перезапустить или откатить его.

🤖 Уже сделал сам: 🚀 запустил пересборку сайта — Cloudflare Pages
соберёт его за 1–2 минуты
🔁 Через 2 мин: уже открывается. Подтвержу отдельным сообщением.
HTTP 502 · 0.81 с

[🔧 Я чиню, час тишины] [🔍 Проверить сейчас]
[📸 Как выглядит сайт] [ℹ️ Что делать]
[🔗 Открыть панель Cloudflare]
```


## Quick start

### Railway (no server needed)

Fork this repo → [Railway](https://railway.com) → **New Project → Deploy from GitHub repo**. Railway picks up the `Dockerfile`; then:

1. **Variables**: `TELEGRAM_BOT_TOKEN` (from [@BotFather](https://t.me/BotFather)) and `DB_PATH=/app/data/bot.db`. Secrets are generated on first start.
2. **Attach Volume** at mount path `/app/data` — the SQLite DB must survive redeploys.
3. **Settings → Networking → Generate Domain** (port **8080**), then set `PUBLIC_BASE_URL=https://<your-app>.up.railway.app`.

Full click-by-click walkthrough in plain language (RU): [docs/SETUP.md](docs/SETUP.md).
Note: the docker-socket features (container auto-restart, disk auto-cleanup) only apply to self-hosting; on Railway they are simply off. If `AUTORESTART_CONTAINERS` is set but the socket is not mounted, the bot says so (🟠) instead of staying quiet.

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
| Real Chrome visitors' speed (Google data) | `CRUX_API_KEY` |
| GitHub hygiene in the weekly report | `GITHUB_TOKEN`, `GITHUB_OWNER` (optional) |
| Container restart | `AUTORESTART_CONTAINERS` + docker.sock mount (the socket is root on the host) |
| Disk cleanup (dangling images and build cache older than a day, at most every 12 h) | `AUTO_CLEANUP=1` + docker.sock, off by default |
| External watchdog | `SELF_HEARTBEAT_URL` |
| Telegram through a proxy (a server in Russia) | `TELEGRAM_PROXY` |

## Architecture

```
main.py                 entry point: DB → services → web server → scheduler → polling
├── monitors/           availability, ssl, domain (rdap), dns, links, deep 5xx,
│                       seo/geo, host — typed results, shared alert ladder
├── services/           notifier (the one door for every message: priorities,
│                       mute, quiet hours, pauses), settings, maintenance,
│                       secrets, runtime (admin), updates (dependency watch),
│                       cloudflare actions, docker API, screenshots, GSC, Yandex,
│                       GitHub hygiene
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
pytest -q             # ~150 tests, no network, no Telegram
pip-audit -r requirements.txt --strict
```

CI (ruff, pytest on Python 3.12, 3.13 and 3.14, `pip-audit --strict`, the image build on the 3.13 leg) runs on every PR and on every push to `main`; a green `main` publishes the image to ghcr.io. Dependabot files one grouped PR for the Python libraries every Saturday at 13:00 (Europe/Moscow) and monthly PRs for the base image and the Actions; a version reaches a PR no sooner than 7 days after its release (a major one — 30). The auto-merge waits for every «lint · test · audit» check itself and merges only patch and minor updates of the Python libraries — exactly the commit the tests checked; the base image, the Actions and major versions wait for a human. Nothing has to be configured in the repository; if you want a branch rule, require «lint · test · audit (3.12)», «(3.13)» and «(3.14)». The container runs as an unprivileged user; `entrypoint.sh` fixes the data volume's ownership first.
