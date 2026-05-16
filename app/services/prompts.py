from app.services.config import settings

def get_system_prompt():
    """
    Возвращает системный промпт для Telegram из настроек (.env.prompts)
    """
    return settings.TELEGRAM_SYSTEM_PROMPT or ""


def get_wb_summary_prompt():
    """
    Возвращает системный промпт для саммаризации отзывов Wildberries.
    """
    return settings.WB_SUMMARY_PROMPT or (
        "Ты аналитик маркетплейсов. На основе карточки товара и отзывов подготовь короткий вывод для покупателя. "
        "Ответ дай в HTML для Telegram: используй <b>заголовки</b>, обычные списки через тире и эмодзи. "
        "Обязательно верни блоки: Общая оценка, Плюсы, Минусы, Вердикт брать/не брать, Для кого подходит. "
        "Если данных мало, прямо скажи об этом."
    )
