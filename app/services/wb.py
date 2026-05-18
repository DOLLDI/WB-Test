import re
from html import escape
from dataclasses import dataclass
from typing import Optional
import asyncio
import httpx
import openai

from app.services.config import settings
from app.services.error_logger import log_api_error
from app.services.prompts import get_wb_summary_prompt
from app.services.wb_search import search_wb_article
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



async def _safe_html_parse(article: str):
    from app.services.wb_html_parser import parse_wb_product_html

    url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    last_error = None

    for i in range(3):
        try:
            return await asyncio.to_thread(parse_wb_product_html, url)
        except Exception as e:
            last_error = e
            await asyncio.sleep(1.5 + i)

    raise last_error


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
    if not isinstance(payload, dict):
        return []

    data = payload.get("data")

    if isinstance(data, dict):
        products = data.get("products")
        if isinstance(products, list):
            return [p for p in products if isinstance(p, dict)]

    products = payload.get("products")
    if isinstance(products, list):
        return [p for p in products if isinstance(p, dict)]

    return []


def _build_wb_image_url(article: str, image_number: int = 1) -> str:
    try:
        nm_id = int(article)
    except (TypeError, ValueError):
        return ""

    vol = nm_id // 100000
    part = nm_id // 1000
    basket_ranges = [
        (143, "01"),
        (287, "02"),
        (431, "03"),
        (719, "04"),
        (1007, "05"),
        (1061, "06"),
        (1115, "07"),
        (1169, "08"),
        (1313, "09"),
        (1601, "10"),
        (1655, "11"),
        (1919, "12"),
        (2045, "13"),
        (2189, "14"),
        (2405, "15"),
        (2621, "16"),
        (2837, "17"),
        (3053, "18"),
        (3269, "19"),
        (3485, "20"),
        (3701, "21"),
        (3917, "22"),
        (4133, "23"),
    ]
    basket = "24"
    for max_vol, basket_number in basket_ranges:
        if vol <= max_vol:
            basket = basket_number
            break

    return (
        f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/"
        f"{nm_id}/images/big/{image_number}.webp"
    )



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
    if not image_url and isinstance(pics, int) and pics > 0:
        image_url = _build_wb_image_url(str(product_data.get("id") or article))

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


async def _fetch_json(
    client: httpx.AsyncClient,
    url: str,
    params: Optional[dict] = None
) -> dict:
    last_error = None

    for attempt in range(3):
        try:
            response = await client.get(
                url,
                params=params,
                headers=WB_HEADERS,
                timeout=20.0
            )

            if response.status_code in (403, 429, 498):
                raise WBTemporaryUnavailable(WB_TEMPORARY_UNAVAILABLE_MESSAGE)

            if response.status_code == 404:
                raise WBNotFound("Товар не найден в Wildberries.")

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, dict):
                raise WBError("WB вернул неожиданный формат данных.")

            return payload

        except (httpx.RequestError, httpx.TimeoutException) as e:
            last_error = e
            await asyncio.sleep(1.5 + attempt * 0.5)
            continue

    raise WBError(f"WB request failed after retries: {last_error}")


async def fetch_wb_product(article: str) -> WBProduct:
    last_error = None

    nm_id = None

    try:
        search_data = await search_wb_article(article)

        if search_data and search_data.get("nmId"):
            nm_id = search_data["nmId"]

    except Exception as e:
        last_error = e
        log_api_error(f"WB search failed: {e}")

    query_id = str(nm_id) if nm_id else article

    candidate_calls = [
        (
            "https://card.wb.ru/cards/v4/detail",
            {"appType": 1, "curr": "rub", "dest": -1257786, "nm": query_id},
        ),
        (
            "https://card.wb.ru/cards/detail",
            {"appType": 1, "curr": "rub", "dest": -1257786, "nm": query_id},
        ),
        (
            "https://card.wb.ru/cards/detail",
            {"appType": 1, "curr": "rub", "dest": -1027, "nm": query_id},
        ),
        (
            "https://card.wb.ru/cards/v1/detail",
            {"appType": 1, "curr": "rub", "dest": -1257786, "nm": query_id},
        ),
    ]

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for url, params in candidate_calls:
            try:
                payload = await _fetch_json(client, url, params=params)

                products = _extract_products(payload)

                if products:
                    return _parse_card_product(products[0], query_id)

                log_api_error(f"WB empty products: article={article}, query_id={query_id}")

            except WBTemporaryUnavailable:
                raise

            except Exception as error:
                last_error = error
                log_api_error(
                    f"WB card failed: url={url}, article={article}, query_id={query_id}, error={error}"
                )
                continue

    try:
        log_api_error(f"WB HTML fallback: article={article}, query_id={query_id}")

        product_html = await _safe_html_parse(query_id)

        return WBProduct(
            article=product_html.article,
            title=product_html.title,
            brand="",
            price=product_html.price,
            sale_price=product_html.price,
            rating=product_html.rating,
            review_count=product_html.review_count,
            image_url=product_html.image_url,
            product_url=product_html.product_url,
            description=product_html.description,
        )

    except Exception as html_error:
        log_api_error(f"WB HTML fallback failed: {html_error}")

        raise WBError(WB_ANALYSIS_UNAVAILABLE_MESSAGE)

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
            except WBNotFound as e:
                last_error = e
                continue
            except Exception:
                continue
    try:
        from app.services.wb_html_parser import parse_wb_reviews_html
        url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
        reviews_html = await asyncio.to_thread(parse_wb_reviews_html, url, 50)
        reviews = []
        for r in reviews_html:
            reviews.append(WBReview(
                text=r.text,
                rating=r.rating,
                created_at=r.created_at,
                pros="",
                cons=""
            ))
        return reviews
    except Exception as html_error:    
        log_api_error(f"WB HTML reviews parse error: {html_error}")
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
