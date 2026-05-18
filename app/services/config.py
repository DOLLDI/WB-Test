import os
from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv('.env.local', override=True)
load_dotenv()
load_dotenv('.env.prompts')

class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str
    VK_GROUP_TOKEN: str
    VK_CONFIRMATION_TOKEN: str = ""
    VK_GROUP_ID: str = ""
    VK_CALLBACK_SECRET: str = ""
    PROXYAPI_URL: str
    PROXYAPI_KEY: str = ""
    ADMIN_IDS: str = ""
    APP_BASE_URL: str = "http://localhost:8000"
    PUBLIC_WEBHOOK_BASE_URL: str = ""
    AUTO_SET_TELEGRAM_WEBHOOK: bool = False
    AUTO_SET_WEBHOOKS: bool = False
    TELEGRAM_DELIVERY_MODE: str = "webhook"
    START_CLOUDFLARED: bool = False
    VK_AUTO_SET_CALLBACK: bool = False
    VK_CALLBACK_SERVER_TITLE: str = "ProxyApiBots"
    CLOUDFLARED_METRICS_URL: str = "http://127.0.0.1:4040/metrics"
    TUNNEL_WAIT_SECONDS: int = 120
    DB_BACKEND: str = "sqlite"
    DATABASE_URL: str = ""
    POSTGRES_DB: str = "proxyapi"
    POSTGRES_USER: str = "proxyapi"
    POSTGRES_PASSWORD: str = ""
    SQLITE_DB_PATH: str = "users.db"
    ERROR_LOG_PATH: str = "proxyapi_errors.log"
    ADMIN_TOKEN: str = "supersecret"
    BILLING_SECRET: str = "change-me-billing-secret"
    PAYMENT_PROVIDER: str = "sandbox"
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = ""
    ROBOKASSA_MERCHANT_LOGIN: str = ""
    ROBOKASSA_PASSWORD1: str = ""
    ROBOKASSA_PASSWORD2: str = ""
    ROBOKASSA_TEST_MODE: bool = True
    MYTAX_API_URL: str = ""
    MYTAX_API_TOKEN: str = ""
    MYTAX_SELLER_INN: str = ""
    FISCAL_RETRY_INTERVAL_SECONDS: int = 300
    FISCAL_MAX_ATTEMPTS: int = 5
    PAYMENT_SIDE_EFFECT_RETRY_INTERVAL_SECONDS: int = 120
    REFERRAL_BONUS_REQUESTS: int = 2
    ANTIFLOOD_WINDOW_SECONDS: int = 10
    ANTIFLOOD_MAX_REQUESTS: int = 3
    WB_PROXY_URL: str = ""
    WB_ENABLE_ROTATING_PROXIES: bool = False
    TELEGRAM_SYSTEM_PROMPT: str = ""
    VK_SYSTEM_PROMPT: str = ""
    WB_SUMMARY_PROMPT: str = ""

    class Config:
        env_file = ".env"

    @property
    def admin_ids(self):
        return [x.strip() for x in self.ADMIN_IDS.split(",") if x.strip()]

    @property
    def db_backend(self):
        return self.DB_BACKEND.strip().lower() or "sqlite"

settings = Settings()
