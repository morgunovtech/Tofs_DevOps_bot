"""What every finding of the search/AI audit means for the owner and what
to do about it — in the owner's language, with steps that depend on the
hosting when the hosting matters. Pure data + tiny helpers, unit-tested.

Each finding carries a stable ``code``; the audit screen turns codes into
headlines and «💡 Что делать» buttons, the fix screen turns them into steps."""

from dataclasses import dataclass, field
from urllib.parse import urlparse

from services import hosting as hosting_svc


@dataclass(frozen=True)
class Fix:
    code: str
    icon: str
    title: str                    # headline: what is wrong, for a human
    why: str                      # what it costs the owner
    steps: tuple[str, ...]        # generic steps, numbered on screen
    by_hosting: dict[str, tuple[str, ...]] = field(default_factory=dict)  # replaces generic steps
    links: tuple[tuple[str, str], ...] = ()   # (label, url with {url}/{host} placeholders)
    short: str = ""               # button label; title when empty


_F = Fix
FIXES: dict[str, Fix] = {
    # ── critical: the site drops out of search ─────────────────────────────
    "robots_search": _F(
        "robots_search", "🚫", "Сайт закрыт от поисковиков в robots.txt",
        "Google и Яндекс перестанут показывать сайт — переходы из поиска уйдут в ноль за пару недель.",
        ("Открой файл robots.txt (кнопка ниже) и найди блок User-agent: Googlebot, YandexBot или * "
         "со строкой Disallow: /.",
         "Удали эту строку или замени на Allow: /. Запреты на отдельные папки (например, /admin/) можно оставить.",
         "Где лежит файл: у конструкторов (Tilda, Wix, Webflow, WordPress) — в настройках сайта → SEO → "
         "«Индексация» или «robots.txt»; у сайта на хостинге — файл robots.txt в корне.",
         "Нажми «🔄 Проверить снова» — я подтвержу, что запрет снят."),
        links=(("📄 Открыть robots.txt", "{url}/robots.txt"),), short="Снять запрет в robots.txt"),
    "noindex_header": _F(
        "noindex_header", "🙈", "Сервер просит поисковики не показывать страницу",
        "Заголовок ответа X-Robots-Tag: noindex — при следующем обходе Google и Яндекс уберут страницу из поиска.",
        ("Этот заголовок ставит сервер или хостинг, не код страницы. Обычно так закрывают тестовую версию "
         "сайта — и настройка случайно доехала до боевого домена.",
         "Найди, где он задан: в настройках веб-сервера (nginx: add_header X-Robots-Tag; Apache: .htaccess "
         "Header set X-Robots-Tag) или в конфиге хостинга.",
         "Убери его для боевого домена и нажми «🔄 Проверить снова»."),
        by_hosting={
            "vercel": ("В проекте открой vercel.json → секция headers: там есть X-Robots-Tag: noindex. Удали "
                       "его для боевого домена (для preview-деплоев Vercel ставит его сам — это нормально).",
                       "Задеплой заново и нажми «🔄 Проверить снова»."),
            "netlify": ("Открой файл _headers или netlify.toml в проекте — там задан X-Robots-Tag: noindex. "
                        "Убери строку для боевого домена.",
                        "Задеплой заново и нажми «🔄 Проверить снова»."),
            "cloudflare": ("Cloudflare → сайт → Rules → Transform Rules → Modify Response Header: найди правило, "
                           "добавляющее X-Robots-Tag, и выключи его.",
                           "Если правила нет — заголовок ставит сам сервер за Cloudflare: смотри его конфиг.",
                           "Нажми «🔄 Проверить снова»."),
        }, short="Убрать noindex с сервера"),
    "noindex_meta": _F(
        "noindex_meta", "🙈", "В коде страницы стоит запрет на индексацию",
        "Тег meta robots noindex — Google и Яндекс уберут страницу из поиска при следующем обходе.",
        ("WordPress: Настройки → Чтение → сними галочку «Попросить поисковые системы не индексировать сайт». "
         "Tilda/Wix/Webflow: настройки страницы → SEO → «Скрыть от поисковиков» выключить.",
         "Свой код: найди в шаблоне строку <meta name=\"robots\" content=\"noindex\"> и удали её.",
         "Нажми «🔄 Проверить снова»."),
        short="Убрать noindex из кода"),
    # ── warnings: the site is found, but worse than it could be ─────────────
    "robots_ai": _F(
        "robots_ai", "🤖", "ИИ-ассистентам запрещён вход на сайт",
        "ChatGPT, Claude и Perplexity не смогут рассказать о сайте и посоветовать его. На Google и Яндекс это не влияет.",
        ("Если запрет поставлен осознанно (не хочешь отдавать тексты ИИ) — ничего делать не нужно.",
         "Иначе открой robots.txt и удали блоки User-agent: GPTBot / ClaudeBot / PerplexityBot со строкой Disallow: /.",
         "Нажми «🔄 Проверить снова»."),
        links=(("📄 Открыть robots.txt", "{url}/robots.txt"),), short="Пустить ИИ-ассистентов"),
    "ai_blocked": _F(
        "ai_blocked", "🤖", "Хостинг отвечает ИИ-ассистентам ошибкой доступа",
        "ChatGPT, Claude и Perplexity получают «доступ запрещён» и не узнают о сайте. Обычно это защита от ботов.",
        ("Найди в панели хостинга защиту от ботов (Bot protection / Anti-bot / WAF) и добавь исключение для "
         "GPTBot, ClaudeBot и PerplexityBot — или выключи блокировку ИИ-ботов целиком.",
         "Если защиту ставил не ты — спроси у того, кто настраивал сайт.",
         "Нажми «🔄 Проверить снова»."),
        by_hosting={
            "cloudflare": ("Cloudflare → сайт → Security → Bots: выключи переключатель «Block AI bots» "
                           "(в новых панелях — Security → Settings → AI Scrapers and Crawlers).",
                           "Если хочешь пускать не всех — оставь блокировку и добавь GPTBot, ClaudeBot, "
                           "PerplexityBot в исключения (Security → WAF → Custom rules → Skip).",
                           "Нажми «🔄 Проверить снова»."),
        }, short="Открыть доступ ИИ-ассистентам"),
    "no_js": _F(
        "no_js", "🤖", "ИИ-ассистенты и часть роботов видят пустую страницу",
        "Текст появляется только после запуска JavaScript. ИИ-ассистенты и часть поисковых роботов JavaScript "
        "не запускают: для них на сайте нет текста — нечего показать в поиске и не о чем рассказать.",
        ("Если сайт собран на React, Vue или Angular — включи серверный рендеринг или статическую сборку: "
         "Next.js, Nuxt и Astro умеют это из коробки; для Vite/CRA есть предрендер (vite-plugin-ssr, prerender.io).",
         "Минимум без переделки: положи заголовок, описание и главный текст прямо в HTML, а JavaScript пусть "
         "только оживляет страницу.",
         "Проверить самому: в Chrome → Настройки → Конфиденциальность → Настройки сайтов → JavaScript → "
         "выключить, открыть сайт. Что видно — то видят и роботы.",
         "Нажми «🔄 Проверить снова» — я скажу, сколько текста стало видно."),
        short="Показать текст без JavaScript"),
    "soft404": _F(
        "soft404", "🗑", "Несуществующие адреса отвечают «всё хорошо»",
        "Любой случайный адрес на сайте выглядит для поисковика настоящей страницей. Индекс засоряется "
        "пустыми дублями, а настоящие страницы ранжируются хуже.",
        ("Несуществующая страница должна отвечать кодом 404 (или 410), а не 200.",
         "Обычная причина — правило «любой адрес → index.html» для одностраничного приложения. Замени его на "
         "отдельную страницу 404.",
         "nginx: убери try_files $uri /index.html; и добавь error_page 404 /404.html;. WordPress и "
         "конструкторы делают это сами — проверь плагины редиректов.",
         "Открой тестовую ссылку ниже: должна быть страница «не найдено». Потом «🔄 Проверить снова»."),
        by_hosting={
            "cloudflare": ("Cloudflare Pages: в файле _redirects не должно быть правила /* /index.html 200. "
                           "Для одностраничного приложения положи в корень 404.html — Pages сам отдаст его с кодом 404.",
                           "Если сайт не на Pages, а просто за Cloudflare — правило живёт на твоём сервере или в "
                           "Rules → Redirect Rules: найди «всё → /».",
                           "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "netlify": ("Убери из _redirects или netlify.toml правило /* /index.html 200 и положи 404.html в корень "
                        "сайта — Netlify отдаст его с кодом 404.",
                        "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "vercel": ("В vercel.json найди rewrites с source \"/(.*)\" → index.html: это правило ловит всё. "
                       "Next.js: сделай страницу pages/404.js или app/not-found.js — Vercel отдаст её с кодом 404.",
                       "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "github": ("GitHub Pages: положи файл 404.html в корень репозитория (или в папку docs) — он отдаётся "
                       "с кодом 404 автоматически.",
                       "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
        },
        links=(("🔗 Открыть несуществующую страницу", "{url}/takoy-stranicy-net"),),
        short="Настроить ответ 404"),
    "https_redirect": _F(
        "https_redirect", "🔓", "http:// не переводит на https://",
        "Кто наберёт адрес руками или перейдёт по старой ссылке, попадёт на незащищённую версию: браузер покажет "
        "«Не защищено», а поисковики увидят два сайта вместо одного.",
        ("Нужен постоянный редирект (301) с http:// на https:// для всех адресов.",
         "nginx: отдельный server на порту 80 с return 301 https://$host$request_uri;. Apache: RewriteRule "
         "в .htaccess. WordPress: Настройки → Общие → оба адреса сайта с https://.",
         "Открой тестовую ссылку ниже: должно перекинуть на https://. Потом «🔄 Проверить снова»."),
        by_hosting={
            "cloudflare": ("Cloudflare → сайт → SSL/TLS → Edge Certificates → включи «Always Use HTTPS».",
                           "Там же поставь режим SSL/TLS «Full (strict)», если он «Flexible».",
                           "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "github": ("GitHub → репозиторий → Settings → Pages → галочка «Enforce HTTPS».",
                       "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "netlify": ("Netlify → Site → Domain management → HTTPS → «Force HTTPS» (появляется после выпуска сертификата).",
                        "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
            "vercel": ("Vercel переводит на https сам. Если нет — домен подключён не через Vercel: проверь, куда "
                       "ведут DNS-записи у регистратора.",
                       "Открой тестовую ссылку ниже, потом «🔄 Проверить снова»."),
        },
        links=(("🔗 Открыть http://-версию", "http://{host}/"),), short="Включить редирект на https"),
    "sitemap_missing": _F(
        "sitemap_missing", "🗺", "Нет карты сайта (sitemap.xml)",
        "Поисковики узнают о новых страницах медленнее, а Яндекс Вебмастер и Google Search Console показывают меньше данных.",
        ("WordPress: плагин Yoast или Rank Math создаёт карту сам. Tilda, Wix, Webflow — тоже сами: проверь, "
         "что в настройках SEO карта включена.",
         "Свой сайт: сгенерируй (Next.js — next-sitemap, Astro — @astrojs/sitemap, или онлайн xml-sitemaps.com) "
         "и положи файл в корень.",
         "Добавь в robots.txt строку Sitemap: {url}/sitemap.xml и нажми «🔄 Проверить снова»."),
        links=(("🗺 Открыть sitemap.xml", "{url}/sitemap.xml"),), short="Добавить карту сайта"),
    "robots_http_error": _F(
        "robots_http_error", "📄", "robots.txt отвечает ошибкой",
        "Если robots.txt отвечает ошибкой сервера, Google может приостановить обход всего сайта.",
        ("Открой robots.txt по кнопке ниже и посмотри, что показывается.",
         "Ошибка 5xx — сбой сервера или хостинга: смотри логи, перезапусти деплой. Ошибка 403 — файл закрыт "
         "правами или правилом защиты: сними запрет.",
         "Нажми «🔄 Проверить снова»."),
        links=(("📄 Открыть robots.txt", "{url}/robots.txt"),), short="Починить robots.txt"),
    "no_title": _F(
        "no_title", "🏷", "У страницы нет заголовка (title)",
        "В результатах поиска она будет без названия или со случайным текстом — по таким ссылкам меньше кликают.",
        ("Конструктор: настройки страницы → SEO → «Заголовок» (Title).",
         "Свой код: в <head> добавь <title>Название страницы — Название сайта</title>, 10–70 символов.",
         "Нажми «🔄 Проверить снова»."),
        short="Добавить заголовок страницы"),
    "no_description": _F(
        "no_description", "📝", "У страницы нет описания (meta description)",
        "Под заголовком в выдаче поисковик покажет случайный кусок текста вместо твоего.",
        ("Напиши 1–2 предложения о странице, 50–170 символов: что здесь и зачем сюда заходить.",
         "Конструктор: настройки страницы → SEO → «Описание». Свой код: <meta name=\"description\" content=\"…\"> в <head>.",
         "Нажми «🔄 Проверить снова»."),
        short="Добавить описание страницы"),
    "canonical_bad": _F(
        "canonical_bad", "🔗", "Тег canonical ведёт не туда",
        "canonical говорит поисковику «настоящая копия страницы вон там». Если он ведёт на http:// или чужой домен, "
        "в поиске покажут ту копию — или не покажут эту страницу вовсе.",
        ("Он должен вести на https-адрес этой же страницы на этом же сайте: <link rel=\"canonical\" href=\"https://{host}/…\">.",
         "Конструктор: обычно правится в настройках домена — сделай https-домен основным.",
         "Нажми «🔄 Проверить снова»."),
        short="Поправить canonical"),
    "jsonld_invalid": _F(
        "jsonld_invalid", "🧩", "Разметка для поиска (JSON-LD) с ошибкой",
        "Поисковики проигнорируют расширенное описание в выдаче: звёзды, цены, хлебные крошки, FAQ.",
        ("Открой проверку Google по кнопке ниже — она покажет строку с ошибкой.",
         "Обычно это лишняя запятая, незакрытая кавычка или подставленный шаблоном пустой текст.",
         "Нажми «🔄 Проверить снова»."),
        links=(("🧪 Проверить в Google", "https://search.google.com/test/rich-results?url={url}"),),
        short="Починить разметку JSON-LD"),
    # ── info: nice to have ──────────────────────────────────────────────────
    "title_len": _F("title_len", "🏷", "Заголовок страницы не той длины",
                    "Слишком короткий ни о чём не говорит, слишком длинный обрежется в выдаче.",
                    ("Сделай заголовок 10–70 символов: о чём страница + название сайта.",)),
    "desc_len": _F("desc_len", "📝", "Описание страницы не той длины",
                   "Короткое не объяснит, зачем заходить; длинное обрежется в выдаче.",
                   ("Сделай описание 50–170 символов, 1–2 предложения.",)),
    "no_canonical": _F("no_canonical", "🔗", "Нет тега canonical",
                       "Не страшно, если у страницы один адрес. Если она открывается и с www, и с параметрами — "
                       "поисковик может посчитать это дублями.",
                       ("Добавь <link rel=\"canonical\" href=\"https://{host}/…\"> с основным адресом страницы.",)),
    "no_h1": _F("no_h1", "📰", "Нет главного заголовка (h1)",
                "Поисковику труднее понять, о чём страница. Один h1 на страницу — норма.",
                ("Оберни главный заголовок страницы в <h1>. В конструкторах — выбери для него стиль «Заголовок 1».",)),
    "og_title": _F("og_title", "💬", "Нет заголовка для превью в мессенджерах (og:title)",
                   "При отправке ссылки в Telegram или WhatsApp превью будет с адресом вместо названия.",
                   ("Добавь <meta property=\"og:title\" content=\"…\">. Конструкторы: настройки страницы → «Превью для соцсетей».",)),
    "og_image": _F("og_image", "🖼", "Нет картинки для превью в мессенджерах (og:image)",
                   "Ссылка на сайт в Telegram, WhatsApp и соцсетях будет без картинки — по ней реже кликают.",
                   ("Добавь картинку 1200×630 и тег <meta property=\"og:image\" content=\"https://{host}/preview.jpg\">. "
                    "Конструкторы: настройки страницы → «Превью для соцсетей».",)),
    "no_lang": _F("no_lang", "🌐", "Не указан язык страницы (lang)",
                  "Поисковики и переводчики гадают, на каком языке сайт.",
                  ("Добавь атрибут: <html lang=\"ru\">.",)),
    "robots_missing": _F("robots_missing", "📄", "Файла robots.txt нет",
                         "Без него всё разрешено — не страшно. Но и карту сайта поисковики не найдут через него.",
                         ("Создай robots.txt в корне с двумя строками: User-agent: * и Sitemap: {url}/sitemap.xml.",)),
    "http_closed": _F("http_closed", "🔒", "http://-версия закрыта",
                      "Порт 80 не отвечает — это нормально, если все ссылки и так с https://.",
                      ("Ничего делать не нужно.",)),
    "llms_missing": _F("llms_missing", "🤖", "Нет файла llms.txt",
                       "Необязательно. Короткая текстовая справка о сайте для ИИ-агентов.",
                       ("Если хочешь — положи в корень llms.txt: пара абзацев о сайте и ссылки на главные страницы.",)),
    "llms_ok": _F("llms_ok", "🤖", "Есть llms.txt", "ИИ-агенты найдут краткую справку о сайте.", ("Ничего делать не нужно.",)),
}


def fix(code: str) -> Fix:
    return FIXES.get(code) or Fix(code, "💡", code, "", ("Нажми «🔄 Проверить снова».",))


def button_label(code: str) -> str:
    f = fix(code)
    return f"💡 {f.short or f.title}"


def steps(code: str, hosting: str | None, url: str) -> list[str]:
    """Numbered steps for this hosting (generic when the hosting is unknown
    or has nothing specific), with {url}/{host} filled in."""
    f = fix(code)
    chosen = f.by_hosting.get(hosting or "") or f.steps
    return [_fill(s, url) for s in chosen]


def links(code: str, hosting: str | None, url: str) -> list[tuple[str, str]]:
    out = [(label, _fill(target, url)) for label, target in fix(code).links]
    panel = hosting_svc.panel_url(hosting)
    if panel and hosting in fix(code).by_hosting:
        out.append((f"🔗 Открыть панель {hosting_svc.label(hosting)}", panel))
    return out


def _fill(text: str, url: str) -> str:
    return text.replace("{url}", url.rstrip("/")).replace("{host}", urlparse(url).hostname or "")
