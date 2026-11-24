"""English for reports/* (see utils/i18n.py): alerts, recoveries, the
morning and evening digests, the weekly report."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── reports/formatter.py: availability ──────────────────────────────────
    " с {time}": " since {time}",
    "🔴 {label} отвечает, но показывает не то{when}": "🔴 {label} answers, but shows the wrong thing{when}",
    "🔴 {label} открывается, но показывает не то{when}": "🔴 {label} opens, but shows the wrong thing{when}",
    "{meaning} Сейчас {what}.": "{meaning} Right now: {what}.",
    "🔴 {label} не открывается{when}: {what}": "🔴 {label} does not open{when}: {what}",
    "🔴 {label} не отвечает{when}: {what}": "🔴 {label} does not answer{when}: {what}",
    "Сбой в {n} из {total} последних проверок": "Failed {n} of the last {total} checks",
    "Проверил {times}": "Checked {times}",
    " {how}: сервер отвечает, но его сертификат не принимается — так его видят и посетители.":
        " {how}: the server answers, but its certificate is not accepted — visitors see it the same way.",
    " {how} и с внешних серверов: не открывается ни у кого.": " {how} and from external servers: it opens for nobody.",
    " {how}; снаружи открывается (с {ups} из {total} серверов), но не {region} — посетители оттуда его тоже не видят.":
        " {how}; it opens from outside (from {ups} of {total} servers), but not {region} — visitors there cannot see it either.",
    " {how}; {region} тоже не открывается, остальные внешние серверы не успели ответить.":
        " {how}; {region} it does not open either, the other external servers did not answer in time.",
    " {how}; снаружи открывается только с {ups} из {total} серверов — похоже, сайт лежит не везде.":
        " {how}; from outside it opens only from {ups} of {total} servers — the site seems down in some places only.",
    " {how}; снаружи проверить не удалось — возможно, проблема на моей стороне.":
        " {how}; could not check from outside — the problem may be on my side.",
    "когда в ответе снова будет как надо": "when the answer is right again",
    "когда на странице снова будет как надо": "when the page is right again",
    "когда поднимется": "when it is back up",
    "Продолжаю проверять {every} и напишу, {back}.": "I keep checking {every} and will write {back}.",
    "не меньше {duration} ({span}); когда именно поднялся — не знаю: с {start} до {end} я его не проверял":
        "at least {duration} ({span}); when exactly it came back I don't know: from {start} to {end} I was not checking it",
    "✅ {label}: снова всё как надо": "✅ {label}: everything is right again",
    "✅ {label} снова открывается": "✅ {label} opens again",
    "✅ {label} снова отвечает": "✅ {label} answers again",
    "Было не так {duration}": "Was wrong for {duration}",
    "Лежал {duration}": "Was down for {duration}",
    "Ничего делать не нужно.": "Nothing to do.",
    # ── certificate / domain ────────────────────────────────────────────────
    " Автопродление не сработало: сертификат не менялся, хотя срок уже близко.":
        " Auto-renewal did not happen: the certificate has not changed although the expiry is close.",
    "выдан {issuer}": "issued by {issuer}",
    " Продлевается у регистратора: {registrar}.": " Renewed at the registrar: {registrar}.",
    # ── links / deep / seo ──────────────────────────────────────────────────
    "Что сделать: если страница переехала — поставь новый адрес; пропала — убери ссылку "
    "или дай копию из web.archive.org; закрытый репозиторий — открой его или убери ссылку.":
        "What to do: if the page moved — put the new address; if it is gone — remove the link "
        "or link a copy from web.archive.org; a private repository — open it or remove the link.",
    "… и ещё {n}": "… and {n} more",
    "🟠 На {label} {n} {links}": "🟠 On {label} {n} {links}",
    "⚠️ Ещё {n} {links} не {opened} с первого раза (таймаут или ошибка сервера) — перепроверю при следующем обходе "
    "и скажу, если так и останется.":
        "⚠️ {n} more {links} did not open at the first try (a timeout or a server error) — I will re-check on the next "
        "crawl and say if it stays that way.",
    "не ответила за 15 секунд": "did not answer within 15 seconds",
    "🔴 {label} исчезает из поиска": "🔴 {label} is vanishing from search",
    "Google и Яндекс уберут сайт при следующем обходе — переходы из поиска пропадут.":
        "Google and Yandex will drop the site at the next crawl — the visits from search will vanish.",
    "Нажми кнопку под сообщением: там шаги для твоего хостинга.": "Tap the button under the message: the steps for your hosting are there.",
    # ── dns ─────────────────────────────────────────────────────────────────
    "Так бывает, когда перестали работать NS-серверы домена (не оплачен хостинг DNS, удалили зону) или после смены NS "
    "не обновили DNSSEC (запись DS у регистратора): браузеры тоже не находят сайт.":
        "This happens when the domain's NS servers stopped working (unpaid DNS hosting, a deleted zone) or DNSSEC was not "
        "updated after a change of NS (the DS record at the registrar): browsers cannot find the site either.",
    "Так бывает, когда у DNS-серверов домена сбой или сломана зона: посетители могут не найти сайт.":
        "This happens when the domain's DNS servers have an outage or the zone is broken: visitors may not find the site.",
    "1. Открыть сайт в браузере: если не открывается — виноват DNS, а не хостинг.\n"
    "2. Проверить домен на dnschecker.org: отвечают ли его NS-серверы.\n"
    "3. Если DNSSEC включён у регистратора, а зона переехала — снять или обновить запись DS.":
        "1. Open the site in a browser: if it does not open, DNS is to blame, not the hosting.\n"
        "2. Check the domain on dnschecker.org: do its NS servers answer.\n"
        "3. If DNSSEC is on at the registrar and the zone moved — remove or update the DS record.",
    "{icon} {host}: DNS не отвечает на запрос {rtype} уже {n} {checks} подряд ({n} ч). Другие имена при этом отвечают — "
    "дело не в моей сети.":
        "{icon} {host}: DNS has not answered the {rtype} query for {n} {checks} in a row ({n} h). Other names answer, so it "
        "is not my network.",
    "Последний известный ответ: {old}": "Last known answer: {old}",
    "адрес сервера (запись A)": "the server address (the A record)",
    "адрес сервера IPv6 (запись AAAA)": "the IPv6 server address (the AAAA record)",
    "псевдоним адреса (запись CNAME)": "the address alias (the CNAME record)",
    "NS-серверы домена": "the domain's NS servers",
    "почтовые серверы (запись MX)": "the mail servers (the MX record)",
    "запись {rtype}": "the {rtype} record",
    "изменился": "changed", "изменились": "changed", "изменилась": "changed",
    "пропал": "is gone", "пропали": "are gone", "пропала": "is gone",
    "{icon} У {host} {verb} {what}": "{icon} {host}: {what} {verb}",
    "Было: {old}": "Was: {old}",
    "Стало: {new}": "Now: {new}",
    "Что это обычно значит: {cause}.": "What it usually means: {cause}.",
    # ── mail ────────────────────────────────────────────────────────────────
    "Записи MX домена ведут на сервер, которого нет. Письма тебе не доходят — отправители получают ошибку.":
        "The domain's MX records point at a server that does not exist. Mail does not reach you — senders get an error.",
    "Почтовый сервер": "The mail server",
    "{who} не отвечает уже две проверки подряд — проверял из нескольких стран. Письма тебе сейчас не доходят: серверы "
    "отправителей будут пробовать ещё несколько дней, потом вернут письмо с ошибкой.":
        "{who} has not answered two checks in a row — I checked from several countries. Mail is not reaching you right now: "
        "the senders' servers will keep trying for a few days, then bounce the letter.",
    "Открыть страницу статуса {provider} (кнопка ниже): если сбой у них — ждать, я напишу, когда почта заработает.":
        "Open the {provider} status page (the button below): if the outage is theirs — wait, I will write when mail works again.",
    "Если у почтового сервиса всё в порядке — проверить записи MX в DNS: они должны вести на него, точные значения есть "
    "в его настройках.":
        "If the mail service is fine — check the MX records in DNS: they must point at it, the exact values are in its settings.",
    "🔴 Почта на {domain} не принимает письма": "🔴 Mail on {domain} does not accept letters",
    "Что делать:": "What to do:",
    "🟠 Почта на {domain} настроена не до конца — письма могут попадать в спам, а подделать твой адрес проще":
        "🟠 Mail on {domain} is not fully set up — letters may land in spam, and your address is easier to forge",
    "Что исправить в DNS:": "What to fix in DNS:",
    "{n} {obstacles}, у каждой — что исправить в DNS:": "{n} {obstacles}, each with what to fix in DNS:",
    "Изменения в DNS доходят за 5–30 минут. Я перепроверяю каждый час и напишу, когда всё будет в порядке.":
        "DNS changes take 5–30 minutes. I re-check every hour and will write when all is well.",
    "✅ Почта на {domain}: настройки в порядке, письмам ничего не мешает.": "✅ Mail on {domain}: the settings are fine, nothing stands in the letters' way.",
    " (не принимала около {duration})": " (was not accepting for about {duration})",
    "✅ Почта на {domain} снова принимает письма{how_long}. Письма, которые слали в это время, обычно доходят сами: серверы "
    "отправителей повторяют попытки.":
        "✅ Mail on {domain} accepts letters again{how_long}. Letters sent in the meantime usually arrive by themselves: "
        "the senders' servers retry.",
    # ── security / visitors ─────────────────────────────────────────────────
    "🔴 На {label} наружу видны служебные файлы — их может скачать любой": "🔴 Service files are exposed on {label} — anyone can download them",
    "Убрал — нажми «🔄 Проверить снова» на карточке сайта, я скажу, что их больше не видно.":
        "Removed them — tap «🔄 Check again» on the site's card, I will say they are no longer visible.",
    "🟠 {label}: защиту сайта стоит усилить": "🟠 {label}: the site's protection could be stronger",
    "Я перепроверяю каждые 6 часов и напишу, когда всё будет в порядке.": "I re-check every 6 hours and will write when all is well.",
    "Что сделать:": "What to do:",
    "{n} {obstacles}, у каждой — что сделать:": "{n} {obstacles}, each with what to do:",
    "✅ На {label} служебные файлы больше не видны снаружи. Если в них были пароли или ключи — смени их, если ещё не сменил.":
        "✅ The service files on {label} are no longer exposed. If they held passwords or keys — change them, if you have not yet.",
    "✅ {label}: защита сайта в порядке.": "✅ {label}: the site's protection is fine.",
    "🟠 {label}: медленно у настоящих посетителей": "🟠 {label}: slow for real visitors",
    "{values} — так у 3 из 4 посетителей, у остальных ещё дольше ({source}).": "{values} — that is for 3 of 4 visitors, the rest wait even longer ({source}).",
    "✅ {label}: у посетителей снова не медленно — {values}.": "✅ {label}: no longer slow for visitors — {values}.",
    # ── status report ───────────────────────────────────────────────────────
    "не могу проверить: {why}": "cannot check: {why}",
    "сертификат не удалось проверить": "could not check the certificate",
    "сертификат истёк {days} дн. назад": "certificate expired {days} d ago",
    "срок домена не удалось проверить": "could not check the domain's expiry",
    "домен истёк {days} дн. назад": "domain expired {days} d ago",
    "открывается": "opens",
    "отвечает": "answers",
    "{verb}, но {notes}": "{verb}, but {notes}",
    "🌅 Доброе утро · {now}": "🌅 Good morning · {now}",
    "🌅 Утренняя сводка, с опозданием: утром меня не было · {now}": "🌅 Morning digest, late: I was away in the morning · {now}",
    "🌙 Вечер · {now}": "🌙 Evening · {now}",
    "🔎 Проверка · {now}": "🔎 Check · {now}",
    "✅ Остальные в порядке: {n}": "✅ The rest are fine: {n}",
    "✅ Сайты в порядке: {n}": "✅ Sites fine: {n}",
    "{icon} Открытых проблем: {n}": "{icon} Open problems: {n}",
    "{head} — в строках сайтов выше": "{head} — in the site lines above",
    "… и ещё {n} — весь список: 🏠 Главная → «Что не так»": "… and {n} more — the whole list: 🏠 Home → «What's wrong»",
    "Всё спокойно 🐕": "All quiet 🐕",
    " (не подтверждено: так написал отправитель)": " (unverified: that is what the sender wrote)",
    "📩 Сообщение с сайта": "📩 A message from the site",
    "Сайт: {site}{note}": "Site: {site}{note}",
    "Страница: {page}": "Page: {page}",
    "Сообщение:": "Message:",
    # ── reports/weekly.py ───────────────────────────────────────────────────
    "с нуля": "from zero",
    "Ровно у остальных {n}: {range}": "Level for the other {n}: {range}",
    "(с прошлой неделей сравнить не удалось)": "(could not compare with last week)",
    "🔎 Google (неделя к неделе):": "🔎 Google (week over week):",
    "⚠️ Google Search Console: не смог получить данные — {why}": "⚠️ Google Search Console: could not get the data — {why}",
    "⚠️ Яндекс.Вебмастер: не смог получить данные — {why}": "⚠️ Yandex.Webmaster: could not get the data — {why}",
    "{n} стр. в поиске": "{n} pages in search",
    "ИКС {sqi}": "SQI {sqi}",
    "{icon} {n} {problems} в Вебмастере": "{icon} {n} {problems} in Webmaster",
    "🔎 Яндекс:": "🔎 Yandex:",
    "не ответил": "did not answer",
    "📊 Итоги недели": "📊 The week in review",
    "нет данных": "no data",
    "Открывается": "Opens",
    "Отвечает": "Answers",
    "{icon} {label}: за неделю {week}, за месяц {month}. {verb} {speed}.": "{icon} {label}: this week {week}, this month {month}. {verb} {speed}.",
    "✅ {n} {sites} — без сбоев за неделю и за месяц": "✅ {n} {sites} — no downtime this week or this month",
    ", отвечает за {range}": ", answers in {range}",
    ", отвечают за {range}": ", answer in {range}",
    "Поиск": "Search", "Инциденты": "Incidents", "Продления": "Renewals", "Зависимости": "Dependencies",
    "⚠️ {section}: не смог собрать — подробности в логе бота": "⚠️ {section}: could not put it together — details in the bot's log",
    "Открыто сейчас ({n}):": "Open now ({n}):",
    "с {day}": "since {day}",
    "Решено за неделю: {n}": "Solved this week: {n}",
    "🎉 За неделю ни одной проблемы": "🎉 Not a single problem this week",
    "🔒 Сертификаты продлились сами: {chips}": "🔒 Certificates renewed by themselves: {chips}",
    # ── reports/scheduler.py: the bot's own network ─────────────────────────
    "⚠️ Уже {duration} сервер бота считает, что у него нет сети: не отвечают адреса из NET_CANARIES. Всё это время сайт "
    "без ответа я считаю сбоем своей сети и тревогу не поднимаю. Если сеть на самом деле есть — впиши в NET_CANARIES "
    "адреса, которые сервер пропускает, или оставь переменную пустой.":
        "⚠️ For {duration} now the bot's server has believed it has no network: the NET_CANARIES addresses do not answer. "
        "All this time a site without an answer counts as my own network failure and raises no alert. If the network is "
        "actually there — put addresses the server lets through into NET_CANARIES, or leave the variable empty.",
    "⚠️ С {start} до {end} у сервера бота не было сети или не работал DNS: проверить сайты было нельзя, тревоги о них я "
    "не поднимал.":
        "⚠️ From {start} to {end} the bot's server had no network or DNS did not work: the sites could not be checked, and "
        "I raised no alerts about them.",
    "Сейчас не открываются: {sites}. Если не поднимутся — пришлю обычную тревогу.": "Not opening now: {sites}. If they do not come back — the usual alert follows.",
    "Сейчас всё открывается.": "Everything opens now.",
    "⚠️ Не проходит проверка сети сервера бота: не отвечают адреса из NET_CANARIES (по умолчанию 1.1.1.1, 8.8.8.8, "
    "9.9.9.9 и имена DNS-сервисов). Пока так, сайт без ответа я посчитаю сбоем своей сети и тревогу не подниму. Если сервер "
    "за файрволом — впиши в NET_CANARIES адреса, которые он пропускает, или оставь переменную пустой, чтобы выключить "
    "проверку.":
        "⚠️ The bot server's network check fails: the NET_CANARIES addresses do not answer (by default 1.1.1.1, 8.8.8.8, "
        "9.9.9.9 and the DNS services' names). While so, a site without an answer counts as my own network failure and "
        "raises no alert. If the server is behind a firewall — put addresses it lets through into NET_CANARIES, or leave "
        "the variable empty to switch the check off.",
    "⚠️ Адреса из NET_CANARIES не отвечают, а сайты отвечают — похоже, сервер за файрволом. Самопроверку сети я выключил "
    "до перезапуска: сайт без ответа снова считается сбоем сайта. Чтобы она работала, впиши в NET_CANARIES адреса, "
    "которые сервер пропускает.":
        "⚠️ The NET_CANARIES addresses do not answer while the sites do — the server seems to be behind a firewall. I switched "
        "the network self-check off until a restart: a site without an answer counts as the site's failure again. To make "
        "it work, put addresses the server lets through into NET_CANARIES.",
    "неизвестная причина": "unknown reason",
    "⚠️ {label}: не могу проверить — {why}. Тревоги по нему не будет, пока это не исправить.":
        "⚠️ {label}: cannot check — {why}. There will be no alerts about it until this is fixed.",
    # ── alerts, groups, recoveries ──────────────────────────────────────────
    "⏳ Тревога об этом не ушла вовремя — проблема открыта{when}. Проверки продолжаются.":
        "⏳ The alert about this did not go out on time — the problem has been open{when}. Checks go on.",
    "Один из сайтов": "One of the sites",
    "Один из серверов": "One of the servers",
    "🔧 Тишину снял — снова пишу обо всём.": "🔧 Silence lifted — I write about everything again.",
    "открывается, но на странице не то": "opens, but the page is wrong",
    ", с {time}": ", since {time}",
    "🔴 Не открываются сразу {sites} — похоже на общий сбой хостинга или сети:": "🔴 {sites} down at once — looks like a hosting or network outage:",
    "Продолжаю проверять и напишу, когда поднимутся.": "I keep checking and will write when they are back.",
    "было не так": "was wrong for",
    "лежал": "was down for",
    "✅ Снова открываются {sites}:": "✅ {sites} open again:",
    "🟠 {label} мигает: {n} {falls} за час, последнее в {time}. На каждое не звоню; когда успокоится — напишу.":
        "🟠 {label} is flapping: {n} {falls} in an hour, the last at {time}. I don't ring for each one; when it settles — I write.",
    "✅ {label} снова открывается и с моего сервера.": "✅ {label} opens from my server again too.",
    "✅ Снова как надо в {time}": "✅ Right again at {time}",
    ", было не так {duration}": ", was wrong for {duration}",
    "✅ Поднялся в {time}": "✅ Back up at {time}",
    ", лежал {duration}": ", was down for {duration}",
    "Было:": "Was:",
    "🔁 Через {minutes} мин: уже открывается. Подтвержу отдельным сообщением.": "🔁 After {minutes} min: opens already. I will confirm in a separate message.",
    "🔁 Через {minutes} мин: проверить не смог — {why}.": "🔁 After {minutes} min: could not check — {why}.",
    "🔁 Через {minutes} мин: всё ещё не открывается ({why}).": "🔁 After {minutes} min: still does not open ({why}).",
    "🔁 Через {minutes} мин: все уже открываются. Подтвержу отдельным сообщением.": "🔁 After {minutes} min: all open already. I will confirm in a separate message.",
    "уже открываются: {sites}": "open already: {sites}",
    "всё ещё не открываются: {sites}": "still down: {sites}",
    "проверить не смог: {sites}": "could not check: {sites}",
    "🔁 Через {minutes} мин: ": "🔁 After {minutes} min: ",
    "🔁 Через 5 мин: {seconds}, уже нормально.": "🔁 After 5 min: {seconds}, normal again.",
    "🔁 Через 5 мин: всё ещё медленно ({seconds}).": "🔁 After 5 min: still slow ({seconds}).",
    "🔁 Через 5 мин: теперь вообще не открывается — напишу отдельно.": "🔁 After 5 min: now it does not open at all — I will write separately.",
    "с {ups} из {total} внешних серверов": "from {ups} of {total} external servers",
    "с внешних серверов": "from external servers",
    "🟠 {label} не открылся с моего сервера, но открывается {where}. Если посетители сайта в том же регионе, что и мой "
    "сервер, у них он, скорее всего, тоже не открывается (блокировка, сбой у провайдера или CDN); если нет — дело в сети "
    "моего сервера. Тревогу со звонком не поднимаю; напишу, когда снова откроется.":
        "🟠 {label} did not open from my server, but opens {where}. If the site's visitors are in the same region as my "
        "server, it most likely does not open for them either (a block, a provider or CDN outage); if not — it is my "
        "server's network. No ringing alert; I will write when it opens again.",
    "🟠 {label} открывается медленно: {seconds} вместо обычных долей секунды, {n} {checks} подряд.":
        "🟠 {label} opens slowly: {seconds} instead of the usual fraction of a second, {n} {checks} in a row.",
    "✅ {label} снова открывается за {seconds}. Ничего делать не нужно.": "✅ {label} opens in {seconds} again. Nothing to do.",
    "✅ Сертификат {label} обновлён, всё в порядке. Ничего делать не нужно.": "✅ The certificate of {label} is renewed, all is well. Nothing to do.",
    "✅ Домен {domain} продлён, всё в порядке. Ничего делать не нужно.": "✅ The domain {domain} is renewed, all is well. Nothing to do.",
    "✅ На {label} все ссылки снова работают.": "✅ All links on {label} work again.",
    "✅ На {label} снимаю тревогу по ссылкам: {n} {links} сейчас {open}, но это похоже на временный сбой на той стороне. "
    "Перепроверю через несколько часов и напишу, только если {still} и тогда.":
        "✅ Clearing the links alert on {label}: {n} {links} {open} right now, but it looks like a temporary failure on "
        "the other side. I will re-check in a few hours and write only if {still} then too.",
    "не открывается": "does not open",
    "не открываются": "do not open",
    "она не откроется": "it still does not open",
    "они не откроются": "they still do not open",
    "✅ На {label} все страницы снова отвечают без ошибок.": "✅ All pages on {label} answer without errors again.",
    "✅ {label}: поисковикам больше ничего не мешает. Ничего делать не нужно.": "✅ {label}: nothing stands in the search engines' way any more. Nothing to do.",
    "✅ {label}: запрет для поисковиков снят — сайт снова открыт для поиска. Мелкие помехи остались, они в «🔎 Поиск и ИИ».":
        "✅ {label}: the ban for search engines is lifted — the site is open to search again. Minor obstacles remain, they are in «🔎 Search and AI».",
    "сегодня": "today",
    "завтра": "tomorrow",
    "{day} в {time}": "{day} at {time}",
    "↗️ Статус {provider}": "↗️ {provider} status",
    "✉️ Почта": "✉️ Mail",
    "🛡 Безопасность": "🛡 Security",
    "👥 Скорость у посетителей": "👥 Speed for visitors",
    "🔎 Поиск и ИИ": "🔎 Search and AI",
    # ── index watch ─────────────────────────────────────────────────────────
    "Люди больше не найдут сайт через Google.\n\nЧто делать:\n• Открыть Google Search Console → Проверка URL для главной: "
    "там будет написана причина.\n• Чаще всего это noindex на странице или запрет в robots.txt — проверь «🔎 Поиск и ИИ» "
    "в боте.":
        "People will no longer find the site through Google.\n\nWhat to do:\n• Open Google Search Console → URL inspection "
        "for the homepage: the reason is written there.\n• Most often it is a noindex on the page or a ban in robots.txt — "
        "check «🔎 Search and AI» in the bot.",
    "🔴 Google не показывает {label} в поиске": "🔴 Google does not show {label} in search",
    "✅ Google снова показывает {label} в поиске. Ничего делать не нужно.": "✅ Google shows {label} in search again. Nothing to do.",
    "🔴 Google убрал {label} из поиска": "🔴 Google removed {label} from search",
    "Критическая — по классификации Яндекса сайт недоступен для робота или под санкциями.":
        "Critical — by Yandex's classification the site is unreachable for its robot or under sanctions.",
    "{icon} Яндекс.Вебмастер: {problems} на {host}": "{icon} Yandex.Webmaster: {problems} on {host}",
    "Что делать: открыть Яндекс.Вебмастер → Диагностика сайта, там каждая проблема с инструкцией.":
        "What to do: open Yandex.Webmaster → Site diagnostics, every problem comes with instructions there.",
    "↗️ Вебмастер": "↗️ Webmaster",
    "✅ Яндекс больше не видит проблем на {host}. Ничего делать не нужно.": "✅ Yandex sees no more problems on {host}. Nothing to do.",
    # ── integration health ──────────────────────────────────────────────────
    "🔌 Проверить подключение": "🔌 Check the connection",
    "🟠 {title}: не работает{why}.\nПока это не починить, {loss}.": "🟠 {title}: not working{why}.\nUntil it is fixed, {loss}.",
    "✅ {title}: снова работает.": "✅ {title}: working again.",
    # ── dead-man switch ─────────────────────────────────────────────────────
    "последний раз на связи {duration} назад": "last in touch {duration} ago",
    "на связь так и не выходит": "has never checked in",
    "🟠 «{job}» молчит: {detail}, а выходит на связь {rhythm}.": "🟠 «{job}» is silent: {detail}, though it checks in {rhythm}.",
    "⚠️ «{job}» так и не выходит на связь — похоже, не подключили.\nЖду связи {rhythm}, но её ещё не было, и я не могу "
    "сказать, работает ли «{job}».\n\nТакой бот или задача есть — нажми «Подключить», там всё по шагам. Нет — «Удалить».":
        "⚠️ «{job}» has never checked in — it seems it was not connected.\nI expect it {rhythm}, but it has not happened yet, "
        "and I cannot tell whether «{job}» works.\n\nIf such a bot or job exists — tap «Connect», it is all step by step. "
        "If not — «Delete».",
    # ── disk and containers ─────────────────────────────────────────────────
    "💾 Диск был заполнен на {pct}% — удалил в docker висячие слои образов ({layers}) и кэш сборки старше суток ({caches}): "
    "−{gb} GB, сейчас {after}%. Остановленные контейнеры и образы с тегами не трогаю; следующая очистка — не раньше чем "
    "через 12 ч.":
        "💾 The disk was {pct}% full — I removed dangling image layers ({layers}) and build cache older than a day ({caches}) "
        "in docker: −{gb} GB, now {after}%. Stopped containers and tagged images I leave alone; the next cleanup — not "
        "sooner than in 12 h.",
    "Почистить docker было нечего: место занимают не образы — смотри тома и логи (docker system df).":
        "There was nothing to clean in docker: it is not the images that take the space — look at volumes and logs (docker system df).",
    "{icon} {disk} заполнен на {pct}% ({used} из {total} GB).": "{icon} {disk} is {pct}% full ({used} of {total} GB).",
    " Почистить docker сам не смог: Docker не отвечает.": " I could not clean docker myself: Docker does not answer.",
    "✅ {disk} снова в норме: {pct}%. Ничего делать не нужно.": "✅ {disk} is back to normal: {pct}%. Nothing to do.",
    "🟠 Не могу достучаться до Docker на сервере бота — автоперезапуск контейнеров и очистка диска сейчас не работают: "
    "сокет Docker принадлежит группе root: нужна <code>ALLOW_ROOT_GROUP_SOCKET=1</code> в .env или отфильтрованный "
    "доступ через socket-proxy (docs/SETUP.md).":
        "🟠 I cannot reach Docker on the bot's server — container auto-restart and disk cleanup do not work right now: "
        "the Docker socket belongs to the root group: <code>ALLOW_ROOT_GROUP_SOCKET=1</code> in .env or filtered access "
        "through socket-proxy (docs/SETUP.md) is needed.",
    "🟠 Не могу достучаться до Docker на сервере бота — автоперезапуск контейнеров и очистка диска сейчас не работают. "
    "Обычно помогает перезапуск контейнера бота: <code>docker compose up -d</code> (сокет смонтируется заново).":
        "🟠 I cannot reach Docker on the bot's server — container auto-restart and disk cleanup do not work right now. "
        "Restarting the bot's container usually helps: <code>docker compose up -d</code> (the socket is mounted anew).",
    "🟠 AUTORESTART_CONTAINERS задан, но сокет Docker в контейнер бота не смонтирован — автоперезапуск не работает. Как "
    "подключить: «🔌 Подключения» → «🐳 Docker на хосте».":
        "🟠 AUTORESTART_CONTAINERS is set, but the Docker socket is not mounted into the bot's container — auto-restart does "
        "not work. How to connect: «🔌 Connections» → «🐳 Docker on the host».",
    "✅ Docker на сервере бота снова отвечает.": "✅ Docker on the bot's server answers again.",
    "🟠 Сервис «{label}» из AUTORESTART_CONTAINERS не найден в Docker: проверь имя (<code>docker ps -a</code>) — контейнер "
    "могли переименовать или удалить.":
        "🟠 The service «{label}» from AUTORESTART_CONTAINERS is not found in Docker: check the name (<code>docker ps -a</code>) "
        "— the container may have been renamed or removed.",
    "✅ Сервис «{label}» на сервере бота заработал после перезапуска. Ничего делать не нужно.":
        "✅ The service «{label}» on the bot's server works after the restart. Nothing to do.",
    "✅ Сервис «{label}» на сервере бота снова работает. Ничего делать не нужно.": "✅ The service «{label}» on the bot's server works again. Nothing to do.",
    "{icon} Сервис «{label}» на сервере бота {problem}. Перезапускал его {n} раза — не помогает, больше сам не трогаю: "
    "нужен человек.":
        "{icon} The service «{label}» on the bot's server {problem}. I restarted it {n} times — it does not help, I leave it "
        "alone now: a human is needed.",
    "🟠 Сервис «{label}» на сервере бота {problem}. Перезапустил его, но дождаться, заработал ли он, в этот раз не успел — "
    "он ещё запускается. Посмотрю через 10 минут и скажу, чем кончилось.":
        "🟠 The service «{label}» on the bot's server {problem}. I restarted it, but could not wait to see whether it works "
        "this time — it is still starting. I will look in 10 minutes and say how it went.",
    "✅ Сервис «{label}» на сервере бота снова работает — перезапустил его сам (было: {problem}). Ничего делать не нужно.":
        "✅ The service «{label}» on the bot's server works again — I restarted it myself (it {problem}). Nothing to do.",
    "Перезапустил, но он всё ещё нездоров.": "I restarted it, but it is still unhealthy.",
    "Сам больше не перезапускаю: {why}. Нужен человек.": "I no longer restart it myself: {why}. A human is needed.",
    "Перезапустить не удалось.": "The restart failed.",
    "🔴 Сервис «{label}» на сервере бота {problem}. {verdict}": "🔴 The service «{label}» on the bot's server {problem}. {verdict}",
    # ── escalation ──────────────────────────────────────────────────────────
    "👀 Знаю, не напоминать": "👀 I know, don't remind me",
    "Так уже {duration}. Проверяю {every} и напишу, когда в ответе снова будет как надо.":
        "For {duration} now. I check {every} and will write when the answer is right again.",
    "Так уже {duration}. Проверяю {every} и напишу, когда на странице снова будет как надо.":
        "For {duration} now. I check {every} and will write when the page is right again.",
    "🔴 {label} лежит уже {duration}: {cause}": "🔴 {label} has been down for {duration}: {cause}",
    "Проверяю {every} и напишу, как только поднимется.": "I check {every} and will write as soon as it is back.",
    "«✅ в ответе снова как надо»": "«✅ the answer is right again»",
    "«✅ на странице снова как надо»": "«✅ the page is right again»",
    "«✅ поднялся»": "«✅ back up»",
    "Чинишь — «🔧 Я чиню», замолчу на {length}. Знаешь и ждёшь — «👀 Знаю, не напоминать»: следующее сообщение будет {back}.":
        "Fixing it — «🔧 I'm fixing it», I go quiet for {length}. Know and waiting — «👀 I know, don't remind me»: the next "
        "message will be {back}.",
    "так уже {duration}": "wrong for {duration}",
    "лежит уже {duration}": "down for {duration}",
    "🔴 Всё ещё не открываются {sites}:": "🔴 Still down: {sites}:",
    "Продолжаю проверять и напишу, как только поднимутся.": "I keep checking and will write as soon as they are back.",
    "Если ты уже чинишь — нажми «🔧 Я чиню все».": "If you are already fixing it — tap «🔧 I'm fixing all».",
    # ── the bot's own health ────────────────────────────────────────────────
    "на диске с базой кончилось место": "the disk with the database is full",
    "освободи место на этом диске (например, старые копии в data/backups) или увеличь его":
        "free space on this disk (old copies in data/backups, for example) or enlarge it",
    "базу держит другая программа": "another program holds the database",
    "закрой программу, которая открыла файл базы (sqlite3, второй экземпляр бота), или перезапусти бота":
        "close the program that opened the database file (sqlite3, a second instance of the bot), or restart the bot",
    "база не принимает запись": "the database refuses writes",
    "посмотри логи бота и место на диске": "look at the bot's logs and the disk space",
    "🔴 Не могу записать в базу: {why}.\nПроверки идут и тревоги приходят, но история не сохраняется, а то, что Telegram "
    "не принял, держу только в памяти до перезапуска.\n\nЧто сделать: {fix}.":
        "🔴 I cannot write to the database: {why}.\nChecks run and alerts arrive, but the history is not saved, and what "
        "Telegram did not accept I keep only in memory until a restart.\n\nWhat to do: {fix}.",
    "✅ База снова записывается: история проверок и очередь сообщений работают. Ничего делать не нужно.":
        "✅ The database is writable again: the check history and the message queue work. Nothing to do.",
    "⚠️ Не смог запустить по расписанию: {jobs}. Остальное работает. Подробности — в логе бота.":
        "⚠️ Could not schedule: {jobs}. The rest works. Details in the bot's log.",
    "⏸ Меня не было {duration} ({start}–{end}): всё это время я не следил за сайтами и не смог бы ничего сообщить. Сейчас "
    "снова на посту.":
        "⏸ I was away for {duration} ({start}–{end}): all that time I was not watching the sites and could not have "
        "reported anything. Back on duty now.",
    "🔴 Сейчас недоступно: {sites}.": "🔴 Down right now: {sites}.",
    "⚠️ С первой попытки не открылись: {sites} — перепроверяю, напишу, если не поднимутся.":
        "⚠️ Did not open at the first try: {sites} — re-checking, I will write if they do not come back.",
    "✅ Сейчас всё работает: {n} из {total}.": "✅ Everything works now: {n} of {total}.",
    "🟠 Сторож {name} не на связи уже {duration}: {why}. Пока так, если я упаду, тебе никто не напишет.\n\nЧто делать: "
    "открой {name} и проверь, что check на месте. Удалил его или ссылка поменялась — вставь новую: кнопка ниже.":
        "🟠 The watchdog {name} has been out of touch for {duration}: {why}. While so, if I go down nobody will write to "
        "you.\n\nWhat to do: open {name} and check that the check is in place. Deleted it or the link changed — paste the "
        "new one: the button below.",
    "✅ Сторож {name} снова на связи. Ничего делать не нужно.": "✅ The watchdog {name} is in touch again. Nothing to do.",
    "🛡 Внешний сторож": "🛡 External watchdog",
    "🟠 Не удалось сохранить резервную копию базы бота. Проверь место на диске.": "🟠 Could not save a backup of the bot's database. Check the disk space.",
    "копия весит {mb} МБ, а Telegram принимает от бота файлы до 50 МБ. Полные ночные копии лежат на сервере: {dir}":
        "the copy weighs {mb} MB, and Telegram accepts files up to 50 MB from a bot. The full nightly copies are on the server: {dir}",
    "Файл {mb} МБ — больше 20 МБ, которые я могу скачать: прислать его мне обратно не получится. Восстановить можно на "
    "сервере: останови бота и положи файл на место bot.db.":
        "The file is {mb} MB — more than the 20 MB I can download: sending it back to me will not work. Restore on the "
        "server instead: stop the bot and put the file in place of bot.db.",
    "не получилось сделать копию ({error}) — чаще всего кончилось место на диске": "could not make the copy ({error}) — most often the disk is full",
    "🗄 Еженедельная копия базы бота: сайты, настройки, история. Ключей подключений и секретов в ней нет — после "
    "восстановления на новом сервере подключения нужно будет ввести заново. Чтобы восстановить — пришли мне этот файл.":
        "🗄 The weekly copy of the bot's database: sites, settings, history. It holds no connection keys or secrets — after "
        "restoring on a new server the connections must be entered again. To restore — send me this file.",
    "🟠 Еженедельную копию базы не прислал: {why}.\nНочные копии на сервере целы. Попробовать ещё раз — 🏠 Главная → "
    "«🤖 Боты» → «💾 Скачать копию базы».":
        "🟠 I did not send the weekly database copy: {why}.\nThe nightly copies on the server are intact. To try again — "
        "🏠 Home → «🤖 Bots» → «💾 Download a database copy».",
    # ── morning extras ──────────────────────────────────────────────────────
    "📈 За неделю:": "📈 This week:",
    "• остальные — без сбоев": "• the rest — no downtime",
    "📈 За неделю: ": "📈 This week: ",
    "все сайты без сбоев": "all sites without downtime",
    "без сбоев": "no downtime",
    "домен {domain} через {days} дн.": "domain {domain} in {days} d",
    "🔜 Истекает: ": "🔜 Expiring: ",
    "🔎 Поиск и ИИ: ": "🔎 Search and AI: ",
    "🔎 Поиск и ИИ: помех нет": "🔎 Search and AI: no obstacles",
    "помех нет": "no obstacles",
    "🔴 сайт закрыт от поиска": "🔴 the site is closed to search",
    "⚠️ не смог проверить": "⚠️ could not check",
    "🔎 Первый аудит {label} для поиска и ИИ: {verdict} — подробности в «🔎 Поиск и ИИ»":
        "🔎 The first search and AI audit of {label}: {verdict} — details in «🔎 Search and AI»",
    "💾 Диск сервера: {icon} занято {pct}%": "💾 Server disk: {icon} {pct}% used",
    "⏱ Скорость за неделю — где она менялась:": "⏱ Speed this week — where it moved:",
    # ── the scheduler's own account ─────────────────────────────────────────
    "проверка сайтов": "the site checks", "утренняя сводка": "the morning digest", "недельный отчёт": "the weekly report",
    "ночная очистка базы": "the nightly database cleanup", "ночная копия базы": "the nightly database copy",
    "досылка сообщений": "message redelivery",
    "прошлый запуск ещё не закончился": "the previous run has not finished yet",
    "⚠️ У меня не работает «{title}»: {why}. Остальное идёт как обычно; подробности — в логе бота.":
        "⚠️ «{title}» is not working for me: {why}. The rest goes on as usual; details in the bot's log.",
    # ── reports/scheduler.py: Yandex rights, llms.txt ──────────────────────
    "🟠 Яндекс.Вебмастер: {host} не подтверждён — Яндекс не показывает по нему статистику и ошибки и не даёт запросить переобход.":
        "🟠 Yandex.Webmaster: {host} is not verified — Yandex shows no statistics or errors for it and will not take a recrawl request.",
    "Что делать: Вебмастер → сайт → «Права доступа» → подтвердить метатегом, файлом или записью DNS. Если метатег был и пропал после редизайна — верни его в код главной.":
        "What to do: Webmaster → the site → «Access rights» → verify with a meta tag, a file or a DNS record. If the meta tag was there and disappeared in a redesign, put it back into the homepage code.",
    "✅ Яндекс.Вебмастер: {host} снова подтверждён.": "✅ Yandex.Webmaster: {host} is verified again.",
    " (в llms.txt)": " (in llms.txt)",
    # ── reports/scheduler.py: Google's chosen main copy ────────────────────
    "🟠 Google считает главной копией {label} другой адрес: {chosen}": "🟠 Google takes another address for the main copy of {label}: {chosen}",
    "Страница называет главной {user}. В поиске Google показывает свой выбор — бывает, что технический адрес или старый домен.":
        "The page names {user} as the main one. Google shows its own choice in search — sometimes a technical address or an old domain.",
    "Что делать: закрой копию noindex или переадресацией на основной адрес (для *.pages.dev — «🔎 Поиск и ИИ» подскажет как), проверь, что canonical и sitemap ведут на основной адрес, и запроси индексирование главной в Search Console.":
        "What to do: close the copy with noindex or a redirect to the main address (for *.pages.dev «🔎 Поиск и ИИ» shows how), check that canonical and the sitemap lead to the main address, and request indexing of the homepage in Search Console.",
    "✅ Google снова считает главной копией {label} адрес, который называет страница.": "✅ Google again takes the address the page names for the main copy of {label}.",
}

PLURALS: dict[str, tuple[str, str]] = {
    "ссылка": ("link", "links"),
    "проверку": ("check", "checks"),
    "проверка": ("check", "checks"),
    "падение": ("fall", "falls"),
    "сайт": ("site", "sites"),
    "клик": ("click", "clicks"),
    "показ": ("impression", "impressions"),
}
