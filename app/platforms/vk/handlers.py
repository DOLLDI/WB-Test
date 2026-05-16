import re
import json

from app.shared.types import VKCallback
from app.platforms.vk.vk_utils import edit_message, send_message, upload_message_photo
from app.services.billing import create_checkout_url
from app.services.db import (
    add_history,
    add_user,
    consume_request_limit,
    get_recent_payments,
    get_referral_stats,
    get_request_access,
    get_user_profile,
    increment_requests,
    set_referred_by,
)
from app.services.config import settings
from app.services.error_logger import log_api_error
from app.services.rate_limit import vk_rate_limiter
from app.services.wb import (
    WBError,
    WBNotFound,
    WBTemporaryUnavailable,
    analyze_wb_product,
    extract_wb_article,
    format_product_preview,
)
import httpx


def _normalize_command(text: str) -> str:
    return (text or "").strip().lower()


def _extract_referral_code(text: str) -> str | None:
    stripped = (text or "").strip()
    lower = stripped.lower()
    prefixes = ("реф ", "ref ", "/ref ", "/start ")
    for prefix in prefixes:
        if lower.startswith(prefix):
            value = stripped[len(prefix):].strip()
            return value or None
    return None


def _build_start_message() -> str:
    return (
        "Привет! Отправьте ссылку на товар Wildberries или его артикул, и я пришлю превью товара, отзывы и короткий вывод по покупке.\n\n"
        "В день доступна 1 бесплатная проверка. Остаток проверок и покупку пакета или PRO можно открыть командой 'профиль'.\n"
        "Для привязки приглашения отправьте: реф КОД."
    )


def build_start_keyboard() -> str:
    return json.dumps(
        {
            "one_time": False,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "Мой кабинет",
                            "payload": json.dumps({"cmd": "profile"}, ensure_ascii=False),
                        }
                    }
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "Купить проверки",
                            "payload": json.dumps({"cmd": "profile"}, ensure_ascii=False),
                        }
                    }
                ],
            ],
        },
        ensure_ascii=False,
    )


def format_profile_text(profile: dict, referral_stats: dict, recent_payments: list[dict], user_id: str) -> str:
    tariff = (profile.get("tariff") or "free").upper()
    balance_requests = profile.get("balance_requests") or 0
    free_left = max(0, 1 - (profile.get("free_requests_used_today") or 0))
    pro_expires_at = profile.get("pro_expires_at") or "-"
    registered_at = profile.get("registered_at") or "-"
    payment_lines = []
    for payment in recent_payments[:3]:
        payment_lines.append(
            f"- {payment['payment_type']} / {payment['amount']} ₽ / {payment['status']} / {payment['created_at']}"
        )
    payments_text = "\n".join(payment_lines) if payment_lines else "- Пока оплат нет"
    referral_code = referral_stats.get("referral_code") or f"vk_{user_id}"
    one_time_url = create_checkout_url("vk", user_id, "one_time")
    pro_url = create_checkout_url("vk", user_id, "pro")
    return (
        "Мой кабинет\n"
        f"Тариф: {tariff}\n"
        f"Остаток проверок: {balance_requests}\n"
        f"Бесплатных проверок сегодня: {free_left}\n"
        f"Подписка активна до: {pro_expires_at}\n"
        f"Дата регистрации: {registered_at}\n\n"
        f"Реферальный код: {referral_code}\n"
        f"Приглашено: {referral_stats.get('invited_total', 0)}\n"
        f"Бонусов начислено за приглашённых: {referral_stats.get('rewarded_total', 0)}\n"
        f"Чтобы привязать реферальный код, отправьте: реф {referral_code}\n\n"
        f"Последние оплаты:\n{payments_text}\n\n"
        f"Купить пакет 5 проверок: {one_time_url}\n"
        f"Купить PRO на 30 дней: {pro_url}\n\n"
        f"Подписка PRO даёт 30 проверок на 30 дней. Если лимит закончился, потребуется продление или пакет проверок. Бонус за первого оплатившего приглашённого: {settings.REFERRAL_BONUS_REQUESTS} проверки."
    )


def build_profile_keyboard(user_id: str, profile: dict) -> str:
    pro_button_text = "Продлить PRO на 30 дней" if (profile.get("tariff") or "").lower() == "pro" else "Купить PRO на 30 дней"
    return json.dumps(
        {
            "inline": True,
            "buttons": [
                [
                    {
                        "action": {
                            "type": "open_link",
                            "link": create_checkout_url("vk", user_id, "one_time"),
                            "label": "Купить пакет 5 проверок",
                        }
                    }
                ],
                [
                    {
                        "action": {
                            "type": "open_link",
                            "link": create_checkout_url("vk", user_id, "pro"),
                            "label": pro_button_text,
                        }
                    }
                ],
                [
                    {
                        "action": {
                            "type": "text",
                            "label": "Обновить кабинет",
                            "payload": json.dumps({"cmd": "profile"}, ensure_ascii=False),
                        }
                    }
                ],
            ],
        },
        ensure_ascii=False,
    )


async def handle_profile_command(peer_id: str):
    profile = await get_user_profile("vk", peer_id)
    referral_stats = await get_referral_stats("vk", peer_id)
    recent_payments = await get_recent_payments("vk", peer_id, 5)
    if not profile:
        return "Профиль пока не найден. Попробуйте ещё раз через несколько секунд.", None
    return (
        format_profile_text(profile, referral_stats, recent_payments, peer_id),
        build_profile_keyboard(peer_id, profile),
    )


async def handle_referral_command(peer_id: str, text: str):
    referral_code = _extract_referral_code(text)
    if not referral_code:
        return "Не удалось прочитать реферальный код. Используйте формат: реф КОД"
    attached = await set_referred_by("vk", peer_id, referral_code)
    if attached:
        return (
            "Реферальный код сохранён. Когда вы впервые оплатите пакет или PRO, "
            "пригласивший пользователь получит бонус."
        )
    return "Не удалось привязать код. Возможно, он неверный, уже привязан или принадлежит вам."


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def format_wb_preview_text(analysis) -> str:
    preview = _strip_html(
        format_product_preview(
            analysis.product,
            analysis.total_reviews_loaded,
            len(analysis.selected_reviews),
        )
    )
    return preview

async def call_proxy(prompt: str) -> str:
    url = settings.PROXYAPI_URL.rstrip("/") + "/chat/completions"
    system_prompt = settings.VK_SYSTEM_PROMPT or ""
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    headers = {"Authorization": f"Bearer {settings.PROXYAPI_KEY}"} if settings.PROXYAPI_KEY else {}
    import logging
    logging.warning(
        "VK call_proxy: url=%s, has_api_key=%s, system_prompt_len=%s, user_prompt_len=%s",
        url,
        bool(settings.PROXYAPI_KEY),
        len(system_prompt),
        len(prompt or ""),
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            j = r.json()
            # Совместимость с OpenAI/ProxyAPI
            if "choices" in j and j["choices"]:
                return j["choices"][0]["message"]["content"]
            fallback_reply = j.get('reply') or j.get('response') or j.get('text')
            if fallback_reply:
                return fallback_reply
            log_api_error(f"VK ProxyAPI unexpected payload: {j}")
            return None
    except Exception as e:
        log_api_error(f"VK ProxyAPI error: {e}")
        logging.warning(f"VK call_proxy error: {e}")
        return None

async def handle_event(data: dict):
    cb = VKCallback(**data)
    if cb.type == 'message_new':
        import logging
        from app.services.db import is_vk_message_processed, mark_vk_message_processed
        obj = cb.object
        msg = obj.get('message') or obj
        peer_id = msg.get('peer_id') or msg.get('from_id')
        text = msg.get('text')
        message_id = msg.get('id')
        logging.warning(
            "VK EVENT: peer_id=%s, message_id=%s, text_len=%s",
            peer_id,
            message_id,
            len(text or ""),
        )
        if not text or message_id is None:
            return
        # Защита от дублей
        if await is_vk_message_processed(message_id):
            logging.warning(f"VK DUPLICATE: message_id={message_id} уже обработан, пропуск")
            return
        peer_id = str(peer_id)
        await add_user("vk", peer_id, None)

        normalized = _normalize_command(text)
        if normalized in {"/start", "start", "начать"}:
            await mark_vk_message_processed(message_id)
            await send_message(int(peer_id), _build_start_message(), keyboard=build_start_keyboard())
            return

        if normalized in {"/profile", "profile", "профиль", "кабинет", "обновить кабинет"}:
            profile_text, profile_keyboard = await handle_profile_command(peer_id)
            await mark_vk_message_processed(message_id)
            await send_message(int(peer_id), profile_text, keyboard=profile_keyboard)
            return

        if _extract_referral_code(text):
            referral_text = await handle_referral_command(peer_id, text)
            await mark_vk_message_processed(message_id)
            if (text or "").strip().lower().startswith("/start "):
                await send_message(
                    int(peer_id),
                    f"{referral_text}\n\n{_build_start_message()}",
                    keyboard=build_start_keyboard(),
                )
            else:
                await send_message(int(peer_id), referral_text)
            return

        allowed_by_rate, retry_after = await vk_rate_limiter.allow(
            key=f"vk:{peer_id}",
            max_requests=settings.ANTIFLOOD_MAX_REQUESTS,
            window_seconds=settings.ANTIFLOOD_WINDOW_SECONDS,
        )
        if not allowed_by_rate:
            await mark_vk_message_processed(message_id)
            await send_message(int(peer_id), f"Слишком много запросов подряд. Подождите примерно {retry_after} сек. и попробуйте снова.")
            return

        allowed, reason, profile = await get_request_access("vk", peer_id)
        if not allowed:
            await mark_vk_message_processed(message_id)
            if reason.startswith("Лимит") and profile:
                await send_message(
                    int(peer_id),
                    "Лимит проверок исчерпан. Выберите пакет проверок или продлите подписку кнопками ниже.",
                    keyboard=build_profile_keyboard(peer_id, profile),
                )
            else:
                await send_message(int(peer_id), reason)
            return
        await mark_vk_message_processed(message_id)
        # Анимация  
        import asyncio
        thinking_msgs = ["Думаю над ответом.", "Думаю над ответом..", "Думаю над ответом..."]
        msg_send = await send_message(int(peer_id), thinking_msgs[0])
        logging.warning(f"VK SEND_MESSAGE: resp={msg_send}")
        sent_id = None
        if isinstance(msg_send, dict):
            sent_id = msg_send.get('response')
            if isinstance(sent_id, dict):
                sent_id = sent_id.get('message_id')
            elif isinstance(sent_id, list) and sent_id:
                sent_id = sent_id[0]
        # Ограничение на 3 анимации, чтобы не спамить VK
        for anim in thinking_msgs[1:]:
            await asyncio.sleep(0.7)
            if sent_id:
                try:
                    await edit_message(int(peer_id), sent_id, anim)
                except Exception as e:
                    logging.warning(f"VK EDIT_MESSAGE error: {e}")

        wb_article = extract_wb_article(text)
        try:
            reply = None
            wb_preview = None
            wb_attachment = None
            if wb_article:
                analysis = await analyze_wb_product(text)
                wb_preview = format_wb_preview_text(analysis)
                if analysis.product.image_url:
                    try:
                        wb_attachment = await upload_message_photo(analysis.product.image_url)
                    except Exception:
                        wb_preview = f"{wb_preview}\nФото: {analysis.product.image_url}"
                reply = _strip_html(analysis.summary_html)
            else:
                reply = await asyncio.wait_for(call_proxy(text), timeout=30)
        except asyncio.TimeoutError:
            reply = None
            wb_preview = None
        except WBTemporaryUnavailable as error:
            error_text = str(error)
            if sent_id:
                try:
                    await edit_message(int(peer_id), sent_id, error_text)
                except Exception:
                    await send_message(int(peer_id), error_text)
            else:
                await send_message(int(peer_id), error_text)
            return
        except WBNotFound as error:
            error_text = str(error)
            if sent_id:
                try:
                    await edit_message(int(peer_id), sent_id, error_text)
                except Exception:
                    await send_message(int(peer_id), error_text)
            else:
                await send_message(int(peer_id), error_text)
            return
        except WBError as error:
            error_text = str(error)
            if sent_id:
                try:
                    await edit_message(int(peer_id), sent_id, error_text)
                except Exception:
                    await send_message(int(peer_id), error_text)
            else:
                await send_message(int(peer_id), error_text)
            return

        if reply and reply.strip():
            consumed, consume_reason = await consume_request_limit("vk", peer_id)
            if not consumed:
                if sent_id:
                    try:
                        await edit_message(int(peer_id), sent_id, consume_reason)
                    except Exception:
                        await send_message(int(peer_id), consume_reason)
                else:
                    await send_message(int(peer_id), consume_reason)
                return
            await increment_requests("vk", peer_id)

        # Финальный ответ\ошибка
        if reply:
            history_reply = reply
            if wb_article:
                await add_history("vk", peer_id, text, history_reply)
                if sent_id:
                    try:
                        await edit_message(int(peer_id), sent_id, "Анализ товара готов.")
                    except Exception as e:
                        logging.warning(f"VK FINAL EDIT_MESSAGE error: {e}")
                if wb_preview:
                    await send_message(int(peer_id), wb_preview, attachment=wb_attachment)
                await send_message(int(peer_id), reply)
            else:
                await add_history("vk", peer_id, text, history_reply)
                if sent_id:
                    try:
                        await edit_message(int(peer_id), sent_id, reply)
                    except Exception as e:
                        logging.warning(f"VK FINAL EDIT_MESSAGE error: {e}")
                        await send_message(int(peer_id), reply)
                else:
                    await send_message(int(peer_id), reply)
        else:
            error_text = "Извините, сервис временно недоступен. Попробуйте позже."
            if sent_id:
                try:
                    await edit_message(int(peer_id), sent_id, error_text)
                except Exception as e:
                    logging.warning(f"VK ERROR EDIT_MESSAGE: {e}")
                    await send_message(int(peer_id), error_text)
            else:
                await send_message(int(peer_id), error_text)
