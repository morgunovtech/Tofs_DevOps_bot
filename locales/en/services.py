"""English for services/* (see utils/i18n.py)."""

from __future__ import annotations

STRINGS: dict[str, str] = {
    # ── services/humanize.py ────────────────────────────────────────────────
    "{seconds} с": "{seconds} s",
    # ── services/notifier.py ────────────────────────────────────────────────
    "Если сайт ляжет — всё равно позвоню. Остальное пришлю сводкой, когда тишина закончится.":
        "If a site goes down, I ring anyway. The rest comes as a digest when the silence ends.",
    "Все 🔴 — сайт лёг или снова открылся, истекает сертификат, открыт .env — придут сразу, со звуком. "
    "Остальное пришлю сводкой, когда тишина закончится. Будить только когда сайт лёг: "
    "⚙️ Настройки → 🔔 Когда писать.":
        "Everything 🔴 — a site down or up again, an expiring certificate, an exposed .env — comes at once, with "
        "a sound. The rest comes as a digest when the silence ends. To be woken only when a site is down: "
        "⚙️ Settings → 🔔 When to write.",
}

PLURALS: dict[str, tuple[str, str]] = {}
