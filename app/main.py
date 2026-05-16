from contextlib import asynccontextmanager
import asyncio

from fastapi import FastAPI
from app.routers import admin, billing, telegram_router, vk_router
from app.services.billing import payment_side_effects_retry_worker
from app.services.config import settings
from app.services.db import init_db
from app.services.fiscal import fiscal_retry_worker
from app.services.logger import init_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    fiscal_retry_task = None
    payment_side_effects_task = asyncio.create_task(payment_side_effects_retry_worker())
    if settings.MYTAX_API_URL and settings.MYTAX_API_TOKEN:
        fiscal_retry_task = asyncio.create_task(fiscal_retry_worker())
    try:
        yield
    finally:
        if fiscal_retry_task:
            fiscal_retry_task.cancel()
            try:
                await fiscal_retry_task
            except asyncio.CancelledError:
                pass
        payment_side_effects_task.cancel()
        try:
            await payment_side_effects_task
        except asyncio.CancelledError:
            pass
        from app.platforms.telegram.aiogram_bot import bot

        await bot.session.close()


import traceback
from fastapi import Request, Response
from fastapi.responses import JSONResponse

init_logging()
app = FastAPI(lifespan=lifespan)

# Глобальный middleware для логирования traceback всех 500 ошибок
@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        tb = traceback.format_exc()
        with open("/tmp/err.log", "a", encoding="utf-8") as f:
            f.write(f"[GLOBAL EXCEPTION] {exc}\n{tb}\n")
        print(f"[GLOBAL EXCEPTION] {exc}\n{tb}")
        from app.services.error_logger import log_api_error
        log_api_error(f"[GLOBAL EXCEPTION] {exc}\n{tb}")
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error", "traceback": tb})

app.include_router(telegram_router.router, prefix="/telegram")
app.include_router(vk_router.router, prefix="/vk")
app.include_router(admin.router, prefix="/admin")
app.include_router(billing.router, prefix="/billing")

@app.get("/health")
async def health():
    return {"status": "ok"}
