"""Morning weight-log reminder.

A fresh weight is what lets the weekly review judge intake against the scale instead
of a calorie *estimate* (see targets.format_weight_trend). This nudge fires only when
both are true:
  - the weekly review is DUE to run today (so the reminder lands the morning of the
    review, not every day), and
  - no weight has been logged in the last ``WEIGHT_STALE`` days.

``run_scheduled_reminder`` builds the production dependencies (its own database
connection and the Telegram messenger) and is what the scheduler calls.

Run manually:  uv run python -m xirtun.run_reminder
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from xirtun import targets
from xirtun.config import Config, load_config
from xirtun.memory import diet as diet_memory
from xirtun.messaging.base import Messenger
from xirtun.messaging.telegram import TelegramMessenger
from xirtun.run_weekly import MIN_INTERVAL
from xirtun.storage import db, runs

logger = logging.getLogger(__name__)

# Treat a weight older than this as stale — the weekly review wants a recent one.
# 6 days (not 7) so the nudge has a day of margin before the review runs.
WEIGHT_STALE = timedelta(days=6)

# The reminder runs in the morning; the review fires later the same day. This window
# lets the morning run predict that tonight's review will be due, so the nudge lands
# the morning OF the review rather than the day before.
_DUE_LOOKAHEAD = timedelta(days=1)

REMINDER_TEXT = (
    "⚖️ Weekly check-in: I don't have a recent weight from you, and your review runs "
    "today. Send /weight <kg> (e.g. /weight 82) so I can judge your calories against "
    "the scale instead of just an estimate."
)


def _review_due_today(conn: sqlite3.Connection, now: datetime, min_interval: timedelta) -> bool:
    """True if the weekly review will become due before the end of today's window."""
    last = runs.last_ok_at(conn)
    return last is None or (now + _DUE_LOOKAHEAD) - last >= min_interval


def _weight_is_stale(conn: sqlite3.Connection, now: datetime, stale: timedelta) -> bool:
    return not targets.weight_history(conn, (now - stale).isoformat())


def send_weight_reminder(
    *,
    conn: sqlite3.Connection,
    diet_path: Path,
    messenger: Messenger,
    tz: tzinfo,
    now: datetime | None = None,
    min_interval: timedelta = MIN_INTERVAL,
    stale: timedelta = WEIGHT_STALE,
) -> bool:
    """Send the weight-log reminder if the review is due today and weight is stale.

    Returns True if a reminder was sent. Stays silent before onboarding, when a recent
    weight exists, or when the review isn't due yet — so the user is nudged ~once a week.
    """
    now = now or datetime.now(tz)

    if diet_memory.is_empty(diet_path):
        return False
    if not _review_due_today(conn, now, min_interval):
        return False
    if not _weight_is_stale(conn, now, stale):
        return False

    messenger.send(REMINDER_TEXT)
    logger.info("sent weight-log reminder")
    return True


def run_scheduled_reminder(config: Config) -> bool:
    """Build production dependencies and send the reminder if it's due."""
    conn = db.get_connection(config.data_dir / "xirtun.db")
    messenger = TelegramMessenger(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        conn=conn,
    )
    return send_weight_reminder(
        conn=conn,
        diet_path=config.data_dir / "diet.md",
        messenger=messenger,
        tz=db.get_timezone(conn, config.timezone),
    )


def main() -> None:
    from xirtun.logging_setup import setup_logging

    setup_logging()
    config = load_config()
    db.init_db(config.data_dir / "xirtun.db")
    logger.info("running weight reminder (manual)")
    run_scheduled_reminder(config)


if __name__ == "__main__":
    main()
