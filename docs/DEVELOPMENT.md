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
| Порядок в GitHub в недельном отчёте | `GITHUB_TOKEN`, `GITHUB_OWNER` (необязательно) |
| Перезапуск контейнеров / очистка диска | `AUTORESTART_CONTAINERS` + монтирование docker.sock |
| Внешний сторож | `SELF_HEARTBEAT_URL` |
| Telegram через прокси (сервер в России) | `TELEGRAM_PROXY` |

## Шаблон Railway (кнопка «Deploy on Railway»)

Кнопка в README и SETUP ведёт на шаблон https://railway.com/deploy/lVRuwW — он живёт в Railway-воркспейсе владельца (Workspace → Templates → TofsDevOps) и собран с нуля, не из боевого проекта: иначе в шаблон попали бы значения переменных, включая токен. Что в нём:

| Что | Значение |
|---|---|
| Сервис | GitHub-репозиторий `morgunovtech/Tofs_DevOps_bot`, ветка `main` |
| `TELEGRAM_BOT_TOKEN` | обязательная, пустая, с подсказкой «токен от @BotFather» |
| `DB_PATH` | `/app/data/bot.db` |
| `TRUST_PROXY` | `1` |
| Диск | `/app/data` |
| Сеть | HTTP proxy на порт `8080` |

Поменялись переменные или порт в коде — поправь и шаблон (Workspace → Templates → TofsDevOps → Apply). В маркетплейс Railway шаблон не опубликован: кнопка работает и так.

## Статус от другого бота

Бот, который отмечается у TofsDevOps (⏰ «Бот или задача по расписанию»), может вместе с отметкой прислать, что он видит, — строки появятся на главном экране, в карточке задачи и в утренней сводке. Тот же адрес отметки, только `POST` с JSON:

```
POST https://<бот>/api/heartbeat/<HEARTBEAT_SECRET>/<задача>
Content-Type: application/json

{"title": "📣 Соцсети",
 "items": [{"name": "Telegram-канал", "level": "ok", "text": "12 340, +18 за сутки"},
           {"name": "LinkedIn", "level": "warning", "text": "cookie истекает через 3 дн."}],
 "open_url": "https://t.me/social_tracker_bot",
 "heartbeat": true}
```

- `items` — от 1 до 10; `name` до 40 символов, `text` до 120; `level` — `ok` ✅, `warning` 🟠, `critical` 🔴, `unknown` ⚠️.
- `title` (до 40) — заголовок блока; `open_url` (https) — кнопка «↗️ Открыть» на карточке.
- `heartbeat: false` — только обновить строки, не засчитывая отметку: например, проверка каждые 30 минут при ежедневной отметке «сбор прошёл».
- Плохой JSON — `400` с причиной и без отметки: отправитель увидит ошибку в логах, а молчание задачи — тревогой. Статус хранится только для задач, которые TofsDevOps ждёт.
- Строки только показываются: тревоги и подробные экраны остаются у самого бота. Если бот замолчал дольше интервала, вместо его строк — «⚠️ молчит — не знаю, что сейчас».

## Архитектура

```
main.py                 точка входа: БД → сервисы → веб-сервер → планировщик → polling
├── monitors/           доступность, ssl, домены (rdap), dns, ссылки, 5xx,
│                       seo/geo, хост — типизированные результаты, общая лестница алертов
├── services/           notifier (единственная дверь для сообщений: приоритеты,
│                       mute, тихие часы, паузы), settings, maintenance, secrets,
│                       runtime (админ), updates (контроль зависимостей),
│                       действия Cloudflare, Docker API, скриншоты, GSC, Яндекс,
│                       порядок в GitHub
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
pytest -q             # ~150 тестов, без сети и без Telegram
pip-audit -r requirements.txt
```

CI гоняет те же три команды на каждый push и PR (Python 3.12, 3.13 и 3.14). Dependabot по субботам открывает один сгруппированный PR; `.github/workflows/dependabot-automerge.yml` автоматически вливает минорные и патч-обновления после зелёного CI — включи **Allow auto-merge** в настройках репозитория и защити `main` правилом с обязательной проверкой «lint · test · audit», иначе мерж не будет ждать тестов. Контейнер работает от непривилегированного пользователя; `entrypoint.sh` сначала чинит владельца data-тома.
