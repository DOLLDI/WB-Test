#!/bin/bash
set -e

# Ждём, пока Postgres будет готов (если используется)
if [ "$DB_BACKEND" = "postgres" ]; then
    echo "⏳ Waiting for PostgreSQL..."
    while ! nc -z "${DATABASE_HOST:-postgres}" "${DATABASE_PORT:-5432}"; do
        sleep 1
    done
    echo "✅ PostgreSQL is ready"
fi

# Запускаем миграции и инициализацию БД (если нужно)
# python -m alembic upgrade head  # Раскомментируй, если используешь alembic

# Запускаем приложение
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
