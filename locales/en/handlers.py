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
}

# Russian singular → (English singular, English plural).
PLURALS: dict[str, tuple[str, str]] = {
    "проблема": ("problem", "problems"),
    "помеха": ("obstacle", "obstacles"),
    "ссылка не открывается": ("link does not open", "links do not open"),
}
