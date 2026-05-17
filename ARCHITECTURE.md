# Architecture & Improvements Overview

## 📋 Проверка проекта

✅ **Синтаксические ошибки**: НЕТ  
✅ **Импорты**: Все корректны  
✅ **Структура БД**: Поддержка SQLite и PostgreSQL  
✅ **Роутеры**: Telegram и VK работают правильно  
✅ **Сервисы**: Все инициализируются корректно

---

## 🔧 Улучшения, которые были сделаны

### 1. **Унификация логики вебхуков** 
**Было**: Две отдельные реализации (init_webhooks.py + main.py lifespan)  
**Стало**: Единая точка инициализации в `main.py` lifespan

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Инициализация БД
    await init_db()
    
    # 2. Фоновые задачи (платежи, fiscal)
    payment_task = asyncio.create_task(payment_side_effects_retry_worker())
    fiscal_task = ...  # optional
    
    # 3. Получение cloudflared URL (с fallback на HTTP API)
    base_url = await asyncio.to_thread(wait_for_cloudflared_url)
    
    # 4. Установка вебхуков (Telegram + VK)
    set_telegram_webhook(base_url)  # автоматическая установка
    set_vk_webhook(base_url)         # информация в логи
    
    yield
    
    # Graceful shutdown
```

**Преимущества:**
- ✅ Меньше дублирования кода
- ✅ Единая точка контроля
- ✅ Лучше обработка ошибок

---

### 2. **Улучшенный способ получения cloudflared URL**
**Было**: Только парсинг `docker logs`  
**Стало**: Попытка HTTP API → fallback на docker logs

```python
def wait_for_cloudflared_url():
    # Способ 1: HTTP API cloudflared (надёжнее)
    # GET http://cloudflared:4040/api/tunnels
    
    # Способ 2: Парсинг docker logs (fallback)
    # docker logs cloudflared
```

**Преимущества:**
- ✅ Более надёжный в контейнеризированной среде
- ✅ Не требует доступа к Docker socket
- ✅ Быстрее находит URL

---

### 3. **Очистка docker-compose.yml**
**Было**: 3 сервиса (postgres + proxyapi-bots + cloudflared + webhook-init)  
**Стало**: 3 сервиса (postgres + proxyapi-bots + cloudflared)

Удалён ненужный сервис `webhook-init` — логика встроена в `main.py`

---

### 4. **Обновлён Dockerfile**
**Добавлено:**
- ✅ `netcat-openbsd` для проверки readiness Postgres
- ✅ Папка `/app/logs` для логов
- ✅ `entrypoint.sh` для корректного запуска
- ✅ Обработка сигналов завершения

```bash
# Новая команда в контейнере
ENTRYPOINT ["./entrypoint.sh"]
# Которая:
# 1. Ждёт Postgres (если используется)
# 2. Запускает миграции (optional)
# 3. Запускает uvicorn
```

---

### 5. **Улучшен Playwright парсер WB**
**Добавлено:**
- ✅ Stealth скрипт (скрывает `navigator.webdriver`)
- ✅ Реалистичный контекст браузера
- ✅ HTTP заголовки (referer, accept-language)
- ✅ Блокировка трекеров через route handler
- ✅ Retry механизм (2 попытки)
- ✅ Корректное закрытие контекста/страницы

```python
# Stealth init script — скрывает признаки автоматизации
Object.defineProperty(navigator, 'webdriver', {get: () => false})
window.navigator.chrome = { runtime: {} }
Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru']})

# Контекст с реалистичными параметрами
browser.new_context(
    user_agent='Chrome/115.0.0.0',
    locale='ru-RU',
    viewport={'width': 1366, 'height': 768},
    timezone_id='Europe/Moscow'
)

# Блокировка аналитики и рекламы
await page.route('**/*', lambda route: _route_handler(route))
```

---

### 6. **Документация**
Созданы новые файлы:
- **`.env.example`** — пример конфигурации со всеми параметрами
- **`DOCKER.md`** — подробная инструкция по Docker (50+ строк)
- **`QUICKSTART.md`** — быстрый старт в 5 шагов

---

## 🏗️ Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                        INTERNET                              │
└──────────────┬──────────────────────────────────────────────┘
               │ https://xxx.trycloudflare.com
               │
┌──────────────▼──────────────────────────────────────────────┐
│                    CLOUDFLARE TUNNEL                         │
│              (автоматический port forwarding)               │
└──────────────┬──────────────────────────────────────────────┘
               │ http://cloudflared:4040
               │
┌──────────────┴──────────────────────────────────────────────┐
│              DOCKER-COMPOSE (docker-compose.yml)             │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  PostgreSQL (optional, default: SQLite)             │   │
│  │  Container: postgres:16-alpine                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  FastAPI App (ProxyApiBots)                         │   │
│  │  Container: python:3.11-slim                        │   │
│  │  Entrypoint: ./entrypoint.sh                        │   │
│  │  Ports: 8000 (HTTP)                                 │   │
│  │  Health: GET /health                                │   │
│  │                                                       │   │
│  │  [Lifespan]:                                        │   │
│  │  - await init_db()                                  │   │
│  │  - payment_side_effects_retry_worker()              │   │
│  │  - fiscal_retry_worker() (optional)                 │   │
│  │  - wait_for_cloudflared_url()                       │   │
│  │  - set_telegram_webhook()                           │   │
│  │  - set_vk_webhook()                                 │   │
│  │                                                       │   │
│  │  [Routes]:                                          │   │
│  │  - POST /telegram/webhook (aiogram Update)          │   │
│  │  - POST /vk/webhook (VK callback)                   │   │
│  │  - GET /admin/* (админ-панель)                      │   │
│  │  - GET /billing/* (платежи)                         │   │
│  │  - GET /health (health check)                       │   │
│  │                                                       │   │
│  │  [Background Tasks]:                                │   │
│  │  - payment_side_effects_retry_worker()              │   │
│  │  - fiscal_retry_worker()                            │   │
│  │                                                       │   │
│  │  [Parsers]:                                         │   │
│  │  - PlaywrightPoolManager (WB парсинг)               │   │
│  │  - WBHTMLParser (fallback)                          │   │
│  │                                                       │   │
│  │  [Database]:                                        │   │
│  │  - SQLite: users.db (default)                       │   │
│  │  - PostgreSQL: postgresql://... (optional)          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Cloudflared (Cloudflare Tunnel)                    │   │
│  │  Container: cloudflare/cloudflared:latest           │   │
│  │  Port: 4040 (metrics API)                           │   │
│  │  Command: tunnel --url http://proxyapi-bots:8000    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

---

## 📊 Database Support

### SQLite (Default)
```
✅ Простой setup
✅ Нет зависимостей
✅ Файл: users.db
❌ Не масштабируется
❌ Нет конкурентности для writes
```

**Используй SQLite для:**
- Локальной разработки
- MVP / proof of concept

### PostgreSQL
```
✅ Масштабируемый
✅ Поддержка конкурентности
✅ Транзакции
✅ Полнотекстовый поиск
```

**Используй PostgreSQL для:**
- Production
- Многопользовательских систем

---

## 🔐 Security Notes

### Telegram Webhook
```
✅ Автоматически устанавливается при старте
✅ HTTPS (через cloudflared)
✅ Telegram проверяет сертификаты
```

### VK Callback
```
⚠️ Требует ручной регистрации в админке
✅ HTTPS (через cloudflared)
✅ Callback API > версия 5.131
```

### Rate Limiting
```
ANTIFLOOD_WINDOW_SECONDS=10
ANTIFLOOD_MAX_REQUESTS=3
```

### Admin Panel
```
Требует:
- ADMIN_TOKEN (в заголовке или cookies)
- ADMIN_ID (в списке ADMIN_IDS)
```

---

## 🚀 Deployment Checklist

### Перед production:

- [ ] Используй PostgreSQL вместо SQLite
- [ ] Установи HTTPS (nginx + Let's Encrypt)
- [ ] Настрой переменные окружения (.env)
- [ ] Настрой backup базы данных
- [ ] Настрой мониторинг логов
- [ ] Настрой alert'ы для ошибок
- [ ] Проверь rate limiting
- [ ] Проверь quota на cloudflared
- [ ] Настрой graceful shutdown
- [ ] Тестируй failover сценарии

---

## 📈 Масштабирование

### Горизонтальное масштабирование:

1. **Load Balancer** (nginx/traefik)
2. **Несколько инстансов приложения**
3. **Shared PostgreSQL**
4. **Redis** для session/cache

```yaml
# docker-compose.yml
services:
  load-balancer:
    image: nginx:latest
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    
  app-1:
    build: .
    depends_on:
      - postgres
    environment:
      - APP_INSTANCE=1
    
  app-2:
    build: .
    depends_on:
      - postgres
    environment:
      - APP_INSTANCE=2
```

---

## 📚 Дополнительные ресурсы

- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Aiogram 3 Guide](https://docs.aiogram.dev/)
- [Docker Compose Reference](https://docs.docker.com/compose/compose-file/)
- [Playwright Python](https://playwright.dev/python/)
- [PostgreSQL Docs](https://www.postgresql.org/docs/)
- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)

---

## ✅ Финальный статус

| Компонент | Статус | Примечание |
|-----------|--------|-----------|
| Синтаксис | ✅ OK | Ошибок нет |
| Импорты | ✅ OK | Все зависимости доступны |
| WB Parser | ✅ Улучшен | Stealth + retries + route blocking |
| Webhooks | ✅ Унифицированы | Одна точка инициализации |
| Docker | ✅ Оптимизирован | Entrypoint + healthchecks |
| Документация | ✅ Создана | 3 новых файла для быстрого старта |

---

## 🎯 Следующие шаги

1. **Локально**: Протестируй с `docker-compose up -d`
2. **Заполни .env**: Используй `.env.example` как шаблон
3. **Настрой VK webhook**: Вручную в админке группы
4. **Мониторь логи**: `docker-compose logs -f`
5. **Деплой**: На реальный сервер с PostgreSQL

---

**🎉 Проект готов к запуску в Docker!**
