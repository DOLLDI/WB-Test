# Quick Start Guide

## 1️⃣ Подготовка (первый запуск)

```bash
# Скопируй пример конфигурации
cp .env.example .env

# Отредактируй .env и добавь реальные токены
nano .env  # или используй IDE/текстовый редактор

# Убедись, что добавлены минимально необходимые параметры:
# - TELEGRAM_BOT_TOKEN
# - VK_GROUP_TOKEN
# - VK_CONFIRMATION_TOKEN
# - PROXYAPI_URL
# - OPENAI_API_KEY
# - ADMIN_IDS
```

---

## 2️⃣ Запуск Docker

```bash
# Включи Docker Desktop (или убедись, что Docker daemon запущен)

# Построй и запусти контейнеры
docker-compose up -d

# Проверь статус
docker-compose ps
```

**Ожидаемый вывод:**
```
NAME                        STATUS
postgres                    Up (healthy)
proxyapi-bots              Up (healthy)
cloudflared                Up
```

---

## 3️⃣ Проверка, что всё работает

```bash
# Проверь логи приложения
docker-compose logs proxyapi-bots -f

# Ищи строки:
# - "✅ FOUND URL via HTTP API" или "via docker logs"
# - "✅ Telegram webhook set successfully"
# - "🎉 WEBHOOKS CONFIGURED"
```

---

## 4️⃣ Получи публичный URL для вебхуков

```bash
# Способ 1: Из логов
docker-compose logs cloudflared | grep trycloudflare

# Способ 2: Напрямую из API cloudflared
curl http://localhost:4040/api/tunnels

# Пример ответа:
# {
#   "tunnels": [
#     {
#       "public_url": "https://abc123def.trycloudflare.com"
#     }
#   ]
# }
```

---

## 5️⃣ Настройка VK вебхука (требуется ручная регистрация)

1. Перейди в [VK Admin](https://vk.com/clubID/settings?act=api) (замени clubID на ID твоей группы)
2. Перейди в **Управление → Callback API**
3. Добавь **Callback server**:
   - **URL**: `https://<твой-cloudflared-url>/vk/webhook`
   - **Версия API**: Самая новая
4. Установи **Confirmation Token** из `.env` в **VK_CONFIRMATION_TOKEN**
5. Нажми "Подтвердить сервер"
6. Включи события, которые нужны (сообщения, etc.)

---

## 📋 Основные команды

| Команда | Описание |
|---------|---------|
| `docker-compose up -d` | Запуск контейнеров в фоне |
| `docker-compose down` | Остановка и удаление контейнеров |
| `docker-compose logs -f` | Просмотр логов всех сервисов |
| `docker-compose logs proxyapi-bots -f` | Логи приложения |
| `docker-compose logs cloudflared -f` | Логи Cloudflare туннеля |
| `docker-compose restart proxyapi-bots` | Перезагрузка приложения |
| `docker-compose build` | Пересборка образа |
| `docker-compose ps` | Статус сервисов |
| `docker exec -it proxyapi-bots bash` | Вход в контейнер |
| `docker-compose down -v` | Удаление контейнеров + тома (данные БД) |

---

## 🔧 Переменные окружения (обязательные)

```env
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
VK_GROUP_TOKEN=vk1.a.ExAmPlEtOkEnHeRe1234567890
VK_CONFIRMATION_TOKEN=12a3b4c5d
PROXYAPI_URL=https://api.proxy.example.com
OPENAI_API_KEY=sk-proj-XXXXX
ADMIN_IDS=123456789
```

---

## ✅ Проверка здоровья

```bash
# Health check приложения
curl http://localhost:8000/health
# {"status":"ok"}

# Проверь Postgres (если используется)
docker-compose exec postgres pg_isready -U proxyapi

# Проверь Cloudflare туннель
curl http://localhost:4040/api/tunnels
```

---

## 🚨 Troubleshooting

### Cloudflare URL не найден (зависает на старте)

```bash
# Проверь логи cloudflared
docker-compose logs cloudflared

# Перезагрузи сервис
docker-compose restart cloudflared
```

### Telegram webhook не устанавливается

```bash
# Проверь токен в .env
grep TELEGRAM_BOT_TOKEN .env

# Проверь логи
docker-compose logs proxyapi-bots | grep -i telegram
```

### Ошибка подключения к Postgres

```bash
# Проверь статус БД
docker-compose ps postgres

# Просмотри логи БД
docker-compose logs postgres

# Убедись в DATABASE_URL в .env
```

### Приложение крашится сразу после старта

```bash
# Посмотри полные логи
docker-compose logs proxyapi-bots --tail 50

# Проверь синтаксис .env
cat .env

# Пересборка образа
docker-compose build --no-cache
docker-compose up -d
```

---

## 💡 Советы

- **Используй `docker-compose logs -f`** для мониторинга в реальном времени
- **Проверяй `.env`** перед каждым запуском — часто проблемы в конфигурации
- **В production используй PostgreSQL** вместо SQLite
- **Включи GitHub Actions** для автоматического деплоя

---

## 📚 Дальнейшая информация

- Подробная документация: [DOCKER.md](DOCKER.md)
- Структура проекта: [README.md](README.md)
- Примеры `.env`: [.env.example](.env.example)
