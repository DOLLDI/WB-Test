"""WildBerries парсер на Playwright - обходит PoW защиту"""

import asyncio
import json
from typing import Optional
from dataclasses import dataclass

from playwright.async_api import async_playwright, Browser, Page
from bs4 import BeautifulSoup

from app.services.error_logger import log_api_error


@dataclass
class WBProductBrowser:
    article: str
    title: str
    price: Optional[float]
    sale_price: Optional[float]
    rating: Optional[float]
    review_count: int
    image_url: str
    product_url: str
    description: str


@dataclass
class WBReviewBrowser:
    text: str
    rating: int
    created_at: str


class PlaywrightPoolManager:
    """Управляет переиспользуемым браузером для экономии ресурсов"""
    
    _browser: Optional[Browser] = None
    _playwright = None
    _lock = asyncio.Lock()
    
    @classmethod
    async def get_browser(cls) -> Browser:
        """Получить или создать браузер"""
        if cls._browser is None:
            async with cls._lock:
                if cls._browser is None:
                    cls._playwright = await async_playwright().start()
                    cls._browser = await cls._playwright.chromium.launch(
                        headless=True,
                        args=[
                            '--no-sandbox',
                            '--disable-blink-features=AutomationControlled',
                            '--disable-dev-shm-usage',
                            '--disable-features=IsolateOrigins,site-per-process',
                        ]
                    )
                    log_api_error("Playwright браузер запущен (обходит PoW защиту WB)")
        return cls._browser
    
    @classmethod
    async def close(cls):
        """Закрыть браузер"""
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if cls._playwright:
            try:
                await cls._playwright.stop()
            except:
                pass
            cls._playwright = None
            log_api_error("Playwright браузер закрыт")


async def _route_handler(route):
    """Простая фильтрация запросов: блокируем трекеры/рекламу, пропускаем остальное"""
    try:
        req = route.request
        url = (req.url or '').lower()
        blocked = ['google-analytics', 'mc.yandex', 'doubleclick', 'analytics', 'sentry', 'googletagmanager', 'gstatic', '/ads', 'adservice']
        if any(x in url for x in blocked):
            try:
                await route.abort()
            except:
                await route.continue_()
        else:
            await route.continue_()
    except Exception:
        try:
            await route.continue_()
        except:
            pass


async def fetch_wb_product_via_browser(article: str) -> WBProductBrowser:
    """Получить данные товара через браузер (обходит PoW защиту)"""
    
    browser = await PlaywrightPoolManager.get_browser()
    page: Optional[Page] = None
    context = None

    # Stealth init script: скрываем webdriver и правим некоторые свойства
    stealth_script = """
    (() => {
      try {
        Object.defineProperty(navigator, 'webdriver', {get: () => false});
      } catch(e){}
      try {
        window.navigator.chrome = { runtime: {} };
      } catch(e){}
      try {
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
      } catch(e){}
      try {
        Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru']});
      } catch(e){}
    })();
    """

    url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    log_api_error(f"Playwright: загружаю страницу товара {url}")

    # Попытки загрузки страницы
    retries = 2
    last_exc = None
    for attempt in range(retries):
        try:
            # Создаём контекст с реалистичными заголовками
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
                locale='ru-RU',
                viewport={'width': 1366, 'height': 768},
                timezone_id='Europe/Moscow',
                bypass_csp=True,
            )

            await context.add_init_script(stealth_script)
            await context.set_extra_http_headers({
                'accept-language': 'ru-RU,ru;q=0.9',
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'referer': 'https://www.wildberries.ru/'
            })

            page = await context.new_page()
            page.set_default_navigation_timeout(40000)
            page.set_default_timeout(20000)

            # Отключаем некоторые трекеры и запросы, которые могут вызывать блокировки
            try:
                await page.route('**/*', lambda route: asyncio.create_task(_route_handler(route)))
            except Exception:
                pass

            # Небольшая задержка перед переходом
            await page.wait_for_timeout(800)
            response = await page.goto(url, wait_until='networkidle')
            status = response.status if response else None
            log_api_error(f"Playwright: попытка {attempt+1} статус {status} для {url}")

            # Дать странице время отрисоваться (включая PoW решения)
            await page.wait_for_timeout(2500)

            # Ждём появления основного селектора продукта
            try:
                await page.wait_for_selector('h1, [data-test="product-title"]', timeout=5000)
            except Exception:
                pass

            # Пробуем извлечь через JS быстрее
            try:
                product_data = await page.evaluate("""
                () => {
                    try {
                        const titleEl = document.querySelector('h1') || document.querySelector('[data-test="product-title"]');
                        const priceEl = document.querySelector('[data-test-price]') || document.querySelector('[class*="price"]');
                        const ratingEl = document.querySelector('[data-test*="rating"]') || document.querySelector('[class*="rating"]');
                        const countEl = document.querySelector('[data-test*="feedbacks"]') || document.querySelector('[class*="feedbacks"]');
                        const imageEl = document.querySelector('img[src*="wbbasket"]') || document.querySelector('img');
                        return {
                            title: titleEl ? titleEl.innerText.trim() : null,
                            price: priceEl ? (priceEl.innerText || priceEl.getAttribute('data-price')) : null,
                            rating: ratingEl ? ratingEl.innerText.trim() : null,
                            reviewCount: countEl ? countEl.innerText.trim() : null,
                            image: imageEl ? imageEl.src : null
                        };
                    } catch(e){ return null }
                }
                """)

                if product_data and (product_data.get('title') or product_data.get('price')):
                    log_api_error(f"Playwright JS extract: {product_data}")
                    return _parse_wb_product_from_js_data(product_data, article, url)
            except Exception as e:
                log_api_error(f"Playwright JS extract failed: {e}")

            # Fallback: парсинг HTML
            content = await page.content()
            log_api_error(f"Playwright: получен контент, длина {len(content)}")
            return _parse_wb_product_from_html(content, article, url)

        except Exception as e:
            last_exc = e
            log_api_error(f"Playwright: ошибка при попытке загрузки {article} (#{attempt+1}) - {e}")
            # если есть страница/контекст — закрыть и попробовать заново
            try:
                if page:
                    await page.close()
            except:
                pass
            try:
                if context:
                    await context.close()
            except:
                pass
            page = None
            context = None
            await asyncio.sleep(1 + attempt)
            continue

    # Если все попытки провалились — логируем и кидаем последнее исключение
    log_api_error(f"Playwright: все попытки загрузки товара {article} завершились неудачно")
    if last_exc:
        raise last_exc
    raise RuntimeError('Неизвестная ошибка при загрузке товара')




def _parse_wb_product_from_js_data(data: dict, article: str, url: str) -> WBProductBrowser:
    """Парсить товар из данных, извлеченных через JavaScript"""
    
    import re
    
    # Парсить название
    title = data.get("title", "").strip() if data.get("title") else f"Товар WB {article}"
    if not title or title == "None":
        title = f"Товар WB {article}"
    
    # Парсить цену
    price = None
    price_str = str(data.get("price", "")).strip()
    if price_str and price_str != "None":
        # Очистить от символов
        price_clean = re.sub(r'[^\d.,]', '', price_str)
        try:
            price = float(price_clean.replace(",", "."))
        except:
            pass
    
    # Парсить рейтинг
    rating = None
    rating_str = str(data.get("rating", "")).strip()
    if rating_str and rating_str != "None":
        match = re.search(r'(\d+\.?\d*)', rating_str)
        if match:
            try:
                rating = float(match.group(1))
            except:
                pass
    
    # Парсить количество отзывов
    review_count = 0
    count_str = str(data.get("reviewCount", "")).strip()
    if count_str and count_str != "None":
        match = re.search(r'(\d+)', count_str)
        if match:
            try:
                review_count = int(match.group(1))
            except:
                pass
    
    # Парсить картинку
    image_url = data.get("image", "")
    if not image_url or image_url == "None":
        image_url = ""
    
    log_api_error(f"JS parse: article={article}, title={title[:50]}, price={price}, rating={rating}, reviews={review_count}")
    
    return WBProductBrowser(
        article=article,
        title=title,
        price=price,
        sale_price=price,
        rating=rating,
        review_count=review_count,
        image_url=image_url,
        product_url=url,
        description="",
    )


async def fetch_wb_reviews_via_browser(article: str, max_reviews: int = 50) -> list[WBReviewBrowser]:
    browser = await PlaywrightPoolManager.get_browser()
    context = None
    page: Optional[Page] = None

    stealth_script = """
    (() => {
      try { Object.defineProperty(navigator, 'webdriver', {get: () => false}); } catch(e){}
      try { window.navigator.chrome = { runtime: {} }; } catch(e){}
      try { Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU','ru']}); } catch(e){}
    })();
    """

    url = f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    try:
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            locale='ru-RU',
            viewport={'width': 1366, 'height': 768},
            timezone_id='Europe/Moscow',
            bypass_csp=True,
        )
        await context.add_init_script(stealth_script)
        await context.set_extra_http_headers({'accept-language': 'ru-RU,ru;q=0.9', 'referer': 'https://www.wildberries.ru/'})
        page = await context.new_page()
        try:
            await page.route('**/*', lambda route: asyncio.create_task(_route_handler(route)))
        except Exception:
            pass

        await page.goto(url, wait_until='networkidle')
        await page.wait_for_timeout(2000)
        content = await page.content()
        reviews = _parse_reviews_from_html(content, max_reviews)
        log_api_error(f"Playwright: загружено {len(reviews)} отзывов для {article}")
        return reviews
    except Exception as e:
        log_api_error(f"Playwright: ошибка при загрузке отзывов {article} - {e}")
        return []
    finally:
        try:
            if page:
                await page.close()
        except:
            pass
        try:
            if context:
                await context.close()
        except:
            pass


def _parse_wb_product_from_api(data: dict, article: str) -> WBProductBrowser:
    """Парсить товар из API JSON"""
    
    try:
        # Структура может быть разной
        if isinstance(data.get("data"), dict) and "products" in data["data"]:
            products = data["data"]["products"]
        elif isinstance(data.get("products"), list):
            products = data["products"]
        else:
            products = []
        
        if not products:
            raise ValueError("Товар не найден в ответе API")
        
        product = products[0]
        
        # Парсить цену
        price = None
        sale_price = None
        
        if isinstance(product.get("sizes"), list) and product["sizes"]:
            size_price = product["sizes"][0].get("price", {})
            if size_price:
                if isinstance(size_price.get("basic"), (int, float)):
                    price = float(size_price["basic"])
                    if price > 1000:
                        price = round(price / 100, 2)
                
                if isinstance(size_price.get("product"), (int, float)):
                    sale_price = float(size_price["product"])
                    if sale_price > 1000:
                        sale_price = round(sale_price / 100, 2)
        
        # Парсить картинку
        image_url = ""
        if isinstance(product.get("pics"), list) and product["pics"]:
            first_pic = product["pics"][0]
            if isinstance(first_pic, str) and first_pic.startswith("http"):
                image_url = first_pic
        
        return WBProductBrowser(
            article=article,
            title=product.get("name") or f"Товар WB {article}",
            price=price,
            sale_price=sale_price,
            rating=product.get("rating") or product.get("reviewRating"),
            review_count=int(product.get("feedbacks") or product.get("feedbackCount") or 0),
            image_url=image_url,
            product_url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx",
            description=product.get("description") or "",
        )
    
    except Exception as e:
        log_api_error(f"Ошибка парсинга API ответа: {e}")
        raise


def _parse_wb_product_from_html(html: str, article: str, url: str) -> WBProductBrowser:
    """Парсить товар из HTML страницы WB"""
    
    soup = BeautifulSoup(html, 'html.parser')
    
    # Парсить название - ищем в разных местах
    title_text = f"Товар WB {article}"
    
    # Попробовать разные селекторы для названия
    title_selectors = [
        "h1",
        "[data-test='product-title']",
        "[class*='ProductHeading']",
        "[class*='product-title']",
        "[class*='ProductName']"
    ]
    
    for selector in title_selectors:
        title_elem = soup.select_one(selector)
        if title_elem:
            text = title_elem.get_text(strip=True)
            if text and len(text) > 3:  # Проверить что это не пустой элемент
                title_text = text
                break
    
    # Парсить цену - ищем цифры в странице
    price = None
    price_patterns = [
        "data-price",
        "data-test-price",
        "[class*='Price']",
        "[class*='price']",
    ]
    
    for pattern in price_patterns:
        price_elem = soup.select_one(pattern)
        if price_elem:
            try:
                price_text = price_elem.get_text(strip=True)
                # Убрать символы и пробелы, оставить только число
                import re
                match = re.search(r'(\d+(?:\s|\d)*)', price_text)
                if match:
                    num_str = match.group(1).replace(" ", "").replace(",", ".")
                    price = float(num_str)
                    if price > 0:
                        break
            except:
                continue
    
    # Если цена не найдена, ищем в атрибутах
    if not price:
        for elem in soup.find_all(True):  # Все элементы
            for attr_name in ['data-price', 'data-cost', 'price']:
                if attr_name in elem.attrs:
                    try:
                        price = float(str(elem.attrs[attr_name]).replace(",", "."))
                        if price > 0:
                            break
                    except:
                        pass
            if price:
                break
    
    # Парсить рейтинг
    rating = None
    rating_selectors = [
        "[data-test*='rating']",
        "[class*='Rating']",
        "[class*='rating']",
        "[class*='ProductRating']"
    ]
    
    for selector in rating_selectors:
        rating_elem = soup.select_one(selector)
        if rating_elem:
            try:
                rating_text = rating_elem.get_text(strip=True)
                # Извлечь первое число (обычно рейтинг)
                import re
                match = re.search(r'(\d+\.?\d*)', rating_text)
                if match:
                    rating = float(match.group(1))
                    if 0 < rating <= 5:
                        break
            except:
                continue
    
    # Парсить количество отзывов
    review_count = 0
    review_selectors = [
        "[data-test*='feedback']",
        "[class*='ReviewCount']",
        "[class*='review-count']",
        "[class*='feedbacks']"
    ]
    
    for selector in review_selectors:
        review_elem = soup.select_one(selector)
        if review_elem:
            try:
                text = review_elem.get_text(strip=True)
                import re
                match = re.search(r'(\d+)', text)
                if match:
                    review_count = int(match.group(1))
                    if review_count > 0:
                        break
            except:
                continue
    
    # Парсить картинку - ищем изображения товара
    image_url = ""
    
    # Попробовать разные селекторы для картинок
    img_selectors = [
        "img[class*='ProductImage']",
        "img[data-test*='product-image']",
        "img[alt*='товара']",
        "img[alt*='product']",
    ]
    
    for selector in img_selectors:
        img = soup.select_one(selector)
        if img and img.has_attr("src"):
            src = img["src"]
            # Проверить что это не логотип/иконка
            if src.startswith("http") and "wbbasket" in src:
                image_url = src
                break
    
    # Если картинка не найдена, ищем все img и берем с wbbasket
    if not image_url:
        for img in soup.find_all("img"):
            if img.has_attr("src"):
                src = img["src"]
                if "wbbasket" in src and src.startswith("http"):
                    image_url = src
                    break
    
    log_api_error(f"HTML parse: article={article}, title={title_text[:50]}, price={price}, rating={rating}, reviews={review_count}, img_url={'yes' if image_url else 'no'}")
    
    return WBProductBrowser(
        article=article,
        title=title_text,
        price=price,
        sale_price=price,
        rating=rating,
        review_count=review_count,
        image_url=image_url,
        product_url=url,
        description="",
    )


def _parse_reviews_from_html(html: str, max_reviews: int) -> list[WBReviewBrowser]:
    """Парсить отзывы из HTML страницы"""
    
    soup = BeautifulSoup(html, 'html.parser')
    reviews = []
    
    # Искать контейнеры отзывов
    review_blocks = soup.select("div[class*='feedback']") or soup.select("div[class*='review']")
    
    for block in review_blocks[:max_reviews]:
        try:
            text = ""
            rating = 0
            created_at = ""
            
            # Попробовать найти текст отзыва
            text_elem = block.select_one("[class*='text']") or block.select_one("p")
            if text_elem:
                text = text_elem.text.strip()
            
            # Попробовать найти рейтинг
            rating_elem = block.select_one("[class*='rating']") or block.select_one("[class*='rate']")
            if rating_elem:
                try:
                    if rating_elem.has_attr("data-rate"):
                        rating = int(rating_elem["data-rate"])
                    else:
                        rating = int(rating_elem.text.strip()[0])
                except:
                    pass
            
            # Попробовать найти дату
            date_elem = block.select_one("[class*='date']")
            if date_elem:
                created_at = date_elem.text.strip()
            
            if text:
                reviews.append(WBReviewBrowser(
                    text=text,
                    rating=rating,
                    created_at=created_at
                ))
        
        except Exception as e:
            log_api_error(f"Ошибка парсинга отзыва: {e}")
            continue
    
    return reviews
