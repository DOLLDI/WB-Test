
# ProxyApiBots

MVP, который предоставляет webhook-эндпойнты для Telegram и VK, перенаправляет сообщения пользователей в Proxy API (ИИ) и отправляет ответы обратно.


## Структура проекта и назначение директорий

```
ProxyApiBots/
├── app/
│   ├── main.py                # Точка входа FastAPI-приложения
│   ├── routers/               # FastAPI-роутеры для интеграции с внешними платформами
│   │   ├── telegram_router.py # Webhook для Telegram
│   │   └── vk_router.py       # Webhook для VK
│   ├── platforms/             # Платформенные реализации ботов и логики
│   │   ├── telegram/
│   │   │   ├── aiogram_bot.py # Основной aiogram-бот, FSM, рассылка, UX для Telegram
│   │   │   ├── handlers.py    # Обработка входящих Telegram-сообщений (webhook)
│   │   │   ├── bot.py         # Отправка сообщений через Telegram API
│   │   │   └── broadcast.py   # Модуль рассылки для Telegram
│   │   ├── vk/
│   │   │   ├── handlers.py    # Обработка событий VK (webhook)
│   │   │   ├── bot.py         # Отправка сообщений через VK API
│   │   │   ├── vk_utils.py    # Вспомогательные функции для VK (edit_message и др.)
│   │   │   └── broadcast.py   # Модуль рассылки для VK
│   ├── services/              # Сервисы и утилиты, общие для всего проекта
│   │   ├── config.py          # Загрузка конфигов из .env и .env.prompts
│   │   ├── db.py              # Работа с SQLite: пользователи, история, статистика
│   │   ├── logger.py          # Инициализация логирования
│   │   ├── prompts.py         # Получение системных промптов для ИИ
│   │   └── error_logger.py    # Логирование ошибок ProxyAPI
│   └── shared/
│       └── types.py           # Общие типы данных (pydantic-схемы для VK/Telegram)
├── requirements.txt           # Зависимости проекта
├── Dockerfile                 # Docker-сборка
├── .env                       # Основные переменные окружения (токены, ключи, URL)
├── .env.prompts               # Системные промпты для ИИ (можно менять без кода)
```

### Кратко по основным файлам и папкам:
- **app/main.py** — точка входа, инициализация FastAPI, подключение роутеров.
- **app/routers/** — роутеры для интеграции с Telegram и VK (webhook endpoints).
- **app/platforms/telegram/** — вся логика Telegram-бота: FSM, рассылка, обработка сообщений, отправка сообщений, интеграция с ProxyAPI.
- **app/platforms/vk/** — вся логика VK-бота: обработка событий, отправка и редактирование сообщений, рассылка, интеграция с ProxyAPI.
- **app/services/** — сервисные модули: работа с БД, логирование, загрузка конфигов, системные промпты.
- **app/shared/types.py** — pydantic-схемы для типизации данных между слоями.
- **.env** — все ключи, токены, URL, ID админов.
- **.env.prompts** — системные инструкции для ИИ (можно менять без перезапуска кода).
- **proxyapi_errors.log** — отдельный файл для ошибок ProxyAPI и внешних API-сбоев, который читает админ-панель.
- **DB_BACKEND** и **DATABASE_URL** — переключают приложение между SQLite и PostgreSQL.
- **POSTGRES_DB**, **POSTGRES_USER**, **POSTGRES_PASSWORD** — переменные для встроенного PostgreSQL-сервиса в Docker Compose.
- **SQLITE_DB_PATH** и **ERROR_LOG_PATH** — пути к SQLite-базе и файлу ошибок, чтобы хранение можно было вынести в volume на сервере или в Docker.
- **WB_SUMMARY_PROMPT** в `.env.prompts` — отдельная инструкция для саммаризации отзывов Wildberries.
- **APP_BASE_URL**, **BILLING_SECRET**, **PAYMENT_PROVIDER**, **ADMIN_TOKEN** — настройки для sandbox-оплаты и админских инструментов.
- **requirements.txt** — список зависимостей.
- **Dockerfile** — инструкция для сборки контейнера.

Такое разделение позволяет легко масштабировать проект, добавлять новые платформы, сервисы и логику без переписывания существующего кода.

## Быстрый старт (локально)
1. Скопируйте файл `.env` и заполните значения:
   - TELEGRAM_BOT_TOKEN, VK_GROUP_TOKEN, VK_CONFIRMATION_TOKEN, PROXYAPI_URL, PROXYAPI_KEY
2. Скопируйте файл `.env.prompts` и задайте системные промпты для ИИ:
   - TELEGRAM_SYSTEM_PROMPT, VK_SYSTEM_PROMPT
3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```
4. Запустите сервер:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

## Путь пользователя
1. Пользователь открывает бота в Telegram или пишет в сообщения сообщества VK и получает стартовое сообщение с подсказкой про ссылку или артикул Wildberries.
2. Пользователь отправляет ссылку вида `https://www.wildberries.ru/catalog/.../detail.aspx` или просто артикул товара.
3. Бот извлекает артикул, загружает карточку товара и отзывы Wildberries, затем отбирает только ограниченный набор отзывов для саммаризации: до 30 свежих и до 20 самых негативных.
4. Перед обращением к ProxyAPI бот проверяет антифлуд и доступный лимит запросов пользователя. Если WB временно недоступен, пользователю возвращается вежливое сообщение без списания платного лимита.
5. В ответ пользователь получает красивое сообщение с превью товара: фото, названием, ценой, ссылкой на карточку и структурированным выводом ИИ.
6. Если бесплатный или платный лимит исчерпан, бот сразу предлагает открыть кабинет и купить пакет проверок или подписку PRO.
7. В кабинете пользователь видит текущий тариф, остаток запросов, дату окончания PRO, реферальный код и последние оплаты.

## Лимиты и тарифы
- Free: 1 бесплатная проверка в сутки, лимит сбрасывается в полночь.
- Разовый: 99 ₽ за пакет из 5 проверок.
- PRO: 349 ₽ за 30 проверок на 30 дней.
- Если у активного PRO закончились проверки, бот предлагает продлить подписку или купить пакет проверок.

## Пример .env
```
TELEGRAM_BOT_TOKEN=...
VK_GROUP_TOKEN=...
VK_CONFIRMATION_TOKEN=...
PROXYAPI_URL=https://api.proxyapi.ru/openai/v1
PROXYAPI_KEY=...
ADMIN_IDS=123456789,987654321
ADMIN_TOKEN=supersecret
APP_BASE_URL=http://localhost:8000
DB_BACKEND=postgres
DATABASE_URL=postgresql://proxyapi:proxyapi@postgres:5432/proxyapi
POSTGRES_DB=proxyapi
POSTGRES_USER=proxyapi
POSTGRES_PASSWORD=change-me-postgres-password
SQLITE_DB_PATH=./data/users.db
ERROR_LOG_PATH=./data/proxyapi_errors.log
BILLING_SECRET=change-me-billing-secret
PAYMENT_PROVIDER=sandbox
REFERRAL_BONUS_REQUESTS=2
ANTIFLOOD_WINDOW_SECONDS=10
ANTIFLOOD_MAX_REQUESTS=3
```

## Пример .env.prompts
```
TELEGRAM_SYSTEM_PROMPT=Ты — дружелюбный ассистент. Отвечай кратко и по делу.
VK_SYSTEM_PROMPT=Ты — дружелюбный ассистент. Отвечай кратко и по делу.
WB_SUMMARY_PROMPT=Ты аналитик маркетплейсов. Дай структурированный вывод по отзывам Wildberries.
```

## Ключевые настройки
- `REFERRAL_BONUS_REQUESTS` — сколько проверок получает пригласивший пользователь после первой успешной оплаты приглашённого.
- `ANTIFLOOD_WINDOW_SECONDS` и `ANTIFLOOD_MAX_REQUESTS` — окно и лимит антифлуда для Telegram и VK.
- `PAYMENT_SIDE_EFFECT_RETRY_INTERVAL_SECONDS` — как часто фоновой worker повторяет доставку post-payment side effects (уведомление, чек).
- `FISCAL_RETRY_INTERVAL_SECONDS` и `FISCAL_MAX_ATTEMPTS` — частота и предел повторных попыток фискализации.
- `TELEGRAM_SYSTEM_PROMPT`, `VK_SYSTEM_PROMPT`, `WB_SUMMARY_PROMPT` хранятся в `.env.prompts`, чтобы менять инструкции без правки кода.

## Wildberries
- Пользователь может прислать в Telegram ссылку на товар Wildberries или артикул.
- Пользователь во VK тоже может прислать ссылку на товар Wildberries или артикул и получить превью товара с итоговым саммари.

## Billing webhook
- При открытии /billing/checkout система создаёт pending-платёж с детерминированным external payment id.
- Тестовое подтверждение по /billing/confirm использует тот же external payment id и переводит платёж в paid.
- Для PRO на checkout и success-страницах показываются и количество проверок, и срок действия подписки.
- Для внешнего провайдера доступен endpoint /billing/webhook.
- Подпись webhook передаётся в заголовке X-Billing-Signature и считается как HMAC-SHA256 от raw body с секретом BILLING_SECRET.
- Тело webhook должно содержать external_payment_id и status. Для успешного подтверждения принимаются статусы paid, succeeded или success.

## YooKassa Sandbox
- Если PAYMENT_PROVIDER=yookassa, /billing/checkout создаёт pending-платёж и перенаправляет пользователя на YooKassa confirmation_url.
- Для работы нужны переменные YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY и при необходимости YOOKASSA_RETURN_URL.
- После оплаты YooKassa должна отправлять webhook на /billing/webhook с событием payment.succeeded.
- Сервер дополнительно перепроверяет webhook через API YooKassa по payment id и metadata.external_payment_id, поэтому одного поддельного POST на /billing/webhook недостаточно для начисления лимитов.
- Пользователь после возврата попадает на /billing/return, а лимиты начисляются не по return URL, а только по webhook провайдера.

## Robokassa Sandbox
- Если PAYMENT_PROVIDER=robokassa, /billing/checkout создаёт pending-платёж и перенаправляет пользователя на платёжную страницу Robokassa.
- Для работы нужны ROBOKASSA_MERCHANT_LOGIN, ROBOKASSA_PASSWORD1, ROBOKASSA_PASSWORD2.
- Серверный callback обрабатывается endpoint /billing/robokassa/result с проверкой подписи Robokassa.
- Пользовательские страницы возврата: /billing/robokassa/success и /billing/robokassa/fail.
- Для тестового режима используйте ROBOKASSA_TEST_MODE=true.

## Чеки и фискализация
- После успешной оплаты система создаёт запись чека и пытается отправить данные во внешний сервис фискализации, если настроены MYTAX_API_URL и MYTAX_API_TOKEN.
- Если внешний сервис не настроен, создаётся локальный sandbox-чек, доступный по странице /billing/receipt?payment_id=....
- Пользователь после успешной оплаты получает уведомление с параметрами платежа и ссылкой на чек в своей платформе.
- Для передачи данных во внешний слой фискализации предусмотрены переменные MYTAX_API_URL, MYTAX_API_TOKEN и MYTAX_SELLER_INN.
- При ошибке внешней фискализации чек помечается статусом error, сохраняются текст последней ошибки и число попыток отправки.
- В админ-панели по поиску платежа можно увидеть статус чека, число попыток, последнюю ошибку и вручную запустить повторную отправку чека.
- Если настроены MYTAX_API_URL и MYTAX_API_TOKEN, приложение также автоматически повторяет отправку чеков со статусом error в фоне.
- Параметры авто-повтора настраиваются через FISCAL_RETRY_INTERVAL_SECONDS и FISCAL_MAX_ATTEMPTS в .env.
- Бот пытается получить карточку товара и отзывы, выбирает для анализа до 30 свежих и до 20 самых негативных отзывов.
- Затем бот отправляет превью товара и итоговый саммари через ProxyAPI.
- Если Wildberries временно ограничил доступ к данным, бот сообщает об этом пользователю и не должен списывать лимит.

## Sandbox-оплата
- В команде `/profile` Telegram-бот показывает кнопки покупки пакета и PRO.
- Кнопки ведут на тестовую страницу `/billing/checkout`, где можно подтвердить оплату в sandbox-режиме.
- После подтверждения открывается `/billing/confirm`, и лимиты начисляются автоматически.
- Чтобы ссылки работали корректно, в `.env` нужно задать `APP_BASE_URL` с внешним адресом сервера или ngrok.

## VK-команды
- `профиль`, `кабинет`, `/profile` — показать тариф, остаток проверок, реферальный код, последние оплаты и ссылки на покупку.
- `реф КОД` — привязать реферальный код вручную во VK.
- Обычные сообщения во VK теперь тоже учитывают антифлуд и лимиты проверок, как и в Telegram.

## Проверка
- Health: GET http://localhost:8000/health

## Вебхуки
- Установка webhook для Telegram:
  ```bash
  curl -F "url=https://YOUR_HOST/telegram/webhook" "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook"
  ```
- VK: укажите callback URL в настройках группы VK как `https://YOUR_HOST/vk/webhook`. При подтверждении VK приложение автоматически вернёт значение VK_CONFIRMATION_TOKEN.

## Docker
- Сборка:
  ```bash
  docker build -t proxyapi-bots .
  ```
- В build context больше не попадают `.env`, `.env.prompts`, локальная БД и логи: это исключено через `.dockerignore`.
- Запуск:
  ```bash
  docker run --env-file .env --env-file .env.prompts -p 8000:8000 proxyapi-bots
  ```
- Для сохранения SQLite и логов между рестартами контейнера добавьте volume:
  ```bash
  docker run --env-file .env --env-file .env.prompts -p 8000:8000 -v ${PWD}/data:/app/data proxyapi-bots
  ```

## Docker Compose
- Для тестового сервера и sandbox-проверки можно использовать готовый `docker-compose.yml`.
- По умолчанию он поднимает два контейнера: приложение и PostgreSQL 16.
- Приложение по умолчанию стартует с `DB_BACKEND=postgres`, а строка подключения берётся из `DATABASE_URL`.
- Если нужен старый SQLite-режим, задайте в `.env` `DB_BACKEND=sqlite` и при желании очистите `DATABASE_URL`.
- SQLite-файл и лог ошибок по-прежнему складываются в `./data` на хосте, а PostgreSQL хранит данные в отдельном compose-volume `postgres_data`.
- В compose уже добавлен `healthcheck` на `/health`, поэтому видно, что сервер действительно поднялся.
- Запуск:
   ```bash
   docker compose up --build -d
   ```
- Остановка:
   ```bash
   docker compose down
   ```

## Проверка на тестовом сервере
1. Скопируйте `.env.bak` в `.env` и заполните минимум: `TELEGRAM_BOT_TOKEN`, `VK_GROUP_TOKEN`, `VK_CONFIRMATION_TOKEN`, `PROXYAPI_URL`, `PROXYAPI_KEY`, `APP_BASE_URL`, `ADMIN_IDS`, `ADMIN_TOKEN`.
2. Убедитесь, что в `.env` для проверки оплаты стоит `PAYMENT_PROVIDER=sandbox`.
3. Для PostgreSQL-пути оставьте `DB_BACKEND=postgres` и проверьте `DATABASE_URL`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`.
4. Если хотите временно запускаться на SQLite вместо PostgreSQL, переключите `DB_BACKEND=sqlite`.
5. Запустите контейнеры через `docker compose up --build -d`.
6. Проверьте состояние сервиса: `http://YOUR_HOST:8000/health` должно вернуть `{"status":"ok"}`.
7. При необходимости проверьте БД-контейнер: `docker compose ps` должен показывать `postgres` в состоянии `healthy`.
8. Подключите внешний URL:
   Telegram webhook: `https://YOUR_HOST/telegram/webhook`
   VK webhook: `https://YOUR_HOST/vk/webhook`
9. Откройте Telegram-бота, вызовите `/profile` и пройдите sandbox-оплату через `/billing/checkout`.
10. После подтверждения убедитесь, что:
   лимиты начислились;
   в админке находится платёж;
   страница `/billing/receipt?payment_id=...` открывается;
   в `./data` появился лог ошибок;
   при postgres-режиме данные переживают рестарт `docker compose down` / `up` за счёт volume.

## Локальный туннель (ngrok)
- Используйте ngrok: `ngrok http 8000`
- Затем укажите webhook URL как `https://<ngrok-id>.ngrok.io/telegram/webhook` и `https://<ngrok-id>.ngrok.io/vk/webhook`

## Proxy API (для тестирования)
- Если реального ProxyAPI нет, укажите PROXYAPI_URL на сервис, который возвращает JSON вида `{ "reply": "..." }`, например простую echo-ручку или webhook.site.

## Примечания / следующие шаги
- Для тестового сервера и sandbox-оплаты compose теперь готов как для PostgreSQL, так и для SQLite.
- Если запуск идёт в PostgreSQL-режиме, приложение само поднимает базовую схему при старте.
