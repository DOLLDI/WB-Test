from contextlib import asynccontextmanager
import asyncio
import requests
import re
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from app.routers import admin, billing, telegram_router, vk_router
from app.platforms.telegram.aiogram_bot import bot as telegram_bot, dp as telegram_dispatcher
from app.services.billing import payment_side_effects_retry_worker
from app.services.config import settings
from app.services.db import init_db
from app.services.fiscal import fiscal_retry_worker
from app.services.logger import init_logging



def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"", "localhost", "127.0.0.1", "0.0.0.0", "proxyapi-bots"}


def _extract_cloudflared_url(metrics_text: str) -> str:
    direct_match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", metrics_text or "")
    if direct_match:
        return direct_match.group(0).rstrip("/")

    hostname_match = re.search(r'userHostname="([^"]+\.trycloudflare\.com)"', metrics_text or "")
    if hostname_match:
        return f"https://{hostname_match.group(1)}".rstrip("/")

    return ""


async def wait_for_public_webhook_base_url() -> str:
    explicit_url = _normalize_base_url(settings.PUBLIC_WEBHOOK_BASE_URL)
    if explicit_url:
        return explicit_url

    app_base_url = _normalize_base_url(settings.APP_BASE_URL)
    if app_base_url and not _is_local_base_url(app_base_url):
        return app_base_url

    metrics_url = settings.CLOUDFLARED_METRICS_URL.strip()
    probe_urls = [metrics_url]
    if metrics_url.endswith("/metrics"):
        metrics_base_url = metrics_url[: -len("/metrics")].rstrip("/")
        probe_urls.extend([f"{metrics_base_url}/quicktunnel", metrics_base_url])
    wait_seconds = max(1, settings.TUNNEL_WAIT_SECONDS)
    deadline = asyncio.get_running_loop().time() + wait_seconds

    async with httpx.AsyncClient(timeout=5.0) as client:
        while asyncio.get_running_loop().time() < deadline:
            last_error = None
            for probe_url in probe_urls:
                try:
                    response = await client.get(probe_url)
                    response.raise_for_status()
                    tunnel_url = _extract_cloudflared_url(response.text)
                    if tunnel_url:
                        return tunnel_url
                except Exception as error:
                    last_error = error
            if last_error:
                print(f"⏳ Waiting for cloudflared URL: {last_error}")
            await asyncio.sleep(2)

    raise RuntimeError(
        "Could not detect Cloudflare Quick Tunnel URL. "
        "Set PUBLIC_WEBHOOK_BASE_URL manually or check the cloudflared container."
    )


def set_telegram_webhook(base_url: str):
    token = settings.TELEGRAM_BOT_TOKEN

    if not token:
        print("⚠️ No Telegram token")
        return

    webhook_url = f"{base_url}/telegram/webhook"

    print("📡 Setting Telegram webhook:", webhook_url)

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/setWebhook",
            params={"url": webhook_url},
            timeout=10
        )

        result = r.json()
        if result.get("ok"):
            print("✅ Telegram webhook set successfully")
        else:
            print("⚠️ Telegram webhook response:", result)

    except Exception as e:
        print("❌ Telegram webhook error:", e)


async def start_telegram_polling():
    await telegram_bot.delete_webhook(drop_pending_updates=False)
    print("Telegram webhook disabled; polling started")
    await telegram_dispatcher.start_polling(
        telegram_bot,
        allowed_updates=telegram_dispatcher.resolve_used_update_types(),
    )


async def set_vk_webhook(base_url: str):
    token = settings.VK_GROUP_TOKEN
    confirmation_token = settings.VK_CONFIRMATION_TOKEN

    if not token or not confirmation_token:
        print("⚠️ No VK tokens configured")
        return

    webhook_url = f"{base_url}/vk/webhook"
    print(f"📡 VK webhook URL (configure in VK admin): {webhook_url}")
    print(f"📡 VK confirmation token: {confirmation_token}")

    if not settings.VK_AUTO_SET_CALLBACK:
        print("📝 VK auto callback setup is disabled. Set VK_AUTO_SET_CALLBACK=true and VK_GROUP_ID to enable it.")
        return

    group_id = settings.VK_GROUP_ID.strip()
    if not group_id:
        print("📝 VK_GROUP_ID is empty. Add it to .env to auto-create VK Callback server.")
        return

    async with httpx.AsyncClient(timeout=20.0) as client:
        base_data = {
            "access_token": token,
            "v": "5.131",
            "group_id": group_id,
        }

        server_id = None
        try:
            servers_response = await client.post(
                "https://api.vk.com/method/groups.getCallbackServers",
                data=base_data,
            )
            servers_response.raise_for_status()
            servers_payload = servers_response.json()
            for item in (servers_payload.get("response") or {}).get("items") or []:
                if item.get("url") == webhook_url or item.get("title") == settings.VK_CALLBACK_SERVER_TITLE:
                    server_id = item.get("id") or item.get("server_id")
                    break
        except Exception as error:
            print(f"⚠️ VK callback server lookup failed: {error}")

        server_payload = {
            **base_data,
            "url": webhook_url,
            "title": settings.VK_CALLBACK_SERVER_TITLE,
        }
        if settings.VK_CALLBACK_SECRET:
            server_payload["secret_key"] = settings.VK_CALLBACK_SECRET

        if server_id:
            server_response = await client.post(
                "https://api.vk.com/method/groups.editCallbackServer",
                data={**server_payload, "server_id": server_id},
            )
        else:
            server_response = await client.post(
                "https://api.vk.com/method/groups.addCallbackServer",
                data=server_payload,
            )
        server_response.raise_for_status()
        server_payload_response = server_response.json()
        if server_payload_response.get("error"):
            print(f"⚠️ VK callback server setup error: {server_payload_response['error']}")
            return

        if not server_id:
            server_id = (server_payload_response.get("response") or {}).get("server_id")
        if not server_id:
            print(f"⚠️ VK did not return server_id: {server_payload_response}")
            return

        settings_response = await client.post(
            "https://api.vk.com/method/groups.setCallbackSettings",
            data={
                "access_token": token,
                "v": "5.131",
                "group_id": group_id,
                "server_id": server_id,
                "api_version": "5.131",
                "message_new": 1,
            },
        )
        settings_response.raise_for_status()
        settings_payload = settings_response.json()
        if settings_payload.get("error"):
            print(f"⚠️ VK callback settings error: {settings_payload['error']}")
            return
        print(f"✅ VK callback server ready: server_id={server_id}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    payment_task = asyncio.create_task(payment_side_effects_retry_worker())
    telegram_polling_task = None
    telegram_delivery_mode = (settings.TELEGRAM_DELIVERY_MODE or "webhook").strip().lower()

    fiscal_task = None
    if settings.MYTAX_API_URL and settings.MYTAX_API_TOKEN:
        fiscal_task = asyncio.create_task(fiscal_retry_worker())

    print("🔥 APP STARTED")
    if settings.AUTO_SET_WEBHOOKS or settings.AUTO_SET_TELEGRAM_WEBHOOK:
        public_base_url = await wait_for_public_webhook_base_url()
        settings.APP_BASE_URL = public_base_url
        print(f"✅ Public webhook base URL: {public_base_url}")
        if telegram_delivery_mode == "polling":
            telegram_polling_task = asyncio.create_task(start_telegram_polling())
        else:
            await asyncio.to_thread(set_telegram_webhook, public_base_url)
        await set_vk_webhook(public_base_url)
    elif telegram_delivery_mode == "polling":
        telegram_polling_task = asyncio.create_task(start_telegram_polling())

    try:
        yield
    finally:
        payment_task.cancel()
        if telegram_polling_task:
            telegram_polling_task.cancel()
        if fiscal_task:
            fiscal_task.cancel()


init_logging()
app = FastAPI(lifespan=lifespan)


import traceback
from fastapi import Request
from fastapi.responses import JSONResponse


@app.middleware("http")
async def log_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)

    except Exception as exc:
        tb = traceback.format_exc()

        print(f"[GLOBAL EXCEPTION] {exc}\n{tb}")
        from app.services.error_logger import log_api_error
        log_api_error(f"Global exception: {exc}\n{tb}")

        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error"}
        )


app.include_router(telegram_router.router, prefix="/telegram")
app.include_router(vk_router.router, prefix="/vk")
app.include_router(admin.router, prefix="/admin")
app.include_router(billing.router, prefix="/billing")


@app.get("/health")
async def health():
    return {"status": "ok"}
