"""English for services/* (see utils/i18n.py). Grouped by module; a key is
the Russian source string, byte for byte."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── services/humanize.py: words ─────────────────────────────────────────
    "{seconds} с": "{seconds} s",
    "нет данных": "no data",
    "за {seconds}{where}": "in {seconds}{where}",
    "хостинг отвечает ошибкой {code}": "the hosting answers with error {code}",
    "страница не найдена (ошибка 404)": "page not found (error 404)",
    "доступ закрыт (ошибка {code})": "access denied (error {code})",
    "сервер просит подождать (ошибка 429, слишком много запросов)": "the server asks to wait (error 429, too many requests)",
    "сервер отверг запрос (ошибка {code})": "the server rejected the request (error {code})",
    "сегодня": "today",
    "завтра": "tomorrow",
    "через {days} дн.": "in {days} d",
    "сертификат": "certificate",
    "домен": "domain",
    "{what} истекает {when}": "{what} expires {when}",
    "не открывается": "does not open",
    "на странице стоп-фраза «{phrase}»": "the page shows the stop phrase «{phrase}»",
    "на странице нет фразы «{phrase}»": "the page lacks the phrase «{phrase}»",
    "в ответе стоп-фраза «{phrase}»": "the answer has the stop phrase «{phrase}»",
    "в ответе нет фразы «{phrase}»": "the answer lacks the phrase «{phrase}»",
    "не ответил за {seconds} секунд": "did not answer within {seconds} seconds",
    "сертификат истёк {days} дн. назад": "certificate expired {days} d ago",
    "сертификат истёк": "certificate expired",
    "сертификат недействителен: {why}": "certificate invalid: {why}",
    "не удалось проверить сертификат": "could not check the certificate",
    "домен истёк {days} дн. назад": "domain expired {days} d ago",
    "не удалось узнать срок домена": "could not find out the domain's expiry",
    "открывается за {seconds}": "opens in {seconds}",
    "{n} {links}": "{n} {links}",
    "{n} из {total} проверенных страниц отдают ошибку": "{n} of {total} checked pages return an error",
    "{n} из {total} проверенных страниц отдают ошибку или не отвечают": "{n} of {total} checked pages return an error or do not answer",
    "страница не загрузилась": "the page did not load",
    "{n} {obstacles} в поиске, напр.: {example}": "{n} search {obstacles}, e.g.: {example}",
    "закрыт от поиска — {why}": "closed to search — {why}",
    "адрес сайта не находится (проблема с DNS)": "the site's address cannot be found (a DNS problem)",
    "сертификат не прошёл проверку": "the certificate failed verification",
    "не ответил вовремя": "did not answer in time",
    "сервер отказал в соединении": "the server refused the connection",
    "сервер недоступен: до него нет маршрута": "the server is unreachable: no route to it",
    "не удалось соединиться с сервером": "could not connect to the server",
    "сервер оборвал соединение": "the server dropped the connection",
    "ответ оборвался на середине": "the answer broke off halfway",
    "адрес перенаправляет по кругу": "the address redirects in a loop",
    "сервер ответил ошибкой": "the server answered with an error",
    "не отвечает на ping": "does not answer ping",
    "закрытый или удалённый репозиторий (у посетителей ошибка 404)": "a private or deleted repository (visitors get error 404)",
    "каждую минуту": "every minute",
    "каждый час": "every hour",
    "каждые {minutes} мин": "every {minutes} min",
    "один раз": "once",
    "дважды подряд": "twice in a row",
    "{n} {times} подряд": "{n} {times} in a row",
    "неизвестная ошибка": "unknown error",
    "необычная ошибка соединения": "an unusual connection error",
    "Что делать:": "What to do:",
    "без сбоев": "no downtime",
    "недоступен ≈{duration}": "down ≈{duration}",
    "{label} показывает не то: {what}": "{label} shows the wrong thing: {what}",
    "{label} не открывается: {what}": "{label} does not open: {what}",
    "{label} не отвечает: {what}": "{label} does not answer: {what}",
    "{label} медленно отвечает": "{label} answers slowly",
    " и ещё {n} {obstacles}": " and {n} more {obstacles}",
    # ── services/humanize.py: explanations (meaning · cause · steps) ────────
    "Посетители видят страницу с ошибкой вместо сайта.": "Visitors see an error page instead of the site.",
    "хостинг отвечает ошибкой сервера — сбой на стороне хостинга, упавший деплой или закончившиеся лимиты тарифа":
        "the hosting answers with a server error — an outage on its side, a failed deploy or an exhausted plan",
    "Подождать 5 минут: я продолжаю проверять и напишу, как только сайт поднимется.":
        "Wait 5 minutes: I keep checking and will write as soon as the site is back.",
    "Если не поднимется — открыть панель хостинга, посмотреть последний деплой и перезапустить или откатить его.":
        "If it does not come back — open the hosting panel, look at the last deploy and restart or roll it back.",
    "Если панель тоже не открывается — проверить статус хостинга и написать в его поддержку.":
        "If the panel does not open either — check the hosting's status page and write to its support.",
    "Посетители получают «страница не найдена» или «доступ закрыт».": "Visitors get «page not found» or «access denied».",
    "адрес главной страницы изменился, включена защита паролем, или хостинг закрыл сайт":
        "the homepage address changed, password protection is on, or the hosting closed the site",
    "Открыть сайт в браузере и посмотреть, что показывается.": "Open the site in a browser and see what it shows.",
    "Если сайт намеренно закрыт паролем — в настройках сайта в боте разрешить этот код ответа.":
        "If the site is closed with a password on purpose — allow this response code in the site's settings in the bot.",
    "Если адрес изменился — обновить адрес сайта в боте.": "If the address changed — update the site's address in the bot.",
    "Сайт грузится так долго, что браузеры и посетители не дожидаются.": "The site takes so long that browsers and visitors give up.",
    "сервер перегружен, завис или отвечает из-за границы очень медленно":
        "the server is overloaded, hung, or answers from abroad very slowly",
    "Подождать 5 минут — часто это проходит само.": "Wait 5 minutes — this often passes by itself.",
    "Если нет — перезапустить приложение в панели хостинга.": "If not — restart the application in the hosting panel.",
    "Если повторяется каждый день — тариф или сервер не тянут нагрузку.": "If it repeats every day — the plan or the server cannot take the load.",
    "Браузер вообще не находит сайт по адресу — как будто его нет.": "The browser cannot find the site at its address at all — as if it did not exist.",
    "истёк домен, сломались DNS-записи или домен переехал": "the domain expired, the DNS records broke or the domain moved",
    "Проверить в панели регистратора, что домен оплачен и не истёк.": "Check in the registrar's panel that the domain is paid for and not expired.",
    "Проверить DNS-записи у регистратора или в Cloudflare: должна быть запись A или CNAME на хостинг.":
        "Check the DNS records at the registrar or in Cloudflare: there must be an A or CNAME record pointing at the hosting.",
    "Если ты недавно менял хостинг — DNS-изменения доходят до часа.": "If you changed hosting recently — DNS changes take up to an hour.",
    "Сервер не принимает соединения.": "The server does not accept connections.",
    "сервер работает, но на нём ничего не запущено на нужном порту — упало приложение или веб-сервер":
        "the server is up, but nothing listens on the right port — the application or the web server went down",
    "Перезапустить приложение или контейнер в панели хостинга.": "Restart the application or the container in the hosting panel.",
    "Посмотреть логи: обычно причина видна в последних строках.": "Look at the logs: the cause is usually in the last lines.",
    "До сервера не достучаться.": "The server cannot be reached.",
    "сервер выключен, перезагружается или закрыт файрволом": "the server is off, rebooting or closed by a firewall",
    "Проверить в панели хостинга, что сервер запущен.": "Check in the hosting panel that the server is running.",
    "Если только что был деплой — подождать пару минут.": "If there was just a deploy — wait a couple of minutes.",
    "Пока всё работает. Если сертификат не продлится, браузеры покажут посетителям красное предупреждение "
    "«соединение не защищено», и большинство уйдёт.":
        "Everything works for now. If the certificate is not renewed, browsers will show visitors a red "
        "«connection not secure» warning, and most will leave.",
    "обычно сертификат продлевается сам за 2–4 недели до срока; раз этого не случилось, автопродление сломалось или отключено":
        "the certificate usually renews itself 2–4 weeks before expiry; since it did not, auto-renewal is broken or off",
    "Открыть панель хостинга или Cloudflare → SSL/TLS и проверить, включено ли автопродление.":
        "Open the hosting panel or Cloudflare → SSL/TLS and check whether auto-renewal is on.",
    "Если сайт на своём сервере — проверить, что работает certbot (systemctl status certbot.timer).":
        "If the site is on your own server — check that certbot runs (systemctl status certbot.timer).",
    "Я напишу снова за 7, 3 и 1 день до срока.": "I will write again 7, 3 and 1 day before expiry.",
    "Посетители видят красное предупреждение «соединение не защищено» и не могут зайти.":
        "Visitors see a red «connection not secure» warning and cannot get in.",
    "сертификат не продлился в срок": "the certificate was not renewed in time",
    "Продлить или перевыпустить сертификат в панели хостинга или Cloudflare → SSL/TLS.":
        "Renew or reissue the certificate in the hosting panel or Cloudflare → SSL/TLS.",
    "На своём сервере: certbot renew, затем перезапуск веб-сервера.": "On your own server: certbot renew, then restart the web server.",
    "Посетители видят красное предупреждение «соединение не защищено».": "Visitors see a red «connection not secure» warning.",
    "сертификат выдан на другой адрес, повреждён или сайт переехал, а сертификат нет":
        "the certificate was issued for another address, is damaged, or the site moved and the certificate did not",
    "Проверить в панели хостинга, на какой адрес выдан сертификат.": "Check in the hosting panel which address the certificate was issued for.",
    "Перевыпустить сертификат для этого адреса.": "Reissue the certificate for this address.",
    "Пока всё работает. После истечения сайт и почта на домене перестанут работать, а через несколько недель "
    "домен смогут купить другие.":
        "Everything works for now. After expiry the site and the domain's mail stop working, and a few weeks "
        "later someone else can buy the domain.",
    "не продлён домен — письмо регистратора ушло в спам или отвязалась карта":
        "the domain was not renewed — the registrar's email went to spam or the card was unlinked",
    "Продлить домен в панели регистратора (обычно на год, это 5–15 минут).": "Renew the domain in the registrar's panel (usually for a year, 5–15 minutes).",
    "Включить там автопродление, чтобы это не повторилось.": "Turn on auto-renewal there so this does not happen again.",
    "Сайт и почта на домене не работают.": "The site and the domain's mail do not work.",
    "домен не продлён в срок": "the domain was not renewed in time",
    "Срочно продлить домен у регистратора: обычно есть льготный период 30 дней, пока домен ещё можно вернуть.":
        "Renew the domain at the registrar urgently: there is usually a 30-day grace period while the domain can still be recovered.",
    "Сайт отвечает, но показывает не то: обычно пустую страницу или ошибку приложения.":
        "The site answers but shows the wrong thing: usually an empty page or an application error.",
    "упал бэкенд при живом веб-сервере, деплой выкатил пустую версию, или на страницу поставили заглушку":
        "the backend died while the web server lives, a deploy shipped an empty version, or a placeholder was put on the page",
    "Если страница пустая — откатить последний деплой.": "If the page is empty — roll back the last deploy.",
    "Если фразу изменили специально — обновить её в настройках сайта в боте.":
        "If the phrase was changed on purpose — update it in the site's settings in the bot.",
    "Сайт отвечает, но посетители видят текст ошибки.": "The site answers, but visitors see an error text.",
    "приложение или база данных упали, а страница с ошибкой всё равно отдаётся":
        "the application or the database went down, and the error page is served anyway",
    "Открыть сайт и прочитать ошибку.": "Open the site and read the error.",
    "Перезапустить приложение или базу в панели хостинга, откатить деплой.":
        "Restart the application or the database in the hosting panel, roll back the deploy.",
    "Сайт открывается, но посетители ждут по несколько секунд, часть уходит.":
        "The site opens, but visitors wait several seconds, and some leave.",
    "перегружен хостинг, тяжёлая страница или проблемы на пути к серверу":
        "an overloaded hosting, a heavy page or trouble on the way to the server",
    "Подождать: часто это временная нагрузка.": "Wait: this is often a temporary load.",
    "Если продолжится — посмотреть нагрузку в панели хостинга или написать в поддержку.":
        "If it goes on — look at the load in the hosting panel or write to support.",
    "Если так всегда — поставить сайт за Cloudflare или перейти на тариф побольше.":
        "If it is always like this — put the site behind Cloudflare or move to a bigger plan.",
    "Посетитель нажмёт — и упрётся в ошибку. Неважно, ведёт ссылка на твой сайт или на чужой: чинить её на твоей странице.":
        "A visitor clicks — and hits an error. Whether the link leads to your site or someone else's, it is fixed on your page.",
    "страницу переименовали или удалили, чужой сайт закрылся или репозиторий стал закрытым":
        "the page was renamed or deleted, someone else's site closed down or a repository went private",
    "Открыть перечисленные ссылки и исправить их или убрать.": "Open the listed links and fix or remove them.",
    "Если страница пропала насовсем — дать копию из web.archive.org.": "If the page is gone for good — link a copy from web.archive.org.",
    "Если свои страницы переехали — настроить редирект со старого адреса на новый.":
        "If your own pages moved — set up a redirect from the old address to the new one.",
    "Главная работает, но часть страниц отдаёт ошибку.": "The homepage works, but some pages return an error.",
    "сломан раздел сайта, база данных или один из сервисов": "a section of the site, the database or one of the services is broken",
    "Открыть перечисленные страницы в браузере.": "Open the listed pages in a browser.",
    "Посмотреть логи хостинга за последний час.": "Look at the hosting logs for the last hour.",
    "Откатить последний деплой, если ошибки появились после него.": "Roll back the last deploy if the errors appeared after it.",
    "Сайт исчезает из Google и Яндекса: страницы перестанут находиться в поиске.":
        "The site is vanishing from Google and Yandex: its pages will stop showing up in search.",
    "в код попал запрет индексации (noindex) или robots.txt закрыл сайт от поисковиков — обычно из-за настроек "
    "тестовой версии, ушедших в прод":
        "a no-index rule (noindex) got into the code or robots.txt closed the site to search engines — usually "
        "staging settings that went to production",
    "Убрать <meta name=\"robots\" content=\"noindex\"> и заголовок X-Robots-Tag со страниц.":
        "Remove <meta name=\"robots\" content=\"noindex\"> and the X-Robots-Tag header from the pages.",
    "Проверить robots.txt: не должно быть Disallow: / для Googlebot и YandexBot.":
        "Check robots.txt: there must be no Disallow: / for Googlebot and YandexBot.",
    "После исправления переиндексация занимает от нескольких дней.": "After the fix, re-indexing takes from a few days.",
    "Поисковикам ничего не мешает, но часть страниц они поймут хуже, чем могли бы, а ИИ-ассистентам может быть "
    "нечего показать.":
        "Nothing stops search engines, but they will understand some pages worse than they could, and AI "
        "assistants may have nothing to show.",
    "недочёты разметки и настроек, которые накопились незаметно": "markup and settings flaws that piled up unnoticed",
    "Открыть «🔎 Поиск и ИИ» в боте: у каждого пункта кнопка «что делать» с шагами под твой хостинг.":
        "Open «🔎 Search and AI» in the bot: every item has a «what to do» button with steps for your hosting.",
    "Пока ничего не сломалось, но адрес сайта в DNS изменился.": "Nothing is broken yet, but the site's address in DNS changed.",
    "ты или кто-то с доступом поменял DNS: переезд хостинга, настройка почты, или чужие руки":
        "you or someone with access changed the DNS: a hosting move, a mail setup, or someone else's hands",
    "Если это ты менял — ничего делать не нужно, я запомнил новое значение.": "If you changed it — nothing to do, I remembered the new value.",
    "Если нет — зайти в панель регистратора или Cloudflare и проверить, кто и что менял.":
        "If not — open the registrar's panel or Cloudflare and check who changed what.",
    "Запись, по которой браузеры находят сайт, пропала из DNS. Если она не вернётся, сайт перестанет открываться.":
        "The record browsers use to find the site vanished from DNS. If it does not come back, the site will stop opening.",
    "запись удалили при правке DNS, домен переехал к другому DNS-провайдеру или истёк":
        "the record was deleted while editing DNS, the domain moved to another DNS provider or expired",
    "Открыть DNS у регистратора или в Cloudflare и вернуть запись A или CNAME на хостинг.":
        "Open the DNS at the registrar or in Cloudflare and restore the A or CNAME record pointing at the hosting.",
    "Доступность сайта я проверяю отдельно: если он перестанет открываться — сразу напишу.":
        "I check the site's availability separately: if it stops opening, I write at once.",
    "Сменились серверы, которые управляют всем доменом. Так выглядит угон домена или переезд к другому DNS-провайдеру.":
        "The servers that run the whole domain changed. This is what a domain hijack or a move to another DNS provider looks like.",
    "перенос домена к другому провайдеру или взлом аккаунта регистратора": "a domain transfer to another provider or a hacked registrar account",
    "Если это не ты — срочно зайти в аккаунт регистратора, сменить пароль и вернуть NS-серверы.":
        "If it was not you — log in to the registrar account urgently, change the password and restore the NS servers.",
    "Включить двухфакторную защиту у регистратора.": "Turn on two-factor protection at the registrar.",
    "Письма на домен теперь принимает другой сервер.": "Mail to the domain is now received by another server.",
    "ты перешёл на другой почтовый сервис — или кто-то с доступом к DNS перенаправил почту":
        "you switched to another mail service — or someone with DNS access redirected the mail",
    "Если это ты — ничего делать не нужно, я запомнил новое значение.": "If it was you — nothing to do, I remembered the new value.",
    "Если нет — срочно вернуть записи MX в DNS и сменить пароль там, где правят DNS: иначе твою почту читает чужой сервер.":
        "If not — restore the MX records in DNS urgently and change the password where the DNS is managed: otherwise someone else's server reads your mail.",
    "Письма на домен больше не доходят: у него не осталось почтовых серверов.": "Mail to the domain no longer arrives: it has no mail servers left.",
    "записи MX удалили — случайно при правке DNS или при переезде": "the MX records were deleted — by accident while editing DNS or during a move",
    "Если почта на домене больше не нужна — ничего делать не нужно.": "If the domain no longer needs mail — nothing to do.",
    "Если нужна — вернуть записи MX в DNS: точные значения есть в настройках почтового сервиса.":
        "If it does — restore the MX records in DNS: the exact values are in the mail service's settings.",
    "Письма на твой домен сейчас не доходят: серверы отправителей будут пробовать ещё несколько дней, потом вернут "
    "письмо с ошибкой.":
        "Mail to your domain is not arriving right now: the senders' servers will keep trying for a few days, then "
        "bounce the letter.",
    "сбой у почтового сервиса или сломались записи MX в DNS": "an outage at the mail service or broken MX records in DNS",
    "Открыть страницу статуса почтового сервиса: если сбой у них — ждать, я напишу, когда почта заработает.":
        "Open the mail service's status page: if the outage is theirs — wait, I will write when mail works again.",
    "Если у них всё в порядке — проверить записи MX в DNS: они должны вести на твой почтовый сервис.":
        "If they are fine — check the MX records in DNS: they must point at your mail service.",
    "Письма, отправленные во время сбоя, обычно доходят сами, когда почта поднимется.":
        "Letters sent during the outage usually arrive by themselves once mail is back.",
    "Почта работает, но письма с твоего домена могут попадать в спам, а подделать твой адрес проще.":
        "Mail works, but letters from your domain may land in spam, and your address is easier to forge.",
    "запись SPF, DKIM или DMARC в DNS отсутствует или с ошибкой — часто остаётся после смены почтового сервиса":
        "the SPF, DKIM or DMARC record in DNS is missing or wrong — often left over after a change of mail service",
    "Открыть «✉️ Почта» в боте: там готовые записи, которые нужно вставить в DNS.":
        "Open «✉️ Mail» in the bot: it has the ready records to put into DNS.",
    "Изменения в DNS доходят за 5–30 минут — потом нажать «🔄 Проверить снова».":
        "DNS changes take 5–30 minutes — then tap «🔄 Check again».",
    "Любой может скачать служебные файлы сайта: код со всей историей или пароли и ключи.":
        "Anyone can download the site's service files: the code with its whole history, or passwords and keys.",
    "хостинг публикует весь репозиторий, а не только папку сборки, или файл с настройками попал в публикуемую папку":
        "the hosting publishes the whole repository instead of the build folder only, or a settings file got into the published folder",
    "Убрать .git и .env из того, что публикует хостинг: публиковаться должна папка сборки (dist, build или public).":
        "Remove .git and .env from what the hosting publishes: only the build folder (dist, build or public) should be published.",
    "Сменить все пароли и ключи, которые были в этих файлах: считать, что их уже забрали.":
        "Change every password and key those files held: assume they are already taken.",
    "Нажать «🔄 Проверить снова» на карточке сайта.": "Tap «🔄 Check again» on the site's card.",
    "Сайт работает, но посетителей он защищает хуже, чем мог бы: соединение можно перехватить или встроить сайт в "
    "чужую страницу.":
        "The site works, but protects its visitors less than it could: the connection can be intercepted, or the "
        "site embedded in someone else's page.",
    "по умолчанию хостинги не включают перенаправление на https и защитные заголовки — их добавляют руками":
        "by default hostings do not turn on the redirect to https and the security headers — they are added by hand",
    "Открыть «🛡 Безопасность» в боте: там готовый текст под твой хостинг.": "Open «🛡 Security» in the bot: it has the ready text for your hosting.",
    "После выкладки нажать «🔄 Проверить снова».": "After deploying, tap «🔄 Check again».",
    "Часть посетителей уходит, не дождавшись, а Google опускает медленные сайты в поиске.":
        "Some visitors leave without waiting, and Google ranks slow sites lower.",
    "тяжёлые картинки и шрифты, много скриптов (счётчики, чаты, виджеты) или медленный сервер":
        "heavy images and fonts, many scripts (counters, chats, widgets) or a slow server",
    "Открыть PageSpeed Insights (кнопка ниже) — там видно, что именно тормозит.":
        "Open PageSpeed Insights (the button below) — it shows what exactly is slow.",
    "Сжать картинки (WebP или AVIF) и задать им размеры — заодно перестанет прыгать вёрстка.":
        "Compress the images (WebP or AVIF) and give them sizes — the layout will stop jumping too.",
    "Убрать скрипты, без которых можно обойтись.": "Remove the scripts you can do without.",
    "Бот или задача по расписанию вовремя не вышли на связь.": "A bot or a scheduled job did not check in on time.",
    "крон не запустился, скрипт упал или сервер задачи выключен": "cron did not run, the script crashed or the job's server is off",
    "Проверить на сервере, запускается ли задача, и посмотреть её лог.": "Check on the server whether the job runs, and look at its log.",
    "Запустить задачу вручную и убедиться, что она дошла до конца.": "Run the job by hand and make sure it runs to the end.",
    "Скоро закончится место на сервере бота: перестанут писаться логи и база.":
        "The bot's server is about to run out of disk: logs and the database will stop being written.",
    "накопились логи, старые docker-образы или бэкапы": "logs, old docker images or backups piled up",
    "Посмотреть, что занимает место: docker system df и du -sh /var/lib/docker /var/log.":
        "See what takes the space: docker system df and du -sh /var/lib/docker /var/log.",
    "Безопасно удалить висячие образы и кэш сборки: docker image prune и docker builder prune. Остановленные "
    "контейнеры удалять только свои.":
        "Safely remove dangling images and the build cache: docker image prune and docker builder prune. Remove "
        "only your own stopped containers.",
    "Один из сервисов на сервере бота не работает.": "One of the services on the bot's server is not working.",
    "контейнер упал и не смог перезапуститься": "the container crashed and could not restart",
    "Посмотреть логи контейнера: docker logs <имя>.": "Look at the container's logs: docker logs <name>.",
    "Перезапустить руками: docker restart <имя>.": "Restart it by hand: docker restart <name>.",
    "Сайт недоступен для посетителей.": "The site is unavailable to visitors.",
    "необычная ошибка, которую я не смог распознать": "an unusual error I could not recognise",
    "Проверить панель хостинга.": "Check the hosting panel.",
    # ── services/notifier.py ────────────────────────────────────────────────
    "Если сайт ляжет — всё равно позвоню. Остальное пришлю сводкой, когда тишина закончится.":
        "If a site goes down, I ring anyway. The rest comes as a digest when the silence ends.",
    "Все 🔴 — сайт лёг или снова открылся, истекает сертификат, открыт .env — придут сразу, со звуком. "
    "Остальное пришлю сводкой, когда тишина закончится. Будить только когда сайт лёг: "
    "⚙️ Настройки → 🔔 Когда писать.":
        "Everything 🔴 — a site down or up again, an expiring certificate, an exposed .env — comes at once, with "
        "a sound. The rest comes as a digest when the silence ends. To be woken only when a site is down: "
        "⚙️ Settings → 🔔 When to write.",
    "Группа стала супергруппой — теперь пишу сюда. В .env замени TELEGRAM_ADMIN_CHAT_ID на <code>{chat_id}</code>.":
        "The group became a supergroup — I write here now. In .env replace TELEGRAM_ADMIN_CHAT_ID with <code>{chat_id}</code>.",
    "🔕 Это случилось в {time}, пока про сайт было тихо («🔧 Я чиню» или плановые работы), и до сих пор не прошло:":
        "🔕 This happened at {time}, while the site was on mute («🔧 I'm fixing it» or planned maintenance), and has not passed yet:",
    "⚠️ Если это сообщение уже было — это повтор: в {time} Telegram не подтвердил доставку.":
        "⚠️ If you have seen this message already, this is a repeat: at {time} Telegram did not confirm delivery.",
    "⏳ Задержалось с {time}, отправляю сейчас:": "⏳ Delayed since {time}, sending now:",
    "\n\n👥 Это копия для помощника: управляет ботом владелец, кнопки — у него.":
        "\n\n👥 This is a helper's copy: the owner runs the bot and has the buttons.",
    "(кнопки под сообщением Telegram не принял — отправил без них)": "(Telegram did not take the buttons under the message — sent without them)",
    "⚠️ Пока Telegram не принимал сообщения, {n} из них не поместились в очередь и потерялись. Что сейчас не так — на 🏠 Главной.":
        "⚠️ While Telegram was not accepting messages, {n} of them did not fit in the queue and were lost. What is wrong now is on 🏠 Home.",
    "бот ещё не запущен": "the bot is not started yet",
    "не знаю твой личный чат со мной: укажи TELEGRAM_ADMIN_USER_ID в настройках сервера":
        "I don't know your private chat with me: set TELEGRAM_ADMIN_USER_ID in the server settings",
    "ты ещё не открывал личный чат со мной — открой его и нажми /start":
        "you have not opened a private chat with me yet — open it and send /start",
    "Telegram не принял файл (сеть или сбой на его стороне)": "Telegram did not accept the file (the network or an outage on its side)",
    "🌙 Накопилось, пока было тихо:": "🌙 Piled up while it was quiet:",
    "(ещё {n} {messages} не сохранил — очередь держит последние {max})":
        "({n} more {messages} not kept — the queue holds the last {max})",
    "🌙 (продолжение)": "🌙 (continued)",
    "⚠️ Одно отложенное сообщение Telegram так и не принял — я его убрал, чтобы не задерживать остальные.":
        "⚠️ Telegram never accepted one deferred message — I dropped it so as not to hold up the rest.",
    # ── services/actions.py ─────────────────────────────────────────────────
    "🔧 Я чиню, {length} тишины": "🔧 I'm fixing it, {length} of silence",
    "🔧 Я чиню все — {length} тишины": "🔧 I'm fixing all — {length} of silence",
    "📸 Как выглядит сайт": "📸 How the site looks",
    "ℹ️ Что делать": "ℹ️ What to do",
    "🚀 Пересобрать сайт": "🚀 Rebuild the site",
    "🧹 Сбросить кэш": "🧹 Purge the cache",
    "⏸ Не пересобирать сам 2 ч": "⏸ Don't rebuild by myself for 2 h",
    "🔗 Открыть панель {panel}": "🔗 Open the {panel} panel",
    "🔗 Открыть панель {label}": "🔗 Open the {label} panel",
    "📋 Показать по сайтам": "📋 Show per site",
    "🔍 Что с {job}": "🔍 What's up with {job}",
    "🔗 Подключить {job}": "🔗 Connect {job}",
    "🗑 Удалить": "🗑 Delete",
    "Пересборка не настроена для этого сайта (⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)":
        "Rebuilding is not set up for this site (⚙️ Settings → 🔌 Connections → ☁️ Cloudflare)",
    "Адрес deploy hook испорчен — задай его заново (⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)":
        "The deploy hook address is broken — set it again (⚙️ Settings → 🔌 Connections → ☁️ Cloudflare)",
    "Не удалось связаться с Cloudflare — нажми «🚀 Пересобрать сайт» чуть позже":
        "Could not reach Cloudflare — tap «🚀 Rebuild the site» a little later",
    "Cloudflare не ответил за {sec} с — нажми «🚀 Пересобрать сайт» чуть позже":
        "Cloudflare did not answer within {sec} s — tap «🚀 Rebuild the site» a little later",
    "Не удалось запустить пересборку — подробности в логе бота": "Could not start the rebuild — details in the bot's log",
    "Запустил пересборку сайта — Cloudflare Pages соберёт его за 1–2 минуты":
        "Started rebuilding the site — Cloudflare Pages will build it in 1–2 minutes",
    "Cloudflare не знает этот хук пересборки (ошибка {status}) — похоже, его удалили. Создай новый в Cloudflare Pages "
    "и вставь его: ⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare":
        "Cloudflare does not know this deploy hook (error {status}) — it seems to have been deleted. Create a new one "
        "in Cloudflare Pages and paste it: ⚙️ Settings → 🔌 Connections → ☁️ Cloudflare",
    "Cloudflare сейчас не принимает команды (ошибка {status}) — нажми «🚀 Пересобрать сайт» чуть позже":
        "Cloudflare is not taking commands right now (error {status}) — tap «🚀 Rebuild the site» a little later",
    "Cloudflare не принял команду пересборки (ошибка {status})": "Cloudflare rejected the rebuild command (error {status})",
    "❌ Cloudflare API token / zone id не настроены (⚙️ Настройки → 🔌 Подключения → Cloudflare)":
        "❌ The Cloudflare API token / zone id are not set (⚙️ Settings → 🔌 Connections → Cloudflare)",
    "❌ Zone ID Cloudflare испорчен — задай его заново (⚙️ Настройки → 🔌 Подключения → Cloudflare)":
        "❌ The Cloudflare Zone ID is broken — set it again (⚙️ Settings → 🔌 Connections → Cloudflare)",
    "❌ Cloudflare не дал прочитать зону: {why}. Проверь токен (права на зону) и Zone ID":
        "❌ Cloudflare would not let me read the zone: {why}. Check the token (zone permissions) and the Zone ID",
    "❌ {host} не в зоне Cloudflare {zone} — кэш не трогаю": "❌ {host} is not in the Cloudflare zone {zone} — leaving the cache alone",
    "🧹 Кэш Cloudflare для {host} сброшен — посетители увидят свежую версию сайта":
        "🧹 The Cloudflare cache for {host} is purged — visitors will see the fresh version of the site",
    "❌ Cloudflare не ответил за {sec} с — попробуй через минуту или сбрось кэш в панели Cloudflare":
        "❌ Cloudflare did not answer within {sec} s — try again in a minute or purge the cache in the Cloudflare panel",
    "❌ Не удалось сбросить кэш — подробности в логе бота; попробуй через минуту":
        "❌ Could not purge the cache — details in the bot's log; try again in a minute",
    # ── services/heartbeats.py ──────────────────────────────────────────────
    "раз в час": "once an hour",
    "раз в 6 часов": "once in 6 hours",
    "раз в сутки": "once a day",
    "раз в неделю": "once a week",
    "каждые {duration}": "every {duration}",
    "молчит {duration}": "silent for {duration}",
    "на связи, только что": "in touch, just now",
    "на связи, последний раз {duration} назад": "in touch, last time {duration} ago",
    "так и не выходит на связь — подключали?": "still has not checked in — was it connected?",
    "пока не выходит на связь": "has not checked in yet",
    "✅ {name} — {state} (выходит на связь {rhythm})": "✅ {name} — {state} (checks in {rhythm})",
    "🟠 {name} — молчит {duration}, а выходит на связь {rhythm}: проверь, запускается ли":
        "🟠 {name} — silent for {duration}, though it checks in {rhythm}: see whether it runs",
    "⏳ {name} — пока не выходит на связь (жду {rhythm})": "⏳ {name} — has not checked in yet (expecting it {rhythm})",
    "{icon} {name} — так и не выходит на связь: подключи или удали, кнопки ниже":
        "{icon} {name} — still has not checked in: connect or delete it, buttons below",
    "⏰ Что видит {name}": "⏰ What {name} sees",
    "молчит — не знаю, что сейчас": "silent — I don't know what is happening now",
    " {duration} назад": " {duration} ago",
    "что видит, присылал последний раз{when} — строки устарели": "last sent what it sees{when} — the lines are stale",
    "не ответил за 10 с": "did not answer within 10 s",
    "не удаётся подключиться (сеть или DNS на моей стороне)": "cannot connect (the network or DNS on my side)",
    "не отвечает": "does not answer",
    "отвечает ошибкой {status}": "answers with error {status}",
    "check не найден": "check not found",
    "не на связи {duration} ({why})": "out of touch for {duration} ({why})",
    "на связи": "in touch",
    "пока не на связи": "not in touch yet",
    "🛡 Внешнего сторожа нет — если я упаду, никто не напишет. Подключить: ⚙️ Настройки → 🔌 Подключения":
        "🛡 No external watchdog — if I go down, nobody will write. Connect one: ⚙️ Settings → 🔌 Connections",
    "{icon} Сторож {name} — {state}": "{icon} Watchdog {name} — {state}",
    "{head}: если я упаду, он сообщит": "{head}: if I go down, it will tell you",
    "{head}: если я упаду, никто не узнает": "{head}: if I go down, nobody will know",
    # ── services/maintenance.py ─────────────────────────────────────────────
    "час": "an hour",
    "ежедневно": "daily",
    "будни": "weekdays",
    "выходные": "weekends",
    "Пн": "Mon", "Вт": "Tue", "Ср": "Wed", "Чт": "Thu", "Пт": "Fri", "Сб": "Sat", "Вс": "Sun",
    # ── services/recommend.py ───────────────────────────────────────────────
    "Сейчас я проверяю только, что {label} отвечает. Если вместо сайта откроется пустая страница или заглушка "
    "хостинга, я этого не замечу. Могу следить, чтобы на главной была фраза «{phrase}», — пропадёт, напишу. Следить?":
        "Right now I only check that {label} answers. If an empty page or a hosting placeholder opens instead of the "
        "site, I will not notice. I can watch for the phrase «{phrase}» on the homepage — if it vanishes, I write. Watch it?",
    "✅ Да, следить": "✅ Yes, watch it",
    "Не надо": "No need",
    "Не предлагать": "Don't suggest",
    "Могу вести публичную страницу «всё ли работает» с аптаймом — удобно давать ссылку клиентам или в README.":
        "I can keep a public «is everything working» page with uptime — handy to link for customers or in a README.",
    "🌐 Включить статус-страницу": "🌐 Turn on the status page",
    "Подключи Google Search Console — узнаю в тот же день, если сайт выпадет из поиска, и покажу клики за неделю.":
        "Connect Google Search Console — I learn the same day if the site drops out of search, and show the week's clicks.",
    "🔎 Подключить Google": "🔎 Connect Google",
    "Есть бэкап по расписанию или другой бот? Добавь его на главный экран — скажу, если он молча перестанет запускаться.":
        "Have a scheduled backup or another bot? Add it to the main screen — I will say if it quietly stops running.",
    "⏰ Добавить задачу или бота": "⏰ Add a job or a bot",
    "Подключи Яндекс.Вебмастер — увижу проблемы сайта глазами Яндекса.": "Connect Yandex.Webmaster — I will see the site's problems through Yandex's eyes.",
    "🔎 Подключить Яндекс": "🔎 Connect Yandex",
    "Если сайт на Cloudflare Pages, дай мне deploy hook — при падении пересоберу сайт сам, не дожидаясь тебя.":
        "If the site is on Cloudflare Pages, give me a deploy hook — when it goes down I rebuild it myself, without waiting for you.",
    "☁️ Подключить Cloudflare": "☁️ Connect Cloudflare",
    # ── services/screenshots.py ─────────────────────────────────────────────
    "лимит бесплатных снимков на сегодня исчерпан": "today's quota of free screenshots is used up",
    "снимки выключены (SCREENSHOT_TEMPLATE=off или в нём нет {url})": "screenshots are off (SCREENSHOT_TEMPLATE=off or it has no {url})",
    "это внутренний адрес: сервис снимков его не откроет, и наружу я его не отправляю":
        "this is an internal address: the screenshot service cannot open it, and I never send it out",
    "сервис снимков не смог открыть страницу ({why})": "the screenshot service could not open the page ({why})",
    "сервис снимков не ответил, попробуй через минуту": "the screenshot service did not answer, try again in a minute",
    # ── services/index_status.py ────────────────────────────────────────────
    " и ": " and ",
    # ── services/integrations.py: HEALTH titles and losses ─────────────────
    "Яндекс.Вебмастер": "Yandex.Webmaster",
    "данные Google о скорости у посетителей": "Google's data on the speed for visitors",
    "check-host.net — вторая точка проверки": "check-host.net — the second vantage point",
    "база уязвимостей OSV": "the OSV vulnerability database",
    "RDAP — сроки доменов": "RDAP — domain expiry dates",
    "тревога «Google убрал сайт из поиска» и клики в воскресном отчёте не работают":
        "the «Google removed the site from search» alert and the clicks in the Sunday report do not work",
    "тревоги о проблемах в Вебмастере и строки Яндекса в отчёте не работают":
        "the alerts about Webmaster problems and the Yandex lines in the report do not work",
    "строка «у посетителей» и её 🟠 не обновляются": "the «for visitors» line and its 🟠 are not updated",
    "воскресная проверка репозиториев не работает": "the Sunday repository check does not work",
    "тревога «сайт лёг» уходит без перепроверки извне, замер из региона не обновляется":
        "the «site is down» alert goes out without an external re-check, the regional timing is not updated",
    "проверка уязвимостей в пакетах бота не работает": "the vulnerability check of the bot's packages does not work",
    "сроки регистрации доменов не обновляются": "the domain registration dates are not updated",
    # ── services/crux.py ────────────────────────────────────────────────────
    "вёрстка не прыгает": "the layout does not jump",
    "вёрстка заметно прыгает при загрузке": "the layout visibly jumps while loading",
    "вёрстка сильно прыгает при загрузке": "the layout jumps badly while loading",
    "страница появляется за {seconds}": "the page appears in {seconds}",
    "кнопки откликаются за {seconds}": "the buttons respond in {seconds}",
    " — можно быстрее": " — could be faster",
    " — медленно": " — slow",
    "посетители Chrome за {days} дней, данные Google": "Chrome visitors over {days} days, Google's data",
    " (данные на {date})": " (data as of {date})",
    "{icon} У посетителей: {values} · {source}{dated}": "{icon} For visitors: {values} · {source}{dated}",
    "👥 У посетителей: у Google пока мало данных по сайту — так у большинства небольших сайтов":
        "👥 For visitors: Google has little data on the site so far — as with most small sites",
    # ── services/automation.py ──────────────────────────────────────────────
    " на один хук (проект Pages)": " per hook (a Pages project)",
    "не больше 3 рестартов за эпизод, потом зову человека": "at most 3 restarts per episode, then I call a human",
    "раза": "once",
    "{n} раз": "{n} times",
    "не чаще {times} в ": "at most {times} in ",
    "{hours} ч": "{hours} h",
    "все авто-действия остановлены (⚙️ → 🔌 Подключения → 🤖)": "all auto-actions are paused (⚙️ → 🔌 Connections → 🤖)",
    "все авто-действия остановлены до {time}": "all auto-actions are paused until {time}",
    "уже делал это в {hhmm} — {limit_text}": "already did it at {hhmm} — {limit_text}",
    "выключено": "switched off",
    "уже запускал в {hhmm} — подожди пару минут": "already started it at {hhmm} — wait a couple of minutes",
    "🤖 Сам не пересобирал: сайт на паузе или в плановых работах.": "🤖 Did not rebuild by myself: the site is paused or in planned maintenance.",
    "🤖 Сам не пересобирал: {why}.": "🤖 Did not rebuild by myself: {why}.",
    "🤖 Уже сделал сам: 🚀 {text}": "🤖 Already did it myself: 🚀 {text}",
    "🤖 Пробовал пересобрать сайт — не вышло. {text}": "🤖 Tried to rebuild the site — it failed. {text}",
    # ── services/runtime.py ─────────────────────────────────────────────────
    "🟠 Моя база не на постоянном диске Railway: при каждом обновлении я забываю сайты, настройки и тебя как "
    "хозяина. Подключи диск: в Railway правый клик по сервису → Attach Volume → Mount Path <code>/app/data</code>, "
    "и переменная <code>DB_PATH=/app/data/bot.db</code>.":
        "🟠 My database is not on a persistent Railway disk: with every update I forget the sites, the settings and "
        "you as the owner. Attach a disk: in Railway right-click the service → Attach Volume → Mount Path "
        "<code>/app/data</code>, and the variable <code>DB_PATH=/app/data/bot.db</code>.",
    # ── services/public_urls.py ─────────────────────────────────────────────
    "⚠️ У бота нет публичного адреса, поэтому ссылка ниже не заработает: вместо YOUR_SERVER нужен адрес сервера "
    "бота — нажми «🌍 Как задать адрес» (на своём сервере это PUBLIC_BASE_URL в .env).":
        "⚠️ The bot has no public address, so the link below will not work: YOUR_SERVER must be the bot server's "
        "address — tap «🌍 How to set the address» (on your own server that is PUBLIC_BASE_URL in .env).",
    # ── services/github.py ──────────────────────────────────────────────────
    "токен не принят: истёк, отозван или скопирован не целиком": "the token was rejected: expired, revoked or not copied whole",
    "GitHub отказал в доступе или лимит запросов исчерпан": "GitHub refused access or the request limit is used up",
    "GitHub ответил ошибкой {status}": "GitHub answered with error {status}",
    "GitHub не ответил": "GitHub did not answer",
    "GitHub вернул ошибку: {first}": "GitHub returned an error: {first}",
    "пустой ответ GitHub": "an empty answer from GitHub",
    "владелец «{owner}» не найден или токену не виден": "the owner «{owner}» is not found or not visible to the token",
    "⚠️ GitHub: не смог проверить — {error}": "⚠️ GitHub: could not check — {error}",
    "⚠️ GitHub: {partial} {words} не прочитано — токену не хватает прав (нужны Metadata, Contents, Pull requests, Commit statuses).":
        "⚠️ GitHub: {partial} {words} not read — the token lacks permissions (Metadata, Contents, Pull requests, Commit statuses are needed).",
    "🟠 Пропали с прошлой недели: {listing} — удалены, переименованы или токен их больше не видит. Если это не ты — проверь доступы аккаунта.":
        "🟠 Gone since last week: {listing} — deleted, renamed, or no longer visible to the token. If it was not you — check the account's access.",
    "🟠 CI упал на основной ветке: {listing} — последний коммит не прошёл проверки, открой и посмотри, что красное.":
        "🟠 CI failed on the main branch: {listing} — the last commit did not pass the checks, open it and see what is red.",
    " ({days} дн.)": " ({days} d)",
    "🟠 PR висят дольше недели: {listing} — влей или закрой.": "🟠 PRs open for over a week: {listing} — merge or close them.",
    "🟠 {n} {words} (не основная и без открытого PR): {listing}. Смёрженные можно удалить.": "🟠 {n} {words} (not the main one and without an open PR): {listing}. Merged ones can be deleted.",
    "🟠 Ветки не удаляются после мержа: {listing} — Settings → General → «Automatically delete head branches».":
        "🟠 Branches are not deleted after merge: {listing} — Settings → General → «Automatically delete head branches».",
    "🟠 Dependabot видит уязвимости: {listing} — вкладка Security, обычно лечится обновлением пакета.":
        "🟠 Dependabot sees vulnerabilities: {listing} — the Security tab, usually fixed by updating the package.",
    "  • и ещё {n} {words}: {items}{words2}": "  • and {n} more {words}: {items}{words2}",
    # ── services/updates.py ─────────────────────────────────────────────────
    "🔄 Бот обновился:\n": "🔄 The bot updated:\n",
    "📄 Лог сборки": "📄 Build log",
    "🔗 Что в этой версии": "🔗 What is in this version",
    "🟠 Новая версия бота не запустилась\nХостинг не смог собрать «{title}» ({sha}). Я работаю на прежней версии ({app_commit}), но и следующие обновления не встанут, пока сборка падает.\n\nЧто делать: открой лог сборки — обычно там видно, какой пакет не поставился. Быстрый выход — откатить этот коммит в GitHub (Revert).":
        "🟠 The new bot version did not start\nThe hosting could not build «{title}» ({sha}). I run the previous version ({app_commit}), but the next updates will not land either while the build fails.\n\nWhat to do: open the build log — it usually shows which package failed to install. The quick way out is to revert this commit on GitHub (Revert).",
    "🛡 В зависимостях бота нашлись известные уязвимости:\n": "🛡 Known vulnerabilities were found in the bot's dependencies:\n",
    "\n\nDependabot откроет PR с обновлением; после мержа и редеплоя я сообщу, что обновился.": "\n\nDependabot will open a PR with the update; after the merge and redeploy I will say I have updated.",
    "✅ Уязвимых зависимостей больше нет.": "✅ No vulnerable dependencies any more.",
    ", последняя проверка {date}": ", last check {date}",
    "⚠️ Уязвимости: база OSV не ответила{when} — повторю через неделю": "⚠️ Vulnerabilities: the OSV database did not answer{when} — I will retry in a week",
    "⚠️ PR Dependabot: GitHub не ответил — список прошлой недели": "⚠️ Dependabot PRs: GitHub did not answer — last week's list",
    "🛡 Уязвимые пакеты: {names} — исправление придёт с Dependabot и встанет само": "🛡 Vulnerable packages: {names} — the fix comes with Dependabot and lands by itself",
    "🟠 Обновление не встало само — тесты не прошли: {pr_links}. Открой PR и посмотри, что упало.": "🟠 An update did not land by itself — the tests failed: {pr_links}. Open the PR and see what broke.",
    "🔀 Ждут твоего решения: {n} PR — {pr_links}. Такие сами не вливаются (крупная версия или базовый образ): открой, прочитай «что поменялось» и нажми Merge.":
        "🔀 Waiting for your decision: {n} PR — {pr_links}. These do not merge by themselves (a major version or the base image): open it, read «what changed» and tap Merge.",
    "без имени": "no name",
    "OSV ответил HTTP {status}": "OSV answered HTTP {status}",
    "OSV не ответил ({err_text})": "OSV did not answer ({err_text})",
    # ── services/integrations.py, gsc.py, yandex_webmaster.py ──────────────
    "это не JSON-ключ сервис-аккаунта (нет client_email или private_key)": "not a service account JSON key (no client_email or private_key)",
    "ключ не подходит ({err_text})": "the key does not fit ({err_text})",
    "Google не принял ключ ({v})": "Google rejected the key ({v})",
    "Google не ответил ({err_text})": "Google did not answer ({err_text})",
    "Google ответил HTTP {status}": "Google answered HTTP {status}",
    # ── utils/text.py: the mark of a clipped message (set through utils.i18n) ──
    "… (обрезано)": "… (truncated)",
    "Search Console ответил HTTP {status}": "Search Console answered HTTP {status}",
    "токен не принят (HTTP {status})": "the token was rejected (HTTP {status})",
    "Вебмастер не ответил": "Webmaster did not answer",
    "Вебмастер не ответил ({err_text})": "Webmaster did not answer ({err_text})",
    # ── services/railway.py ─────────────────────────────────────────────────
    "💳 Тариф Railway — пробный период длится 30 дней с регистрации. Потом Railway переводит аккаунт на бесплатный план с $1 в месяц, а мне нужно около $2: я остановлюсь, и никто не напишет. Ещё через 30 дней Railway удалит диск с базой. Перейди на Hobby — $5 в месяц, нужна карта, которой можно платить за рубежом (российские не подойдут).":
        "💳 Railway plan — the trial lasts 30 days from sign-up. Then Railway moves the account to the free plan with $1 a month, and I need about $2: I will stop, and nobody will write. 30 days later Railway deletes the disk with the database. Move to Hobby — $5 a month; a card that can pay abroad is needed (Russian ones will not do).",
    "💳 Тариф Railway\n\nПробный период — 30 дней и $5 с регистрации, без карты. Потом аккаунт переходит на бесплатный план: $1 в месяц. Мне нужно около $2 — я работаю круглосуточно, — так что на бесплатном плане я остановлюсь, и никто не напишет. Диск с базой Railway удаляет через 30 дней после конца пробного периода.\n\nЧтобы этого не было: тарифы Railway → <b>Hobby</b>, $5 в месяц, в них входит $5 расхода. Нужна карта, которой можно платить за рубежом: российские не подойдут.\n\nНе хочешь платить — меня можно бесплатно поставить на свой сервер или домашний компьютер через Docker: инструкция в README репозитория.":
        "💳 Railway plan\n\nThe trial is 30 days and $5 from sign-up, no card. Then the account moves to the free plan: $1 a month. I need about $2 — I work around the clock — so on the free plan I will stop, and nobody will write. Railway deletes the disk with the database 30 days after the trial ends.\n\nTo avoid that: Railway plans → <b>Hobby</b>, $5 a month, with $5 of usage included. A card that can pay abroad is needed: Russian ones will not do.\n\nDon't want to pay — I can run for free on your own server or home computer through Docker: the instructions are in the repository's README.",
    "💳 Тарифы Railway": "💳 Railway plans",
    "✅ Уже на Hobby": "✅ Already on Hobby",
    "примерно через {days_left} дн.": "in about {days_left} days",
    "💳 Пробный период Railway заканчивается ": "💳 The Railway trial ends ",
    " — он длится 30 дней с регистрации, считаю от дня, когда ты меня запустил. Если аккаунт ещё на пробном, перейди на Hobby ($5 в месяц): на бесплатном плане я остановлюсь, и никто не напишет, а ещё через 30 дней Railway удалит базу с сайтами.\n\nЕсли уже на Hobby — нажми «✅ Уже на Hobby», больше не напомню.":
        " — it lasts 30 days from sign-up, counted from the day you started me. If the account is still on the trial, move to Hobby ($5 a month): on the free plan I will stop, and nobody will write, and 30 days later Railway deletes the database with the sites.\n\nIf you are on Hobby already — tap «✅ Already on Hobby», I will not remind you again.",
    # ── web/webhook.py ──────────────────────────────────────────────────────
    "📩 {site}: ещё {n} {words} с сайта за час. Я их сохранил, но не присылал по одному — открыть все:":
        "📩 {site}: {n} more {words} from the site this hour. I kept them but did not send them one by one — open them all:",
    "⚠️ «{job}» выходит на связь по старому адресу — общему для всех задач. Он продолжает работать, но лучше заменить его на собственный адрес задачи: тогда утечка одной строки не откроет остальные. Адрес — в карточке задачи, «🔗 Как подключена».":
        "⚠️ «{job}» checks in through the old address shared by every job. It keeps working, but the job's own address is better: then one leaked line does not expose the rest. The address is on the job's card, «🔗 How it is connected».",
    "✅ «{job}» снова на связи — без связи было {away}.": "✅ «{job}» is in touch again — it was out of touch for {away}.",
    "✅ «{job}» вышел на связь — теперь слежу за ним.": "✅ «{job}» checked in — I am watching it now.",
    "📩 Виджет обратной связи обновился вместе с ботом. На сайтах со старым сниппетом он больше не появляется — возьми новый: ⚙️ Настройки → 📩 Обратная связь.":
        "📩 The feedback widget updated together with the bot. On sites with the old snippet it no longer appears — take the new one: ⚙️ Settings → 📩 Feedback.",
}

# Russian singular → (English singular, English plural).
PLURALS: dict[str, tuple[str, str]] = {
    "уязвимость": ("vulnerability", "vulnerabilities"),
    "вспомогательный пакет": ("helper package", "helper packages"),
    "репозиторий": ("repository", "repositories"),
    "ветка": ("branch", "branches"),
    "лишняя ветка": ("stray branch", "stray branches"),
    "сообщение": ("message", "messages"),
    "раз": ("time", "times"),
    "старое сообщение": ("old message", "old messages"),
    "критическая проблема": ("critical problem", "critical problems"),
    "серьёзная проблема": ("serious problem", "serious problems"),
}
