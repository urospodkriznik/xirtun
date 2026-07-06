"""Application configuration.

Loaded and validated once at startup via ``load_config()``, then passed explicitly
to the components that need it. ``Config`` is an immutable (``frozen=True``) settings
object, so values can't change while the app is running.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# The default timezone before a user sets their own via onboarding (see
# storage/db.get_timezone). Not configurable via env — this is a single-user app
# and the timezone is meant to live in the DB so it can change from chat.
DEFAULT_TIMEZONE = ZoneInfo("UTC")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    telegram_chat_id: str
    gemini_api_key: str
    cheap_model: str
    strong_model: str
    data_dir: Path
    weekly_cron: str
    weight_reminder_cron: str
    timezone: tzinfo


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    """Return env var `name`, or fail loudly if it's missing/empty."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    load_dotenv()  # reads a local .env in dev; harmless no-op when real env vars are set

    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        telegram_token=_require("TELEGRAM_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        cheap_model=os.environ.get("LLM_CHEAP_MODEL", "gemini-2.5-flash-lite"),
        strong_model=os.environ.get("LLM_STRONG_MODEL", "gemini-2.5-pro"),
        data_dir=data_dir,
        weekly_cron=os.environ.get("WEEKLY_CRON", "0 17 * * *"),
        # Morning check; should fire earlier in the day than WEEKLY_CRON so the nudge
        # lands the morning of the review.
        weight_reminder_cron=os.environ.get("WEIGHT_REMINDER_CRON", "0 8 * * *"),
        timezone=DEFAULT_TIMEZONE,
    )
