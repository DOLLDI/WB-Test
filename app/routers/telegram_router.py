from fastapi import APIRouter, Request, HTTPException
from aiogram.types import Update
from pathlib import Path
from app.platforms.telegram.aiogram_bot import bot, dp
from app.services.error_logger import log_api_error, PUBLIC_WEBHOOK_ERROR_DETAIL
router = APIRouter()

TELEGRAM_WEBHOOK_LOG_PATH = Path("logs/telegram_webhook_in.log")


def append_telegram_webhook_log(line: str):
    TELEGRAM_WEBHOOK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TELEGRAM_WEBHOOK_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)

@router.post('/webhook')
async def telegram_webhook(req: Request):
    import json, traceback
    try:
        append_telegram_webhook_log("[HANDLER ENTERED]\n")
        print("[HANDLER ENTERED]")
    except Exception as log_exc:
        print(f"[LOGGING ERROR] {log_exc}")
    try:
        try:
            data = await req.json()
        except Exception as json_exc:
            tb = traceback.format_exc()
            print(f"[JSON PARSE ERROR] {json_exc}\n{tb}")
            log_api_error(f"Telegram webhook JSON error: {json_exc}\n{tb}")
            raise HTTPException(status_code=400, detail="Invalid JSON")
        try:
            append_telegram_webhook_log(f"[INCOMING UPDATE] {json.dumps(data, ensure_ascii=False)}\n")
            print(f"[INCOMING UPDATE] {json.dumps(data, ensure_ascii=False)}")
        except Exception as log_exc:
            print(f"[LOGGING ERROR] {log_exc}")
        update = Update.model_validate(data)
        await dp.feed_update(bot, update)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        log_api_error(f"Telegram webhook error: {e}\n{tb}")
        raise HTTPException(status_code=500, detail=PUBLIC_WEBHOOK_ERROR_DETAIL)
    return {"ok": True}
