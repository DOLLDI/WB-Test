import asyncio
import re
from html import escape
from html.parser import HTMLParser
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.services.config import settings
from app.services.billing import create_checkout_url
from app.services.db import (
    add_user,
    add_history,
    consume_request_limit,
    delete_old_history,
    get_recent_payments,
    get_recent_history,
    get_request_access,
    get_referral_stats,
    get_user_profile,
    increment_requests,
    set_referred_by,
)
from app.services.prompts import get_system_prompt
from app.services.rate_limit import telegram_rate_limiter
from app.services.wb import (
    WBError,
    WBNotFound,
    WBTemporaryUnavailable,
    analyze_wb_product,
    extract_wb_article,
    format_product_preview,
)
from app.services.error_logger import PUBLIC_TECHNICAL_ERROR_MESSAGE, log_api_error
import openai
import os
BOT_USERNAME = None

BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

async def on_startup(bot: Bot):
    global BOT_USERNAME
    me = await bot.get_me()
    BOT_USERNAME = me.username

dp.startup.register(on_startup)

def h(value) -> str:
    return escape("" if value is None else str(value), quote=False)


class TelegramHTMLSanitizer(HTMLParser):
    allowed_tags = {"b", "strong", "i", "em", "u", "s", "code", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        normalized_tag = tag.lower()
        if normalized_tag in self.allowed_tags:
            self.parts.append(f"<{normalized_tag}>")
        else:
            self.parts.append(h(self.get_starttag_text() or ""))

    def handle_endtag(self, tag):
        normalized_tag = tag.lower()
        if normalized_tag in self.allowed_tags:
            self.parts.append(f"</{normalized_tag}>")
        else:
            self.parts.append(h(f"</{tag}>"))

    def handle_startendtag(self, tag, attrs):
        self.parts.append(h(self.get_starttag_text() or ""))

    def handle_data(self, data):
        self.parts.append(h(data))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(h(f"<!--{data}-->"))


def sanitize_telegram_html(value: str) -> str:
    sanitizer = TelegramHTMLSanitizer()
    sanitizer.feed(value or "")
    sanitizer.close()
    return "".join(sanitizer.parts) or "Извините, ответ не получен от ИИ."


def format_ai_reply_for_telegram(reply: str) -> str:
    lines = []
    for raw_line in escape(reply.strip()).splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if line.startswith("### "):
            lines.append(f"<b>{line[4:].strip()}</b>")
            continue
        if line.startswith("## "):
            lines.append(f"<b>{line[3:].strip()}</b>")
            continue
        if line.startswith("# "):
            lines.append(f"<b>{line[2:].strip()}</b>")
            continue
        if re.match(r"^[-*]\s+", line):
            lines.append(f"• {line[2:].strip()}")
            continue
        lines.append(raw_line)

    formatted = "\n".join(lines)
    formatted = re.sub(r"(?<!\*)\*\*(.+?)\*\*(?!\*)", r"<b>\1</b>", formatted)
    formatted = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"<i>\1</i>", formatted)
    formatted = re.sub(r"`([^`]+)`", r"<code>\1</code>", formatted)
    return formatted or "Извините, ответ не получен от ИИ."


def normalize_optional_broadcast_value(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    if cleaned.lower() in {"", "-", "нет", "пропустить", "skip"}:
        return None
    return cleaned


def build_broadcast_preview_text(data: dict) -> str:
    text = escape(data.get("broadcast_text") or "")
    image_url = escape(data["broadcast_image_url"]) if data.get("broadcast_image_url") else "не задано"
    if data.get("broadcast_button_text") and data.get("broadcast_button_url"):
        button_info = f"{escape(data['broadcast_button_text'])} -> {escape(data['broadcast_button_url'])}"
    else:
        button_info = "не задана"
    return (
        "<b>Предпросмотр рассылки</b>\n\n"
        f"<b>Текст:</b>\n<code>{text}</code>\n\n"
        f"<b>Изображение:</b> {image_url}\n"
        f"<b>Кнопка:</b> {button_info}\n\n"
        "Подтвердить рассылку?"
    )


# FSM для админки
class AdminPanel(StatesGroup):
    waiting_broadcast_text = State()
    waiting_broadcast_image = State()
    waiting_broadcast_button_text = State()
    waiting_broadcast_button_url = State()
    confirm_broadcast = State()

# Главное меню убрано для всех кром админа
main_keyboard = None

# Reply-клавиатура для админа
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
user_reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Мой кабинет")]
    ],
    resize_keyboard=True
)

admin_reply_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/start"), KeyboardButton(text="/admin")]
    ],
    resize_keyboard=True
)

# Клавиатура админ-панели
admin_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Рассылка пользователям", callback_data="broadcast")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="Выйти", callback_data="exit_admin")]
    ]
)

# Клавиатура подтверждения рассылки 
confirm_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить", callback_data="confirm_broadcast")],
        [InlineKeyboardButton(text="Отклонить", callback_data="cancel_broadcast")]
    ]
)

profile_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Купить пакет 5 проверок", url="__ONE_TIME_URL__")],
        [InlineKeyboardButton(text="Купить PRO на 30 дней", url="__PRO_URL__")],
        [InlineKeyboardButton(text="Обновить кабинет", callback_data="refresh_profile")],
    ]
)


# Вход в админ-панель
@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    user_id = str(message.from_user.id)
    if user_id in settings.admin_ids:
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer("Админ-панель", reply_markup=admin_keyboard)
        await state.clear()
    else:
        await message.answer("⛔️ У вас нет доступа к админ-панели.")

@dp.callback_query(lambda c: c.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    if user_id in settings.admin_ids:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer("Админ-панель", reply_markup=admin_keyboard)
        await state.clear()
    else:
        await callback.answer("⛔️ Нет доступа", show_alert=True)

# Кнопка "Выйти из админ-панели"
@dp.callback_query(lambda c: c.data == "exit_admin")
async def exit_admin(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    data = await state.get_data()
    prompt_msg_id = data.get("broadcast_prompt_msg_id")
    if prompt_msg_id:
        try:
            await callback.bot.delete_message(callback.message.chat.id, prompt_msg_id)
        except Exception:
            pass
    await callback.answer("Вы вышли из админ-панели.", show_alert=True)
    await state.clear()

# Кнопка "Статистика"
@dp.callback_query(lambda c: c.data == "stats")
async def admin_stats(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    if user_id not in settings.admin_ids:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    from app.services.db import get_stats
    rows = await get_stats()
    text = "<b>Статистика сервиса:</b>\n"
    total_users = 0
    total_requests = 0
    for platform, users, requests in rows:
        text += f"{platform}: пользователей — {users}, запросов — {requests or 0}\n"
        total_users += users
        total_requests += requests or 0
    text += f"Всего: пользователей — {total_users}, запросов — {total_requests}"
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(text, reply_markup=admin_keyboard)

# Кнопка "Рассылка пользователям"
@dp.callback_query(lambda c: c.data == "broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    user_id = str(callback.from_user.id)
    if user_id not in settings.admin_ids:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    try:
        await callback.message.delete()
    except Exception:
        pass
    msg = await callback.message.answer(
        "Отправьте текст для рассылки. После этого я по очереди спрошу URL картинки и данные кнопки."
    )
    await state.update_data(broadcast_prompt_msg_id=msg.message_id)
    await state.set_state(AdminPanel.waiting_broadcast_text)

@dp.message(AdminPanel.waiting_broadcast_text)
async def admin_broadcast_text(message: Message, state: FSMContext):
    await state.update_data(broadcast_text=message.text)
    data = await state.get_data()
    prompt_msg_id = data.get("broadcast_prompt_msg_id")
    if prompt_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_msg_id)
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("Отправьте URL изображения для рассылки или '-' чтобы пропустить.")
    await state.set_state(AdminPanel.waiting_broadcast_image)


@dp.message(AdminPanel.waiting_broadcast_image)
async def admin_broadcast_image(message: Message, state: FSMContext):
    await state.update_data(broadcast_image_url=normalize_optional_broadcast_value(message.text))
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer("Отправьте текст кнопки или '-' чтобы пропустить кнопку.")
    await state.set_state(AdminPanel.waiting_broadcast_button_text)


@dp.message(AdminPanel.waiting_broadcast_button_text)
async def admin_broadcast_button_text(message: Message, state: FSMContext):
    button_text = normalize_optional_broadcast_value(message.text)
    await state.update_data(broadcast_button_text=button_text)
    try:
        await message.delete()
    except Exception:
        pass
    if not button_text:
        await state.update_data(broadcast_button_url=None)
        data = await state.get_data()
        await message.answer(build_broadcast_preview_text(data), reply_markup=confirm_keyboard)
        await state.set_state(AdminPanel.confirm_broadcast)
        return
    await message.answer("Отправьте URL для кнопки или '-' чтобы убрать кнопку.")
    await state.set_state(AdminPanel.waiting_broadcast_button_url)


@dp.message(AdminPanel.waiting_broadcast_button_url)
async def admin_broadcast_button_url(message: Message, state: FSMContext):
    button_url = normalize_optional_broadcast_value(message.text)
    if not button_url:
        await state.update_data(broadcast_button_text=None, broadcast_button_url=None)
    else:
        await state.update_data(broadcast_button_url=button_url)
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    await message.answer(build_broadcast_preview_text(data), reply_markup=confirm_keyboard)
    await state.set_state(AdminPanel.confirm_broadcast)

# Подтверждение рассылки инлайн
@dp.callback_query(lambda c: c.data == "confirm_broadcast")
async def admin_broadcast_confirm(callback: CallbackQuery, state: FSMContext):
    import logging
    logging.info(f"[ADMIN] confirm_broadcast: user={callback.from_user.id} state={await state.get_state()}")
    if not await state.get_state():
        await callback.answer("Уже отправлено!", show_alert=False)
        return
    data = await state.get_data()
    text = data.get("broadcast_text")
    image_url = data.get("broadcast_image_url")
    button_text = data.get("broadcast_button_text")
    button_url = data.get("broadcast_button_url")
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    from app.platforms.telegram.broadcast import broadcast as tg_broadcast
    from app.platforms.vk.broadcast import broadcast_vk
    logging.info("[ADMIN] Telegram broadcast start")
    tg_result = await tg_broadcast(text, bot, image_url=image_url, button_text=button_text, button_url=button_url)
    logging.info("[ADMIN] Telegram broadcast done")
    logging.info("[ADMIN] VK broadcast start")
    vk_result = await broadcast_vk(text, image_url=image_url, button_text=button_text, button_url=button_url)
    logging.info("[ADMIN] VK broadcast done")
    await callback.answer("Рассылка отправлена!")
    await callback.message.answer(
        "Рассылка завершена.\n"
        f"Telegram: {tg_result['sent']}/{tg_result['attempted']}\n"
        f"VK: {vk_result['sent']}/{vk_result['attempted']}\n"
        f"Всего ошибок: {tg_result['failed'] + vk_result['failed']}",
        reply_markup=admin_keyboard,
    )

# Отклонение рассылки 
@dp.callback_query(lambda c: c.data == "cancel_broadcast")
async def admin_broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer("Рассылка отменена.", reply_markup=admin_keyboard)
    await state.clear()

# Хендлер команды /start и регистрация пользователя
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user_id = str(message.from_user.id)
    await add_user("telegram", user_id, message.from_user.username)
    start_parts = (message.text or "").split(maxsplit=1)
    referral_payload = start_parts[1].strip() if len(start_parts) > 1 else ""
    referral_attached = False
    if referral_payload:
        referral_attached = await set_referred_by("telegram", user_id, referral_payload)
    if user_id in settings.admin_ids:
        await message.answer(
            "Привет, админ! Быстрое меню доступно ниже. Для проверки лимитов открой /profile.",
            reply_markup=admin_reply_keyboard
        )
    else:
        text = (
            "Привет! Отправьте ссылку на товар Wildberries или его артикул, и я покажу превью товара, отзывы и короткий вывод по покупке.\n\n"
            "В день доступна 1 бесплатная проверка. Остаток проверок, оплату и продление PRO можно посмотреть в /profile.\n"
            "Если у вас есть приглашение, можно открыть бота по реферальной ссылке или передать код через /start КОД."
        )
        if referral_attached:
            text += "\n\nРеферальная ссылка сохранена. Когда вы впервые оплатите пакет или PRO, пригласивший пользователь получит бонус."
        await message.answer(text, reply_markup=user_reply_keyboard)


def format_profile_text(profile: dict, referral_stats: dict, recent_payments: list[dict], referral_link: str) -> str:
    tariff = h((profile.get("tariff") or "free").upper())
    balance_requests = profile.get("balance_requests") or 0
    free_left = max(0, 1 - (profile.get("free_requests_used_today") or 0))
    pro_expires_at = h(profile.get("pro_expires_at") or "-")
    registered_at = h(profile.get("registered_at") or "-")
    payment_lines = []
    for payment in recent_payments[:3]:
        payment_lines.append(
            f"- {h(payment['payment_type'])} / {h(payment['amount'])} ₽ / {h(payment['status'])} / {h(payment['created_at'])}"
        )
    payments_text = "\n".join(payment_lines) if payment_lines else "- Пока оплат нет"
    return (
        "<b>Мой кабинет</b>\n"
        f"Тариф: <b>{tariff}</b>\n"
        f"Остаток проверок: <b>{balance_requests}</b>\n"
        f"Бесплатных проверок сегодня: <b>{free_left}</b>\n"
        f"Подписка активна до: <b>{pro_expires_at}</b>\n"
        f"Дата регистрации: <b>{registered_at}</b>\n\n"
        f"<b>Реферальный код:</b> <code>{h(referral_stats.get('referral_code') or '-')}</code>\n"
        f"<b>Приглашено:</b> <b>{referral_stats.get('invited_total', 0)}</b>\n"
        f"<b>Бонусов начислено за приглашённых:</b> <b>{referral_stats.get('rewarded_total', 0)}</b>\n"
        f"<b>Реферальная ссылка:</b>\n<code>{h(referral_link)}</code>\n\n"
        f"<b>Последние оплаты:</b>\n{payments_text}\n\n"
        f"Подписка PRO даёт 30 проверок на 30 дней. Если лимит закончился, потребуется продление или пакет проверок. Бонус за первого оплатившего приглашённого: <b>{settings.REFERRAL_BONUS_REQUESTS}</b> проверки."
    )


# async def build_profile_payload(user_id: str):
#     profile = await get_user_profile("telegram", user_id)
#     referral_stats = await get_referral_stats("telegram", user_id)
#     recent_payments = await get_recent_payments("telegram", user_id, 5)
#     bot_me = await bot.get_me()
#     referral_code = referral_stats.get("referral_code") or f"telegram_{user_id}"
#     referral_link = f"https://t.me/{bot_me.username}?start={referral_code}" if bot_me.username else referral_code
#     return profile, referral_stats, recent_payments, referral_link
async def build_profile_payload(user_id: str):
    profile = await get_user_profile("telegram", user_id)
    referral_stats = await get_referral_stats("telegram", user_id)
    recent_payments = await get_recent_payments("telegram", user_id, 5)

    referral_code = referral_stats.get("referral_code") or f"telegram_{user_id}"

    if BOT_USERNAME:
        referral_link = f"https://t.me/{BOT_USERNAME}?start={referral_code}"
    else:
        referral_link = referral_code

    return profile, referral_stats, recent_payments, referral_link


def build_profile_keyboard(user_id: str, profile: dict) -> InlineKeyboardMarkup:
    pro_button_text = "Продлить PRO на 30 дней" if (profile.get("tariff") or "").lower() == "pro" else "Купить PRO на 30 дней"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Купить пакет 5 проверок",
                    url=create_checkout_url("telegram", user_id, "one_time"),
                )
            ],
            [
                InlineKeyboardButton(
                    text=pro_button_text,
                    url=create_checkout_url("telegram", user_id, "pro"),
                )
            ],
            [InlineKeyboardButton(text="Обновить кабинет", callback_data="refresh_profile")],
        ]
    )


async def send_profile_message(message: Message):
    user_id = str(message.from_user.id)
    await add_user("telegram", user_id, message.from_user.username)
    profile, referral_stats, recent_payments, referral_link = await build_profile_payload(user_id)
    if not profile:
        await message.answer("Профиль пока не найден. Попробуйте ещё раз через несколько секунд.")
        return
    await message.answer(
        format_profile_text(profile, referral_stats, recent_payments, referral_link),
        reply_markup=build_profile_keyboard(user_id, profile),
    )


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    try:
        await send_profile_message(message)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        from app.services.error_logger import log_api_error
        log_api_error(f"/profile error: {e}\n{traceback.format_exc()}")
        await message.answer("Произошла ошибка при получении профиля. Попробуйте позже.")


@dp.message(F.text == "Мой кабинет")
async def cmd_profile_button(message: Message):
    try:
        await send_profile_message(message)
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        from app.services.error_logger import log_api_error
        log_api_error(f"Мой кабинет error: {e}\n{traceback.format_exc()}")
        await message.answer("Произошла ошибка при получении профиля. Попробуйте позже.")


@dp.callback_query(lambda c: c.data == "refresh_profile")
async def refresh_profile(callback: CallbackQuery):
    user_id = str(callback.from_user.id)
    profile, referral_stats, recent_payments, referral_link = await build_profile_payload(user_id)
    if not profile:
        await callback.answer("Профиль пока недоступен", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            format_profile_text(profile, referral_stats, recent_payments, referral_link),
            reply_markup=build_profile_keyboard(user_id, profile),
        )
    except Exception:
        await callback.message.answer(
            format_profile_text(profile, referral_stats, recent_payments, referral_link),
            reply_markup=build_profile_keyboard(user_id, profile),
        )
    await callback.answer("Кабинет обновлён")


# Основной хендлер сообщений 
ADMIN_SERVICE_TEXTS = {
    "админ-панель",
    "рассылка пользователям",
    "статистика",
    "выйти из админ-панели",
    "подтвердить рассылку",
    "отклонить рассылку"
}


@dp.message(F.text)
async def handle_message(message: Message, state: FSMContext):
    if message.text.lower().strip() in ADMIN_SERVICE_TEXTS:
        return
    user_id = str(message.from_user.id)
    data = await state.get_data()
    last_broadcast_text = data.get("last_broadcast_text")
    if last_broadcast_text and message.text.strip() == last_broadcast_text and user_id in settings.admin_ids:
        return
    await add_user("telegram", user_id, message.from_user.username)
    allowed_by_rate, retry_after = await telegram_rate_limiter.allow(
        key=f"telegram:{user_id}",
        max_requests=settings.ANTIFLOOD_MAX_REQUESTS,
        window_seconds=settings.ANTIFLOOD_WINDOW_SECONDS,
    )
    if not allowed_by_rate:
        await message.answer(
            f"Слишком много запросов подряд. Подождите примерно {retry_after} сек. и попробуйте снова."
        )
        return
    allowed, reason, profile = await get_request_access("telegram", user_id)
    if not allowed:
        if reason.startswith("Лимит") and profile:
            await message.answer(
                "Лимит проверок исчерпан. Выберите пакет проверок или продлите подписку ниже.",
                reply_markup=build_profile_keyboard(user_id, profile),
            )
        else:
            await message.answer(reason)
        return
    prompt = message.text
    wb_article = extract_wb_article(prompt)
    system_prompt = get_system_prompt()
    await delete_old_history()
    history = await get_recent_history("telegram", user_id)
    messages = [{"role": "system", "content": system_prompt}]
    for q, a in history:
        if q:
            messages.append({"role": "user", "content": q})
        if a:
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": prompt})

    msg = await message.answer("<i>Генерирую ответ.</i>")
    loading = True
    reply = ""

    async def animate_loading():
        dots = 1
        while loading:
            text = f"<i>Генерирую ответ{'.' * dots}</i>"
            try:
                await msg.edit_text(text)
            except Exception:
                pass
            dots = dots + 1 if dots < 3 else 1
            await asyncio.sleep(0.5)

    task = asyncio.create_task(animate_loading())
    try:
        if wb_article:
            analysis = await analyze_wb_product(wb_article)
            consumed, consume_reason = await consume_request_limit("telegram", user_id)
            if not consumed:
                loading = False
                await task
                await msg.edit_text(consume_reason)
                return
            await increment_requests("telegram", user_id)
            loading = False
            await task
            preview_text = format_product_preview(
                analysis.product,
                analysis.total_reviews_loaded,
                len(analysis.selected_reviews),
            )
            try:
                await msg.delete()
            except Exception:
                pass
            if analysis.product.image_url:
                await message.answer_photo(analysis.product.image_url, caption=preview_text)
            else:
                await message.answer(preview_text)
            safe_summary_html = sanitize_telegram_html(analysis.summary_html)
            await message.answer(safe_summary_html)
            await add_history("telegram", user_id, prompt, safe_summary_html)
            return
        async for chunk in proxyapi_stream_with_context(messages):
            reply += chunk
        loading = False
        await task
        if not reply or not reply.strip():
            reply = "Извините, ответ не получен от ИИ."
        formatted_reply = format_ai_reply_for_telegram(reply)
        consumed, consume_reason = await consume_request_limit("telegram", user_id)
        if not consumed:
            await msg.edit_text(consume_reason)
            return
        await increment_requests("telegram", user_id)
        await msg.edit_text(formatted_reply)
        await add_history("telegram", user_id, prompt, reply)
    except WBTemporaryUnavailable as error:
        loading = False
        await task
        await msg.edit_text(str(error))
    except WBNotFound as error:
        loading = False
        await task
        await msg.edit_text(str(error))
    except WBError as error:
        loading = False
        await task
        log_api_error(f"WBError: {error}")
        await msg.edit_text(str(error))
    except Exception as e:
        loading = False
        await task
        import traceback
        tb = traceback.format_exc()
        log_api_error(f"Telegram API error: {e}\n{tb}")
        await msg.edit_text(PUBLIC_TECHNICAL_ERROR_MESSAGE)


# Генератор для стриминга 
async def proxyapi_stream_with_context(messages):
    import logging
    logging.warning(
        "TG proxyapi_stream_with_context: base_url=%s, messages_count=%s",
        settings.PROXYAPI_URL,
        len(messages),
    )
    client = openai.AsyncOpenAI(
        api_key=settings.PROXYAPI_KEY,
        base_url=settings.PROXYAPI_URL
    )
    response = await client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=messages,
        stream=False
    )
    yield response.choices[0].message.content or ""



# Функция для запуска aiogram-бота с БД


def run():
    import sys
    if sys.platform.startswith("linux") or sys.platform == "darwin":
        try:
            import uvloop  # type: ignore # uvloop работает только на Unix-системах
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        except ImportError:
            pass
    from app.services.db import init_db

    async def main():
        await init_db()

        dp.startup.register(on_startup)  # 👈 ВОТ ЭТОГО НЕ ХВАТАЛО

        await dp.start_polling(bot)

    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен")


if __name__ == "__main__":
    run()
