import json
import logging

import httpx

from app.services.config import settings
from app.services.db import list_platform_user_ids
from app.platforms.vk.vk_utils import upload_message_photo


VK_API = "https://api.vk.com/method"


async def _call_vk_method(client: httpx.AsyncClient, method: str, data: dict):
    response = await client.post(
        f"{VK_API}/{method}",
        data={
            "access_token": settings.VK_GROUP_TOKEN,
            "v": "5.131",
            **data,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"VK API {method} error: {payload['error']}")
    return payload.get("response")
async def broadcast_vk(
    text: str,
    image_url: str | None = None,
    button_text: str | None = None,
    button_url: str | None = None,
):
    """
    Рассылка сообщения всем пользователям VK.
    Поддерживает текст, картинку и кнопку-ссылку.
    Если загрузка картинки в VK не удалась, используется fallback-ссылка в тексте.
    """
    final_text = text
    attempted = 0
    sent = 0
    failed = 0
    keyboard = None
    if button_text and button_url:
        keyboard = json.dumps(
            {
                "inline": True,
                "buttons": [[
                    {
                        "action": {
                            "type": "open_link",
                            "link": button_url,
                            "label": button_text,
                        }
                    }
                ]],
            },
            ensure_ascii=False,
        )
    async with httpx.AsyncClient(timeout=20.0) as client:
        attachment = None
        if image_url:
            try:
                attachment = await upload_message_photo(image_url)
            except Exception as error:
                logging.warning(f"[VK BROADCAST] image upload fallback for {image_url}: {error}")
                final_text = f"{final_text}\n\nИзображение: {image_url}"

        for peer_id in await list_platform_user_ids("vk"):
            attempted += 1
            try:
                params = {
                    "peer_id": peer_id,
                    "message": final_text,
                    "random_id": 0,
                }
                if keyboard:
                    params["keyboard"] = keyboard
                if attachment:
                    params["attachment"] = attachment
                resp = await _call_vk_method(client, "messages.send", params)
                sent += 1
                logging.info(f"[VK BROADCAST] sent to {peer_id}: {resp}")
            except Exception as error:
                failed += 1
                logging.error(f"[VK BROADCAST] error for {peer_id}: {error}")
    return {
        "platform": "vk",
        "attempted": attempted,
        "sent": sent,
        "failed": failed,
    }
