import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class WBProductHTML:
    article: str
    title: str
    price: Optional[float]
    rating: Optional[float]
    review_count: int
    image_url: str
    product_url: str
    description: str

@dataclass
class WBReviewHTML:
    text: str
    rating: int
    created_at: str


def parse_wb_product_html(url: str) -> WBProductHTML:
    # resp = requests.get(url, headers={
    #     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    # })
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    article = url.split("/")[-2]
    title = soup.find("h1").text.strip() if soup.find("h1") else ""
    price = None
    price_tag = soup.select_one("[class*='price']")
    if price_tag:
        price = float(price_tag.text.replace("₽", "").replace(" ", "").replace(",", "."))
    rating = None
    rating_tag = soup.select_one("[class*='rating']")
    if rating_tag:
        try:
            rating = float(rating_tag.text.replace(",", "."))
        except Exception:
            pass
    review_count = 0
    review_count_tag = soup.select_one("[class*='count']")
    if review_count_tag:
        try:
            review_count = int(review_count_tag.text.replace("отзывов", "").replace("отзыв", "").replace(" ", ""))
        except Exception:
            pass
    image_url = ""
    img_tag = soup.find("img")
    if img_tag and img_tag.has_attr("src"):
        image_url = img_tag["src"]
    description = ""
    desc_tag = soup.select_one("div.collapsable__text")
    if desc_tag:
        description = desc_tag.text.strip()
    return WBProductHTML(
        article=article,
        title=title,
        price=price,
        rating=rating,
        review_count=review_count,
        image_url=image_url,
        product_url=url,
        description=description,
    )

def parse_wb_reviews_html(url: str, max_reviews: int = 50) -> List[WBReviewHTML]:
    resp = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    reviews = []
    review_blocks = soup.select("div.feedback__item")
    for block in review_blocks[:max_reviews]:
        text = block.select_one(".feedback__text").text.strip() if block.select_one(".feedback__text") else ""
        rating = 0
        rating_tag = block.select_one(".feedback__rating")
        if rating_tag and rating_tag.has_attr("data-rate"):
            try:
                rating = int(rating_tag["data-rate"])
            except Exception:
                pass
        created_at = block.select_one(".feedback__date").text.strip() if block.select_one(".feedback__date") else ""
        reviews.append(WBReviewHTML(text=text, rating=rating, created_at=created_at))
    return reviews

# Пример использования:
# product = parse_wb_product_html("https://www.wildberries.ru/catalog/15141101/detail.aspx")
# reviews = parse_wb_reviews_html("https://www.wildberries.ru/catalog/15141101/detail.aspx")
# print(product)
# print(reviews)
