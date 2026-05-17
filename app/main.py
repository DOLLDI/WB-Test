from contextlib import asynccontextmanager
import asyncio
import requests
import time
import json
import re
import os
from fastapi import FastAPI
from app.routers import admin, billing, telegram_router, vk_router
from app.services.billing import payment_side_effects_retry_worker
from app.services.config import settings
from app.services.db import init_db
from app.services.fiscal import fiscal_retry_worker
from app.services.logger import init_logging
print("🔥 MAIN FILE IS RUNNING")
# ----------------------------
# CONFIG
# ----------------------------


# ----------------------------
# WAIT CLOUDFLARED URL (FROM LOG FILE)
# ----------------------------
import re
import os

def wait_for_cloudflared_url():
    """Получить публичный URL от cloudflared из log файла"""
    print("⏳ Waiting for cloudflared tunnel URL...")
    log_file = "/app/shared/cloudflared.log"
    
    for attempt in range(120):  # 120 попыток * 0.5сек = 60 сек максимум
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    content = f.read()
                    # Ищем строку типа: "https://environments-pants-submissions-little.trycloudflare.com"
                    match = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', content)
                    if match:
                        url = match.group(0)
                        print(f"✅ FOUND URL from cloudflared logs: {url}")
                        return url
            else:
                print(f"⏳ retry {attempt}: log file not found yet")
        except Exception as e:
            print(f"⏳ retry {attempt}: {type(e).__name__} - {str(e)[:50]}")

        time.sleep(0.5)

    raise RuntimeError("❌ Cloudflared URL not found in logs after 120 attempts")

# ----------------------------
# TELEGRAM WEBHOOK
# ----------------------------
def set_telegram_webhook(base_url: str):
    token = settings.TELEGRAM_BOT_TOKEN

    if not token:
        print("⚠️ No Telegram token")
        return

    webhook_url = f"{base_url}/telegram/webhook"

    print("📡 Setting Telegram webhook:", webhook_url)

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/setWebhook",
            params={"url": webhook_url},
            timeout=10
        )

        result = r.json()
        if result.get("ok"):
            print("✅ Telegram webhook set successfully")
        else:
            print("⚠️ Telegram webhook response:", result)

    except Exception as e:
        print("❌ Telegram webhook error:", e)


# ----------------------------
# VK WEBHOOK
# ----------------------------
def set_vk_webhook(base_url: str):
    """VK использует push callbacks — нужно зарегистрировать в админке группы"""
    token = settings.VK_GROUP_TOKEN
    confirmation_token = settings.VK_CONFIRMATION_TOKEN

    if not token or not confirmation_token:
        print("⚠️ No VK tokens configured")
        return

    webhook_url = f"{base_url}/vk/webhook"
    print(f"📡 VK webhook URL (configure in VK admin): {webhook_url}")
    print(f"📡 VK confirmation token: {confirmation_token}")
    print("📝 Please configure this URL manually in VK group settings → Callback servers")



# ----------------------------
# LIFESPAN (ONLY ONE)
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🔥 LIFESPAN START")

    await init_db()

    payment_task = asyncio.create_task(payment_side_effects_retry_worker())

    fiscal_task = None
    if settings.MYTAX_API_URL and settings.MYTAX_API_TOKEN:
        fiscal_task = asyncio.create_task(fiscal_retry_worker())

    try:
        base_url = await asyncio.to_thread(wait_for_cloudflared_url)
        print(f"🌍 Public URL: {base_url}")

        # Установка вебхуков
        set_telegram_webhook(base_url)
        set_vk_webhook(base_url)

        print("🎉 WEBHOOKS CONFIGURED")

        yield

    finally:
        print("🧹 Shutting down...")

        payment_task.cancel()
        if fiscal_task:
            fiscal_task.cancel()

        await asyncio.gather(
            payment_task,
            fiscal_task if fiscal_task else asyncio.sleep(0),
            return_exceptions=True
        )

        from app.platforms.telegram.aiogram_bot import bot
        try:
            await bot.session.close()
        except Exception as e:
            print(f"⚠️ Bot session close error: {e}")


# ----------------------------
# APP
# ----------------------------
init_logging()
app = FastAPI(lifespan=lifespan)


# ----------------------------
# ERROR MIDDLEWARE
# ----------------------------
import traceback
from fastapi import Request
from fastapi.responses import JSONResponse


@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)

    except Exception as exc:
        tb = traceback.format_exc()

        print(f"[GLOBAL EXCEPTION] {exc}\n{tb}")

        with open("/tmp/err.log", "a", encoding="utf-8") as f:
            f.write(f"{exc}\n{tb}\n")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )


# ----------------------------
# ROUTES
# ----------------------------
app.include_router(telegram_router.router, prefix="/telegram")
app.include_router(vk_router.router, prefix="/vk")
app.include_router(admin.router, prefix="/admin")
app.include_router(billing.router, prefix="/billing")


@app.get("/health")
async def health():
    return {"status": "ok"}