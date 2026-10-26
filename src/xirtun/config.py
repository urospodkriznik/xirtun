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

from dotenv import dotenv_values, load_dotenv

# The default timezone before a user sets their own via onboarding (see
# storage/db.get_timezone). Not configurable via env — each instance serves one
# person, and their timezone is meant to live in their DB so it can change from chat.
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


# Keys a person's own file must set itself, never inherit from the shared .env: they
# decide WHO this instance talks to, so inheriting them would answer one person with
# another person's bot.
PERSONAL_KEYS = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _require(name: str) -> str:
    """Return env var `name`, or fail loudly if it's missing/empty."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _load_env_files() -> None:
    """Load settings into the environment, layered (ADR-017).

    Without ENV_FILE (dev, one person) a single local .env holds everything. With it,
    two files apply: that person's own file, then the shared .env beside it for
    whatever theirs leaves out — models, schedules, a shared API key. Neither load
    overrides a value already set, so the order IS the precedence: the real
    environment (systemd's DATA_DIR) > the person's file > the shared .env.
    """
    env_file = os.environ.get("ENV_FILE")
    if not env_file:
        load_dotenv()
        return

    personal = Path(env_file)
    if not personal.is_file():
        raise ConfigError(f"ENV_FILE points to a missing file: {env_file}")
    values = dotenv_values(personal)
    missing = [key for key in PERSONAL_KEYS if not values.get(key)]
    if missing:
        raise ConfigError(
            f"{personal} must set {', '.join(missing)} itself — "
            "these are never taken from the shared .env"
        )
    load_dotenv(personal)
    load_dotenv(personal.parent / ".env")   # a missing shared file is fine: nothing to add


def load_config() -> Config:
    _load_env_files()

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
