# TofsDevOps — свой сервер, архитектура, разработка

Продолжение [README.ru.md](../README.ru.md) для тех, кто ставит бота на свой сервер или хочет менять код. Установка на Railway по кликам — в [SETUP.md](SETUP.md), возможности — в [FEATURES.md](FEATURES.md).

## Свой сервер (Docker Compose)

Нужны: Docker и токен бота от [@BotFather](https://t.me/BotFather).

```bash
git clone https://github.com/morgunovtech/Tofs_DevOps_bot.git
cd Tofs_DevOps_bot
cp .env.example .env
# Заполни ОДНО значение: TELEGRAM_BOT_TOKEN (от @BotFather)
docker compose up -d --build
```

Отправь боту `/start` — первый пользователь становится админом, и бот сам
проведёт через добавление первого сайта. Сайты, время отчётов, тихие часы и
heartbeat-джобы управляются из чата; `.env` нужен только для опциональных
интеграций ниже (задокументированы в [.env.example](.env.example)):

| Что включается | Переменные |
|---|---|
| Кнопка передеплоя + автопередеплой | `DEPLOY_HOOKS`, `AUTO_REDEPLOY` |
| Кнопка сброса кэша Cloudflare | `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ZONE_ID` |
| Dead-man switch | `HEARTBEAT_JOBS` |
| Статус индексации Google | `GSC_SERVICE_ACCOUNT_FILE`, `GSC_PROPERTY` |
| Статус индексации Яндекса | `YANDEX_WEBMASTER_TOKEN` |
| Перезапуск контейнеров / очистка диска | `AUTORESTART_CONTAINERS` + монтирование docker.sock |
| Внешний сторож | `SELF_HEARTBEAT_URL` |

## Архитектура

```
main.py                 точка входа: БД → сервисы → веб-сервер → планировщик → polling
├── monitors/           доступность, ssl, домены (rdap), dns, ссылки, 5xx,
│                       seo/geo, хост — типизированные результаты, общая лестница алертов
├── services/           notifier (единственная дверь для сообщений: приоритеты,
│                       mute, тихие часы, паузы), settings, maintenance, secrets,
│                       runtime (админ), updates (контроль зависимостей),
│                       действия Cloudflare, Docker API, скриншоты, GSC, Яндекс
├── reports/            планировщик (19 джоб), форматтеры, ASCII-недельный отчёт
├── handlers/           по роутеру на экран за одним AdminFilter
├── web/                aiohttp: API фидбека, heartbeat, статус-страница + JSON
│                       + бейджи, виджет; HTML-шаблон в web/templates
├── db/                 aiosqlite, WAL, миграции через PRAGMA user_version,
│                       ретенция, бэкапы
├── utils/              url/текст/время — общие помощники для всего выше
└── tests/              pytest: чистые функции, БД, мониторы против локального
                        HTTP-сервера, правила notifier, веб-эндпоинты, экраны бота
```

Стек: Python 3.12+, [aiogram 3](https://github.com/aiogram/aiogram), aiohttp, APScheduler, SQLite (WAL), `zoneinfo` из stdlib. Внешних систем мониторинга нет — бот и *есть* мониторинг. Графических библиотек тоже нет: графики — моноширинный текст.

Про модель безопасности виджета: «секрет» виджета уезжает каждому посетителю сайта, поэтому endpoint считается публичным — защита строится на rate limiting и жёстких лимитах размера, а не на токене. Именно поэтому у heartbeat-эндпоинта *другой* секрет. Осознанный и задокументированный компромисс для инструмента личного масштаба.

## Разработка

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check .          # линтер + порядок импортов
pytest -q             # ~70 тестов, без сети и без Telegram
pip-audit -r requirements.txt
```

CI гоняет те же три команды на каждый push и PR (Python 3.12 и 3.13). Dependabot по субботам открывает один сгруппированный PR; `.github/workflows/dependabot-automerge.yml` автоматически вливает минорные и патч-обновления после зелёного CI — включи **Allow auto-merge** в настройках репозитория и защити `main` правилом с обязательной проверкой «lint · test · audit», иначе мерж не будет ждать тестов. Контейнер работает от непривилегированного пользователя; `entrypoint.sh` сначала чинит владельца data-тома.
