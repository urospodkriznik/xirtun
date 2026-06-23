"""Entry point. Wires config -> database -> Gemini -> Telegram and runs the bot.

Run:  uv run python -m xirtun.main
Needs TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY (+ optional TIMEZONE) in .env.

On first run (empty diet.md) the bot interviews the user; afterwards every message
goes through the intake pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, tzinfo
from pathlib import Path

from xirtun.config import load_config
from xirtun.llm.base import LLMClient
from xirtun.llm.gemini import GeminiClient
from xirtun.logging_setup import setup_logging
from xirtun.messaging.base import IncomingMessage, Messenger
from xirtun.messaging.telegram import TelegramMessenger
from xirtun.pipeline.intake import dispatch
from xirtun.storage import db

logger = logging.getLogger(__name__)


def make_intake_handler(
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    diet_path: Path,
    tz: tzinfo,
) -> Callable[[IncomingMessage], None]:
    def handle(message: IncomingMessage) -> None:
        logger.info("recv from %s: %s", message.sender_id, message.text)
        dispatch(
            message.text,
            chat_id=message.sender_id,
            llm=llm,
            conn=conn,
            messenger=messenger,
            diet_path=diet_path,
            now=datetime.now(tz),
        )

    return handle


def main() -> None:
    setup_logging()
    config = load_config()

    db_path = config.data_dir / "xirtun.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)

    diet_path = config.data_dir / "diet.md"
    llm = GeminiClient(config.gemini_api_key, config.cheap_model)
    messenger = TelegramMessenger(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        conn=conn,
    )
    messenger.run(make_intake_handler(llm, conn, messenger, diet_path, config.timezone))


if __name__ == "__main__":
    main()
