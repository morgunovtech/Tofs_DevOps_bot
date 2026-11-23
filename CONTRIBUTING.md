# Contributing

Thanks for looking. This is a personal bot shared as-is; issues and pull requests are welcome. The bot speaks Russian or English to its owner (`BOT_LANG`; Russian is the source language of every string, English lives in `locales/en/`) and most of the long docs are Russian (`docs/FEATURES.md`, `docs/SETUP.md`, `docs/WHY.md`, `docs/DEVELOPMENT.md`); `docs/FEATURES.en.md` is the English inventory of everything it does.

## Before you open an issue

- **A bug**: say what you did, what the bot answered (or didn't), and the relevant log lines (`docker compose logs -f` or the Railway log). Never paste tokens, `.env` or the database.
- **A check or a report you miss**: say what you would want to be woken up for and what can wait until the morning — the bot's rule is to stay silent while everything is fine and to ring only for what needs a person now.
- **A security problem**: don't open an issue, see [SECURITY.md](SECURITY.md).

## Working on the code

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pip install --require-hashes --no-deps -r requirements.txt   # exactly what the image gets
ruff check .          # lint + import order
pytest -q             # no network, no Telegram
pip-audit -r requirements.txt --strict
```

Python 3.12–3.14 (the upper bound comes from the pinned aiogram). The tests never touch the network: `tests/conftest.py` closes sockets, Telegram is a fake bot, every external API is a local server. CI runs the same on all three Python versions, plus `pip-audit` for the lock and the dev tools, a build and a run of the production image, and an arm64 build in PRs. Dependencies: direct pins live in `requirements.in`, the hashed lock in `requirements.txt` — how to regenerate it is in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

Where things live:

- `monitors/` — one checker per thing checked; `services/` — Cloudflare, Docker, Search Console, Yandex, screenshots; `reports/` — the scheduler and the digests; `web/` — webhooks, the status page, the widget; `handlers/` — the chat; `db/` — SQLite.
- Every proactive message goes through `services/notifier.py` (priorities, mute, quiet hours); nobody else decides whether to ring.
- The words of an alert come from `services/humanize.py`: headline → what it means → what to do → the technical line last. Numbers, not adjectives.
- Screens and buttons are named in the docs exactly as the bot names them; `tests/test_docs.py` checks links, screen names and that `cp .env.example .env` changes nothing but the token.
- The principles shared with the owner's other bots (icons, keyboard, alert anatomy, notification policy, self-maintenance) are summarised in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md); a change of behaviour starts there.

Open the pull request against `main`; CI must be green. Keep `README.md`, `README.ru.md` and `docs/FEATURES.md` in step with what you change.
