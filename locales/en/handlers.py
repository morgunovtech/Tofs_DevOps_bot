"""English for handlers/* and main.py (see utils/i18n.py). Grouped by
module; a key is the Russian source string, byte for byte."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── handlers/common.py ──────────────────────────────────────────────────
    "🏠 Главная": "🏠 Home",
    "📖 Что я умею": "📖 What I do",
    "❓ Значки": "❓ Icons",
    "🔄 Проверить снова": "🔄 Check again",
    "✖️ Отмена": "✖️ Cancel",
    "⛔ Доступ только для администратора.": "⛔ Only the administrator can do that.",
    "⛔ Доступ запрещён": "⛔ Access denied",
    "👥 Ты помощник: я присылаю тебе только «сайт лёг» и «снова работает», управляет ботом "
    "владелец. Перестать — /stop.":
        "👥 You are a helper: I send you only «site is down» and «up again»; the owner runs the bot. "
        "To stop — /stop.",
    "Здесь я не управляюсь: пиши мне в наш личный чат "
    "(или в группу из TELEGRAM_ADMIN_CHAT_ID).":
        "I cannot be controlled from here: write to me in our private chat "
        "(or in the group from TELEGRAM_ADMIN_CHAT_ID).",
    "Мной сейчас никто не может управлять: в .env задан TELEGRAM_ADMIN_CHAT_ID группы, "
    "но не задан TELEGRAM_ADMIN_USER_ID. Впиши туда свой числовой id (его покажет @userinfobot) "
    "и перезапусти бота.":
        "Nobody can control me right now: .env sets a group in TELEGRAM_ADMIN_CHAT_ID but no "
        "TELEGRAM_ADMIN_USER_ID. Put your numeric id there (@userinfobot shows it) and restart the bot.",
    "🔐 Этот адрес — как пароль: кто его знает, может выходить на связь вместо "
    "задачи. Не публикуй его и не клади в файлы репозитория.":
        "🔐 This address is like a password: whoever knows it can check in instead of the job. "
        "Do not publish it or put it in repository files.",
    "⚠️ Не смог удалить твоё сообщение с ключом — удали его сам. "
    "В группе для этого мне нужно право администратора «Удаление сообщений».":
        "⚠️ I could not delete your message with the key — delete it yourself. "
        "In a group I need the «Delete messages» admin right for that.",
    "Кнопка устарела — нажми «🏠 Главная» под полем ввода": "This button is outdated — tap «🏠 Home» under the input field",
    "Не понял. Всё — кнопками под полем ввода: 🏠 Главная, 📖 Что я умею, ❓ Значки.":
        "I did not get that. Everything is on the buttons under the input field: 🏠 Home, 📖 What I do, ❓ Icons.",
    "⌛ Это нажатие пришло, пока я был выключен или перезапускался, — ничего не сделал. "
    "Если это ещё нужно, нажми кнопку снова.":
        "⌛ This tap arrived while I was off or restarting — I did nothing. "
        "If you still need it, tap the button again.",
    "Telegram просит подождать {sec} с — нажми ещё раз чуть позже":
        "Telegram asks to wait {sec} s — tap again a little later",
    "❌ Действие не удалось. Попробуй ещё раз чуть позже.": "❌ The action failed. Try again a little later.",
    "❌ Что-то пошло не так во время проверки.\nПопробуй ещё раз чуть позже.":
        "❌ Something went wrong during the check.\nTry again a little later.",
    "❌ Не получилось открыть экран. Попробуй ещё раз чуть позже.": "❌ Could not open the screen. Try again a little later.",
    "❌ Что-то пошло не так. Попробуй ещё раз чуть позже.": "❌ Something went wrong. Try again a little later.",
    # ── main.py ─────────────────────────────────────────────────────────────
    "TELEGRAM_BOT_TOKEN не задан — возьми токен у @BotFather и впиши его в .env":
        "TELEGRAM_BOT_TOKEN is not set — get a token from @BotFather and put it in .env",
    "TELEGRAM_BOT_TOKEN не похож на токен бота (цифры, двоеточие, буквы) — скопируй его у @BotFather целиком":
        "TELEGRAM_BOT_TOKEN does not look like a bot token (digits, a colon, letters) — copy it from @BotFather whole",
    "Telegram не принял TELEGRAM_BOT_TOKEN: токен отозван или скопирован не целиком — возьми новый у @BotFather":
        "Telegram rejected TELEGRAM_BOT_TOKEN: the token was revoked or not copied whole — get a new one from @BotFather",
    "Главные кнопки теперь всегда под полем ввода: 🏠 Главная, 📖 Что я умею и ❓ Значки. Меню ☰ убрал.":
        "The main buttons now always sit under the input field: 🏠 Home, 📖 What I do and ❓ Icons. The ☰ menu is gone.",
    "🟠 Обновил схему базы без копии перед обновлением: не смог её сохранить ({reason}). Откат на прошлую "
    "версию потребует ночной копии из data/backups. Проверь место на диске с базой.":
        "🟠 I updated the database schema without a copy taken first: I could not save one ({reason}). Rolling back "
        "to the previous version would need a nightly copy from data/backups. Check the disk the database is on.",
    # ── handlers/start.py ───────────────────────────────────────────────────
    "Владельцем можно стать только в личном чате со мной: открой его и нажми Start. "
    "Группа для тревог задаётся переменными TELEGRAM_ADMIN_CHAT_ID и TELEGRAM_ADMIN_USER_ID.":
        "You can become the owner only in a private chat with me: open it and tap Start. "
        "A group for alerts is set with TELEGRAM_ADMIN_CHAT_ID and TELEGRAM_ADMIN_USER_ID.",
    "Я только что запустился — нажми /start ещё раз.": "I have just started — send /start again.",
    "🐕 TofsDevOps следит за твоими сайтами и пишет, только когда что-то сломалось.\n\n"
    "Что значат значки:\n"
    "🔴 — нужно действие сейчас: сайт не открывается, кто-то сменил настройки домена, сайт закрыли "
    "от поисковиков. Звоню даже ночью и в тишину.\n"
    "🟠 — нужно действие, но не сегодня: например, сертификат скоро закончится. Приходит без звука.\n"
    "✅ — всё в порядке или снова работает.\n"
    "⚠️ — не смог проверить: сам попробую ещё раз.\n"
    "⏳ — жду первой проверки или первой связи.\n"
    "🔧 — ты чинишь сайт («🔧 Я чиню»): проверяю, но не пишу о нём.\n\n"
    "Где что:\n"
    "🏠 Главная (кнопка под полем ввода) — всё на одном экране, по блокам: сайты, почта, боты и "
    "системы (и то, что о себе присылают твои боты). Кнопка блока открывает всё о нём, там же «➕ Добавить».\n"
    "⚙️ Настройки — во сколько присылать сводку, ночная тишина, пробная тревога.\n\n"
    "Нужно, чтобы я помолчал: /mute 8h — приходят только звонящие: сайт лёг, а при «🔔 Будить: всё "
    "критичное» ещё снова работает, смена настроек домена, сайт закрыт от поисковиков; остальное — когда "
    "тишина кончится. Будить только когда сайт лёг — ⚙️ Настройки → 🔔 Когда писать.\n\n"
    "Команды: /menu — главный экран, /mute 8h — помолчать, /unmute — снова писать обо всём, /help — эта справка.":
        "🐕 TofsDevOps watches your sites and writes only when something broke.\n\n"
        "What the icons mean:\n"
        "🔴 — act now: the site does not open, someone changed the domain settings, the site was closed "
        "to search engines. I ring even at night and through silence.\n"
        "🟠 — act, but not today: the certificate is about to expire, for example. Arrives without a sound.\n"
        "✅ — all is well or working again.\n"
        "⚠️ — I could not check: I will try again myself.\n"
        "⏳ — waiting for the first check or the first check-in.\n"
        "🔧 — you are fixing the site («🔧 I'm fixing it»): I check it but stay quiet about it.\n\n"
        "Where things are:\n"
        "🏠 Home (the button under the input field) — everything on one screen, in blocks: sites, mail, bots and "
        "systems (and what your bots report about themselves). A block's button opens all of it, with «➕ Add» there too.\n"
        "⚙️ Settings — when to send the summary, quiet hours at night, a test alert.\n\n"
        "Need me to be quiet: /mute 8h — only what rings comes through: the site is down, and with «🔔 Wake me: "
        "everything critical» also up again, changed domain settings, the site closed to search engines; the rest "
        "waits until the silence ends. To be woken only when a site is down — ⚙️ Settings → 🔔 When to write.\n\n"
        "Commands: /menu — the main screen, /mute 8h — be quiet, /unmute — write about everything again, /help — this help.",
    "Главные кнопки — под полем ввода: 🏠 Главная, 📖 Что я умею и ❓ Значки.":
        "The main buttons are under the input field: 🏠 Home, 📖 What I do and ❓ Icons.",
    "Ночью с {start}:00 до {end}:00 не бужу — некритичное подожду до утра.":
        "From {start}:00 to {end}:00 I don't wake you — anything non-critical waits until morning.",
    "Тихих часов нет — можно включить в ⚙️ Настройках.": "No quiet hours — you can turn them on in ⚙️ Settings.",
    "Как я работаю:": "How I work:",
    "• Проверяю каждый сайт каждые {minutes} мин.": "• I check every site every {minutes} min.",
    "• Пишу только если что-то сломалось, и сразу говорю, что делать.":
        "• I write only when something broke, and say right away what to do.",
    "• Каждое утро в {hour}:00 одно короткое «всё в порядке».": "• Every morning at {hour}:00 one short «all is well».",
    "• Время — по поясу {zone}: у меня сейчас {now}. Если у тебя другое — ⚙️ Настройки → 🕐 Часовой пояс.":
        "• Times are in the {zone} zone: for me it is {now} now. If yours differs — ⚙️ Settings → 🕐 Time zone.",
    "🕐 Сменить часовой пояс": "🕐 Change the time zone",
    "Если сайт лёг, напишу в любое время": "If a site goes down, I write at any hour",
    " и напомню через {repeat} мин, потом всё реже (не чаще раза в 4 ч), пока не поднимется.":
        " and remind you after {repeat} min, then less and less often (at most once in 4 h) until it is back.",
    "✅ Готово: теперь, если сайт ляжет, я позвоню и тебе — и напишу, когда он снова заработает. "
    "Больше ничего присылать не буду, управляет ботом владелец. Перестать — /stop.":
        "✅ Done: from now on, if a site goes down, I ring you too — and write when it works again. "
        "I send nothing else; the owner runs the bot. To stop — /stop.",
    "Ты уже помощник — «сайт лёг» и «снова работает» приходят и тебе. Перестать — /stop.":
        "You are a helper already — «site is down» and «up again» reach you too. To stop — /stop.",
    "У владельца уже три помощника — больше нельзя. Попроси его убрать кого-нибудь.":
        "The owner already has three helpers — no more are allowed. Ask them to remove someone.",
    "Ссылка не сработала: она одноразовая и живёт сутки. Попроси у владельца новую.":
        "The link did not work: it is single-use and lives a day. Ask the owner for a new one.",
    "Это приглашение для помощника — перешли ссылку тому, кто подстрахует тебя.":
        "This invite is for a helper — forward the link to the person who will back you up.",
    "Открой эту ссылку в личном чате со мной.": "Open this link in a private chat with me.",
    "👥 {name} теперь получает «сайт лёг» и «снова работает». Убрать — ⚙️ Настройки → 👥 Помощники.":
        "👥 {name} now receives «site is down» and «up again». To remove — ⚙️ Settings → 👥 Helpers.",
    "Группа стала супергруппой — теперь пишу сюда. В .env замени TELEGRAM_ADMIN_CHAT_ID на <code>{chat_id}</code>.":
        "The group became a supergroup — I write here now. In .env replace TELEGRAM_ADMIN_CHAT_ID with <code>{chat_id}</code>.",
    "Ты владелец. Чтобы я помолчал — /mute 8h.": "You are the owner. To make me quiet — /mute 8h.",
    "Больше не присылаю тревоги. Вернуться можно по новой ссылке от владельца.":
        "I no longer send you alerts. A new link from the owner brings you back.",
    "👥 {name} больше не получает «сайт лёг» и «снова работает».": "👥 {name} no longer receives «site is down» and «up again».",
    "🔑 Ты стал администратором этого бота — все отчёты и тревоги будут приходить сюда.":
        "🔑 You are now the administrator of this bot — every report and alert comes here.",
    "👋 Привет! Я TofsDevOps — слежу за твоими сайтами, чтобы тебе не пришлось.\n"
    "Всё под рукой — кнопки под полем ввода: 🏠 Главная, 📖 Что я умею и ❓ Значки.\n\n"
    "Назван в честь Тофса — ирландского терьера, который принимает аптайм близко к сердцу.":
        "👋 Hi! I am TofsDevOps — I watch your sites so you don't have to.\n"
        "Everything is at hand on the buttons under the input field: 🏠 Home, 📖 What I do and ❓ Icons.\n\n"
        "Named after Tofs, an Irish terrier who takes uptime to heart.",
    # ── handlers/menu.py ────────────────────────────────────────────────────
    "сертификат": "certificate",
    "домен": "domain",
    "{what} истёк": "{what} expired",
    "{what} через {days} дн.": "{what} in {days} d",
    "ждёт первой проверки": "waiting for the first check",
    "не могу проверить: {why}": "cannot check: {why}",
    "закрыт от поиска": "closed to search",
    "{n} {obstacles} в поиске": "{n} search {obstacles}",
    "чинится, ": "being fixed, ",
    "открывается": "opens",
    "почты нет": "no mail",
    " · проверено в {time}": " · checked at {time}",
    "✅ Всё в порядке": "✅ All is well",
    "сторож {name}": "watchdog {name}",
    "Внешнего сторожа нет": "No external watchdog",
    "подключи в «🤖 Боты»": "connect one in «🤖 Bots»",
    "резервная копия": "backup",
    "новая версия бота": "new bot version",
    "не запустилась, работаю на прежней": "did not start, running the previous one",
    "➕ Добавить первый сайт": "➕ Add the first site",
    "⚙️ Настройки": "⚙️ Settings",
    "👋 Пока ни за чем не слежу. Добавь первый сайт — и я начну проверять его каждые {minutes} мин и напишу, "
    "только если что-то сломается. Там же можно добавить бота или задачу по расписанию: скажу, если замолчит.":
        "👋 I am not watching anything yet. Add the first site — I will check it every {minutes} min and write "
        "only if something breaks. A bot or a scheduled job can be added there too: I will say if it goes silent.",
    "<b>Сайты</b>": "<b>Sites</b>",
    "… и ещё {n}": "… and {n} more",
    ", из них с проблемами: {bad}": ", with problems: {bad}",
    "<b>Почта</b>": "<b>Mail</b>",
    "<b>Боты и системы</b>": "<b>Bots and systems</b>",
    "⏳ Первая проверка через минуту": "⏳ First check in a minute",
    "⏳ Жду первой связи": "⏳ Waiting for the first check-in",
    "Тишина до {time}": "Silence until {time}",
    "плановые работы": "planned maintenance",
    "тихие часы": "quiet hours",
    "Что не так": "What's wrong",
    "🌐 Сайты": "🌐 Sites",
    "➕ Сайт": "➕ Site",
    "🤖 Боты": "🤖 Bots",
    "Нечего отменять — этот шаг уже закрыт": "Nothing to cancel — this step is already closed",
    "Отменено": "Cancelled",
    # ── handlers/blocks.py (the backup line of the main screen) ────────────
    "первая — сегодня ночью": "the first one tonight",
    "⏳ Резервная копия базы — первую сделаю сегодня ночью.": "⏳ Database backup — I make the first one tonight.",
    "последняя {when}": "last one {when}",
    "🟠 Резервная копия базы — последняя {when}: ночные копии не делаются. Чаще всего кончилось место на диске.":
        "🟠 Database backup — the last one {when}: the nightly copies are not being made. Most often the disk is full.",
    "по воскресеньям присылаю тебе в личку — без ключей подключений":
        "on Sundays I send it to you in private — without the connection keys",
    "в Telegram не присылаю (выключено в «🔔 Когда писать»)": "not sent to Telegram (switched off in «🔔 When to write»)",
    "✅ Резервная копия базы — {when}. Делаю каждую ночь, {weekly}.": "✅ Database backup — {when}. Made every night, {weekly}.",
    # ── handlers/mail.py (the mail line of the main screen) ────────────────
    "почтовый сервер": "mail server",
    "ещё не проверял": "not checked yet",
    "не принимает письма": "does not accept mail",
    " и ещё {n} {obstacles}": " and {n} more {obstacles}",
    # ── handlers/about.py ───────────────────────────────────────────────────
    "🌐 Сайт работает": "🌐 The site works",
    "Проверяю каждый сайт каждые {minutes} мин": "I check every site every {minutes} min",
    "узнаёшь, что сайт лёг, раньше клиентов — и когда он снова поднялся":
        "you learn the site is down before your customers do — and when it is back up",
    "Слежу за фразой на странице (предложу при добавлении сайта)": "I watch for a phrase on the page (offered when you add a site)",
    "ловлю «открывается, но не то»: пустую страницу, заглушку хостинга, текст ошибки":
        "I catch «it opens, but it's wrong»: an empty page, a hosting placeholder, an error text",
    "Обхожу страницы из карты сайта и ссылки с главной": "I crawl the pages from the sitemap and the links on the homepage",
    "битые страницы и ссылки не теряют тебе посетителей": "broken pages and links stop costing you visitors",
    "Могу проверять главную функцию — форму или API (в карточке сайта)": "I can check the main function — a form or an API (on the site's card)",
    "знаешь, что сайт не просто открывается, а работает": "you know the site not just opens but works",
    "🔒 Ничего не истечёт и не сломается тихо": "🔒 Nothing expires or breaks quietly",
    "Слежу за сроком SSL-сертификата и домена": "I watch the SSL certificate and the domain expiry",
    "предупрежу заранее: браузер не покажет «опасно», домен не уйдёт":
        "I warn you in advance: no «not secure» in the browser, the domain does not slip away",
    "Сравниваю настройки домена (DNS) каждый час": "I compare the domain settings (DNS) every hour",
    "сразу заметишь, если их кто-то сменил": "you notice at once if someone changed them",
    "Проверяю почту домена": "I check the domain's mail",
    "письма клиентов доходят и не уходят в спам": "customers' emails arrive and do not land in spam",
    "Ищу открытые наружу файлы с кодом и паролями, проверяю https и защитные заголовки":
        "I look for exposed files with code and passwords, check https and the security headers",
    "закроешь дыры раньше, чем их найдут": "you close the holes before someone finds them",
    "🔍 Тебя находят": "🔍 You get found",
    "Каждый день проверяю, открыт ли сайт для Google, Яндекса и ИИ-ассистентов":
        "Every day I check whether the site is open to Google, Yandex and AI assistants",
    "сайт не выпадет из поиска из-за одной строки в robots.txt": "the site does not drop out of search over one line in robots.txt",
    "🤖 Боты и задачи": "🤖 Bots and jobs",
    "Слежу, чтобы бэкапы, рассылки и другие боты выходили на связь (🤖 Боты → ➕)":
        "I make sure backups, newsletters and other bots check in (🤖 Bots → ➕)",
    "узнаешь, что бэкап не сделался, а не в день, когда он понадобится":
        "you learn the backup did not run — not on the day you need it",
    "🔔 Пишу только по делу": "🔔 I write only when it matters",
    "Молчу, когда всё хорошо; утром — одно «всё в порядке»": "I stay quiet while all is well; in the morning — one «all is well»",
    "не привыкаешь пропускать мои сообщения": "you never learn to ignore my messages",
    "В каждой тревоге — что случилось, что это значит для посетителей и что делать":
        "Every alert says what happened, what it means for visitors and what to do",
    "не нужно быть инженером": "no need to be an engineer",
    "Звоню, только когда сайт лёг; остальное приходит без звука": "I ring only when a site is down; the rest arrives silently",
    "Звоню по 🔴 и когда упавший сайт снова открылся; остальное приходит без звука":
        "I ring for 🔴 and when a down site opens again; the rest arrives silently",
    "не дёргаю по мелочам": "no nagging over small things",
    "По воскресеньям — отчёт за неделю": "On Sundays — the week's report",
    "видишь, сколько сайт был недоступен и что стоит улучшить": "you see how long the site was down and what is worth improving",
    "Звоню и помощникам — до трёх человек (⚙️ Настройки → 👥)": "I ring your helpers too — up to three people (⚙️ Settings → 👥)",
    "отвечаешь за сайт не один": "you are not alone in charge of the site",
    "📖 <b>Что я умею — сам, без настроек</b>": "📖 <b>What I do — by myself, with no setup</b>",
    "Ещё больше — если подключить сервисы: «🔌 Что можно подключить».": "Even more with services connected: «🔌 What can be connected».",
    "🔌 Что можно подключить": "🔌 What can be connected",
    "🔌 <b>Что можно подключить</b> — всё необязательно, я работаю и без этого.":
        "🔌 <b>What can be connected</b> — all optional, I work without it.",
    "<i>Что даёт → кому нужно</i>": "<i>What it gives → who needs it</i>",
    " ⚠️ подключено, но не отвечает": " ⚠️ connected, but not answering",
    " ✅ подключено": " ✅ connected",
    "← Что я умею": "← What I do",
    "🌐 Вторая точка проверки": "🌐 Second vantage point",
    "перед тревогой «сайт лёг» перепроверю с серверов check-host.net в других странах; по желанию — "
    "скорость из страны твоей аудитории":
        "before a «site is down» alert I re-check from check-host.net servers in other countries; optionally — "
        "the speed from your audience's country",
    "почти всем: меньше ложных тревог. Бесплатный сервис check-host.net, ему уходят адреса сайтов":
        "almost everyone: fewer false alarms. The free check-host.net service; it receives the sites' addresses",
    "🌐 Вторая точка": "🌐 Second point",
    "🛡 Внешний сторож": "🛡 External watchdog",
    "healthchecks.io напишет тебе, если перестану работать я сам": "healthchecks.io writes to you if I myself stop working",
    "всем: иначе моё молчание может значить, что я упал. Бесплатно": "everyone: otherwise my silence may mean I am down. Free",
    "🛡 Сторож": "🛡 Watchdog",
    "🔎 Google Search Console": "🔎 Google Search Console",
    "в тот же день скажу, если главная выпала из поиска Google; клики и показы — в отчёте за неделю":
        "the same day I tell you if the homepage dropped out of Google; clicks and impressions in the weekly report",
    "если клиенты приходят из поиска Google": "if customers come from Google search",
    "🔎 Search Console": "🔎 Search Console",
    "🔎 Яндекс.Вебмастер": "🔎 Yandex.Webmaster",
    "сколько страниц в поиске Яндекса, ИКС и проблемы, которые видит Яндекс":
        "how many pages are in Yandex search, the site quality index and the problems Yandex sees",
    "если твоя аудитория ищет через Яндекс": "if your audience searches with Yandex",
    "🔎 Вебмастер": "🔎 Webmaster",
    "⚡ Скорость у посетителей": "⚡ Speed for visitors",
    "сколько реальные посетители в Chrome ждут твой сайт — по данным Google; станет медленно — скажу":
        "how long real Chrome visitors wait for your site — from Google's data; I say when it gets slow",
    "если у сайта заметный трафик: о небольших сайтах Google данных не собирает":
        "if the site has noticeable traffic: Google collects no data on small sites",
    "⚡ Скорость": "⚡ Speed",
    "☁️ Cloudflare и хостинг": "☁️ Cloudflare and hosting",
    "кнопки «передеплой» и «сброс кэша» прямо в тревоге, упавший сайт перезапущу сам":
        "«redeploy» and «purge cache» buttons right in the alert; a down site I redeploy myself",
    "если сайт на Cloudflare или у хостинга есть deploy hook": "if the site is on Cloudflare or the hosting has a deploy hook",
    "☁️ Cloudflare": "☁️ Cloudflare",
    "🐙 GitHub": "🐙 GitHub",
    "по воскресеньям — что прибрать в репозиториях: старые PR, лишние ветки, красный CI":
        "on Sundays — what to tidy in the repositories: old PRs, stray branches, red CI",
    "если код сайтов лежит на GitHub": "if the sites' code lives on GitHub",
    "🌐 Статус-страница": "🌐 Status page",
    "публичная страница «всё работает» с аптаймом и бейджи для README": "a public «all systems go» page with uptime, and badges for the README",
    "если хочешь показывать клиентам или команде, что всё в порядке": "if you want to show customers or the team that all is well",
    # ── handlers/alerts.py ──────────────────────────────────────────────────
    "🚀 Да, пересобрать": "🚀 Yes, rebuild",
    "🚀 Пересобрать {label}? Cloudflare Pages выложит то, что сейчас лежит в ветке сайта, сборка займёт 1–2 минуты.":
        "🚀 Rebuild {label}? Cloudflare Pages will publish what is in the site's branch right now; the build takes 1–2 minutes.",
    "🧹 Да, сбросить кэш": "🧹 Yes, purge the cache",
    "🧹 Сбросить кэш Cloudflare для {host}? Первые посетители после сброса пойдут мимо кэша прямо на сервер сайта.":
        "🧹 Purge the Cloudflare cache for {host}? The first visitors after the purge go past the cache straight to the site's server.",
    "Я уже пересобирал его сам в {done}.": "I already rebuilt it myself at {done}.",
    "Не понял действие": "Unknown action",
    "Запускаю…": "Starting…",
    "Сайт не найден или убран из мониторинга — ничего не делаю.": "The site is not found or no longer monitored — doing nothing.",
    "⌛ Этот вопрос устарел — нажми кнопку под тревогой ещё раз.": "⌛ This question is outdated — tap the button under the alert again.",
    "⏳ Не запускаю второй раз: {why}.": "⏳ Not starting it a second time: {why}.",
    "⏳ Запускаю пересборку…": "⏳ Starting the rebuild…",
    "⏳ Сбрасываю кэш…": "⏳ Purging the cache…",
    "Все сайты из этой тревоги уже открываются": "Every site from this alert opens already",
    "Тревога устарела — открой «Что не так»": "The alert is outdated — open «What's wrong»",
    "Понял, молчу {length}": "Got it, quiet for {length}",
    "🔧 Понял, ты чинишь: {items}. {capitalize} не пишу про них, проверки продолжаются. Поднимутся раньше — сразу напишу и сниму тишину.":
        "🔧 Got it, you are fixing: {items}. {capitalize} of silence about them, the checks go on. If they come back sooner — I write at once and lift the silence.",
    "\n\nНомер — что делать с этим сайтом: «🔧 Я чиню», «🔄 Проверить снова», «✓ Закрыть».":
        "\n\nA number — what to do with that site: «🔧 I'm fixing it», «🔄 Check again», «✓ Close».",
    "Сайт не найден или убран из мониторинга": "The site is not found or no longer monitored",
    "Уже проверяю — ответ будет в чате": "Checking already — the answer comes to the chat",
    "Сайт сейчас работает — пауза не нужна. Чинишь что-то другое — поставь паузу на карточке сайта.":
        "The site works right now — no pause needed. Fixing something else — set the pause on the site's card.",
    "🔧 Понял, ты чинишь {label}. {capitalize} не пишу про него, проверки продолжаются. Поднимется раньше — сразу напишу и сниму тишину.":
        "🔧 Got it, you are fixing {label}. {capitalize} of silence about it, the checks go on. If it comes back sooner — I write at once and lift the silence.",
    "✖️ Хорошо, ничего не делаю.": "✖️ All right, doing nothing.",
    "Неизвестное действие": "Unknown action",
    "⏳ Проверяю {label}…": "⏳ Checking {label}…",
    "✅ {label} сейчас открывается{speed}.": "✅ {label} opens right now{speed}.",
    "✅ {label} сейчас отвечает{speed}.": "✅ {label} answers right now{speed}.",
    "⚠️ {label}: проверить сейчас не могу — {why}.": "⚠️ {label}: cannot check right now — {why}.",
    "🔴 {label} всё ещё не открывается: {why}.": "🔴 {label} still does not open: {why}.",
    "Здесь нет страницы, которую можно сфотографировать: это API или порт": "There is no page to photograph here: it is an API or a port",
    "📸 Делаю снимок — до нескольких минут": "📸 Taking a screenshot — up to a few minutes",
    "❌ Не получилось сделать снимок {host}: {reason}.": "❌ Could not take a screenshot of {host}: {reason}.",
    # ── handlers/automation.py ──────────────────────────────────────────────
    "🚀 Пересобираю сайт, когда он падает": "🚀 I rebuild the site when it goes down",
    "🔁 Перезапускаю контейнеры {names}": "🔁 I restart the containers {names}",
    "🧹 Чищу docker, когда диск заполнен": "🧹 I clean docker when the disk is full",
    "🤖 Что я делаю сам": "🤖 What I do by myself",
    "вкл": "on",
    "выкл": "off",
    "выключить": "turn off",
    "включить": "turn on",
    "Сейчас ничего: нет deploy hook и нет доступа к Docker.": "Nothing right now: no deploy hook and no access to Docker.",
    "до включения": "until turned on",
    "до {time}": "until {time}",
    "⏸ Сейчас всё остановлено {when}.": "⏸ Everything is paused {when}.",
    "▶️ Включить снова": "▶️ Turn on again",
    "⏸ Остановить на 2 часа": "⏸ Pause for 2 hours",
    "⏹ До включения": "⏹ Until turned on",
    "\nПоследнее:": "\nLatest:",
    "сам": "by myself",
    "по кнопке": "by a button",
    "выполняю…": "running…",
    "← К подключениям": "← Connections",
    "Остановил": "Paused",
    "⏸ Ничего не делаю сам {words}: не пересобираю, не перезапускаю, не чищу. Включить: ⚙️ Настройки → 🔌 Подключения → 🤖 Что бот делает сам.":
        "⏸ Doing nothing by myself {words}: no rebuilds, no restarts, no cleanup. To turn on: ⚙️ Settings → 🔌 Connections → 🤖 What the bot does by itself.",
    "2 часа": "for 2 hours",
    "Включил": "Turned on",
    "Готово": "Done",
    # ── handlers/blocks.py ──────────────────────────────────────────────────
    "🤖 Боты и системы": "🤖 Bots and systems",
    "Ботов и задач по расписанию пока нет. Добавь — и я скажу, если они замолчат.": "No bots or scheduled jobs yet. Add one — and I will say if it goes silent.",
    "🟠 Новая версия бота не запустилась — работаю на прежней. Лог сборки — в сообщении «Новая версия бота не запустилась».":
        "🟠 The new bot version did not start — running the previous one. The build log is in the «New bot version did not start» message.",
    "🛡 Подключить сторожа": "🛡 Connect a watchdog",
    "💾 Скачать копию базы": "💾 Download a database copy",
    "➕ Бот или задача": "➕ Bot or job",
    "Делаю копию…": "Making a copy…",
    "💾 Копия базы бота на {time}: сайты, настройки, история. Ключей подключений и секретов в ней нет. Чтобы восстановить — пришли мне этот файл.":
        "💾 A copy of the bot's database as of {time}: sites, settings, history. It holds no connection keys or secrets. To restore — send me this file.",
    "❌ Копию не прислал: {problem}.": "❌ Did not send the copy: {problem}.",
    "💾 Прислал копию тебе в личку.": "💾 Sent the copy to you in private.",
    "Этот бот больше ничего не присылает": "This bot no longer sends anything",
    ", {duration} назад": ", {duration} ago",
    "{status_title} · от {name}{ago}": "{status_title} · from {name}{ago}",
    "Подробности — графики, тревоги, безопасность аккаунтов — в самом {name}.": "The details — charts, alerts, account security — are in {name} itself.",
    "↗️ Открыть {name}": "↗️ Open {name}",
    # ── handlers/feedback.py ────────────────────────────────────────────────
    "📩 Обратная связь с сайтов ({total}):": "📩 Feedback from the sites ({total}):",
    "Пока ни одного сообщения.": "No messages yet.",
    "Дальше пусто.": "Nothing further.",
    "\n⚠️ Задай PUBLIC_BASE_URL, чтобы ссылки ниже были готовы к вставке.": "\n⚠️ Set PUBLIC_BASE_URL so the links below are ready to paste.",
    "Подключение виджета на сайт (перед <code>&lt;/body&gt;</code>):\n<code>{widget_snippet}</code>\nЛюбая ссылка с атрибутом <code>data-devops-feedback</code> откроет форму.\nЯзык формы — как у страницы (<code>&lt;html lang&gt;</code>): русский или английский; закрепить — <code>lang:'ru'</code> или <code>lang:'en'</code> в настройке. После обновления бота сниппет может смениться — бот скажет, и его надо заменить на сайтах.\nСтрогий Content-Security-Policy на сайте? Подключение без inline-скрипта и с копией файла у себя — в начале файла {widget_url}":
        "Adding the widget to a site (before <code>&lt;/body&gt;</code>):\n<code>{widget_snippet}</code>\nAny link with the <code>data-devops-feedback</code> attribute opens the form.\nThe form's language follows the page (<code>&lt;html lang&gt;</code>): Russian or English; pin it with <code>lang:'ru'</code> or <code>lang:'en'</code> in the setup. After a bot update the snippet may change — the bot will say so, and it must be replaced on the sites.\nA strict Content-Security-Policy on the site? Setup without an inline script and with your own copy of the file — at the top of {widget_url}",
    "← Новее": "← Newer",
    "Старее →": "Older →",
    "🗑 Удалить эти {n}": "🗑 Delete these {n}",
    "← Для посетителей": "← For visitors",
    "Кнопка устарела — открой список заново": "The button is outdated — open the list again",
    "🗑 Да, удалить": "🗑 Yes, delete",
    "← Не надо": "← Don't",
    "Удалить эти сообщения посетителей? Вернуть их будет нельзя.": "Delete these visitors' messages? They cannot be brought back.",
    "🗑 Удалил: {n}": "🗑 Deleted: {n}",
    # ── handlers/heartbeats.py ──────────────────────────────────────────────
    "🤖 Это бот или сервис": "🤖 It is a bot or a service",
    "🗓 Это задача по расписанию": "🗓 It is a scheduled job",
    "Выходит на связь {rhythm}. Замолчит — напишу.": "Checks in {rhythm}. If it goes silent — I write.",
    "🔗 Как подключена": "🔗 How it is connected",
    "🟠 {n}, а выходит на связь {rhythm}.": "🟠 {n}, though it checks in {rhythm}.",
    "Обычно это значит, что задача не запустилась или упала, не дойдя до конца. Проверь её. Если меняли адрес для отметок — подключи заново.":
        "This usually means the job did not run or crashed before the end. Check it. If the check-in address was changed — connect it again.",
    "🔗 Подключить заново": "🔗 Connect again",
    "{icon} Пока не выходит на связь · жду {rhythm}": "{icon} Has not checked in yet · expecting it {rhythm}",
    "Я жду, что «{n}» будет выходить со мной на связь {rhythm} — так я узнаю, что работа запустилась и дошла до конца. Связи ещё не было: похоже, не подключили.":
        "I expect «{n}» to check in with me {rhythm} — that is how I learn the work ran and finished. It has not happened yet: it seems not connected.",
    "Что такое «{n}» у тебя?": "What is «{n}» for you?",
    "⚠️ Выходит на связь по старому адресу, общему для всех задач. Он работает, но лучше заменить его на собственный адрес задачи — он за кнопкой «🔗» ниже: тогда утечка одной строки не откроет остальные.":
        "⚠️ Checks in through the old address shared by every job. It works, but the job's own address is better — it is behind the «🔗» button below: then one leaked line does not expose the rest.",
    "🗑 Такой задачи нет — убрать": "🗑 No such job — remove it",
    "🗑 Убрать": "🗑 Remove",
    "← Боты": "← Bots",
    "🌍 Как задать адрес": "🌍 How to set the address",
    "Задачи больше нет": "The job is gone",
    "🔑 Адрес мог утечь — сменить": "🔑 The address may have leaked — change it",
    "← Назад": "← Back",
    "⏰ {name}\n\nЧто такое «{name}» у тебя?": "⏰ {name}\n\nWhat is «{name}» for you?",
    "🤖 Подключить «{name}»\n\n{not_public_note}Дай этому боту адрес — нажми на него, он скопируется:\n{url_line}\n\nБот должен открывать его каждый раз, когда удачно поработал. В его настройках такое поле обычно называется <code>HEALTHCHECK_URL</code>, <code>HEARTBEAT_URL</code> или «Ping URL».\n\n{HEARTBEAT_SECRET_NOTE}\n\nКак только связь появится, строка на главном экране станет ✅.":
        "🤖 Connect «{name}»\n\n{not_public_note}Give this bot the address — tap it to copy:\n{url_line}\n\nThe bot must open it every time it has done its work well. In its settings such a field is usually called <code>HEALTHCHECK_URL</code>, <code>HEARTBEAT_URL</code> or «Ping URL».\n\n{HEARTBEAT_SECRET_NOTE}\n\nAs soon as it checks in, its line on the main screen turns ✅.",
    "🗓 Подключить «{name}»\n\n{not_public_note}В самом конце задача должна открыть адрес — нажми на него, он скопируется:\n{url_line}\n{HEARTBEAT_SECRET_NOTE}\n\nГде задача запускается:\n• <b>GitHub Actions</b> — адрес в файл workflow не вставляй: репозиторий читают другие. В репозитории: Settings → Secrets and variables → Actions → New repository secret, имя <code>HEARTBEAT_URL</code>, значение — адрес выше. Последний шаг workflow:\n{GITHUB_STEP}\n• <b>cron на своём сервере</b> — допиши в конец строки задачи:\n<code>&& curl -fsS {url}</code>\n• <b>Планировщик хостинга</b> (Railway Cron, Render…) — положи адрес в переменную окружения сервиса <code>HEARTBEAT_URL</code> и допиши в конец команды <code>&& curl -fsS \"$HEARTBEAT_URL\"</code>; в render.yaml и railway.json адрес не пиши.\n\nЗадача упадёт раньше — адрес не откроется, и я напишу. Как только связь появится, строка на главном экране станет ✅.":
        "🗓 Connect «{name}»\n\n{not_public_note}At its very end the job must open the address — tap it to copy:\n{url_line}\n{HEARTBEAT_SECRET_NOTE}\n\nWhere the job runs:\n• <b>GitHub Actions</b> — do not put the address into the workflow file: others read the repository. In the repository: Settings → Secrets and variables → Actions → New repository secret, name <code>HEARTBEAT_URL</code>, value — the address above. The last step of the workflow:\n{GITHUB_STEP}\n• <b>cron on your own server</b> — append to the end of the job's line:\n<code>&& curl -fsS {url}</code>\n• <b>The hosting's scheduler</b> (Railway Cron, Render…) — put the address into the service's environment variable <code>HEARTBEAT_URL</code> and append <code>&& curl -fsS \"$HEARTBEAT_URL\"</code> to the command; do not write the address in render.yaml or railway.json.\n\nIf the job fails earlier, the address is not opened and I write. As soon as it checks in, its line on the main screen turns ✅.",
    "⏰ Бот или задача по расписанию\n\nСамая коварная поломка бэкапа, выгрузки или другого бота — тишина: он молча перестаёт работать, и никто не замечает. Добавь его сюда — он будет выходить со мной на связь после каждого запуска, а замолчит — я напишу.\n\nКак назвать? Латиницей, коротко: например <code>socialbot</code> или <code>site-backup</code>.":
        "⏰ A bot or a scheduled job\n\nThe sneakiest failure of a backup, an export or another bot is silence: it quietly stops working, and nobody notices. Add it here — it will check in with me after every run, and if it goes silent — I write.\n\nWhat to call it? Latin letters, short: <code>socialbot</code> or <code>site-backup</code>, for example.",
    "Только латиница, цифры и дефис, до 20 символов. Попробуй ещё раз:": "Latin letters, digits and a hyphen only, up to 20 characters. Try again:",
    "Больше {MAX_HB_JOBS} задач добавить нельзя — удали ненужные.": "No more than {MAX_HB_JOBS} jobs can be added — remove the ones you don't need.",
    "«{name}» уже есть — выходит на связь {rhythm}.": "«{name}» exists already — it checks in {rhythm}.",
    "Открыть её": "Open it",
    "Как часто «{name}» будет выходить на связь?": "How often will «{name}» check in?",
    "Начни заново: 🏠 Главная → «🤖 Боты» → «➕ Бот или задача»": "Start over: 🏠 Home → «🤖 Bots» → «➕ Bot or job»",
    "Не добавил. {again}": "Not added. {again}",
    "Добавил ✅": "Added ✅",
    "✅ «{name}» под присмотром и уже на главном экране.\n\nОсталось подключить. Что такое «{name}»?":
        "✅ «{name}» is under watch and already on the main screen.\n\nIt remains to connect it. What is «{name}»?",
    "🗑 Да, не ждать": "🗑 Yes, stop expecting it",
    "Оставить": "Keep",
    "Перестать ждать «{name}»? Если она сломается, я не узнаю.": "Stop expecting «{name}»? If it breaks, I will not know.",
    "Ок, оставил": "OK, kept",
    "Удалил «{name}» — больше не жду": "Removed «{name}» — no longer expecting it",
    "Её уже нет": "It is gone already",
    "🗑 «{name}» больше не жду.": "🗑 No longer expecting «{name}».",
    "Её уже нет.": "It is gone already.",
    "Удалил": "Removed",
    "\n\nОна была записана в переменной <code>HEARTBEAT_JOBS</code> на сервере бота. Там она может остаться — я её больше не жду.":
        "\n\nIt was written in the <code>HEARTBEAT_JOBS</code> variable on the bot's server. It may stay there — I no longer expect it.",
    "🗑 «{name}» больше не жду.{note}": "🗑 No longer expecting «{name}».{note}",
    # ── handlers/incidents.py ───────────────────────────────────────────────
    "{head}✅ Сейчас всё работает — открытых проблем нет.": "{head}✅ Everything works right now — no open problems.",
    "{head}{icon} Что сейчас не так ({n}):": "{head}{icon} What's wrong right now ({n}):",
    "{n}. {icon} {incident}\n   <i>с {time}</i>": "{n}. {icon} {incident}\n   <i>since {time}</i>",
    "\nНомер — что это значит и что делать; там же «🔧 Я чиню» и «✓ Закрыть». 🔴 — нужно действие сейчас, 🟠 — не сегодня.":
        "\nA number — what it means and what to do; «🔧 I'm fixing it» and «✓ Close» are there too. 🔴 — act now, 🟠 — not today.",
    "🗑 Закрыть все": "🗑 Close all",
    "закрыта {time}": "closed {time}",
    "с {time}": "since {time}",
    "✅ Было: {incident}": "✅ Was: {incident}",
    "<i>Эта проблема уже закрыта — шаги на случай, если повторится.</i>": "<i>This problem is closed already — the steps in case it comes back.</i>",
    "🔗 Ссылки": "🔗 Links",
    "🔧 Я чиню, час тишины": "🔧 I'm fixing it, an hour of silence",
    "🔗 Открыть панель {registrar}": "🔗 Open the {registrar} panel",
    "🌍 К сайту": "🌍 To the site",
    "✓ Закрыть": "✓ Close",
    "← Проблемы": "← Problems",
    "Этой проблемы уже нет в списке": "This problem is no longer in the list",
    "🗑 Да, закрыть все": "🗑 Yes, close all",
    "← Отмена": "← Cancel",
    "Закрыть все открытые проблемы ({n})?\n\nЛежащие сайты я сначала проверю: что всё ещё не открывается, останется в списке. Остальное, если оно настоящее, следующая проверка откроет снова — 🔴 со звонком.":
        "Close all open problems ({n})?\n\nI check the down sites first: what still does not open stays in the list. The rest, if it is real, the next check reopens — 🔴 with a ring.",
    "Подтверждение устарело — открой список и нажми ещё раз": "The confirmation is outdated — open the list and tap again",
    "Проверяю лежащие сайты…": "Checking the down sites…",
    "🗑 Закрыл {closed}.": "🗑 Closed {closed}.",
    " Не закрыл — всё ещё не открываются: ": " Not closed — still down: ",
    "Уже закрыта": "Closed already",
    "Проверяю, открывается ли…": "Checking whether it opens…",
    "Закрыл ✅": "Closed ✅",
    "🔴 Не закрываю: {label} всё ещё не открывается ({why}). Закрою сам, когда поднимется; чинишь — нажми «🔧 Я чиню», и я замолчу.\n\n{explain_text}":
        "🔴 Not closing: {label} still does not open ({why}). I close it myself when it is back; fixing it — tap «🔧 I'm fixing it» and I go quiet.\n\n{explain_text}",
    "✅ Закрыл: {label} открывается.": "✅ Closed: {label} opens.",
    "Эта проблема уже закрыта": "This problem is closed already",
    "Не напоминаю": "Not reminding",
    "👀 Понял: больше не напоминаю про {incident}. Когда поднимется — напишу.": "👀 Got it: no more reminders about {incident}. When it is back — I write.",
    # ── handlers/links.py ───────────────────────────────────────────────────
    "Сайт не найден — список сайтов изменился. Открой меню заново.": "The site is not found — the site list changed. Open the menu again.",
    "🔌 {label} — API-проверка: страниц со ссылками у неё нет.": "🔌 {label} is an API check: it has no pages with links.",
    "← К сайту": "← To the site",
    "Проверяю ссылки на {label}…\n(обычно до 30 секунд)": "Checking the links on {label}…\n(usually up to 30 seconds)",
    "❌ Не смог проверить {label}: {why}": "❌ Could not check {label}: {why}",
    "🟡 На {label} {n} {words} из {checked} не {words2} с первого раза — похоже на временный сбой, перепроверю при следующем обходе.":
        "🟡 On {label} {n} of {checked} {words} did not open at the first try — looks like a temporary failure, I will re-check on the next crawl.",
    "✅ На {label} все проверенные ссылки открываются ({checked})": "✅ On {label} every checked link opens ({checked})",
    "На странице {total_links} ссылок — за раз проверяю {checked}, остальные — в следующие обходы.":
        "The page has {total_links} links — I check {checked} at a time, the rest on the next crawls.",
    # ── handlers/mail.py ────────────────────────────────────────────────────
    " · {duration} назад": " · {duration} ago",
    "записи MX ведут на сервер, которого нет": "the MX records point at a server that does not exist",
    "{who} не отвечает": "{who} does not answer",
    "🔴 Почта {domain} не принимает письма — {why}": "🔴 Mail on {domain} does not accept letters — {why}",
    "⏳ Почта: {who} не ответил — перепроверю через {MAIL_RECHECK_MIN} минут": "⏳ Mail: {who} did not answer — I will re-check in {MAIL_RECHECK_MIN} minutes",
    " (основной сервер молчит, принимает запасной)": " (the main server is silent, a backup one accepts)",
    "✅ Почта принимает письма · {who}{backup}": "✅ Mail accepts letters · {who}{backup}",
    "Почта · {who} — принимает ли письма, не проверяю: внешние проверки выключены": "Mail · {who} — whether it accepts letters I don't check: external checks are off",
    "Почта · {who}": "Mail · {who}",
    "⏳ Почта: ещё не проверял": "⏳ Mail: not checked yet",
    "✅ Почты на домене нет, слать письма от его имени запрещено": "✅ The domain has no mail, and sending on its behalf is forbidden",
    "✅ Почты на домене нет": "✅ The domain has no mail",
    "✅ Почта только отправляет письма — входящей на домене нет": "✅ Mail only sends letters — the domain has no inbound mail",
    "🟠 Почта {domain}: {text} — письма могут уходить в спам": "🟠 Mail on {domain}: {text} — letters may land in spam",
    "🟠 Почта {domain}: {n} {words} — письма могут уходить в спам": "🟠 Mail on {domain}: {n} {words} — letters may land in spam",
    "    DMARC только наблюдает: подделки твоего адреса всё ещё доходят. Поменяй p=none на p=quarantine в записи _dmarc — тогда они будут уходить в спам.":
        "    DMARC only observes: forgeries of your address still get through. Change p=none to p=quarantine in the _dmarc record — then they go to spam.",
    "✅ SPF — одна запись, без ошибок": "✅ SPF — one record, no errors",
    "✅ DKIM — письма подписываются": "✅ DKIM — letters are signed",
    "ℹ️ DKIM — у этого почтового сервиса имя подписи выбираешь ты, проверить не могу": "ℹ️ DKIM — with this mail service you choose the selector name, I cannot check it",
    "✅ DMARC — подделки твоего адреса отклоняются": "✅ DMARC — forgeries of your address are rejected",
    "✅ DMARC — подделки твоего адреса уходят в спам": "✅ DMARC — forgeries of your address go to spam",
    "ℹ️ DMARC — только наблюдает: подделки всё ещё доходят. Когда SPF и DKIM в порядке, поменяй p=none на p=quarantine":
        "ℹ️ DMARC — only observes: forgeries still get through. Once SPF and DKIM are fine, change p=none to p=quarantine",
    "✉️ Почта на {domain}": "✉️ Mail on {domain}",
    "Ещё не проверял — проверю в течение часа или по кнопке ниже.": "Not checked yet — I will within an hour, or by the button below.",
    "Почты на этом домене нет: никто не принимает письма на него. Если она появится — начну следить сам.":
        "This domain has no mail: nobody accepts letters to it. If it appears — I start watching by myself.",
    "✅ Слать письма от его имени запрещено записью SPF «v=spf1 -all» — подделать адрес не выйдет.":
        "✅ Sending on its behalf is forbidden by the SPF record «v=spf1 -all» — the address cannot be forged.",
    "Письма принимает: {who} ({items})": "Letters are accepted by: {who} ({items})",
    "Входящей почты на домене нет — проверяю только то, что нужно для отправки.": "The domain has no inbound mail — I check only what sending needs.",
    "Изменения в DNS доходят за 5–30 минут — потом нажми «🔄 Перепроверить почту».": "DNS changes take 5–30 minutes — then tap «🔄 Re-check mail».",
    "Всё в порядке: письма доходят, а подделать твой адрес сложно.": "All is well: letters arrive, and your address is hard to forge.",
    "↗️ Статус {who}": "↗️ {who} status",
    "🔄 Перепроверить почту": "🔄 Re-check mail",
    "Почту этого домена я больше не проверяю": "I no longer check this domain's mail",
    "Почту этого домена я больше не проверяю.": "I no longer check this domain's mail.",
    "Проверяю почту…": "Checking mail…",
    "▰▱▱ Проверяю почту {domain} — это может занять до пары минут…": "▰▱▱ Checking the mail of {domain} — this may take up to a couple of minutes…",
    "⚠️ Сейчас проверить не получилось: DNS или внешняя проверка не ответили — показываю прошлую проверку.\n\n":
        "⚠️ Could not check right now: DNS or the external check did not answer — showing the previous check.\n\n",
    # ── handlers/maintenance.py ─────────────────────────────────────────────
    "🔧 Плановые работы — в это время я не пишу о падениях и не напоминаю, проверки и статистика продолжаются.\n":
        "🔧 Planned maintenance — during it I don't write about outages or remind, the checks and statistics go on.\n",
    "➕ Добавить окно": "➕ Add a window",
    "Пока ни одного окна.": "No windows yet.",
    "все сайты": "all sites",
    "сайт #{site_id}": "site #{site_id}",
    " · 🟢 активно сейчас": " · 🟢 active now",
    "← Когда писать": "← When to write",
    "Лимит {MAX_MAINT_WINDOWS} окон — удали ненужные.": "The limit is {MAX_MAINT_WINDOWS} windows — remove the ones you don't need.",
    "🌐 Все сайты": "🌐 All sites",
    "🔧 Для чего эти плановые работы?": "🔧 What is this maintenance for?",
    "Не понял": "I did not get that",
    "Каждый день": "Every day",
    "Будни": "Weekdays",
    "Выходные": "Weekends",
    "🔧 В какие дни?": "🔧 On which days?",
    "✏️ Своё время": "✏️ Custom time",
    "🔧 В какое время (по вашей таймзоне)?": "🔧 At what time (in your time zone)?",
    "Сайт не найден.": "The site is not found.",
    "← Плановые работы": "← Planned maintenance",
    "Не добавил: время окна неверное или уже есть {MAX_MAINT_WINDOWS} окон — удали ненужные.":
        "Not added: the window's time is wrong or there are {MAX_MAINT_WINDOWS} windows already — remove the ones you don't need.",
    "✅ Окно добавлено.\n\n": "✅ The window is added.\n\n",
    "🔧 Пришли время окна в формате <code>ЧЧ:ММ-ЧЧ:ММ</code>, например <code>01:30-03:00</code>.\nОкно через полночь тоже можно: <code>23:00-06:00</code>.":
        "🔧 Send the window's time as <code>HH:MM-HH:MM</code>, for example <code>01:30-03:00</code>.\nA window across midnight works too: <code>23:00-06:00</code>.",
    "Не понял. Формат: <code>ЧЧ:ММ-ЧЧ:ММ</code>, и время начала должно отличаться от конца.":
        "I did not get that. The format is <code>HH:MM-HH:MM</code>, and the start must differ from the end.",
    "Начни заново: ⚙️ Настройки → 🔔 Когда писать → 🗓 Плановые работы.": "Start over: ⚙️ Settings → 🔔 When to write → 🗓 Planned maintenance.",
    "Удаляю…": "Deleting…",
    "Уже удалено.": "Deleted already.",
    # ── handlers/mute.py ────────────────────────────────────────────────────
    "Использование: /mute &lt;время&gt;\nПримеры: /mute 1h, /mute 8h, /mute 30m, /mute 1d\nПо умолчанию: 8h":
        "Usage: /mute &lt;time&gt;\nExamples: /mute 1h, /mute 8h, /mute 30m, /mute 1d\nDefault: 8h",
    "Максимум — 7 дней: дольше тишина уже не пауза, а выключенный мониторинг.": "7 days at most: a longer silence is not a pause but monitoring switched off.",
    "🔕 Молчу до {time}.\n{what_rings_through_mute}\nСнова писать обо всём: /unmute": "🔕 Quiet until {time}.\n{what_rings_through_mute}\nTo write about everything again: /unmute",
    "🔔 Снова пишу обо всём.": "🔔 Writing about everything again.",
    "1 час": "1 hour",
    "4 часа": "4 hours",
    "8 часов": "8 hours",
    "1 день": "1 day",
    "🔔 Снять тишину": "🔔 Lift the silence",
    "🔕 Сейчас приглушено до {time}": "🔕 Muted until {time}",
    "{status}На сколько замолчать?\n{what_rings_through_mute}": "{status}How long should I be quiet?\n{what_rings_through_mute}",
    "Не понял длительность.": "I did not get the duration.",
    # ── handlers/security.py ────────────────────────────────────────────────
    "⏳ Безопасность: ещё не проверял": "⏳ Security: not checked yet",
    "только https": "https only",
    "защитные заголовки": "security headers",
    "служебные файлы снаружи не видны": "service files are not exposed",
    "✅ Защита: {items}": "✅ Protection: {items}",
    "{icon} Защита: {text}": "{icon} Protection: {text}",
    "{worst} Защита: {n} {words}": "{worst} Protection: {n} {words}",
    "✅ .git и .env снаружи не видны": "✅ .git and .env are not exposed",
    "ℹ️ Сайт работает по http:// — переход на https и защитные заголовки проверяю только у https-сайтов.":
        "ℹ️ The site runs on http:// — the redirect to https and the security headers I check on https sites only.",
    "✅ По http:// сайт перенаправляет на https://": "✅ On http:// the site redirects to https://",
    "⚠️ Не понял, перенаправляет ли http:// на https://": "⚠️ Could not tell whether http:// redirects to https://",
    "ℹ️ Защитные заголовки: GitHub Pages не даёт ставить свои. Если они нужны — поставь сайт за Cloudflare (бесплатно) и добавь их там.":
        "ℹ️ Security headers: GitHub Pages does not let you set your own. If you need them — put the site behind Cloudflare (free) and add them there.",
    "🟠 Защитные заголовки: нет {n} из {n2} — {items}": "🟠 Security headers: {n} of {n2} missing — {items}",
    "✅ Защитные заголовки: все {n} на месте": "✅ Security headers: all {n} in place",
    "✅ CSP есть: браузер не запустит чужие скрипты": "✅ CSP is set: the browser will not run foreign scripts",
    "ℹ️ CSP нет. Это следующий уровень защиты — запрещает браузеру запускать чужие скрипты. Ставить осторожно: неправильный CSP ломает сайт, поэтому я о нём не напоминаю.":
        "ℹ️ No CSP. It is the next level of protection — it forbids the browser to run foreign scripts. Set it with care: a wrong CSP breaks the site, so I don't nag about it.",
    "🛡 Безопасность {label}": "🛡 Security of {label}",
    "Ещё не проверял — проверю в течение шести часов или по кнопке ниже.": "Not checked yet — I will within six hours, or by the button below.",
    "После выкладки нажми «🔄 Проверить снова».": "After deploying, tap «🔄 Check again».",
    "<i>Проверял {words}.</i>": "<i>Checked {words}.</i>",
    "Безопасность этого адреса я не проверяю": "I don't check the security of this address",
    "Безопасность этого адреса я не проверяю.": "I don't check the security of this address.",
    "Проверяю…": "Checking…",
    "▰▱▱ Смотрю {label} снаружи…": "▰▱▱ Looking at {label} from outside…",
    "⚠️ Сайт сейчас не ответил — показываю прошлую проверку. Попробуй через пару минут.\n\n":
        "⚠️ The site did not answer right now — showing the previous check. Try again in a couple of minutes.\n\n",
    "только что": "just now",
    "{duration} назад": "{duration} ago",
    # ── handlers/seo.py ─────────────────────────────────────────────────────
    "📇 Google: подключён, данные появятся после утренней проверки": "📇 Google: connected, the data comes after the morning check",
    "📇 Google: по данным на {on} главная {words} в индексе": "📇 Google: as of {on} the homepage {words} in the index",
    "была": "was",
    "не была": "was not",
    " — свежих нет": " — nothing fresher",
    "📇 Google: главная в индексе": "📇 Google: the homepage is in the index",
    " (обход {crawl})": " (crawled {crawl})",
    "📇 Google: главной нет в индексе 🔴 ({v})": "📇 Google: the homepage is not in the index 🔴 ({v})",
    "📇 Яндекс: подключён, данные появятся после утренней проверки": "📇 Yandex: connected, the data comes after the morning check",
    "{pages} стр. в поиске": "{pages} pages in search",
    "в поиске 0 страниц 🔴": "0 pages in search 🔴",
    "Яндекс": "Yandex",
    "{icon} {n} {words} в Вебмастере": "{icon} {n} {words} in Webmaster",
    "по данным на {fmt_day}: ": "as of {fmt_day}: ",
    "📇 Яндекс: ": "📇 Yandex: ",
    "⏳ Аудита ещё не было — нажми «🔄 Проверить снова».": "⏳ No audit yet — tap «🔄 Check again».",
    "⚠️ Часть проверки не удалась (robots.txt или главная не ответили) — вердикта нет; открытые проблемы остаются в «Что сейчас не так».":
        "⚠️ Part of the check failed (robots.txt or the homepage did not answer) — no verdict; the open problems stay in «What's wrong right now».",
    "⚠️ Сайт не открывался, когда я проверял — аудит пропущен. Нажми «🔄 Проверить снова».": "⚠️ The site did not open when I checked — the audit was skipped. Tap «🔄 Check again».",
    "🔴 <b>Сайт закрыт от поиска</b> — Google и Яндекс уберут его при следующем обходе.": "🔴 <b>The site is closed to search</b> — Google and Yandex will drop it at the next crawl.",
    " Есть {hindrances} — начни с {words}.": " There are {hindrances} — start with {words}.",
    "неё": "it",
    "них": "them",
    " С моей стороны помех нет — причину покажет Search Console или Вебмастер.": " I see no obstacles on my side — Search Console or Webmaster will show the reason.",
    "🔴 <b>В {where} сайта нет</b>.{tail}": "🔴 <b>The site is not in {where}</b>.{tail}",
    "🟠 Сайт открыт для поисковиков, но есть {hindrances} — у каждой кнопка «что делать».": "🟠 The site is open to search engines, but there are {hindrances} — each has a «what to do» button.",
    "✅ Сайт открыт для поисковиков, помех нет.": "✅ The site is open to search engines, no obstacles.",
    "✅ Сайт открыт для поисковиков и ИИ-ассистентов, помех нет.": "✅ The site is open to search engines and AI assistants, no obstacles.",
    "🔎 {label} в поиске и у ИИ": "🔎 {label} in search and for AI",
    "🤖 ИИ-ассистенты: сайт для них закрыт.": "🤖 AI assistants: the site is closed to them.",
    "🤖 ИИ-ассистенты: запрос с их подписью получает отказ — скорее всего, сайт для них закрыт.": "🤖 AI assistants: a request with their signature is refused — most likely the site is closed to them.",
    "🤖 ИИ-ассистенты: видят почти пустую страницу — {chars} {words} текста без JavaScript.": "🤖 AI assistants: see an almost empty page — {chars} {words} of text without JavaScript.",
    "<b>Что мешает</b> (номер — кнопка ниже):": "<b>What stands in the way</b> (the number is a button below):",
    "    … и ещё {n} стр.": "    … and {n} more pages",
    "… и ещё {n}: покажу после того, как починишь эти.": "… and {n} more: I show them once you fix these.",
    "проверил {pages} {words}": "checked {pages} {words}",
    "без JavaScript видно {chars} {words} текста": "{chars} {words} of text visible without JavaScript",
    ". ИИ-ассистентов robots.txt пускает, и запрос с их подписью проходит, но настоящих ботов Cloudflare узнаёт по адресу — пускает ли он их, видно только в его панели (кнопка ниже)":
        ". robots.txt lets AI assistants in and a request with their signature passes, but Cloudflare recognises real bots by address — whether it lets them in shows only in its panel (the button below)",
    ". Критичное (🔴) присылаю сразу, как замечу; остальное (🟠) не шлю — оно здесь, в «Проблемах» и в сводках":
        ". Critical (🔴) I send as soon as I notice; the rest (🟠) I don't send — it is here, in «Problems» and in the digests",
    ". В индексе ли сайт, знают только Google и Яндекс": ". Whether the site is in the index, only Google and Yandex know",
    "🔌 Подключить Google или Яндекс": "🔌 Connect Google or Yandex",
    "Что я увидел:": "What I saw:",
    "<b>Чем грозит:</b> {why}": "<b>What it risks:</b> {why}",
    "<b>Что делать:</b>": "<b>What to do:</b>",
    "<i>Шаги для {v} — я узнал хостинг по ответам сервера.</i>": "<i>Steps for {v} — I recognised the hosting by the server's answers.</i>",
    "Смотрю на {label} глазами Google, Яндекса и ИИ-ассистентов…\n(это занимает около 30 секунд)": "Looking at {label} through the eyes of Google, Yandex and AI assistants…\n(this takes about 30 seconds)",
    "⚠️ Проверка не удалась — на экране прошлые данные. Попробуй ещё раз позже.": "⚠️ The check failed — the screen shows the previous data. Try again later.",
    "Сайт не найден": "The site is not found",
    "Это API-проверка или порт: поиск и ИИ к ним не относятся": "It is an API check or a port: search and AI do not apply to them",
    # ── handlers/site_settings.py ───────────────────────────────────────────
    "⚙️ Настройки: {label}": "⚙️ Settings: {label}",
    "⏱ Интервал проверки: {v} мин": "⏱ Check interval: {v} min",
    " (по умолчанию)": " (default)",
    "🔁 Неудачных проверок подряд до тревоги: {v}": "🔁 Failed checks in a row before an alert: {v}",
    "🐢 Порог «медленно»: {v} мс": "🐢 «Slow» threshold: {v} ms",
    "🔢 Приемлемые коды: ": "🔢 Accepted codes: ",
    "по умолчанию (любой &lt; 400)": "default (any &lt; 400)",
    "⚠️ Фраза «{kw}» не проверяется: метод HEAD не скачивает страницу": "⚠️ The phrase «{kw}» is not checked: the HEAD method does not download the page",
    "не должна появляться (стоп-фраза)": "must not appear (a stop phrase)",
    "должна быть {where}": "must be {where}",
    "🔍 Фраза: «{kw}» — {mode}": "🔍 Phrase: «{kw}» — {mode}",
    "🔍 Ключевая фраза: не задана": "🔍 Key phrase: not set",
    "📡 Запрос: {method}": "📡 Request: {method}",
    ", заголовков: {n_headers}": ", headers: {n_headers}",
    ", заголовки не распознаны — задай заново": ", the headers could not be read — set them again",
    ", тело {n} симв.": ", body {n} chars",
    "🔌 Это API-проверка: только ответ — без поиска, ссылок и SEO.": "🔌 This is an API check: the answer only — no search, links or SEO.",
    "🏷 Бейдж аптайма:\n<code>{badge_url}</code>": "🏷 Uptime badge:\n<code>{badge_url}</code>",
    "⏱ Интервал": "⏱ Interval",
    "🔁 Сбоев до тревоги": "🔁 Failures before an alert",
    "🐢 Медленно": "🐢 Slow",
    "🔢 Коды ответа": "🔢 Response codes",
    "🔍 Фраза": "🔍 Phrase",
    "📡 Метод": "📡 Method",
    "📡 Заголовки": "📡 Headers",
    "📡 Тело": "📡 Body",
    "← Простые настройки": "← Simple settings",
    "чаще (раз в минуту)": "more often (once a minute)",
    "обычно (раз в 5 минут)": "normally (once in 5 minutes)",
    "реже (раз в 15 минут)": "less often (once in 15 minutes)",
    "раз в {interval} мин": "once in {interval} min",
    "в ответе": "in the answer",
    "на странице": "on the page",
    "⏱ Проверяю {how_often}.": "⏱ I check {how_often}.",
    "⚠️ Фраза «{kw}» не проверяется: метод HEAD не скачивает страницу. Переключи метод на GET в «🛠 Для продвинутых».":
        "⚠️ The phrase «{kw}» is not checked: the HEAD method does not download the page. Switch the method to GET in «🛠 Advanced».",
    "не должно появляться": "must not appear",
    "должно быть {where}": "must be {where}",
    "🔍 Слежу за фразой «{kw}» — {mode}.": "🔍 Watching the phrase «{kw}» — {mode}.",
    "🔍 За фразой в ответе не слежу. Задай слово, без которого ответ неправильный (например, имя поля из JSON), — замечу «HTTP 200, а внутри ошибка».":
        "🔍 Not watching a phrase in the answer. Set a word without which the answer is wrong (a JSON field name, for example) — I will catch «HTTP 200 with an error inside».",
    "🔍 За фразой на странице не слежу. Это ловит случай «сайт открывается, но показывает пустую страницу или ошибку».":
        "🔍 Not watching a phrase on the page. It catches the case «the site opens but shows an empty page or an error».",
    "🔌 Это API-проверка: слежу только за ответом, поиск, ссылки и SEO к ней не относятся.": "🔌 This is an API check: I watch the answer only; search, links and SEO do not apply.",
    "⏱ Чаще": "⏱ More often",
    "⏱ Обычно": "⏱ Normally",
    "⏱ Реже": "⏱ Less often",
    "🔍 Следить за фразой": "🔍 Watch a phrase",
    "🛠 Для продвинутых": "🛠 Advanced",
    "🗑 Убрать из мониторинга": "🗑 Stop monitoring",
    "↩︎ По умолчанию": "↩︎ Default",
    "⏱ Как часто проверять {label}?": "⏱ How often to check {label}?",
    "{m} мин": "{m} min",
    "🔁 Сколько неудачных проверок подряд должно случиться, чтобы {label} считался лежащим?\n(1 — тревога с первой неудачи, больше — меньше ложных тревог)":
        "🔁 How many failed checks in a row before {label} counts as down?\n(1 — an alert at the first failure; more — fewer false alarms)",
    "🐢 С какого времени ответа {label} считать медленным?\n(3 проверки подряд дольше порога → проблема «сайт отвечает медленно»)":
        "🐢 From which response time is {label} slow?\n(3 checks in a row over the threshold → the problem «the site answers slowly»)",
    "{ms} мс": "{ms} ms",
    "🔢 Какие HTTP-коды считать нормой для {label}?\n\nПримеры:\n• <code>200-299</code> — только успешные\n• <code>200-399,401</code> — плюс страница за паролем\n• <code>default</code> — вернуть правило по умолчанию (любой &lt; 400)":
        "🔢 Which HTTP codes count as normal for {label}?\n\nExamples:\n• <code>200-299</code> — successful only\n• <code>200-399,401</code> — plus a page behind a password\n• <code>default</code> — back to the default rule (any &lt; 400)",
    "✅ Фраза должна быть {where}": "✅ The phrase must be {where}",
    "🚫 Стоп-фраза (не должна появляться)": "🚫 Stop phrase (must not appear)",
    "🗑 Убрать фразу": "🗑 Remove the phrase",
    "🔍 Фраза в ответе {label}: API может ответить «200», а внутри — ошибка. Фраза это ловит.\n\n• «должна быть» — например, имя поля из правильного ответа: <code>scoreLabel</code>\n• «стоп-фраза» — например, <code>\"error\"</code>: появилась — значит сломалось\n\nРегистр не важен.":
        "🔍 A phrase in the answer of {label}: an API may answer «200» with an error inside. The phrase catches it.\n\n• «must be» — a field name from a correct answer, for example: <code>scoreLabel</code>\n• «stop phrase» — <code>\"error\"</code>, for example: it appeared, so something broke\n\nCase does not matter.",
    "🔍 Слежка за фразой на {label}: сайт может «открываться», но показывать пустую страницу или ошибку вместо себя. Фраза это ловит.\n\n• «должна быть» — например, название компании из шапки: пропала — значит вместо сайта что-то другое\n• «стоп-фраза» — например, «Fatal error»: появилась — значит сломалось\n\nРегистр не важен.":
        "🔍 Watching a phrase on {label}: a site may «open» but show an empty page or an error instead of itself. The phrase catches it.\n\n• «must be» — the company name from the header, for example: it vanished, so something else is in the site's place\n• «stop phrase» — «Fatal error», for example: it appeared, so something broke\n\nCase does not matter.",
    "📡 Каким методом проверять {label}?\n(HEAD дешевле, но не умеет проверять фразу; POST/PUT отправляют тело, если задано — тело в JSON уходит с Content-Type: application/json)\n\nЛюбой метод, кроме GET и HEAD, делает проверку API-проверкой: слежу только за ответом, поиск, ссылки и SEO для неё не проверяю.":
        "📡 Which method to check {label} with?\n(HEAD is cheaper but cannot check a phrase; POST/PUT send the body if set — a JSON body goes with Content-Type: application/json)\n\nAny method other than GET and HEAD makes it an API check: I watch the answer only; search, links and SEO are not checked for it.",
    "📡 Заголовки запроса для {label}, по одному на строку:\n<code>Authorization: Bearer xxx\nAccept: application/json</code>\n\nПришли <code>-</code>, чтобы убрать все заголовки.":
        "📡 Request headers for {label}, one per line:\n<code>Authorization: Bearer xxx\nAccept: application/json</code>\n\nSend <code>-</code> to remove all headers.",
    "📡 Тело запроса для {label} (отправляется с POST/PUT/PATCH/DELETE), до 4000 символов.\nПришли <code>-</code>, чтобы убрать тело.":
        "📡 Request body for {label} (sent with POST/PUT/PATCH/DELETE), up to 4000 characters.\nSend <code>-</code> to remove the body.",
    "Кнопка устарела — открой настройки заново": "The button is outdated — open the settings again",
    "Сохранено ✅": "Saved ✅",
    "HEAD не скачивает страницу, и фраза «{phrase}» перестанет проверяться. Сначала убери фразу или оставь GET.":
        "HEAD does not download the page, and the phrase «{phrase}» would stop being checked. Remove the phrase first or keep GET.",
    "которая должна быть {where}": "that must be {where}",
    "при появлении которой нужна тревога (стоп-фраза)": "whose appearance needs an alert (a stop phrase)",
    "🔍 Пришли фразу, {what}.\nОт 2 до 100 символов, регистр не важен.": "🔍 Send the phrase {what}.\n2 to 100 characters, case does not matter.",
    "Сайт не найден — начни заново.": "The site is not found — start over.",
    "Не понял. Формат: <code>200-299</code> или <code>200-399,401</code> (коды 100–599). Или <code>default</code> для сброса.":
        "I did not get that. The format is <code>200-299</code> or <code>200-399,401</code> (codes 100–599). Or <code>default</code> to reset.",
    "Нужна фраза от 2 до 100 символов. Попробуй ещё раз:": "The phrase must be 2 to 100 characters. Try again:",
    "📡 Этот сайт проверяется методом HEAD: страница не скачивается, и фразу проверить нельзя. Переключи метод на GET («🛠 Для продвинутых» → «📡 Метод») и задай фразу снова.":
        "📡 This site is checked with HEAD: the page is not downloaded, so a phrase cannot be checked. Switch the method to GET («🛠 Advanced» → «📡 Method») and set the phrase again.",
    "← К настройкам": "← Settings",
    "⏳ Проверяю страницу — это до 15 секунд…": "⏳ Checking the page — up to 15 seconds…",
    "✅ Проверил: сейчас всё так, как ты описал.": "✅ Checked: right now it is as you described.",
    "⚠️ Сейчас проверить не удалось — сохранил, проверю при следующей проверке.": "⚠️ Could not check right now — saved, I will check it on the next check.",
    "🔍 Стоп-фраза «{kw}» уже есть {where} — сохрани я её, тревога пришла бы на первых же проверках. Так бывает, если фраза встречается в обычном тексте или в коде страницы. Пришли другую фразу — или сохрани эту, если сайт сейчас действительно сломан.":
        "🔍 The stop phrase «{kw}» is already {where} — had I saved it, the alert would come at the very first checks. This happens when the phrase occurs in ordinary text or in the page's code. Send another phrase — or save this one if the site really is broken right now.",
    "🔍 Сейчас {where} нет фразы «{kw}» — сохрани я её, тревога пришла бы на первых же проверках. Так бывает, если текст рисует JavaScript, в фразе опечатка или она с другой страницы. Пришли другую фразу — или сохрани эту, если сайт сейчас действительно сломан.":
        "🔍 Right now the phrase «{kw}» is not {where} — had I saved it, the alert would come at the very first checks. This happens when JavaScript draws the text, the phrase has a typo or belongs to another page. Send another phrase — or save this one if the site really is broken right now.",
    "💾 Сохранить всё равно": "💾 Save anyway",
    "Устарело — задай фразу заново": "Outdated — set the phrase again",
    "Не понял. Каждая строка: <code>Имя: значение</code>.": "I did not get that. Every line: <code>Name: value</code>.",
    "Слишком длинно (лимит 4000 символов).": "Too long (the limit is 4000 characters).",
    # ── handlers/sites.py ───────────────────────────────────────────────────
    "Внутренние адреса (локальная сеть, 127.0.0.1, 10.x, 192.168.x) выключены на этом сервере: ALLOW_PRIVATE_TARGETS=0.":
        "Internal addresses (the local network, 127.0.0.1, 10.x, 192.168.x) are switched off on this server: ALLOW_PRIVATE_TARGETS=0.",
    "Это не адрес сервера. Пришли домен или IP сайта": "That is not a server address. Send the site's domain or IP",
    "Не понял хост. Примеры: tcp://mail.example.com:25, ping://10.0.0.1, ping://[2001:db8::1]": "Could not read the host. Examples: tcp://mail.example.com:25, ping://10.0.0.1, ping://[2001:db8::1]",
    "Для tcp нужен порт: tcp://host:порт (например, tcp://mail.example.com:25)": "tcp needs a port: tcp://host:port (for example, tcp://mail.example.com:25)",
    "Не похоже на адрес. Пришли что-то вроде example.com": "That does not look like an address. Send something like example.com",
    "Не похоже на домен. Пришли что-то вроде example.com": "That does not look like a domain. Send something like example.com",
    "Адрес API — без пробелов и не длиннее {API_PATH_LIMIT} символов после домена.": "An API address has no spaces and at most {API_PATH_LIMIT} characters after the domain.",
    ", в ответе не должно быть «{kw}»": ", the answer must not contain «{kw}»",
    ", в ответе должно быть «{kw}»": ", the answer must contain «{kw}»",
    "API-проверка: {method}{expect}. Слежу только за ответом — поиск, ссылки и SEO к API не относятся.": "An API check: {method}{expect}. I watch the answer only — search, links and SEO do not apply to an API.",
    "Сертификат и домен": "The certificate and the domain",
    "Домен": "The domain",
    "{what} — на карточке {label}: проверяю их один раз на адрес": "{what} — on the card of {label}: I check them once per address",
    "Почта и защита": "Mail and protection",
    "Почта": "Mail",
    "Защита": "Protection",
    "Доступность": "Availability",
    "Поиск": "Search",
    "Сайт": "The site",
    "⏳ Ещё не проверял — первая проверка в течение минуты": "⏳ Not checked yet — the first check within a minute",
    "{UNCHECKED} Не могу проверить: {why}": "{UNCHECKED} Cannot check: {why}",
    "🔴 {words}, но показывает не то: {why}": "🔴 {words}, but shows the wrong thing: {why}",
    "Открывается": "Opens",
    "Отвечает": "Answers",
    "Не открывается": "Does not open",
    "Не отвечает": "Does not answer",
    ", обычно за {seconds}": ", usually in {seconds}",
    "без сбоев ни за неделю, ни за месяц": "no downtime this week or this month",
    "простой: за неделю {week}, за месяц {month}": "downtime: this week {week}, this month {month}",
    "последний сбой {time} — {duration}, {why}": "last outage {time} — {duration}, {why}",
    "⚠️ Фраза «{phrase}» не проверяется: метод HEAD": "⚠️ The phrase «{phrase}» is not checked: the HEAD method",
    "✅ Фразы «{phrase}» на странице нет, как и должно быть": "✅ The phrase «{phrase}» is absent from the page, as it should be",
    "✅ Фраза «{phrase}» на месте": "✅ The phrase «{phrase}» is in place",
    "⏳ Сертификат: ещё не проверял": "⏳ Certificate: not checked yet",
    "🔴 Сертификат: {why}": "🔴 Certificate: {why}",
    "до {date} · ": "until {date} · ",
    "✅ Сертификат {until}ещё {days} дн.": "✅ Certificate {until}{days} d left",
    ", выдан {issuer}": ", issued by {issuer}",
    "{icon} Сертификат {words}{issuer}": "{icon} Certificate {words}{issuer}",
    "истёк": "expired",
    "{until}осталось {days} дн.": "{until}{days} d left",
    "⏳ Домен: ещё не проверял": "⏳ Domain: not checked yet",
    "Домен: {v}": "Domain: {v}",
    "срок регистратура не сообщает": "the registry does not report the expiry",
    "⚠️ Домен: {why}": "⚠️ Domain: {why}",
    "оплачен до {date}": "paid until {date}",
    "✅ Домен {until} · ещё {days} дн.": "✅ Domain {until} · {days} d left",
    ", регистратор {registrar}": ", registrar {registrar}",
    "{icon} Домен {words}{registrar}": "{icon} Domain {words}{registrar}",
    "{until} · осталось {days} дн.": "{until} · {days} d left",
    "🔴 DNS: NS-серверы {domain} не отвечают уже {n} ч": "🔴 DNS: the NS servers of {domain} have not answered for {n} h",
    "✅ DNS не менялись с {time}": "✅ DNS unchanged since {time}",
    "✅ Страницы из карты сайта: {n} из {n} отвечают": "✅ Sitemap pages: {n} of {n} answer",
    "🟠 Страницы из карты сайта: {bad} из {n} отвечают ошибкой сервера": "🟠 Sitemap pages: {bad} of {n} answer with a server error",
    " или не отвечают": " or do not answer",
    "не ответила": "did not answer",
    "ошибка {code}": "error {code}",
    "    … и ещё {n}": "    … and {n} more",
    "✅ Ссылки: {words} открываются": "✅ Links: {words} open",
    "{total} из {total}": "{total} of {total}",
    "все": "all",
    "⚠️ Ссылки: не смог проверить": "⚠️ Links: could not check",
    " из {total}": " of {total}",
    "🟠 Ссылки: {n}{of} не {words}": "🟠 Links: {n}{of} {words}",
    "    Что сделать: поставь новый адрес, убери ссылку или дай копию из web.archive.org.": "    What to do: put the new address, remove the link or link a copy from web.archive.org.",
    "✅ Поиск и ИИ: помех нет": "✅ Search and AI: no obstacles",
    "🟠 Поиск и ИИ: {n} {words}": "🟠 Search and AI: {n} {words}",
    "🔴 Поиск и ИИ: сайт закрыт от поисковиков": "🔴 Search and AI: the site is closed to search engines",
    "⚠️ Поиск и ИИ: не смог проверить": "⚠️ Search and AI: could not check",
    "    Пускает ли Cloudflare ИИ-ассистентов, снаружи не видно: настоящих ботов он узнаёт по адресу. Где посмотреть — кнопка ниже.":
        "    Whether Cloudflare lets AI assistants in cannot be seen from outside: it recognises real bots by address. Where to look — the button below.",
    "🔧 Режим «я чиню»: про этот сайт не пишу, проверки идут.": "🔧 «I'm fixing it» mode: not writing about this site, the checks go on.",
    "✅ Починил, пиши снова": "✅ Fixed, write again",
    "🔧 Я чиню: {length} тишины": "🔧 I'm fixing it: {length} of silence",
    "Сейчас плановые работы — про этот сайт не пишу.": "Planned maintenance right now — not writing about this site.",
    "✉️ Как починить почту": "✉️ How to fix mail",
    "🛡 Как защитить сайт": "🛡 How to protect the site",
    "👥 Почему медленно": "👥 Why slow",
    "🤖 Проверить в Cloudflare": "🤖 Check in Cloudflare",
    "⚙️ Настройки сайта": "⚙️ Site settings",
    "📋 Все сайты · {n}/{n2}": "📋 All sites · {n}/{n2}",
    "➕ Добавить сайт": "➕ Add a site",
    "Сайтов пока нет": "No sites yet",
    "🌐 Сайты ({n})": "🌐 Sites ({n})",
    " · страница {n} из {pages}": " · page {n} of {pages}",
    "{head}\nСначала те, где что-то не так.": "{head}\nThe ones with a problem first.",
    "Сайт не найден — список сайтов изменился.": "The site is not found — the site list changed.",
    "Уже проверяю — подожди": "Checking already — wait",
    "⌛ Проверка {label} не уложилась в {n} мин — сайт или его DNS отвечают очень медленно. Ниже карточка по тому, что успел узнать.":
        "⌛ The check of {label} did not finish within {n} min — the site or its DNS answer very slowly. Below is the card from what I managed to learn.",
    "▱▱▱ Проверяю, открывается ли {label}...": "▱▱▱ Checking whether {label} opens...",
    "сертификат, ": "the certificate, ",
    "Смотрю {cert}срок домена{words} и защиту сайта...": "Looking at {cert}the domain expiry{words} and the site's protection...",
    ", почту": ", the mail",
    "Проверяю ссылки и смотрю на сайт глазами Google, Яндекса и ИИ (около 30 секунд)...": "Checking the links and looking at the site through the eyes of Google, Yandex and AI (about 30 seconds)...",
    "➕ Пришли адрес сайта, например <code>example.com</code>.\n\nНе сайт? Выбери ниже.": "➕ Send the site's address, for example <code>example.com</code>.\n\nNot a site? Choose below.",
    "⏰ Бот или задача по расписанию": "⏰ A bot or a scheduled job",
    "Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга. Бота или задачу по расписанию добавить можно.":
        "The limit is {MAX_SITES} sites — stop monitoring something. A bot or a scheduled job can still be added.",
    "🛠 API, порт или ping": "🛠 API, port or ping",
    "🛠 Кроме сайтов я умею следить за сервисами без веб-страницы:\n\n• <code>example.com/api/health</code> — главная функция сайта, его API: код ответа, время и фраза в ответе. Адрес под <code>/api/</code> или на <code>api.</code>-домене я считаю API и не проверяю у него поиск, ссылки и SEO; метод POST, заголовки и тело задаются в настройках после добавления\n• <code>tcp://mail.example.com:25</code> — отвечает ли порт (почта, SSH, база данных)\n• <code>ping://10.0.0.1</code> — отвечает ли хост на ping\n\nПришли адрес в таком виде.":
        "🛠 Besides sites I can watch services without a web page:\n\n• <code>example.com/api/health</code> — the site's main function, its API: the response code, time and a phrase in the answer. An address under <code>/api/</code> or on an <code>api.</code> domain counts as an API, and I don't check search, links or SEO for it; the POST method, headers and body are set in the settings after adding\n• <code>tcp://mail.example.com:25</code> — whether a port answers (mail, SSH, a database)\n• <code>ping://10.0.0.1</code> — whether a host answers ping\n\nSend the address in that form.",
    "▱▱ Добавил {label} — делаю первую проверку...": "▱▱ Added {label} — doing the first check...",
    "✅ {label} под присмотром.": "✅ {label} is under watch.",
    "{UNCHECKED} Проверить не могу: {why}.": "{UNCHECKED} Cannot check: {why}.",
    "🔴 Сейчас {words}, но показывает не то: {why}. Я уже слежу и напишу, когда станет как надо.": "🔴 Right now it {words}, but shows the wrong thing: {why}. I am watching already and will write when it is right.",
    "отвечает": "answers",
    "🔴 Сейчас {words}: {why}. Я уже слежу и напишу, когда поднимется.": "🔴 Right now it {words}: {why}. I am watching already and will write when it is back.",
    "не открывается": "does not open",
    "не отвечает": "does not answer",
    "\n🔌 Это API: слежу только за ответом — код, время и фраза в ответе. Поиск, ссылки и SEO к API не относятся.": "\n🔌 This is an API: I watch the answer only — the code, time and a phrase in the answer. Search, links and SEO do not apply to an API.",
    "Нужен POST, токен или тело запроса — «📡 Настроить запрос»: там метод, заголовки, тело и фраза, которая должна быть в ответе.":
        "Need POST, a token or a request body — «📡 Set up the request»: the method, headers, body and the phrase the answer must contain are there.",
    " Ошибка выше уйдёт, когда запрос станет правильным.": " The error above goes away once the request is right.",
    "🔒 Сертификат безопасности действует ещё {days_left} дн.": "🔒 The security certificate is valid for {days_left} more days.",
    "⚠️ Сертификат: не удалось проверить, попробую при следующей проверке.": "⚠️ Certificate: could not check, I will try at the next check.",
    "утром": "in the morning",
    ", ссылки — в ближайшие {links_check_interval_hours} ч": ", the links — within {links_check_interval_hours} h",
    "Срок домена проверю {domain_at}, поиск и ИИ — {v} (итоги — в утренней сводке){links}.": "I check the domain expiry {domain_at}, search and AI — {v} (the results come in the morning digest){links}.",
    "Срок домена проверю {domain_at}.": "I check the domain expiry {domain_at}.",
    "🌍 Открыть карточку": "🌍 Open the card",
    "📡 Настроить запрос": "📡 Set up the request",
    "💡 На главной есть «{phrase}». Следить, чтобы она не пропадала? Так я замечу пустую страницу или ошибку вместо сайта.":
        "💡 The homepage has «{phrase}». Watch that it does not vanish? That way I notice an empty page or an error in the site's place.",
    "Ок, больше не предложу": "OK, I won't suggest it again",
    "Подсказка устарела — задай фразу в настройках сайта": "The hint is outdated — set the phrase in the site's settings",
    "Слежу ✅": "Watching ✅",
    "✅ Слежу, чтобы на {label} была фраза «{phrase}». Поменять можно в настройках сайта.": "✅ Watching that {label} has the phrase «{phrase}». You can change it in the site's settings.",
    "🗑 Да, убрать": "🗑 Yes, remove",
    "Убрать {label} из мониторинга?\nОткрытые инциденты по нему закроются, история останется. Его настройки (фраза, метод, заголовки, пороги) и пауза «🔧 Я чиню» сбросятся — если добавишь его снова, начнёшь с чистого листа.":
        "Stop monitoring {label}?\nIts open incidents close, the history stays. Its settings (phrase, method, headers, thresholds) and the «🔧 I'm fixing it» pause are reset — if you add it again, you start from scratch.",
    "🗑 {label} больше не под присмотром. История сохранилась, настройки сброшены.": "🗑 {label} is no longer under watch. The history is kept, the settings are reset.",
    "Сайт уже убран.": "The site is removed already.",
    "Кнопка устарела — открой карточку сайта заново": "The button is outdated — open the site's card again",
    "Пауза снята — снова пишу обо всём": "The pause is lifted — writing about everything again",
    "🔧 {duration} не пишу про этот сайт, проверки идут": "🔧 {duration} of silence about this site, the checks go on",
    "список сайтов: в файле не список": "the site list: not a list in the file",
    "{url} (лимит {MAX_SITES})": "{url} (the limit is {MAX_SITES})",
    "{url}: {items} (неверные значения)": "{url}: {items} (invalid values)",
    "ещё {n} записей сверх {IMPORT_CAP}": "{n} more entries over {IMPORT_CAP}",
    "задача {name}": "job {name}",
    "подключение {key} — задай заново в «🔌 Подключения»": "connection {key} — set it again in «🔌 Connections»",
    "настройка {key}": "setting {key}",
    "настройка timezone": "the timezone setting",
    "плановые работы: неверное время или дни, чужой сайт или уже {MAX_MAINT_WINDOWS} окон": "maintenance: wrong time or days, a foreign site, or {MAX_MAINT_WINDOWS} windows already",
    "Не смог разобрать файл. Нужна копия базы (.db), которую я присылаю по воскресеньям.": "Could not read the file. I need the database copy (.db) I send on Sundays.",
    "✅ Из файла: сайтов добавлено {added}, обновлено {updated}.": "✅ From the file: sites added {added}, updated {updated}.",
    "настроек {settings}": "settings {settings}",
    "плановых работ {windows}": "maintenance windows {windows}",
    "задач {jobs}": "jobs {jobs}",
    "подключений {integrations}": "connections {integrations}",
    " Также: ": " Also: ",
    "\nПропущено:\n": "\nSkipped:\n",
    "  … и ещё {n}": "  … and {n} more",
    "❌ Не восстановил: {e}. Текущая база не тронута.": "❌ Not restored: {e}. The current database is untouched.",
    "✅ Восстановил из копии: активных сайтов {n}. Прежняя база сохранена рядом: {basename}. Проверки продолжаются.":
        "✅ Restored from the copy: {n} active sites. The previous database is kept next to it: {basename}. The checks go on.",
    " и ещё {n}": " and {n} more",
    "💾 «{name}» — копия базы бота. Восстановить из неё сайты, настройки и подключения?": "💾 «{name}» is a copy of the bot's database. Restore the sites, settings and connections from it?",
    "Сайтов в копии: {n}, сейчас: {n2}.": "Sites in the copy: {n}, now: {n2}.",
    "Пропадут: {list}.": "Will vanish: {list}.",
    "Появятся: {list}.": "Will appear: {list}.",
    "Подключения станут как в копии: ": "The connections become as in the copy: ",
    "Ключи подключений, заголовки и тела API-проверок, которых в копии нет, останутся текущими (на новом сервере их нужно ввести заново).":
        "Connection keys and the headers and bodies of API checks the copy lacks stay as they are now (on a new server they must be entered again).",
    "В копии окон тишины: {windows}, сайтов на паузе «🔧 Я чиню»: {paused}.": "Maintenance windows in the copy: {windows}, sites paused with «🔧 I'm fixing it»: {paused}.",
    "Владелец и помощники останутся как сейчас, ": "The owner and the helpers stay as they are now, ",
    "секреты ссылок — тоже.": "the link secrets too.",
    "секреты ссылок возьму из копии — старые ссылки задач продолжат работать.": "the link secrets come from the copy — the old job links keep working.",
    " Текущая база сохранится рядом.": " The current database is kept next to it.",
    "Не вижу размер файла — пришли его ещё раз как документ (📎 → Файл).": "I can't see the file size — send it again as a document (📎 → File).",
    "Файл слишком большой (лимит 20 МБ — столько отдаёт Telegram).": "The file is too big (the limit is 20 MB — that is what Telegram hands over).",
    "Не смог скачать файл из Telegram ({e}). Пришли его ещё раз.": "Could not download the file from Telegram ({e}). Send it again.",
    "файл": "file",
    "❌ Не похоже на мою копию базы: {e}.": "❌ That does not look like my database copy: {e}.",
    "📥 «{name}» — файл экспорта из прежней версии. Добавить из него сайты и настройки? Лишнее не удалю.": "📥 «{name}» is an export file from a previous version. Add the sites and settings from it? I delete nothing.",
    "Не знаю, что делать с этим файлом. Чтобы восстановить бота, пришли копию базы (.db), которую я присылаю по воскресеньям.":
        "I don't know what to do with this file. To restore the bot, send the database copy (.db) I send on Sundays.",
    "✅ Восстановить": "✅ Restore",
    "✖️ Не надо": "✖️ Don't",
    "Это старый вопрос": "That is an old question",
    "Это старый вопрос — пришли файл ещё раз.": "That is an old question — send the file again.",
    "Ничего не менял": "Changed nothing",
    "Ничего не менял.": "Changed nothing.",
    "Восстанавливаю…": "Restoring…",
    # ── handlers/speed.py ───────────────────────────────────────────────────
    "    сервер начинает отвечать за {seconds}": "    the server starts answering in {seconds}",
    "👥 Скорость у посетителей — {label}": "👥 Speed for visitors — {label}",
    "<i>Посетители Chrome за {WINDOW_DAYS} дней, данные Google</i>": "<i>Chrome visitors over {WINDOW_DAYS} days, Google's data</i>",
    "Не подключено: 🔌 Подключения → ⚡ Скорость у посетителей Chrome. Бесплатно, нужен ключ Google; на сайт ничего ставить не нужно.":
        "Not connected: 🔌 Connections → ⚡ Speed for Chrome visitors. Free, needs a Google key; nothing to install on the site.",
    "Спрошу у Google {v}.": "I will ask Google {v}.",
    "в ближайшее утро": "next morning",
    "У Google мало данных по сайту — так у большинства небольших сайтов: Google считает только сайты, у которых достаточно посетителей в Chrome. Как сайт открывается сейчас, покажет PageSpeed Insights.":
        "Google has little data on the site — as with most small sites: Google counts only sites with enough Chrome visitors. How the site opens right now, PageSpeed Insights will show.",
    "<i>Числа — как у 3 из 4 посетителей, у остальных дольше. Google считает быстрым: страница появляется за 2.5 с или быстрее, кнопки откликаются за 0.2 с, вёрстка почти не прыгает.</i>":
        "<i>The numbers are for 3 of 4 visitors; the rest wait longer. Google calls fast: the page appears in 2.5 s or less, the buttons respond in 0.2 s, the layout barely jumps.</i>",
    "⚡ Подключить данные Google": "⚡ Connect Google's data",
    "Скорость у посетителей знаю только для страниц сайта": "I know the speed for visitors only for the site's pages",
}

# Russian singular → (English singular, English plural).
PLURALS: dict[str, tuple[str, str]] = {
    "проблема": ("problem", "problems"),
    "символ": ("character", "characters"),
    "страницу": ("page", "pages"),
    "открывается": ("does not open", "do not open"),
    "ссылка": ("link", "links"),
    "открылась": ("", ""),
    "помеха": ("obstacle", "obstacles"),
    "ссылка не открывается": ("link does not open", "links do not open"),
}
