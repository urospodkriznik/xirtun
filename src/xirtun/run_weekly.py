"""The weekly review: guarded orchestration around the agent loop, plus an entrypoint.

``run_weekly_review`` adds idempotency — it skips if a successful run happened within
``MIN_INTERVAL``, unless ``force`` is set — and records every run in the ``runs``
table. ``run_scheduled_review`` builds the production dependencies (its own database
connection, the strong model, the Telegram messenger) and is what the scheduler, the
``/weekly`` command, the startup catch-up, and this module's CLI all call.

Run manually:  uv run python -m xirtun.run_weekly
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, tzinfo
from pathlib import Path

from xirtun.agent.weekly import WeeklyResult, run_weekly
from xirtun.config import Config, load_config
from xirtun.llm.base import LLMClient
from xirtun.llm.gemini import GeminiClient
from xirtun.logging_setup import setup_logging
from xirtun.messaging.base import Messenger
from xirtun.messaging.telegram import TelegramMessenger
from xirtun.pipeline import weekly_qa
from xirtun.storage import db, runs

logger = logging.getLogger(__name__)

# A successful review within this window suppresses scheduled and catch-up runs, so a
# manual run and the cron firing (or a boot-time catch-up) can't double-fire the week.
MIN_INTERVAL = timedelta(days=7)


def _deliver(
    result: WeeklyResult, *, conn: sqlite3.Connection, chat_id: str, messenger: Messenger,
    manner: str, now: datetime | None,
) -> None:
    """Decide send timing for the report vs. its calibrating questions.

    "manual" (/weekly): if there are questions, HOLD the report and ask first — the
    user is presumably at the keyboard. "scheduled": always send the report right
    away (no one's guaranteed to be around), then follow up with any questions.
    """
    if manner == "manual" and result.questions:
        weekly_qa.start(conn, chat_id, mode="interactive", questions=result.questions,
                         report=result.report, now=now)
        messenger.send(weekly_qa.format_intro(result.questions))
        return

    if result.report:
        messenger.send(result.report)
    if result.questions:
        weekly_qa.start(conn, chat_id, mode="capture", questions=result.questions, now=now)
        messenger.send(weekly_qa.format_followup(result.questions))


def run_weekly_review(
    *,
    llm: LLMClient,
    conn: sqlite3.Connection,
    diet_path: Path,
    observations_path: Path,
    messenger: Messenger,
    tz: tzinfo,
    chat_id: str,
    manner: str = "scheduled",
    force: bool = False,
    now: datetime | None = None,
    min_interval: timedelta = MIN_INTERVAL,
) -> bool:
    """Run the weekly review unless one succeeded recently. Returns True if it ran."""
    now = now or datetime.now(tz)

    if not force:
        last = runs.last_ok_at(conn)
        if last is not None and now - last < min_interval:
            logger.info("weekly review skipped — last ran %s", last.isoformat())
            return False

    run_id = runs.start(conn, now)
    try:
        result = run_weekly(
            llm=llm,
            conn=conn,
            diet_path=diet_path,
            observations_path=observations_path,
            tz=tz,
            now=now,
        )
        if result.incomplete:
            # The agent ran out of tool-call turns before it could write the report —
            # some tool calls (e.g. set_targets) may already have taken effect, but
            # nothing was ever sent. Marking this "ok" would silently reset the 7-day
            # guard with nothing delivered; mark it distinctly so last_ok_at (which
            # only counts status='ok') is untouched and the next scheduled tick retries.
            logger.warning("weekly review incomplete (ran out of turns) — will retry next tick")
            messenger.send(
                "⚠️ Your weekly review didn't finish in time — I'll retry at the next "
                "scheduled slot rather than send a partial report."
            )
            runs.finish(conn, run_id, datetime.now(tz), "incomplete")
            return True
        _deliver(result, conn=conn, chat_id=chat_id, messenger=messenger, manner=manner, now=now)
        runs.finish(conn, run_id, datetime.now(tz), "ok")
        return True
    except Exception:
        runs.finish(conn, run_id, datetime.now(tz), "error")
        logger.exception("weekly review failed")
        raise


def run_scheduled_review(config: Config, *, force: bool = False, manner: str = "scheduled") -> bool:
    """Build production dependencies and run the review.

    Opens its own database connection because it may run on a background thread (the
    scheduler), and SQLite connections can't be shared across threads. Uses the strong
    model — the weekly review is the one place the big model is worth paying for.
    """
    conn = db.get_connection(config.data_dir / "xirtun.db")
    llm = GeminiClient(config.gemini_api_key, config.strong_model)
    messenger = TelegramMessenger(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        conn=conn,
    )
    return run_weekly_review(
        llm=llm,
        conn=conn,
        diet_path=config.data_dir / "diet.md",
        observations_path=config.data_dir / "observations.md",
        messenger=messenger,
        tz=db.get_timezone(conn, config.timezone),
        chat_id=config.telegram_chat_id,
        manner=manner,
        force=force,
    )


def main() -> None:
    setup_logging()
    config = load_config()
    db.init_db(config.data_dir / "xirtun.db")
    logger.info("starting weekly review (manual)")
    run_scheduled_review(config, force=True)


if __name__ == "__main__":
    main()
