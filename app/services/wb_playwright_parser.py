from playwright.async_api import async_playwright
from bs4 import BeautifulSoup


async def parse_wb_product_html(url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        page = await browser.new_page()

        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    title = ""
    price = None
    rating = None

    # ⚠️ WB часто меняет DOM — это fallback логика
    title_tag = soup.find("h1")
    if title_tag:
        title = title_tag.text.strip()

    price_tag = soup.find("ins") or soup.find("span", class_="price")
    if price_tag:
        price_text = price_tag.text.strip().replace("₽", "").replace(" ", "")
        try:
            price = float(price_text)
        except:
            price = None

    return type("WBHtmlProduct", (), {
        "article": url.split("/")[-2] if "/" in url else "",
        "title": title or "Unknown",
        "price": price,
        "rating": rating,
        "review_count": 0,
        "image_url": "",
        "product_url": url,
        "description": ""
    })