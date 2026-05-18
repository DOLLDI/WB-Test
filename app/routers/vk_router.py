from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from app.platforms.vk import handlers
from app.services import config

router = APIRouter()

@router.post('/webhook')
async def vk_webhook(req: Request):
    data = await req.json()
    if data.get('type') == 'confirmation':
        return PlainTextResponse(config.settings.VK_CONFIRMATION_TOKEN)
    if config.settings.VK_CALLBACK_SECRET and data.get("secret") != config.settings.VK_CALLBACK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid VK callback secret")
    await handlers.handle_event(data)
    return PlainTextResponse("ok")
