# ✅ Deployment Checklist

Этот файл содержит пошаговый чеклист для запуска проекта в Docker.

---

## 🔧 Шаг 1: Подготовка переменных окружения

- [ ] Скопируй `.env.example` → `.env`
  ```bash
  cp .env.example .env
  ```

- [ ] Добавь в `.env` **минимально необходимые параметры**:
  ```env
  TELEGRAM_BOT_TOKEN=<твой_telegram_token>
  VK_GROUP_TOKEN=<твой_vk_token>
  VK_CONFIRMATION_TOKEN=<твой_confirmation_token>
  PROXYAPI_URL=<url_к_proxy_api>
  OPENAI_API_KEY=<твой_openai_key>
  ADMIN_IDS=<твой_user_id>
  ```

- [ ] Проверь, что все параметры заполнены:
  ```bash
  grep -E "^[A-Z_]+=\S+$" .env | wc -l
  # Должно быть > 10 параметров
  ```

---

## 🐳 Шаг 2: Запуск Docker

- [ ] Убедись, что Docker Desktop запущен (или `docker ps` работает)

- [ ] Пересборка образов (первый раз):
  ```bash
  docker-compose build
  ```

- [ ] Запуск контейнеров:
  ```bash
  docker-compose up -d
  ```

- [ ] Проверка статуса (должны быть "Up"):
  ```bash
  docker-compose ps
  ```

---

## ✅ Шаг 3: Проверка инициализации

- [ ] Подожди 5-10 секунд (cloudflared должен подняться)

- [ ] Проверь логи приложения:
  ```bash
  docker-compose logs proxyapi-bots | tail -20
  ```

- [ ] Ищи эти строки в логах:
  ```
  ✅ FOUND URL via HTTP API: https://xxx.trycloudflare.com
  ✅ Telegram webhook set successfully
  🎉 WEBHOOKS CONFIGURED
  ```

- [ ] Если cloudflared зависает, перезагрузи:
  ```bash
  docker-compose restart cloudflared
  docker-compose logs cloudflared
  ```

---

## 🌍 Шаг 4: Получение публичного URL

- [ ] Получи URL cloudflared из логов:
  ```bash
  docker-compose logs cloudflared | grep trycloudflare
  ```

  ИЛИ через API:
  ```bash
  curl http://localhost:4040/api/tunnels | jq '.tunnels[0].public_url'
  ```

- [ ] Сохрани URL (нужен для VK webhook):
  ```
  https://abc-123-def.trycloudflare.com
  ```

---

## 🔔 Шаг 5: Настройка Telegram Webhook

- [ ] Telegram webhook **уже установлен автоматически** ✅

- [ ] Проверь, что он правильно установлен:
  ```bash
  curl "https://api.telegram.org/bot{TOKEN}/getWebhookInfo" | jq .
  ```

- [ ] В ответе должен быть:
  ```json
  {
    "ok": true,
    "result": {
      "url": "https://abc-123-def.trycloudflare.com/telegram/webhook",
      "has_custom_certificate": false,
      "pending_update_count": 0
    }
  }
  ```

---

## 🔴 Шаг 6: Настройка VK Webhook (ТРЕБУЕТСЯ РУЧНАЯ РЕГИСТРАЦИЯ)

- [ ] Открой [VK Admin Panel](https://vk.com/clubYOUR_GROUP_ID/settings?act=api)
  (замени YOUR_GROUP_ID на ID твоей группы)

- [ ] Перейди: **Управление** → **API usage** → **Callback API**

- [ ] Добавь **Callback Server**:
  - [ ] **URL**: `https://abc-123-def.trycloudflare.com/vk/webhook`
  - [ ] **Title**: ProxyApiBots (или любое название)
  - [ ] **Server Status**: Нажми "Confirm" ← ВСЕ БУДЕТ OK

- [ ] Включи события (checkbox'ы):
  - [ ] ✅ **Message new** (новые сообщения) — ОБЯЗАТЕЛЬНО
  - [ ] Message reply (ответы)
  - [ ] Message edit (редактирование)
  - [ ] Group join (вход в группу)
  - [ ] Etc. (выбери нужные)

- [ ] **Версия API**: Выбери самую новую (5.131+)

- [ ] Нажми **Save** и **Confirm Server**

---

## 🧪 Шаг 7: Тестирование

### Telegram

- [ ] Напиши боту любое сообщение
- [ ] Должен получить ответ ← проверяет работу webhook'а

### VK

- [ ] Напиши в DM группы (личное сообщение)
- [ ] Должен получить ответ ← проверяет работу callback API

### Health Check

- [ ] Проверь, что приложение живо:
  ```bash
  curl http://localhost:8000/health
  # {"status":"ok"}
  ```

---

## 📊 Шаг 8: Мониторинг

- [ ] Следи за логами в реальном времени:
  ```bash
  docker-compose logs -f proxyapi-bots
  ```

- [ ] Проверяй ошибки:
  ```bash
  docker-compose logs proxyapi-bots | grep -i error
  ```

- [ ] Проверяй файлы логов (внутри контейнера):
  ```bash
  docker-compose exec proxyapi-bots cat /app/data/proxyapi_errors.log
  ```

---

## 🚨 Troubleshooting

### Проблема: "Cloudflared URL not found"

**Решение:**
```bash
# 1. Проверь логи cloudflared
docker-compose logs cloudflared

# 2. Перезагрузи cloudflared
docker-compose restart cloudflared

# 3. Пересоздай сервис
docker-compose down cloudflared
docker-compose up -d cloudflared
```

### Проблема: "Telegram webhook error"

**Решение:**
```bash
# 1. Проверь токен в .env
grep TELEGRAM_BOT_TOKEN .env

# 2. Проверь статус webhook'а
curl "https://api.telegram.org/bot{YOUR_TOKEN}/getWebhookInfo"

# 3. Посмотри полные логи
docker-compose logs proxyapi-bots | grep -A5 -B5 "Telegram"
```

### Проблема: Приложение не стартует

**Решение:**
```bash
# 1. Посмотри полные логи
docker-compose logs proxyapi-bots --tail 50

# 2. Проверь синтаксис .env
nano .env  # или используй редактор

# 3. Пересоздай контейнер
docker-compose down
docker-compose build --no-cache
docker-compose up -d

# 4. Проверь наличие необходимых переменных
docker-compose config | grep TELEGRAM_BOT_TOKEN
```

### Проблема: "Connection refused" при подключении к PostgreSQL

**Решение:**
```bash
# 1. Проверь, запущена ли PostgreSQL
docker-compose ps postgres

# 2. Проверь логи PostgreSQL
docker-compose logs postgres

# 3. Убедись в правильности DATABASE_URL в .env
grep DATABASE_URL .env

# 4. Используй sqlite по умолчанию (не нужна PostgreSQL)
# Просто удали DB_BACKEND=postgres и используй sqlite
```

---

## ✅ Финальная проверка

После запуска убедись, что:

- [ ] Docker контейнеры запущены и healthy
  ```bash
  docker-compose ps
  ```

- [ ] Приложение слушает на порту 8000
  ```bash
  docker ps | grep proxyapi-bots
  ```

- [ ] Cloudflare туннель активен и имеет публичный URL
  ```bash
  curl http://localhost:4040/api/tunnels
  ```

- [ ] Telegram webhook установлен
  ```bash
  curl "https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
  ```

- [ ] VK webhook зарегистрирован в админке
  - Перейди в VK Admin → Callback API (должен быть зелёный статус)

- [ ] Health check работает
  ```bash
  curl http://localhost:8000/health
  ```

---

## 📚 Документация

Если нужна подробная информация, смотри:

- **[QUICKSTART.md](QUICKSTART.md)** — Быстрый старт в 5 шагов
- **[DOCKER.md](DOCKER.md)** — Подробная инструкция Docker
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Архитектура и дизайн
- **[.env.example](.env.example)** — Пример всех переменных окружения
- **[README.md](README.md)** — Общее описание проекта

---

## 🎯 Следующие шаги

После успешного запуска в Docker:

1. **Локальное тестирование** (🟢 ТЕКУЩИЙ ЭТАП)
   - Убедись, что все работает локально

2. **Деплой на сервер**
   - Используй PostgreSQL вместо SQLite
   - Настрой backup БД
   - Добавь мониторинг и alert'ы

3. **Production optimizations**
   - Используй gunicorn вместо uvicorn (multiple workers)
   - Настрой nginx reverse proxy
   - Добавь Redis для cache
   - Включи HTTPS сертификаты

---

## 🆘 Если что-то сломалось

1. **Посмотри логи:**
   ```bash
   docker-compose logs -f proxyapi-bots
   ```

2. **Проверь .env:**
   ```bash
   cat .env | grep -v "^#" | grep -v "^$"
   ```

3. **Перезагрузи всё:**
   ```bash
   docker-compose down
   docker-compose up -d
   docker-compose logs -f
   ```

4. **Очисти всё и начни с нуля:**
   ```bash
   docker-compose down -v
   docker system prune -a --volumes
   docker-compose up -d
   ```

---

**✅ Ты готов! Начни с Шага 1 и следуй по порядку.** 🚀
