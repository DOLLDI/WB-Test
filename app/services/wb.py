import datetime
import re
from html import escape
from dataclasses import dataclass
from typing import Optional

import httpx
import openai

from app.services.config import settings
from app.services.error_logger import log_api_error
from app.services.prompts import get_wb_summary_prompt

WB_URL_RE = re.compile(r"wildberries\.ru/catalog/(\d+)/detail\.aspx", re.IGNORECASE)
WB_ARTICLE_RE = re.compile(r"\b(\d{6,12})\b")
WB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": "https://www.wildberries.ru/",
}

WB_TEMPORARY_UNAVAILABLE_MESSAGE = "WB временно ограничил доступ к данным, попробуйте через 15 минут."
WB_ANALYSIS_UNAVAILABLE_MESSAGE = "Не удалось обработать данные Wildberries. Попробуйте позже."


class WBError(Exception):
    pass


class WBTemporaryUnavailable(WBError):
    pass


class WBNotFound(WBError):
    pass


@dataclass
class WBReview:
    text: str
    rating: int
    created_at: str = ""
    pros: str = ""
    cons: str = ""


@dataclass
class WBProduct:
    article: str
    title: str
    brand: str = ""
    price: Optional[float] = None
    sale_price: Optional[float] = None
    rating: Optional[float] = None
    review_count: int = 0
    image_url: str = ""
    product_url: str = ""
    description: str = ""


@dataclass
class WBAnalysisResult:
    product: WBProduct
    selected_reviews: list[WBReview]
    summary_html: str
    total_reviews_loaded: int


def extract_wb_article(text: str) -> Optional[str]:
    url_match = WB_URL_RE.search(text)
    if url_match:
        return url_match.group(1)
    article_match = WB_ARTICLE_RE.search(text)
    if article_match:
        return article_match.group(1)
    return None


def _parse_price(raw_value) -> Optional[float]:
    if raw_value in (None, ""):
        return None
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None
    if value > 1000:
        return round(value / 100.0, 2)
    return round(value, 2)


def _extract_products(payload: dict) -> list[dict]:
    candidates = []
    if isinstance(payload.get("data"), dict):
        data = payload["data"]
        if isinstance(data.get("products"), list):
            candidates = data["products"]
    if not candidates and isinstance(payload.get("products"), list):
        candidates = payload["products"]
    return [item for item in candidates if isinstance(item, dict)]


def _parse_card_product(product_data: dict, article: str) -> WBProduct:
    sizes = product_data.get("sizes") or []
    size_price = sizes[0].get("price", {}) if sizes and isinstance(sizes[0], dict) else {}
    image_url = ""
    pics = product_data.get("pics") or product_data.get("images") or []
    if isinstance(pics, list) and pics:
        first_image = pics[0]
        if isinstance(first_image, str) and first_image.startswith("http"):
            image_url = first_image
    if not image_url:
        media_files = product_data.get("mediaFiles") or []
        if isinstance(media_files, list) and media_files:
            first_media = media_files[0]
            if isinstance(first_media, str) and first_media.startswith("http"):
                image_url = first_media

    return WBProduct(
        article=article,
        title=product_data.get("name") or f"Товар WB {article}",
        brand=product_data.get("brand") or "",
        price=_parse_price(size_price.get("basic") or product_data.get("priceU")),
        sale_price=_parse_price(size_price.get("product") or product_data.get("salePriceU")),
        rating=product_data.get("reviewRating") or product_data.get("rating"),
        review_count=int(product_data.get("feedbacks") or product_data.get("feedbackCount") or 0),
        image_url=image_url,
        product_url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
        description=product_data.get("description") or "",
    )


def _parse_reviews(payload: dict) -> list[WBReview]:
    review_items = []
    if isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("feedbacks"), list):
        review_items = payload["data"]["feedbacks"]
    elif isinstance(payload.get("feedbacks"), list):
        review_items = payload["feedbacks"]

    reviews: list[WBReview] = []
    for item in review_items:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or "").strip()
        pros = (item.get("pros") or "").strip()
        cons = (item.get("cons") or "").strip()
        if not text and not pros and not cons:
            continue
        reviews.append(
            WBReview(
                text=text,
                rating=int(item.get("productValuation") or item.get("valuation") or 0),
                created_at=str(item.get("createdDate") or item.get("createdAt") or ""),
                pros=pros,
                cons=cons,
            )
        )
    return reviews


def select_reviews_for_summary(reviews: list[WBReview]) -> list[WBReview]:
    if not reviews:
        return []
    newest = reviews[:30]
    lowest = sorted(reviews, key=lambda review: (review.rating or 0, review.created_at or ""))[:20]
    selected: list[WBReview] = []
    seen = set()
    for review in newest + lowest:
        key = (review.text, review.pros, review.cons, review.created_at)
        if key in seen:
            continue
        seen.add(key)
        selected.append(review)
    return selected


def _build_summary_user_prompt(product: WBProduct, reviews: list[WBReview]) -> str:
    price_block = []
    if product.price is not None:
        price_block.append(f"Цена без скидки: {product.price} RUB")
    if product.sale_price is not None:
        price_block.append(f"Цена со скидкой: {product.sale_price} RUB")
    price_text = "; ".join(price_block) if price_block else "Цена не получена"

    review_lines = []
    for index, review in enumerate(reviews, start=1):
        parts = [f"{index}. Оценка: {review.rating}/5"]
        if review.text:
            parts.append(f"Текст: {review.text}")
        if review.pros:
            parts.append(f"Плюсы из отзыва: {review.pros}")
        if review.cons:
            parts.append(f"Минусы из отзыва: {review.cons}")
        if review.created_at:
            parts.append(f"Дата: {review.created_at}")
        review_lines.append(" | ".join(parts))

    return (
        f"Проанализируй товар Wildberries.\n"
        f"Артикул: {product.article}\n"
        f"Название: {product.title}\n"
        f"Бренд: {product.brand or 'Не указан'}\n"
        f"Рейтинг карточки: {product.rating or 'Нет данных'}\n"
        f"Количество отзывов в карточке: {product.review_count}\n"
        f"{price_text}\n"
        f"Ссылка: {product.product_url}\n\n"
        f"Ниже отзывы для саммаризации ({len(reviews)} шт.):\n"
        + "\n".join(review_lines)
    )


# async def _fetch_json(client: httpx.AsyncClient, url: str, params: Optional[dict] = None) -> dict:
#     response = await client.get(url, params=params, headers=WB_HEADERS)
#     if response.status_code in (403, 429, 498):
#         raise WBTemporaryUnavailable(WB_TEMPORARY_UNAVAILABLE_MESSAGE)
#     if response.status_code == 404:
#         raise WBNotFound("Товар не найден в Wildberries.")
#     response.raise_for_status()
#     try:
#         payload = response.json()
#     except Exception:
#         log_api_error(f"WB NOT JSON RESPONSE: {response.text[:1000]}")
#         raise WBError("WB вернул не JSON (антибот/блокировка)")
#     if not isinstance(payload, dict):
#         raise WBError("WB вернул неожиданный формат данных.")
#     return payload

async def _fetch_json(client: httpx.AsyncClient, url: str, params: Optional[dict] = None) -> dict:
    response = await client.get(url, params=params, headers=WB_HEADERS)

    log_api_error(f"WB STATUS: {response.status_code}")
    log_api_error(f"WB URL: {url}")
    
    # Check for anti-bot protection (PoW challenge)
    x_pow = response.headers.get("x-pow", "")
    if x_pow:
        log_api_error(f"WB DETECTED: Anti-bot protection (x-pow header present)")
        log_api_error(f"WB PoW: {x_pow[:100]}")
        raise WBTemporaryUnavailable("WildBerries требует дополнительную верификацию. Попробуйте через минуту.")
    
    log_api_error(f"WB TEXT: {response.text[:300]}")

    if response.status_code in (403, 429, 498):
        raise WBTemporaryUnavailable(WB_TEMPORARY_UNAVAILABLE_MESSAGE)

    if response.status_code == 404:
        raise WBNotFound("Товар не найден в Wildberries или WB ограничил доступ.")

    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    log_api_error(f"WB CONTENT-TYPE: {content_type}")

    from json import JSONDecodeError

    text = response.text[:1000]

    try:
        payload = response.json()
    except (JSONDecodeError, ValueError):
        log_api_error(f"WB METHOD: {response.request.method}")
        log_api_error(f"WB STATUS: {response.status_code}")
        log_api_error(f"WB CONTENT-TYPE: {response.headers.get('content-type')}")
        log_api_error(f"WB URL: {response.request.url}")
        log_api_error(f"WB RESPONSE LENGTH: {len(response.text)}")
        log_api_error(f"WB NOT JSON RESPONSE: {text}")
        raise WBError("WB антибот / не JSON ответ")

    if not isinstance(payload, dict):
        log_api_error(f"WB INVALID JSON TYPE: {type(payload)}")
        raise WBError("WB вернул неожиданный формат данных")

    return payload


async def fetch_wb_product(article: str) -> WBProduct:
    candidate_calls = [
        (
            "https://card.wb.ru/cards/v2/detail",
            {
                "appType": 1,
                "curr": "rub",
                "nm": article,
            },
        ),
        (
            "https://card.wb.ru/cards/detail",
            {
                "appType": 1,
                "curr": "rub",
                "nm": article,
            },
        ),
    ]
    # candidate_calls = [
    #     (
    #         "https://card.wb.ru/cards/v2/detail",
    #         {"appType": 1, "curr": "rub", "dest": -1257786, "nm": article},
    #     ),
    #     (
    #         "https://card.wb.ru/cards/detail",
    #         {"appType": 1, "curr": "rub", "dest": -1257786, "nm": article},
    #     ),
    # ]
    # candidate_calls = [
    #     (
    #         "https://card.wb.ru/cards/v2/detail",
    #         {"appType": 1, "curr": "rub", "nm": article},
    #     ),
    #     (
    #         "https://card.wb.ru/cards/detail",
    #         {"appType": 1, "curr": "rub", "nm": article},
    #     ),
    # ]
    last_error = None


    # async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
    #     for url, params in candidate_calls:
    #         try:
    #             payload = await _fetch_json(client, url, params=params)
    #             response_text = response.text
    #             log_api_error(f"WB STATUS: {response.status_code}")
    #             log_api_error(f"WB TEXT: {response_text[:500]}")
    #             log_api_error(f"WB CALL: {url} PARAMS: {params}")
    #             log_api_error(f"WB RAW RESPONSE: {payload}")
    #             products = _extract_products(payload)
                # if not products:
                #     log_api_error(f"WB EMPTY PRODUCTS: {payload}")
                #     continue
            #     if not products:
            #         log_api_error(f"""
            #     WB EMPTY PRODUCTS
            #     URL: {url}
            #     PARAMS: {params}
            #     PAYLOAD: {payload}
            #     """)
            #         continue
            #     return _parse_card_product(products[0], article)
            # except WBTemporaryUnavailable:
            #     raise
            # except Exception as error:
            #     last_error = error
            #     continue
    # Fallback: парсинг HTML если API не дал результат


    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url, params in candidate_calls:
            try:
                payload = await _fetch_json(client, url, params=params)

                log_api_error(f"WB CALL: {url} PARAMS: {params}")
                log_api_error(f"WB RAW RESPONSE: {payload}")

                products = _extract_products(payload)

                if not products:
                    log_api_error(f"WB EMPTY PRODUCTS for {url}: {payload}")
                    continue

                return _parse_card_product(products[0], article)

            except WBTemporaryUnavailable:
                # Временная блокировка - переходим на Playwright
                last_error = None
                continue
            except WBNotFound:
                # Товар не найден - переходим на Playwright
                last_error = None
                continue
            except Exception as error:
                last_error = error
                continue
    
    log_api_error(f"WB API failed for article={article}, trying Playwright fallback")
    try:
        log_api_error(f"WB Playwright fallback: start parsing article={article}")
        from app.services.wb_playwright_parser import fetch_wb_product_via_browser
        product_browser = await fetch_wb_product_via_browser(article)
        log_api_error(f"WB Playwright fallback: parse result for article={article}: {product_browser}")
        return WBProduct(
            article=product_browser.article,
            title=product_browser.title,
            brand="",
            price=product_browser.price,
            sale_price=product_browser.sale_price,
            rating=product_browser.rating,
            review_count=product_browser.review_count,
            image_url=product_browser.image_url,
            product_url=product_browser.product_url,
            description=product_browser.description,
        )
    except Exception as playwright_error:
        log_api_error(f"WB Playwright parse error: {playwright_error}")
        if last_error:
            raise WBError(WB_ANALYSIS_UNAVAILABLE_MESSAGE) from last_error
        raise WBError(WB_ANALYSIS_UNAVAILABLE_MESSAGE) from playwright_error


async def fetch_wb_reviews(article: str) -> list[WBReview]:
    candidate_calls = [
        (
            f"https://feedbacks2.wb.ru/feedbacks/v1/{article}",
            {"take": 50, "skip": 0},
        ),
        (
            f"https://feedbacks2.wb.ru/feedbacks/v2/{article}",
            {"take": 50, "skip": 0},
        ),
    ]
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url, params in candidate_calls:
            try:
                payload = await _fetch_json(client, url, params=params)
                reviews = _parse_reviews(payload)
                if reviews:
                    return reviews
            except WBTemporaryUnavailable:
                raise
            except WBNotFound:
                continue
            except Exception:
                continue
    # Fallback: парсинг через Playwright если API не дал результат
    try:
        from app.services.wb_playwright_parser import fetch_wb_reviews_via_browser
        url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
        reviews_browser = await fetch_wb_reviews_via_browser(article, max_reviews=50)
        reviews = []
        for r in reviews_browser:
            reviews.append(WBReview(
                text=r.text,
                rating=r.rating,
                created_at=r.created_at,
                pros="",
                cons=""
            ))
        return reviews
    except Exception as playwright_error:
        from app.services.error_logger import log_api_error
        log_api_error(f"WB Playwright reviews parse error: {playwright_error}")
        return []


async def summarize_wb_reviews(product: WBProduct, reviews: list[WBReview]) -> str:
    client = openai.AsyncOpenAI(api_key=settings.PROXYAPI_KEY, base_url=settings.PROXYAPI_URL)
    response = await client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": get_wb_summary_prompt()},
            {"role": "user", "content": _build_summary_user_prompt(product, reviews)},
        ],
        stream=False,
    )
    return response.choices[0].message.content or "<b>Анализ не получен.</b>"


def format_product_preview(product: WBProduct, loaded_reviews: int, selected_reviews: int) -> str:
    lines = [
        f"<b>{escape(str(product.title), quote=False)}</b>",
        f"Артикул: <code>{escape(str(product.article), quote=False)}</code>",
    ]
    if product.brand:
        lines.append(f"Бренд: <b>{escape(str(product.brand), quote=False)}</b>")
    if product.sale_price is not None:
        lines.append(f"Цена: <b>{product.sale_price} ₽</b>")
    elif product.price is not None:
        lines.append(f"Цена: <b>{product.price} ₽</b>")
    if product.rating is not None:
        lines.append(f"Рейтинг: <b>{product.rating}</b>")
    lines.append(f"Отзывов загружено: <b>{loaded_reviews}</b>, в анализ взято: <b>{selected_reviews}</b>")
    return "\n".join(lines)


async def analyze_wb_product(user_input: str) -> WBAnalysisResult:
    article = extract_wb_article(user_input)
    if not article:
        raise WBNotFound("Не удалось распознать артикул или ссылку Wildberries.")

    try:
        product = await fetch_wb_product(article)
        reviews = await fetch_wb_reviews(article)
        selected_reviews = select_reviews_for_summary(reviews)
        summary_html = await summarize_wb_reviews(product, selected_reviews)
        return WBAnalysisResult(
            product=product,
            selected_reviews=selected_reviews,
            summary_html=summary_html,
            total_reviews_loaded=len(reviews),
        )
    except WBError:
        raise
    except openai.APIError as error:
        log_api_error(error)
        raise WBError("Не удалось получить анализ от ИИ. Попробуйте позже.") from error
    except Exception as error:
        log_api_error(error)
        raise WBError(WB_ANALYSIS_UNAVAILABLE_MESSAGE) from error