"""One-shot entrypoint for the weekly review (invoked by the scheduler, or manually).

Run manually:  uv run python -m xirtun.run_weekly

Opens its own database connection: it may run on a different thread than the bot, and
SQLite connections can't be shared across threads. Uses the strong model — the weekly
review is the one place the big model is worth paying for.
"""

from __future__ import annotations

import logging

from xirtun.agent.weekly import run_weekly
from xirtun.config import load_config
from xirtun.llm.gemini import GeminiClient
from xirtun.logging_setup import setup_logging
from xirtun.messaging.telegram import TelegramMessenger
from xirtun.storage import db

logger = logging.getLogger(__name__)


def main() -> None:
    setup_logging()
    config = load_config()

    db_path = config.data_dir / "xirtun.db"
    db.init_db(db_path)
    conn = db.get_connection(db_path)  # this run's own connection

    llm = GeminiClient(config.gemini_api_key, config.strong_model)
    messenger = TelegramMessenger(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        conn=conn,
    )

    logger.info("starting weekly review")
    run_weekly(
        llm=llm,
        conn=conn,
        diet_path=config.data_dir / "diet.md",
        observations_path=config.data_dir / "observations.md",
        messenger=messenger,
        tz=config.timezone,
    )


if __name__ == "__main__":
    main()
