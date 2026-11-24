"""English for services/seo_fixes.py (see utils/i18n.py): the headline,
cost, steps and buttons of every search/AI finding. {url} and {host} are
filled in by seo_fixes._fill after translation — keep them."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── critical ────────────────────────────────────────────────────────────
    "После правки открой 2–3 внутренние страницы по прямой ссылке — они должны открываться. Потом открой тестовую ссылку "
    "ниже: должна быть страница «не найдено» — и нажми «🔄 Проверить снова».":
        "After the change open 2–3 inner pages by direct link — they must open. Then open the test link below: it must be a "
        "«not found» page — and tap «🔄 Check again».",
    "Сайт закрыт от поисковиков в robots.txt": "The site is closed to search engines in robots.txt",
    "Google и Яндекс перестанут показывать сайт — переходы из поиска уйдут в ноль за пару недель.":
        "Google and Yandex will stop showing the site — visits from search drop to zero within a couple of weeks.",
    "Снять запрет в robots.txt": "Lift the ban in robots.txt",
    "Открой файл robots.txt (кнопка ниже) и найди блок User-agent: Googlebot, YandexBot или * со строкой Disallow: /.":
        "Open robots.txt (the button below) and find the User-agent: Googlebot, YandexBot or * block with the line Disallow: /.",
    "Удали эту строку или замени на Allow: /. Запреты на отдельные папки (например, /admin/) можно оставить.":
        "Delete that line or replace it with Allow: /. Bans on single folders (/admin/, for example) can stay.",
    "Где лежит файл: у конструкторов (Tilda, Wix, Webflow, WordPress) — в настройках сайта → SEO → «Индексация» или "
    "«robots.txt»; у сайта на хостинге — файл robots.txt в корне.":
        "Where the file is: on site builders (Tilda, Wix, Webflow, WordPress) — site settings → SEO → «Indexing» or "
        "«robots.txt»; on a hosted site — the robots.txt file in the root.",
    "Нажми «🔄 Проверить снова» — я подтвержу, что запрет снят.": "Tap «🔄 Check again» — I will confirm the ban is lifted.",
    "📄 Открыть robots.txt": "📄 Open robots.txt",
    "Сервер просит поисковики не показывать страницу": "The server asks search engines not to show the page",
    "Заголовок ответа X-Robots-Tag: noindex — при следующем обходе Google и Яндекс уберут страницу из поиска.":
        "The response header X-Robots-Tag: noindex — at the next crawl Google and Yandex remove the page from search.",
    "Убрать noindex с сервера": "Remove noindex from the server",
    "Этот заголовок ставит сервер или хостинг, не код страницы. Обычно так закрывают тестовую версию сайта — и настройка "
    "случайно доехала до боевого домена.":
        "This header is set by the server or the hosting, not by the page's code. It usually hides a staging version — and "
        "the setting reached the production domain by accident.",
    "Найди, где он задан: в настройках веб-сервера (nginx: add_header X-Robots-Tag; Apache: .htaccess Header set "
    "X-Robots-Tag) или в конфиге хостинга.":
        "Find where it is set: in the web server's settings (nginx: add_header X-Robots-Tag; Apache: .htaccess Header set "
        "X-Robots-Tag) or in the hosting's config.",
    "Убери его для боевого домена и нажми «🔄 Проверить снова».": "Remove it for the production domain and tap «🔄 Check again».",
    "В проекте открой vercel.json → секция headers: там есть X-Robots-Tag: noindex. Удали его для боевого домена (для "
    "preview-деплоев Vercel ставит его сам — это нормально).":
        "In the project open vercel.json → the headers section: it has X-Robots-Tag: noindex. Remove it for the production "
        "domain (Vercel sets it for preview deploys itself — that is normal).",
    "Задеплой заново и нажми «🔄 Проверить снова».": "Deploy again and tap «🔄 Check again».",
    "Открой файл _headers или netlify.toml в проекте — там задан X-Robots-Tag: noindex. Убери строку для боевого домена.":
        "Open the _headers file or netlify.toml in the project — X-Robots-Tag: noindex is set there. Remove the line for the production domain.",
    "Cloudflare → сайт → Rules → Transform Rules → Modify Response Header: найди правило, добавляющее X-Robots-Tag, и выключи его.":
        "Cloudflare → site → Rules → Transform Rules → Modify Response Header: find the rule adding X-Robots-Tag and switch it off.",
    "Если правила нет — заголовок ставит сам сервер за Cloudflare: смотри его конфиг.":
        "If there is no rule — the server behind Cloudflare sets the header itself: look at its config.",
    "Нажми «🔄 Проверить снова».": "Tap «🔄 Check again».",
    "В коде страницы стоит запрет на индексацию": "The page's code carries a no-index rule",
    "Тег meta robots noindex — Google и Яндекс уберут страницу из поиска при следующем обходе.":
        "The meta robots noindex tag — Google and Yandex remove the page from search at the next crawl.",
    "Убрать noindex из кода": "Remove noindex from the code",
    "WordPress: Настройки → Чтение → сними галочку «Попросить поисковые системы не индексировать сайт». Tilda/Wix/Webflow: "
    "настройки страницы → SEO → «Скрыть от поисковиков» выключить.":
        "WordPress: Settings → Reading → untick «Discourage search engines from indexing this site». Tilda/Wix/Webflow: page "
        "settings → SEO → switch «Hide from search engines» off.",
    "Свой код: найди в шаблоне строку <meta name=\"robots\" content=\"noindex\"> и удали её.":
        "Your own code: find the line <meta name=\"robots\" content=\"noindex\"> in the template and delete it.",
    # ── AI assistants ───────────────────────────────────────────────────────
    "ИИ-ассистентам запрещён вход на сайт": "AI assistants are banned from the site",
    "ChatGPT, Claude и Perplexity не смогут рассказать о сайте и посоветовать его. На Google и Яндекс это не влияет.":
        "ChatGPT, Claude and Perplexity cannot tell about the site or recommend it. Google and Yandex are not affected.",
    "Пустить ИИ-ассистентов": "Let AI assistants in",
    "Если запрет поставлен осознанно (не хочешь отдавать тексты ИИ) — ничего делать не нужно.":
        "If the ban is deliberate (you don't want to hand your texts to AI) — nothing to do.",
    "Иначе открой robots.txt и удали блоки User-agent: GPTBot / ClaudeBot / PerplexityBot со строкой Disallow: /.":
        "Otherwise open robots.txt and delete the User-agent: GPTBot / ClaudeBot / PerplexityBot blocks with the line Disallow: /.",
    "Запрос с подписью ИИ-ассистента получает отказ": "A request signed as an AI assistant is refused",
    "Обычный запрос сайт пропускает, а подписанный как GPTBot, ClaudeBot или PerplexityBot — нет. Настоящие боты присылают "
    "ту же подпись, поэтому, скорее всего, ChatGPT, Claude и Perplexity тоже получают отказ и не узнают о сайте. Наверняка "
    "это видно только в панели хостинга: я прихожу не с адреса настоящих ботов. Обычно отказывает защита от ботов.":
        "An ordinary request gets through, one signed as GPTBot, ClaudeBot or PerplexityBot does not. The real bots send the "
        "same signature, so most likely ChatGPT, Claude and Perplexity are refused too and never learn about the site. Only "
        "the hosting panel knows for sure: I don't come from the real bots' addresses. Usually it is the bot protection that refuses.",
    "Открыть доступ ИИ-ассистентам": "Open access to AI assistants",
    "Найди в панели хостинга защиту от ботов (Bot protection / Anti-bot / WAF) и добавь исключение для GPTBot, ClaudeBot и "
    "PerplexityBot — или выключи блокировку ИИ-ботов целиком. Если у защиты есть журнал, там видно, кому и когда она отказала.":
        "Find the bot protection in the hosting panel (Bot protection / Anti-bot / WAF) and add an exception for GPTBot, ClaudeBot "
        "and PerplexityBot — or switch the AI-bot block off entirely. If the protection keeps a log, it shows whom it refused and when.",
    "Если защиту ставил не ты — спроси у того, кто настраивал сайт.": "If you did not set up the protection — ask whoever set up the site.",
    "Если не хочешь отдавать тексты ИИ — оставь как есть: это осознанный выбор.": "If you don't want to hand your texts to AI — leave it: that is a deliberate choice.",
    "Cloudflare → домен → AI Crawl Control → вкладка Crawlers: у GPTBot, ClaudeBot и PerplexityBot в колонке Action поставь "
    "Allow. Там же видно, приходили ли настоящие боты и что получили.":
        "Cloudflare → domain → AI Crawl Control → the Crawlers tab: set Allow in the Action column for GPTBot, ClaudeBot and "
        "PerplexityBot. It also shows whether the real bots came and what they got.",
    "Если там всё разрешено, а отказ остаётся — ищи своё правило: Security → WAF → Custom rules, правило с GPTBot или "
    "ClaudeBot в условии.":
        "If everything is allowed there and the refusal stays — look for your own rule: Security → WAF → Custom rules, a rule "
        "with GPTBot or ClaudeBot in its condition.",
    "Пускает ли Cloudflare ИИ-ассистентов — видно только в его панели": "Whether Cloudflare lets AI assistants in shows only in its panel",
    "Cloudflare узнаёт настоящих ИИ-ботов по адресу, с которого они приходят, — не только по подписи. Мой запрос с подписью "
    "GPTBot приходит не с адреса OpenAI, поэтому такую блокировку я снаружи не вижу. А новые сайты Cloudflare с июля 2025 "
    "года по умолчанию закрывает от ИИ-краулеров.":
        "Cloudflare recognises real AI bots by the address they come from — not by the signature alone. My request signed as "
        "GPTBot does not come from an OpenAI address, so I cannot see such a block from outside. And since July 2025 Cloudflare "
        "closes new sites to AI crawlers by default.",
    "Проверить в Cloudflare": "Check in Cloudflare",
    "Открой Cloudflare → домен → AI Crawl Control → вкладка Crawlers.": "Open Cloudflare → domain → AI Crawl Control → the Crawlers tab.",
    "Посмотри колонку Action у GPTBot, ClaudeBot и PerplexityBot: Block значит, что эти ассистенты сайт не видят. Там же "
    "видно, приходили ли они и что получили.":
        "Look at the Action column for GPTBot, ClaudeBot and PerplexityBot: Block means these assistants cannot see the site. "
        "It also shows whether they came and what they got.",
    "Хочешь, чтобы ИИ-ассистенты знали о сайте и советовали его, — поставь Allow. Не хочешь отдавать тексты ИИ — оставь "
    "Block, это осознанный выбор.":
        "Want AI assistants to know the site and recommend it — set Allow. Don't want to hand your texts to AI — keep Block, "
        "that is a deliberate choice.",
    "🔗 Открыть Cloudflare": "🔗 Open Cloudflare",
    "ИИ-ассистенты и часть роботов видят пустую страницу": "AI assistants and some robots see an empty page",
    "Текст появляется только после запуска JavaScript. ИИ-ассистенты и часть поисковых роботов JavaScript не запускают: для "
    "них на сайте нет текста — нечего показать в поиске и не о чем рассказать.":
        "The text appears only after JavaScript runs. AI assistants and some search robots do not run JavaScript: for them the "
        "site has no text — nothing to show in search and nothing to tell about.",
    "Показать текст без JavaScript": "Show the text without JavaScript",
    "Если сайт собран на React, Vue или Angular — включи серверный рендеринг или статическую сборку: Next.js, Nuxt и Astro "
    "умеют это из коробки; для Vite/CRA есть предрендер (vike, prerender.io).":
        "If the site is built with React, Vue or Angular — turn on server rendering or a static build: Next.js, Nuxt and Astro "
        "do it out of the box; Vite/CRA have prerendering (vike, prerender.io).",
    "Минимум без переделки: положи заголовок, описание и главный текст прямо в HTML, а JavaScript пусть только оживляет "
    "страницу.":
        "The minimum without a rebuild: put the title, description and main text straight into the HTML, and let JavaScript "
        "only bring the page to life.",
    "Проверить самому: открой сайт в Chrome, нажми F12, потом Ctrl+Shift+P (на Mac — Cmd+Shift+P), набери «Disable "
    "JavaScript» и обнови страницу. Что видно — то видят и роботы. Это действует только в этой вкладке, пока открыта "
    "панель F12.":
        "Check it yourself: open the site in Chrome, press F12, then Ctrl+Shift+P (Cmd+Shift+P on a Mac), type «Disable "
        "JavaScript» and reload the page. What you see is what the robots see. It applies to this tab only, while the F12 "
        "panel is open.",
    "Нажми «🔄 Проверить снова» — я скажу, сколько текста стало видно.": "Tap «🔄 Check again» — I will say how much text became visible.",
    # ── soft 404 ────────────────────────────────────────────────────────────
    "Несуществующие адреса отвечают «всё хорошо»": "Non-existent addresses answer «all is well»",
    "Любой случайный адрес на сайте выглядит для поисковика настоящей страницей. Индекс засоряется пустыми дублями, а "
    "настоящие страницы ранжируются хуже.":
        "Any random address on the site looks like a real page to a search engine. The index fills with empty duplicates, and "
        "the real pages rank lower.",
    "Настроить ответ 404": "Set up the 404 answer",
    "Несуществующая страница должна отвечать кодом 404 (или 410), а не 200.": "A non-existent page must answer with code 404 (or 410), not 200.",
    "Обычная причина — правило «любой адрес → index.html». Если сайт — приложение в браузере (React, Vue, Vite), это правило "
    "НЕ удаляй: на нём держатся все его адреса, без него внутренние страницы перестанут открываться по прямой ссылке и из "
    "поиска. Тогда сделай отдельную страницу 404 через предрендер или серверный рендеринг.":
        "The usual cause is an «any address → index.html» rule. If the site is an application in the browser (React, Vue, Vite), "
        "do NOT delete that rule: all its addresses depend on it, and without it the inner pages stop opening by direct link "
        "and from search. Make a separate 404 page through prerendering or server rendering instead.",
    "nginx (если сайт — не приложение в браузере): убери try_files $uri /index.html; и добавь error_page 404 /404.html;. "
    "WordPress и конструкторы делают это сами — проверь плагины редиректов.":
        "nginx (if the site is not an application in the browser): remove try_files $uri /index.html; and add error_page 404 "
        "/404.html;. WordPress and site builders do it themselves — check the redirect plugins.",
    "Cloudflare Pages: правило /* /index.html 200 в файле _redirects убирай только если сайт — не приложение в браузере; "
    "тогда положи в корень 404.html — Pages сам отдаст его с кодом 404.":
        "Cloudflare Pages: remove the /* /index.html 200 rule from _redirects only if the site is not an application in the "
        "browser; then put 404.html in the root — Pages serves it with code 404 itself.",
    "Если сайт не на Pages, а просто за Cloudflare — правило живёт на твоём сервере или в Rules → Redirect Rules: найди «всё → /».":
        "If the site is not on Pages but just behind Cloudflare — the rule lives on your server or in Rules → Redirect Rules: find «everything → /».",
    "Правило /* /index.html 200 в _redirects или netlify.toml убирай только если сайт — не приложение в браузере; тогда положи "
    "404.html в корень сайта — Netlify отдаст его с кодом 404.":
        "Remove the /* /index.html 200 rule from _redirects or netlify.toml only if the site is not an application in the "
        "browser; then put 404.html in the site's root — Netlify serves it with code 404.",
    "В vercel.json найди rewrites с source \"/(.*)\" → index.html: это правило ловит всё; убирай его только если сайт — не "
    "приложение в браузере. Next.js: сделай страницу pages/404.js или app/not-found.js — Vercel отдаст её с кодом 404.":
        "In vercel.json find the rewrites with source \"/(.*)\" → index.html: that rule catches everything; remove it only if "
        "the site is not an application in the browser. Next.js: make a pages/404.js or app/not-found.js page — Vercel serves "
        "it with code 404.",
    "GitHub Pages: положи файл 404.html в корень репозитория (или в папку docs) — он отдаётся с кодом 404 автоматически.":
        "GitHub Pages: put a 404.html file in the repository root (or the docs folder) — it is served with code 404 automatically.",
    "🔗 Открыть несуществующую страницу": "🔗 Open a non-existent page",
    "Сайт собран как приложение в браузере (React, Vue, Vite): правило «любой адрес → index.html» НЕ удаляй — на нём держатся "
    "все его адреса, без него внутренние страницы перестанут открываться по прямой ссылке и из поиска.":
        "The site is built as an application in the browser (React, Vue, Vite): do NOT delete the «any address → index.html» "
        "rule — all its addresses depend on it, and without it the inner pages stop opening by direct link and from search.",
    "Чтобы неизвестный адрес отвечал кодом 404: включи предрендер или серверный рендеринг со страницей 404 (Next.js, Nuxt и "
    "Astro умеют это из коробки) — или перечисли настоящие адреса приложения в _redirects (/about /index.html 200 …), а общее "
    "правило /* /index.html 200 замени на /* /404.html 404 (или убери его и положи 404.html) — тогда остальные получат 404 "
    "(Netlify, Cloudflare Pages).":
        "To make an unknown address answer with code 404: turn on prerendering or server rendering with a 404 page (Next.js, "
        "Nuxt and Astro do it out of the box) — or list the application's real addresses in _redirects (/about /index.html "
        "200 …) and replace the general /* /index.html 200 rule with /* /404.html 404 (or remove it and add 404.html) — then "
        "the rest get 404 (Netlify, Cloudflare Pages).",
    # ── https ───────────────────────────────────────────────────────────────
    "http:// не переводит на https://": "http:// does not redirect to https://",
    "Кто наберёт адрес руками или перейдёт по старой ссылке, попадёт на незащищённую версию: браузер покажет «Не защищено», а "
    "поисковики увидят два сайта вместо одного.":
        "Whoever types the address by hand or follows an old link lands on the unprotected version: the browser shows «Not "
        "secure», and search engines see two sites instead of one.",
    "Включить редирект на https": "Turn on the redirect to https",
    "Нужен постоянный редирект (301) с http:// на https:// для всех адресов.": "A permanent redirect (301) from http:// to https:// is needed for every address.",
    "nginx: отдельный server на порту 80 с return 301 https://$host$request_uri;. Apache: RewriteRule в .htaccess. WordPress: "
    "Настройки → Общие → оба адреса сайта с https://.":
        "nginx: a separate server on port 80 with return 301 https://$host$request_uri;. Apache: a RewriteRule in .htaccess. "
        "WordPress: Settings → General → both site addresses with https://.",
    "Открой тестовую ссылку ниже: должно перекинуть на https://. Потом «🔄 Проверить снова».":
        "Open the test link below: it must jump to https://. Then «🔄 Check again».",
    "Cloudflare → сайт → SSL/TLS → Edge Certificates → включи «Always Use HTTPS».": "Cloudflare → site → SSL/TLS → Edge Certificates → turn on «Always Use HTTPS».",
    "Открой тестовую ссылку ниже, потом «🔄 Проверить снова».": "Open the test link below, then «🔄 Check again».",
    "Режим SSL/TLS (SSL/TLS → Overview) для этого менять не нужно. Переходить с «Flexible» на «Full (strict)» можно, только "
    "когда на твоём сервере стоит действующий сертификат (например, бесплатный Cloudflare Origin CA) — иначе сайт перестанет "
    "открываться с ошибкой 52x (521/525 — если на сервере нет HTTPS, 526 — если сертификат негодный).":
        "The SSL/TLS mode (SSL/TLS → Overview) need not change for this. Move from «Flexible» to «Full (strict)» only when your "
        "server has a valid certificate (the free Cloudflare Origin CA, for example) — otherwise the site stops opening with a "
        "52x error (521/525 when the server has no HTTPS, 526 when the certificate is bad).",
    "GitHub → репозиторий → Settings → Pages → галочка «Enforce HTTPS».": "GitHub → repository → Settings → Pages → tick «Enforce HTTPS».",
    "Netlify → Site → Domain management → HTTPS → «Force HTTPS» (появляется после выпуска сертификата).":
        "Netlify → Site → Domain management → HTTPS → «Force HTTPS» (appears once the certificate is issued).",
    "Vercel переводит на https сам. Если нет — домен подключён не через Vercel: проверь, куда ведут DNS-записи у регистратора.":
        "Vercel redirects to https by itself. If not — the domain is not connected through Vercel: check where the DNS records at the registrar point.",
    "🔗 Открыть http://-версию": "🔗 Open the http:// version",
    # ── sitemap, robots, meta ───────────────────────────────────────────────
    "Нет карты сайта (sitemap.xml)": "No sitemap (sitemap.xml)",
    "Поисковики узнают о новых страницах медленнее, а Яндекс Вебмастер и Google Search Console показывают меньше данных.":
        "Search engines learn about new pages more slowly, and Yandex Webmaster and Google Search Console show less data.",
    "Добавить карту сайта": "Add a sitemap",
    "WordPress: плагин Yoast или Rank Math создаёт карту сам. Tilda, Wix, Webflow — тоже сами: проверь, что в настройках SEO "
    "карта включена.":
        "WordPress: the Yoast or Rank Math plugin makes the sitemap itself. Tilda, Wix, Webflow do too: check that the sitemap "
        "is on in the SEO settings.",
    "Свой сайт: сгенерируй (Next.js — next-sitemap, Astro — @astrojs/sitemap, или онлайн xml-sitemaps.com) и положи файл в корень.":
        "Your own site: generate it (Next.js — next-sitemap, Astro — @astrojs/sitemap, or online at xml-sitemaps.com) and put the file in the root.",
    "Добавь в robots.txt строку Sitemap: {url}/sitemap.xml и нажми «🔄 Проверить снова».":
        "Add the line Sitemap: {url}/sitemap.xml to robots.txt and tap «🔄 Check again».",
    "🗺 Открыть sitemap.xml": "🗺 Open sitemap.xml",
    "robots.txt отвечает ошибкой": "robots.txt answers with an error",
    "Если robots.txt отвечает ошибкой сервера, Google может приостановить обход всего сайта.":
        "If robots.txt answers with a server error, Google may pause crawling the whole site.",
    "Починить robots.txt": "Fix robots.txt",
    "Открой robots.txt по кнопке ниже и посмотри, что показывается.": "Open robots.txt by the button below and see what it shows.",
    "Ошибка 5xx — сбой сервера или хостинга: смотри логи, перезапусти деплой. Ошибка 403 — файл закрыт правами или правилом "
    "защиты: сними запрет.":
        "A 5xx error is a server or hosting failure: look at the logs, redeploy. A 403 error means the file is closed by "
        "permissions or a protection rule: lift the ban.",
    "У страницы нет заголовка (title)": "The page has no title (title)",
    "В результатах поиска она будет без названия или со случайным текстом — по таким ссылкам меньше кликают.":
        "In search results it shows without a name or with random text — such links get fewer clicks.",
    "Добавить заголовок страницы": "Add a page title",
    "Конструктор: настройки страницы → SEO → «Заголовок» (Title).": "Site builder: page settings → SEO → «Title».",
    "Свой код: в <head> добавь <title>Название страницы — Название сайта</title>, 10–70 символов.":
        "Your own code: add <title>Page name — Site name</title> to <head>, 10–70 characters.",
    "У страницы нет описания (meta description)": "The page has no description (meta description)",
    "Под заголовком в выдаче поисковик покажет случайный кусок текста вместо твоего.":
        "Under the title in the results the search engine shows a random piece of text instead of yours.",
    "Добавить описание страницы": "Add a page description",
    "Напиши 1–2 предложения о странице, 50–170 символов: что здесь и зачем сюда заходить.":
        "Write 1–2 sentences about the page, 50–170 characters: what is here and why to come.",
    "Конструктор: настройки страницы → SEO → «Описание». Свой код: <meta name=\"description\" content=\"…\"> в <head>.":
        "Site builder: page settings → SEO → «Description». Your own code: <meta name=\"description\" content=\"…\"> in <head>.",
    "Тег canonical ведёт не туда": "The canonical tag points the wrong way",
    "canonical говорит поисковику «настоящая копия страницы вон там». Если он ведёт на http:// или чужой домен, в поиске "
    "покажут ту копию — или не покажут эту страницу вовсе.":
        "canonical tells the search engine «the real copy of the page is over there». If it points to http:// or a foreign "
        "domain, search shows that copy — or does not show this page at all.",
    "Поправить canonical": "Fix canonical",
    "Он должен вести на https-адрес этой же страницы на этом же сайте: <link rel=\"canonical\" href=\"https://{host}/…\">.":
        "It must point to the https address of this same page on this same site: <link rel=\"canonical\" href=\"https://{host}/…\">.",
    "Конструктор: обычно правится в настройках домена — сделай https-домен основным.": "Site builder: usually fixed in the domain settings — make the https domain the primary one.",
    "Разметка для поиска (JSON-LD) с ошибкой": "The search markup (JSON-LD) has an error",
    "Поисковики проигнорируют расширенное описание в выдаче: звёзды, цены, хлебные крошки, FAQ.":
        "Search engines ignore the rich description in the results: stars, prices, breadcrumbs, FAQ.",
    "Починить разметку JSON-LD": "Fix the JSON-LD markup",
    "Открой проверку Google по кнопке ниже — она покажет строку с ошибкой.": "Open Google's checker by the button below — it shows the line with the error.",
    "Обычно это лишняя запятая, незакрытая кавычка или подставленный шаблоном пустой текст.":
        "Usually it is a stray comma, an unclosed quote or an empty text the template substituted.",
    "🧪 Проверить в Google": "🧪 Check in Google",
    "Заголовок страницы не той длины": "The page title is the wrong length",
    "Слишком короткий ни о чём не говорит, слишком длинный обрежется в выдаче — по таким ссылкам меньше кликают.":
        "Too short says nothing, too long gets cut in the results — such links get fewer clicks.",
    "Поправить длину заголовка": "Fix the title length",
    "Сделай заголовок 10–70 символов: о чём страница + название сайта.": "Make the title 10–70 characters: what the page is about + the site's name.",
    "Конструктор: настройки страницы → SEO → «Заголовок» (Title). Свой код: тег <title> в <head>.":
        "Site builder: page settings → SEO → «Title». Your own code: the <title> tag in <head>.",
    "Описание страницы не той длины": "The page description is the wrong length",
    "Короткое не объяснит, зачем заходить; длинное обрежется в выдаче.": "A short one does not explain why to come; a long one gets cut in the results.",
    "Поправить длину описания": "Fix the description length",
    "Сделай описание 50–170 символов, 1–2 предложения: что здесь и зачем сюда заходить.":
        "Make the description 50–170 characters, 1–2 sentences: what is here and why to come.",
    "Конструктор: настройки страницы → SEO → «Описание». Свой код: <meta name=\"description\"> в <head>.":
        "Site builder: page settings → SEO → «Description». Your own code: <meta name=\"description\"> in <head>.",
    "Нет главного заголовка (h1)": "No main heading (h1)",
    "Поисковику труднее понять, о чём страница. Один h1 на страницу — норма.": "The search engine has a harder time telling what the page is about. One h1 per page is the norm.",
    "Добавить заголовок h1": "Add an h1 heading",
    "Оберни главный заголовок страницы в <h1>. В конструкторах — выбери для него стиль «Заголовок 1».":
        "Wrap the page's main heading in <h1>. In site builders — give it the «Heading 1» style.",
    "Нет заголовка для превью в мессенджерах (og:title)": "No title for the preview in messengers (og:title)",
    "При отправке ссылки в Telegram или WhatsApp превью будет с адресом вместо названия.":
        "When the link is sent in Telegram or WhatsApp, the preview shows the address instead of the name.",
    "Добавить заголовок для превью": "Add a preview title",
    "Добавь <meta property=\"og:title\" content=\"…\">. Конструкторы: настройки страницы → «Превью для соцсетей».":
        "Add <meta property=\"og:title\" content=\"…\">. Site builders: page settings → «Social preview».",
    "Нет картинки для превью в мессенджерах (og:image)": "No picture for the preview in messengers (og:image)",
    "Ссылка на сайт в Telegram, WhatsApp и соцсетях будет без картинки — по ней реже кликают.":
        "A link to the site in Telegram, WhatsApp and social networks shows without a picture — it gets fewer clicks.",
    "Добавить картинку для превью": "Add a preview picture",
    "Добавь картинку 1200×630 и тег <meta property=\"og:image\" content=\"https://{host}/preview.jpg\">. Конструкторы: "
    "настройки страницы → «Превью для соцсетей».":
        "Add a 1200×630 picture and the tag <meta property=\"og:image\" content=\"https://{host}/preview.jpg\">. Site builders: "
        "page settings → «Social preview».",
    "Не указан язык страницы (lang)": "The page's language is not set (lang)",
    "Поисковики и переводчики гадают, на каком языке сайт.": "Search engines and translators have to guess the site's language.",
    "Указать язык страницы": "Set the page's language",
    "Добавь атрибут с кодом языка страницы: <html lang=\"ru\"> для русского, <html lang=\"en\"> для английского, <html "
    "lang=\"uz\"> для узбекского. Конструкторы ставят его сами — проверь язык сайта в настройках.":
        "Add the attribute with the page's language code: <html lang=\"en\"> for English, <html lang=\"ru\"> for Russian, <html "
        "lang=\"uz\"> for Uzbek. Site builders set it themselves — check the site's language in the settings.",
    "Карта сайта ведёт на другой адрес": "The sitemap points to another address",
    "Карта есть, но в ней адреса другого сайта — часто старого домена после переезда. Поисковики не узнают из неё о страницах "
    "этого сайта, а Search Console и Вебмастер покажут ошибки карты.":
        "There is a sitemap, but it holds another site's addresses — often the old domain after a move. Search engines will not "
        "learn about this site's pages from it, and Search Console and Webmaster show sitemap errors.",
    "Исправить карту сайта": "Fix the sitemap",
    "Открой sitemap.xml по кнопке ниже и посмотри, какой домен стоит в адресах.": "Open sitemap.xml by the button below and see which domain the addresses carry.",
    "Пересобери карту с правильным доменом: WordPress (Yoast, Rank Math) и конструкторы берут адрес сайта из настроек — поправь "
    "его там; свой генератор (next-sitemap, @astrojs/sitemap) — в его конфиге.":
        "Rebuild the sitemap with the right domain: WordPress (Yoast, Rank Math) and site builders take the site's address from "
        "the settings — fix it there; your own generator (next-sitemap, @astrojs/sitemap) — in its config.",
    # ── proofs of ownership ───────────────────────────────────
    "Метатег подтверждения прав с заглушкой": "A verification meta tag with a placeholder",
    "Сайт не подтверждён в поисковике: нет его статистики, ошибок обхода и запроса индексации, а заглушка в коде выдаёт, что настройку не доделали.":
        "The site is not verified with the search engine: no statistics, no crawl errors, no indexing requests — and the placeholder in the code shows the setup was never finished.",
    "Открой кабинет поисковика — Google Search Console, Яндекс.Вебмастер или Bing Webmaster — и добавь сайт, способ подтверждения «HTML-тег» (метатег).":
        "Open the search engine's console — Google Search Console, Yandex.Webmaster or Bing Webmaster — and add the site with the «HTML tag» (meta tag) verification method.",
    "Скопируй выданный код и вставь его в content вместо заглушки. Подтверждаешь сайт через DNS — просто удали метатег.":
        "Copy the code it gives you and put it into content instead of the placeholder. If you verify the site through DNS, just delete the meta tag.",
    "Нажми «Подтвердить» в кабинете, потом «🔄 Проверить снова» здесь.": "Tap «Verify» in the console, then «🔄 Check again» here.",
    "Вставить код подтверждения": "Insert the verification code",
    "Два разных кода подтверждения одного поисковика": "Two different verification codes of one search engine",
    "Один из кодов почти наверняка старый — от прежнего аккаунта или прежнего владельца, и он по-прежнему даёт права на сайт.":
        "One of the codes is almost certainly old — from a former account or a former owner — and it still grants rights to the site.",
    "Открой кабинет поисковика → настройки сайта → «Пользователи и права» (в Вебмастере — «Права доступа») и посмотри, какой код подтверждает тебя.":
        "Open the search engine's console → site settings → «Users and permissions» (in Webmaster — «Access rights») and see which code verifies you.",
    "Второй метатег удали из кода, а лишнего владельца — из кабинета, если он там остался.":
        "Delete the other meta tag from the code, and the extra owner from the console if it is still there.",
    "Убрать лишний код подтверждения": "Remove the extra verification code",
    # ── Cloudflare's AI-bot setting ───────────────────────────
    "Cloudflare не пускает ИИ-ассистентов": "Cloudflare turns AI assistants away",
    "В зоне включено «Block AI bots»: ChatGPT, Claude, Perplexity и другие ассистенты получают отказ — сайт не попадает в их ответы и ссылки.":
        "«Block AI bots» is on in the zone: ChatGPT, Claude, Perplexity and other assistants are refused — the site does not make it into their answers and links.",
    "Открой Cloudflare → свой домен → AI Crawl Control (или Security → Bots) и выключи «Block AI bots».":
        "Open Cloudflare → your domain → AI Crawl Control (or Security → Bots) and switch «Block AI bots» off.",
    "Если хочешь закрыть только обучение моделей, оставь блок для обучающих краулеров (GPTBot, ClaudeBot), а ассистентам, которые ищут ответ по запросу человека (ChatGPT-User, Claude-User, PerplexityBot), поставь «Allow».":
        "To keep only model training out, leave the block on the training crawlers (GPTBot, ClaudeBot) and set «Allow» for the assistants that look up an answer for a person (ChatGPT-User, Claude-User, PerplexityBot).",
    "Нажми «🔄 Проверить снова» — я перечитаю настройку.": "Tap «🔄 Check again» — I will read the setting again.",
    "Пустить ИИ-ассистентов в Cloudflare": "Let AI assistants in through Cloudflare",
    # ── the Pages technical address ───────────────────────────
    "Технический адрес *.pages.dev открыт поисковикам": "The *.pages.dev technical address is open to search engines",
    "Google видит две копии сайта и сам выбирает, какую показывать, — бывает, что технический адрес, а основной остаётся «просканирован, не проиндексирован».":
        "Google sees two copies of the site and picks which one to show by itself — sometimes the technical address, while the main one stays «crawled, not indexed».",
    "Добавь в файл _headers в корне проекта блок «https://:project.pages.dev/*» со строкой «X-Robots-Tag: noindex» (с отступом) — Cloudflare Pages отдаст этот заголовок только на техническом адресе.":
        "Add to the _headers file at the project's root a «https://:project.pages.dev/*» block with the line «X-Robots-Tag: noindex» (indented) — Cloudflare Pages sends that header on the technical address only.",
    "Страницы, которые отдают Functions (заметки, sitemap, llms.txt), _headers не трогает: в функции добавь тот же заголовок, когда запрос пришёл на *.pages.dev, — или переадресуй такой запрос на основной адрес.":
        "_headers does not touch the pages Functions serve (notes, sitemap, llms.txt): in the function add the same header when the request came to *.pages.dev — or redirect such a request to the main address.",
    "Задеплой и нажми «🔄 Проверить снова».": "Deploy and tap «🔄 Check again».",
    "Закрыть технический адрес": "Close the technical address",
}

PLURALS: dict[str, tuple[str, str]] = {}
