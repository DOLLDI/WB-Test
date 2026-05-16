import logging
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.db import list_platform_user_ids

async def broadcast(
    text: str,
    bot,
    image_url: str | None = None,
    button_text: str | None = None,
    button_url: str | None = None,
):
    """
    Рассылка сообщения всем пользователям Telegram, кроме самого бота.
    """
    bot_id = (await bot.get_me()).id
    attempted = 0
    sent = 0
    failed = 0
    reply_markup = None
    if button_text and button_url:
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=button_text, url=button_url)]]
        )
    for raw_user_id in await list_platform_user_ids("telegram"):
        user_id = int(raw_user_id)
        if user_id == bot_id:
            continue
        attempted += 1
        try:
            if image_url:
                await bot.send_photo(user_id, image_url, caption=text, reply_markup=reply_markup)
            else:
                await bot.send_message(user_id, text, reply_markup=reply_markup)
            sent += 1
            logging.info(f"[TG BROADCAST] sent to {user_id}")
        except Exception as e:
            failed += 1
            logging.warning(f"[TG BROADCAST] error for {user_id}: {e}")
    return {
        "platform": "telegram",
        "attempted": attempted,
        "sent": sent,
        "failed": failed,
    }
