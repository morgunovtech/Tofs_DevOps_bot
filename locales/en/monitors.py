"""English for monitors/* (see utils/i18n.py): the words monitors store in
incidents and snapshots, translated where the bot shows them."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── monitors/host_checker.py ────────────────────────────────────────────
    "Диск на сервере бота": "The bot server's disk",
    "Диск с базой бота": "The bot database's disk",
    "остановлен": "is stopped",
    "перезапускается снова и снова": "keeps restarting",
    "не запускается (dead)": "does not start (dead)",
    "создан, но не запущен": "is created but not started",
    "на паузе": "is paused",
    "не проходит проверку здоровья (healthcheck)": "fails its health check (healthcheck)",
    # ── monitors/region.py ──────────────────────────────────────────────────
    "снаружи": "from outside",
    "из страны {code}": "from {code}",
    "из России": "from Russia", "из Украины": "from Ukraine", "из Казахстана": "from Kazakhstan", "из Германии": "from Germany",
    "из Нидерландов": "from the Netherlands", "из Франции": "from France", "из Британии": "from Britain", "из США": "from the US",
    "из Польши": "from Poland", "из Турции": "from Turkey", "из Бразилии": "from Brazil", "из Японии": "from Japan",
    "из Индии": "from India", "из Израиля": "from Israel", "из Швейцарии": "from Switzerland", "из Финляндии": "from Finland",
    "из Италии": "from Italy", "из Чехии": "from Czechia", "из Испании": "from Spain", "из Португалии": "from Portugal",
    "из Болгарии": "from Bulgaria", "из Гонконга": "from Hong Kong", "из Молдовы": "from Moldova", "из Сербии": "from Serbia",
    "из Швеции": "from Sweden", "из Литвы": "from Lithuania", "из Ирана": "from Iran", "из Венгрии": "from Hungary",
}

PLURALS: dict[str, tuple[str, str]] = {}
