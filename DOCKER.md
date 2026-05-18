# Docker Setup Guide

## Быстрый запуск

### 1. Подготовка переменных окружения

Скопируй `.env.example` в `.env` и заполни необходимые токены:

```bash
cp .env.example .env
# Отредактируй .env и добавь реальные токены
```

### 2. Запуск Docker Compose

```bash
docker-compose up -d
```

**Что запустится:**
- `proxyapi-postgres` — PostgreSQL БД
- `proxyapi-bots` — FastAPI приложение (порт 8000)
- `proxyapi-cloudflared` — бесплатный Cloudflare Quick Tunnel для публичного HTTPS URL

### 3. Проверка статуса

```bash
docker-compose ps
docker-compose logs proxyapi-bots   # Основное приложение
docker-compose logs cloudflared     # Cloudflare туннель
```

### 4. Получение публичного URL

После запуска приложение автоматически:
- Получает URL от cloudflared
- Устанавливает webhook Telegram
- Создаёт/обновляет VK Callback server, если заполнен `VK_GROUP_ID`
- Если `VK_GROUP_ID` не задан, выводит готовый VK webhook URL в логи

Проверь логи:
```bash
docker-compose logs proxyapi-bots | grep -E "(Public webhook|Telegram webhook|VK callback|VK webhook)"
```

---

## Конфигурация

### Database Backend

В `docker-compose.yml` по умолчанию используется **PostgreSQL**.

Для использования **PostgreSQL**, измени `.env`:
```env
DB_BACKEND=postgres
DATABASE_URL=postgresql://proxyapi:proxyapi@postgres:5432/proxyapi
```

### Telegram Webhook

Webhook **устанавливается автоматически** через `main.py` lifespan:
```
/telegram/webhook
```

### VK Webhook

VK webhook ставится автоматически, если в `.env` указаны:
```env
VK_GROUP_ID=123456789
VK_AUTO_SET_CALLBACK=true
```

Если `VK_GROUP_ID` не задан, приложение всё равно выведет готовый URL: `https://<cloudflared-url>/vk/webhook`.

---

## Обслуживание

### Просмотр логов

```bash
# Все логи
docker-compose logs -f

# Только приложение
docker-compose logs -f proxyapi-bots

# Только Cloudflare туннель
docker-compose logs -f cloudflared
```

### Перезагрузка приложения

```bash
docker-compose restart proxyapi-bots
```

### Полная пересборка

```bash
docker-compose down
docker-compose build
docker-compose up -d
```

### Очистка всех данных

```bash
docker-compose down -v
```

---

## Troubleshooting

### Cloudflare URL не найден

Если приложение зависает на ожидании cloudflared URL:

1. **Проверь логи cloudflare:**
   ```bash
   docker-compose logs cloudflared
   ```

2. **Убедись, что сервис cloudflared запущен:**
   ```bash
   docker-compose ps cloudflared
   ```

3. **Если не стартует, пересоздай сервис:**
   ```bash
   docker-compose restart cloudflared
   ```

### Telegram webhook не устанавливается

Проверь в логах:
```bash
docker-compose logs proxyapi-bots | grep -i telegram
```

- Если `⚠️ No Telegram token` — добавь `TELEGRAM_BOT_TOKEN` в `.env`
- Если `❌ Telegram webhook error` — проверь token и интернет-соединение

### Database Connection Failed

Если используешь PostgreSQL:
```bash
# Проверь статус БД
docker-compose ps postgres

# Проверь логи БД
docker-compose logs postgres

# Убедись в правильности DATABASE_URL в .env
```

Если используешь SQLite:
```bash
# Проверь, создана ли папка data
ls -la data/users.db
```

---

## Запуск без Docker (локальная разработка)

### 1. Установка зависимостей

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Установка Playwright browsers

```bash
python -m playwright install chromium
```

### 3. Запуск приложения

```bash
# Убедись, что .env содержит правильные настройки
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## Production Deployment

### Рекомендации:

1. **Используй PostgreSQL** вместо SQLite
2. **Отключи режим разработки** — используй gunicorn вместо uvicorn
3. **Настрой переменные окружения** перед запуском
4. **Добавь reverse proxy** (nginx) перед FastAPI для HTTPS и балансировки

Пример с gunicorn:
```bash
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app
```

---

## API Health Check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Дополнительно

- **Logs**: Проверь `/tmp/err.log` и `proxyapi_errors.log` для деталей ошибок
- **Admin Panel**: URL будет доступен по `/admin` с использованием `ADMIN_TOKEN`
- **Billing**: Поддержка Yookassa и Robokassa платежей
