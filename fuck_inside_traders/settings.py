from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    dry_run: bool
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    polymarket_api_base_url: str
    gdelt_api_base_url: str
    gdelt_enabled: bool
    log_level: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://fit:fit@localhost:5432/fit",
        ),
        dry_run=_env_bool("DRY_RUN", True),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        polymarket_api_base_url=os.getenv(
            "POLYMARKET_API_BASE_URL",
            "https://gamma-api.polymarket.com",
        ).rstrip("/"),
        gdelt_api_base_url=os.getenv(
            "GDELT_API_BASE_URL",
            "https://api.gdeltproject.org/api/v2/doc/doc",
        ),
        gdelt_enabled=_env_bool("GDELT_ENABLED", True),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
