# FROM python:3.11-slim

# WORKDIR /app

# ENV PYTHONDONTWRITEBYTECODE=1 \
# 	PYTHONUNBUFFERED=1

# COPY requirements.txt ./
# RUN pip install --no-cache-dir -r requirements.txt \
# 	&& mkdir -p /app/data

# COPY app ./app

# EXPOSE 8000

# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
FROM public.ecr.aws/docker/library/python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# системные зависимости (ВАЖНО для playwright + bs4 + chromium)
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    netcat-openbsd \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libatspi2.0-0 \
    libdrm2 \
    libxshmfence1 \
    libxfixes3 \
    libxext6 \
    libxrender1 \
    libglib2.0-0 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

RUN curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

# зависимости Python
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# 🔥 УСТАНОВКА BROWSERS ДЛЯ PLAYWRIGHT
RUN python -m playwright install chromium

# создаём папку под данные
RUN mkdir -p /app/data /app/logs

# копируем код
COPY app ./app
COPY entrypoint.sh ./entrypoint.sh

# Делаем entrypoint выполняемым
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
