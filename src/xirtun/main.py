"""Entry point. Wires config -> database -> Gemini -> Telegram and runs the bot.

Run:  uv run python -m xirtun.main
Needs TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY in .env. Timezone defaults to
UTC and is set from the onboarding interview instead (stored in the DB).

Starts the weekly-review scheduler, runs a catch-up review if one is overdue, then
serves the Telegram bot. On first run (empty diet.md) the bot interviews the user;
afterwards every message goes through the intake pipeline.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, tzinfo
from functools import partial
from pathlib import Path

from xirtun.config import load_config
from xirtun.llm.base import LLMClient
from xirtun.llm.gemini import GeminiClient
from xirtun.logging_setup import setup_logging
from xirtun.memory import diet as memory
from xirtun.messaging.base import IncomingMessage, Messenger
from xirtun.messaging.telegram import TelegramMessenger
from xirtun.pipeline.intake import dispatch
from xirtun.run_weekly import run_scheduled_review
from xirtun.scheduler import reschedule, start_scheduler
from xirtun.storage import db

logger = logging.getLogger(__name__)

# The slash-command menu shown in Telegram clients (names: lowercase, no hyphens).
COMMANDS = [
    ("meal", "Start a new meal entry"),
    ("exercise", "Log a workout"),
    ("note", "Save a note or goal for your weekly review"),
    ("undo", "Remove your last logged entry"),
    ("today", "Today's meals and totals"),
    ("week", "Your past 7 days"),
    ("lastmeals", "Your last 3 meals"),
    ("lastsymptoms", "Your last 3 symptoms"),
    ("lastworkouts", "Your last 3 workouts"),
    ("lastnotes", "Your last 3 notes"),
    ("shop", "Suggest a shopping list"),
    ("food", "Save a food's nutrition label"),
    ("myfood", "List your saved foods"),
    ("checkfood", "Check if a food is saved"),
    ("delfood", "Remove a saved food"),
    ("savemeal", "Save a recurring meal"),
    ("mymeals", "List your saved meals"),
    ("delmeal", "Remove a saved meal"),
    ("target", "Your daily calorie & protein target"),
    ("weight", "Update your weight"),
    ("export", "Download your diary as a JSON backup"),
    ("weekly", "Run your weekly review now"),
    ("profile", "Show your profile"),
    ("cleardata", "Erase all your data"),
    ("timezone", "Set your timezone"),
    ("help", "What I can do"),
]


def make_intake_handler(
    llm: LLMClient,
    conn: sqlite3.Connection,
    messenger: Messenger,
    diet_path: Path,
    default_tz: tzinfo,
    weekly_cb: Callable[[], None],
    on_timezone_change: Callable[[tzinfo], None] | None = None,
) -> Callable[[IncomingMessage], None]:
    def handle(message: IncomingMessage) -> None:
        logger.info("recv from %s: %s", message.sender_id, message.text)
        # Re-read per message (not captured once) so a timezone set via onboarding
        # or /timezone takes effect immediately, without a restart.
        tz = db.get_timezone(conn, default_tz)
        dispatch(
            message.text,
            chat_id=message.sender_id,
            llm=llm,
            conn=conn,
            messenger=messenger,
            diet_path=diet_path,
            weekly_cb=weekly_cb,
            on_timezone_change=on_timezone_change,
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
        transcribe=llm.transcribe,  # voice notes -> text via the cheap model
    )
    try:
        messenger.set_commands(COMMANDS)
    except Exception:  # noqa: BLE001 — non-fatal if Telegram is unreachable at boot
        logger.exception("failed to register command menu")

    scheduler = start_scheduler(config, conn)
    # Catch up a missed weekly review (but not before the user has onboarded).
    if not memory.is_empty(diet_path):
        run_scheduled_review(config, force=False)

    weekly_cb = partial(run_scheduled_review, config, force=True)
    on_timezone_change = partial(reschedule, scheduler, config)
    messenger.run(
        make_intake_handler(
            llm, conn, messenger, diet_path, config.timezone, weekly_cb, on_timezone_change,
        )
    )


if __name__ == "__main__":
    main()
