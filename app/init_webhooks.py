import os
import time
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CLOUDFLARED_URL = "http://cloudflared:4040"


def get_public_url():
    print("⏳ Waiting for cloudflared API...")

    for _ in range(60):
        try:
            r = requests.get(f"{CLOUDFLARED_URL}/metrics")
            if r.status_code == 200:
                break
        except:
            pass
        time.sleep(2)

    # cloudflared exposes active tunnel in logs endpoint via metrics is NOT URL
    # BUT quick tunnel always has this endpoint:

    r = requests.get(f"http://cloudflared:4040/api/tunnels")
    data = r.json()

    url = data["tunnels"][0]["public_url"]

    print(f"✅ Cloudflare URL: {url}")
    return url


def set_telegram_webhook(base_url: str):
    webhook_url = f"{base_url}/telegram/webhook"

    print(f"📡 Setting webhook: {webhook_url}")

    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        params={"url": webhook_url}
    )

    print(r.text)


def check_webhook(base_url: str):
    try:
        r = requests.get(f"{base_url}/health", timeout=5)
        print("health:", r.status_code)
    except Exception as e:
        print("health failed:", e)


if __name__ == "__main__":
    url = get_public_url()
    check_webhook(url)
    set_telegram_webhook(url)

    print("DONE:", url)