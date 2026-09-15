"""Human language for everything the bot says about a problem.

Monitors keep short technical strings ("HTTP 502", "Timeout (15s)",
"SSL expires in 5 days"); this module turns them into what a person who
has never heard of DNS needs: what happened, what it means for visitors,
what it usually is, and what to do. Pure functions, unit-tested.
"""

import re
from dataclasses import dataclass

# ── Problem types (incident check_type → words) ──────────────────────────────

PROBLEM_TYPES = {
    "availability": "не открывается",
    "performance": "медленно отвечает",
    "ssl": "сертификат",
    "domain": "домен",
    "links": "битые ссылки",
    "deep": "ошибки на страницах",
    "seo": "видимость в поиске",
}


def problem_type(check_type: str) -> str:
    return PROBLEM_TYPES.get(check_type, check_type)


# ── Short phrases for technical error strings ────────────────────────────────

def fmt_seconds(ms: int | float | None) -> str:
    if ms is None:
        return "—"
    s = ms / 1000
    return f"{s:.2f} с" if s < 1 else (f"{s:.1f} с" if s < 10 else f"{s:.0f} с")


def speed(ms: int | float | None) -> str:
    """'быстро (0.12 с)' · 'нормально (0.8 с)' · 'медленно (3 с)'."""
    if ms is None:
        return "нет данных"
    label = "быстро" if ms < 500 else ("нормально" if ms < 2000 else "медленно")
    return f"{label} ({fmt_seconds(ms)})"


def _http(code: int) -> str:
    if code >= 500:
        return f"хостинг отвечает ошибкой {code}"
    if code == 404:
        return "страница не найдена (ошибка 404)"
    if code in (401, 403):
        return f"доступ закрыт (ошибка {code})"
    if code == 429:
        return "сервер просит подождать (ошибка 429, слишком много запросов)"
    return f"сервер отверг запрос (ошибка {code})"


_RULES: list[tuple[re.Pattern, object]] = [
    (re.compile(r"^Site down$"), lambda m: "не открывается"),
    (re.compile(r"^(?:Site down: )?HTTP (\d{3})$"), lambda m: _http(int(m.group(1)))),
    (re.compile(r"^(?:Site down: )?Timeout \((\d+)s\)$"), lambda m: f"не ответил за {m.group(1)} секунд"),
    (re.compile(r"^(?:Site down|Bad content): (.+)$", re.S), lambda m: describe_error(m.group(1))),
    (re.compile(r"^SSL expired (\d+) days ago$"), lambda m: f"сертификат истёк {m.group(1)} дн. назад"),
    (re.compile(r"^SSL expires in (\d+) days!?$"), lambda m: f"сертификат истекает через {m.group(1)} дн."),
    (re.compile(r"^SSL certificate has expired$"), lambda m: "сертификат истёк"),
    (re.compile(r"^SSL certificate invalid: (.+)$", re.S), lambda m: f"сертификат недействителен: {m.group(1)}"),
    (re.compile(r"^SSL check failed: (.+)$", re.S), lambda m: "не удалось проверить сертификат"),
    (re.compile(r"^Domain expired (\d+) days ago!?$"), lambda m: f"домен истёк {m.group(1)} дн. назад"),
    (re.compile(r"^Domain expires in (\d+) days!?$"), lambda m: f"домен истекает через {m.group(1)} дн."),
    (re.compile(r"^Lookup failed: .+$", re.S), lambda m: "не удалось узнать срок домена"),
    (re.compile(r"^Slow responses: ~(\d+)ms$"), lambda m: f"открывается за {fmt_seconds(int(m.group(1)))}"),
    (re.compile(r"^(\d+) broken (?:internal )?link\(s\)"), lambda m: f"{m.group(1)} ссылок ведут в никуда"),
    (re.compile(r"^5xx на (\d+) из (\d+) проверенных страниц$"),
     lambda m: f"{m.group(1)} из {m.group(2)} проверенных страниц отдают ошибку"),
    (re.compile(r"^Could not fetch page$"), lambda m: "страница не загрузилась"),
    (re.compile(r"name or service not known|nodename nor servname|does not resolve|"
                r"name resolution|хост не резолвится|Temporary failure in name", re.I),
     lambda m: "адрес сайта не находится (проблема с DNS)"),
    (re.compile(r"connection refused|Connect call failed", re.I), lambda m: "сервер отказал в соединении"),
    (re.compile(r"cannot connect to host", re.I), lambda m: "не удалось соединиться с сервером"),
    (re.compile(r"certificate verify failed|SSL: ", re.I), lambda m: "сертификат не прошёл проверку"),
    (re.compile(r"хост не отвечает на ping"), lambda m: "не отвечает на ping"),
]


def link_reason(status_code: int | None, error: str | None) -> str:
    """Why one link is broken: '404' → 'страница не найдена (ошибка 404)'."""
    if status_code:
        return _http(status_code)
    return describe_error(error)


def describe_error(text: str | None) -> str:
    """Technical error / incident message → short human phrase (Russian
    text that is already human passes through unchanged)."""
    if not text:
        return "неизвестная ошибка"
    text = text.strip()
    for pattern, render in _RULES:
        m = pattern.search(text)
        if m:
            return render(m)
    return text


# ── Explanations: meaning, usual cause, steps ────────────────────────────────

@dataclass(frozen=True)
class Explanation:
    key: str
    meaning: str            # what it means for visitors
    cause: str              # what it usually is
    steps: tuple[str, ...]  # what to do


_E = Explanation
EXPLANATIONS: dict[str, Explanation] = {
    "http_5xx": _E("http_5xx",
                   "Посетители видят страницу с ошибкой вместо сайта.",
                   "хостинг отвечает ошибкой сервера — сбой на стороне хостинга, упавший деплой или "
                   "закончившиеся лимиты тарифа",
                   ("Подождать 5 минут: я проверяю каждую минуту и напишу, как только сайт поднимется.",
                    "Если не поднимется — открыть панель хостинга, посмотреть последний деплой и "
                    "перезапустить или откатить его.",
                    "Если панель тоже не открывается — проверить статус хостинга и написать в его поддержку.")),
    "http_4xx": _E("http_4xx",
                   "Посетители получают «страница не найдена» или «доступ закрыт».",
                   "адрес главной страницы изменился, включена защита паролем, или хостинг закрыл сайт",
                   ("Открыть сайт в браузере и посмотреть, что показывается.",
                    "Если сайт намеренно закрыт паролем — в настройках сайта в боте разрешить этот код ответа.",
                    "Если адрес изменился — обновить адрес сайта в боте.")),
    "timeout": _E("timeout",
                  "Сайт грузится так долго, что браузеры и посетители не дожидаются.",
                  "сервер перегружен, завис или отвечает из-за границы очень медленно",
                  ("Подождать 5 минут — часто это проходит само.",
                   "Если нет — перезапустить приложение в панели хостинга.",
                   "Если повторяется каждый день — тариф или сервер не тянут нагрузку.")),
    "dns": _E("dns",
              "Браузер вообще не находит сайт по адресу — как будто его нет.",
              "истёк домен, сломались DNS-записи или домен переехал",
              ("Проверить в панели регистратора, что домен оплачен и не истёк.",
               "Проверить DNS-записи у регистратора или в Cloudflare: должна быть запись A или CNAME на хостинг.",
               "Если ты недавно менял хостинг — DNS-изменения доходят до часа.")),
    "refused": _E("refused",
                  "Сайт не открывается ни у кого.",
                  "сервер работает, но на нём ничего не запущено на нужном порту — упало приложение или веб-сервер",
                  ("Перезапустить приложение или контейнер в панели хостинга.",
                   "Посмотреть логи: обычно причина видна в последних строках.")),
    "connect": _E("connect",
                  "Сайт не открывается ни у кого.",
                  "сервер выключен, перезагружается или закрыт файрволом",
                  ("Проверить в панели хостинга, что сервер запущен.",
                   "Если только что был деплой — подождать пару минут.")),
    "ssl_expiring": _E("ssl_expiring",
                       "Пока всё работает. Если сертификат не продлится, браузеры покажут посетителям "
                       "красное предупреждение «соединение не защищено», и большинство уйдёт.",
                       "обычно сертификат продлевается сам за 2–4 недели до срока; раз этого не случилось, "
                       "автопродление сломалось или отключено",
                       ("Открыть панель хостинга или Cloudflare → SSL/TLS и проверить, включено ли автопродление.",
                        "Если сайт на своём сервере — проверить, что работает certbot (systemctl status certbot.timer).",
                        "Я напишу снова за 7, 3 и 1 день до срока.")),
    "ssl_expired": _E("ssl_expired",
                      "Посетители видят красное предупреждение «соединение не защищено» и не могут зайти.",
                      "сертификат не продлился в срок",
                      ("Продлить или перевыпустить сертификат в панели хостинга или Cloudflare → SSL/TLS.",
                       "На своём сервере: certbot renew, затем перезапуск веб-сервера.")),
    "ssl_invalid": _E("ssl_invalid",
                      "Посетители видят красное предупреждение «соединение не защищено».",
                      "сертификат выдан на другой адрес, повреждён или сайт переехал, а сертификат нет",
                      ("Проверить в панели хостинга, на какой адрес выдан сертификат.",
                       "Перевыпустить сертификат для этого адреса.")),
    "domain_expiring": _E("domain_expiring",
                          "Пока всё работает. После истечения сайт и почта на домене перестанут работать, "
                          "а через несколько недель домен смогут купить другие.",
                          "не продлён домен — письмо регистратора ушло в спам или отвязалась карта",
                          ("Продлить домен в панели регистратора (обычно на год, это 5–15 минут).",
                           "Включить там автопродление, чтобы это не повторилось.")),
    "domain_expired": _E("domain_expired",
                         "Сайт и почта на домене не работают.",
                         "домен не продлён в срок",
                         ("Срочно продлить домен у регистратора: обычно есть льготный период 30 дней, "
                          "пока домен ещё можно вернуть.",)),
    "keyword_missing": _E("keyword_missing",
                          "Сайт отвечает, но показывает не то: обычно пустую страницу или ошибку приложения.",
                          "упал бэкенд при живом веб-сервере, деплой выкатил пустую версию, или на страницу "
                          "поставили заглушку",
                          ("Открыть сайт в браузере и посмотреть, что показывается.",
                           "Если страница пустая — откатить последний деплой.",
                           "Если фразу изменили специально — обновить её в настройках сайта в боте.")),
    "stop_phrase": _E("stop_phrase",
                      "Сайт отвечает, но посетители видят текст ошибки.",
                      "приложение или база данных упали, а страница с ошибкой всё равно отдаётся",
                      ("Открыть сайт и прочитать ошибку.",
                       "Перезапустить приложение или базу в панели хостинга, откатить деплой.")),
    "slow": _E("slow",
               "Сайт открывается, но посетители ждут по несколько секунд, часть уходит.",
               "перегружен хостинг, тяжёлая страница или проблемы на пути к серверу",
               ("Подождать: часто это временная нагрузка.",
                "Если продолжится — посмотреть нагрузку в панели хостинга или написать в поддержку.",
                "Если так всегда — поставить сайт за Cloudflare или перейти на тариф побольше.")),
    "links": _E("links",
                "Часть ссылок на сайте ведёт на «страница не найдена».",
                "страницы переименовали или удалили, а ссылки на них остались",
                ("Открыть перечисленные ссылки и исправить их или убрать.",
                 "Если страницы переехали — настроить редирект со старого адреса на новый.")),
    "deep": _E("deep",
               "Главная работает, но часть страниц отдаёт ошибку.",
               "сломан раздел сайта, база данных или один из сервисов",
               ("Открыть перечисленные страницы в браузере.",
                "Посмотреть логи хостинга за последний час.",
                "Откатить последний деплой, если ошибки появились после него.")),
    "seo_critical": _E("seo_critical",
                       "Сайт исчезает из Google и Яндекса: страницы перестанут находиться в поиске.",
                       "в код попал запрет индексации (noindex) или robots.txt закрыл сайт от поисковиков — "
                       "обычно из-за настроек тестовой версии, ушедших в прод",
                       ("Убрать <meta name=\"robots\" content=\"noindex\"> и заголовок X-Robots-Tag со страниц.",
                        "Проверить robots.txt: не должно быть Disallow: / для Googlebot и YandexBot.",
                        "После исправления переиндексация занимает от нескольких дней.")),
    "seo_warning": _E("seo_warning",
                      "Сайт находится в поиске, но его можно показать лучше.",
                      "мелкие недочёты разметки страниц",
                      ("Открыть «🔍 Поиск и ИИ» в боте: там каждый пункт с пояснением.",)),
    "dns_change": _E("dns_change",
                     "Пока ничего не сломалось, но адрес сайта в DNS изменился.",
                     "ты или кто-то с доступом поменял DNS: переезд хостинга, настройка почты, или чужие руки",
                     ("Если это ты менял — ничего делать не нужно, я запомнил новое значение.",
                      "Если нет — зайти в панель регистратора или Cloudflare и проверить, кто и что менял.")),
    "ns_change": _E("ns_change",
                    "Сменились серверы, которые управляют всем доменом. Так выглядит угон домена или "
                    "переезд к другому DNS-провайдеру.",
                    "перенос домена к другому провайдеру или взлом аккаунта регистратора",
                    ("Если это не ты — срочно зайти в аккаунт регистратора, сменить пароль и вернуть NS-серверы.",
                     "Включить двухфакторную защиту у регистратора.")),
    "heartbeat": _E("heartbeat",
                    "Задача по расписанию (бэкап, синхронизация) не отчиталась вовремя.",
                    "крон не запустился, скрипт упал или сервер задачи выключен",
                    ("Проверить на сервере, запускается ли задача, и посмотреть её лог.",
                     "Запустить задачу вручную и убедиться, что она дошла до конца.")),
    "disk": _E("disk",
               "Скоро закончится место на сервере бота: перестанут писаться логи и база.",
               "накопились логи, старые docker-образы или бэкапы",
               ("Удалить старые образы и контейнеры: docker system prune.",
                "Посмотреть, что занимает место: du -sh /var/lib/docker /var/log.")),
    "container": _E("container",
                    "Один из сервисов на сервере бота не работает.",
                    "контейнер упал и не смог перезапуститься",
                    ("Посмотреть логи контейнера: docker logs <имя>.",
                     "Перезапустить руками: docker restart <имя>.")),
    "unknown": _E("unknown",
                  "Сайт недоступен для посетителей.",
                  "необычная ошибка, которую я не смог распознать",
                  ("Открыть сайт в браузере и посмотреть, что показывается.",
                   "Проверить панель хостинга.")),
}


def classify(check_type: str, message: str | None) -> str:
    """Explanation key for an incident / alert."""
    msg = (message or "").lower()
    if check_type == "availability":
        if "стоп-фраза" in msg:
            return "stop_phrase"
        if "нет фразы" in msg:
            return "keyword_missing"
        m = re.search(r"http (\d{3})", msg)
        if m:
            return "http_5xx" if int(m.group(1)) >= 500 else "http_4xx"
        if "timeout" in msg or "не ответил" in msg:
            return "timeout"
        if any(s in msg for s in ("name or service", "nodename", "resolve", "name resolution", "dns")):
            return "dns"
        if "refused" in msg or "отказал" in msg:
            return "refused"
        if "connect" in msg or "соедин" in msg:
            return "connect"
        return "unknown"
    if check_type == "ssl":
        if "expired" in msg or "истёк" in msg:
            return "ssl_expired"
        if "invalid" in msg or "недействит" in msg:
            return "ssl_invalid"
        return "ssl_expiring"
    if check_type == "domain":
        return "domain_expired" if ("expired" in msg or "истёк" in msg) else "domain_expiring"
    return {"performance": "slow", "links": "links", "deep": "deep",
            "seo": "seo_critical"}.get(check_type, "unknown")


def explain(check_type: str, message: str | None) -> Explanation:
    return EXPLANATIONS[classify(check_type, message)]


def steps_block(expl: Explanation) -> str:
    return "Что делать:\n" + "\n".join(f"• {s}" for s in expl.steps)


# ── Uptime in minutes, not percent ───────────────────────────────────────────

def downtime(total: int, ok: int, interval_min: float) -> str:
    """'без сбоев' · 'недоступен ≈12 мин' — from check counts and cadence."""
    if not total:
        return "нет данных"
    failed = max(0, total - ok)
    if not failed:
        return "без сбоев"
    minutes = failed * interval_min
    if minutes < 60:
        return f"недоступен ≈{max(1, round(minutes))} мин"
    hours, rest = divmod(round(minutes), 60)
    if hours < 24:
        return f"недоступен ≈{hours} ч" + (f" {rest} мин" if rest else "")
    days, h = divmod(hours, 24)
    return f"недоступен ≈{days} дн" + (f" {h} ч" if h else "")


def incident_headline(check_type: str, message: str | None, label: str) -> str:
    """'example.com не открывается: хостинг отвечает ошибкой 502'."""
    what = describe_error(message)
    if check_type == "availability":
        return f"{label} не открывается: {what}" if not what.startswith("сертификат") else f"{label}: {what}"
    if check_type == "performance":
        return f"{label} {what}" if what.startswith("открывается") else f"{label} медленно отвечает"
    if check_type in ("ssl", "domain"):
        return f"{label}: {what}"
    if check_type == "links":
        return f"{label}: {what}"
    if check_type == "deep":
        return f"{label}: {what}"
    if check_type == "seo":
        return f"{label}: {what}"
    return f"{label}: {what}"


# ── Registrar panels (from RDAP registrar names) ─────────────────────────────

_REGISTRARS: list[tuple[str, str]] = [
    ("cloudflare", "https://dash.cloudflare.com/?to=/:account/domains"),
    ("namecheap", "https://ap.www.namecheap.com/domains/list/"),
    ("godaddy", "https://dcc.godaddy.com/control/portfolio"),
    ("regru", "https://www.reg.ru/user/domains"), ("reg.ru", "https://www.reg.ru/user/domains"),
    ("ru-center", "https://www.nic.ru/manager/"), ("nic.ru", "https://www.nic.ru/manager/"),
    ("timeweb", "https://timeweb.cloud/my/domains"),
    ("beget", "https://cp.beget.com/domains"),
    ("gandi", "https://admin.gandi.net/domain"),
    ("ovh", "https://www.ovh.com/manager/"),
    ("hostinger", "https://hpanel.hostinger.com/domains"),
    ("name.com", "https://www.name.com/account/domain"),
    ("porkbun", "https://porkbun.com/account/domainsSpeedy"),
    ("ionos", "https://my.ionos.com/domains"),
    ("squarespace", "https://account.squarespace.com/domains"),
    ("tucows", "https://www.hover.com/domains"), ("hover", "https://www.hover.com/domains"),
    ("dynadot", "https://www.dynadot.com/domain/manage"),
    ("google", "https://domains.squarespace.com/"),
]


def registrar_url(registrar: str | None) -> str | None:
    """Panel link for a registrar name as RDAP/WHOIS reports it."""
    name = (registrar or "").lower()
    if not name or name == "unknown":
        return None
    for needle, url in _REGISTRARS:
        if needle in name:
            return url
    return None
