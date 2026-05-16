from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from app.platforms.vk import handlers
from app.services import config

router = APIRouter()

@router.post('/webhook')
async def vk_webhook(req: Request):
    data = await req.json()
    if data.get('type') == 'confirmation':
        return PlainTextResponse(config.settings.VK_CONFIRMATION_TOKEN)
    await handlers.handle_event(data)
    return PlainTextResponse("ok")
