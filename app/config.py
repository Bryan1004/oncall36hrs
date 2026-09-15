from dataclasses import dataclass
import os


@dataclass
class Config:
    data_dir: str = os.getenv("DATA_DIR", "data/app")
    admin_user: str = os.getenv("ADMIN_USER", "admin")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "")
    bridge_token: str = os.getenv("BRIDGE_TOKEN", "")
    delivery_enabled: bool = os.getenv("DELIVERY_ENABLED", "false").lower() == "true"
    public_url: str = os.getenv("PUBLIC_URL", "http://localhost:8787").rstrip("/")
    confirmation_url: str = os.getenv("CONFIRMATION_URL", "").rstrip("/")
    bark_server: str = os.getenv("BARK_SERVER", "https://api.day.app").rstrip("/")
    bark_home_device_key: str = os.getenv("BARK_HOME_DEVICE_KEY", "")
    bark_away_device_key: str = os.getenv("BARK_AWAY_DEVICE_KEY", "")
    bark_device_key: str = os.getenv("BARK_DEVICE_KEY", "")
    telegram_api_id: str = os.getenv("TELEGRAM_API_ID", "")
    telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")
    wa_url: str = os.getenv("WA_URL", "http://localhost:3001")
    retention_days: int = int(os.getenv("RETENTION_DAYS", "14"))
