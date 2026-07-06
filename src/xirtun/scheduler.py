"""APScheduler setup: fires the weekly review on the configured cron schedule.

Runs in-process (a background thread inside the bot), so a single artifact behaves
identically on a laptop and on the VM. Robustness comes from a restart policy plus
the idempotency guard in run_weekly_review (see docs/decisions.md ADR-006).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import tzinfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from xirtun.config import Config
from xirtun.run_reminder import run_scheduled_reminder
from xirtun.run_weekly import run_scheduled_review
from xirtun.storage import db

logger = logging.getLogger(__name__)


def _triggers(config: Config, tz: tzinfo) -> tuple[CronTrigger, CronTrigger]:
    return (
        CronTrigger.from_crontab(config.weekly_cron, timezone=tz),
        CronTrigger.from_crontab(config.weight_reminder_cron, timezone=tz),
    )


def start_scheduler(config: Config, conn: sqlite3.Connection) -> BackgroundScheduler:
    # The user's timezone (set via onboarding, or config's UTC default if unset yet)
    # decides what wall-clock time these cron expressions fire at.
    tz = db.get_timezone(conn, config.timezone)
    scheduler = BackgroundScheduler(timezone=tz)

    weekly_trigger, reminder_trigger = _triggers(config, tz)
    scheduler.add_job(
        lambda: run_scheduled_review(config, force=False),
        trigger=weekly_trigger,
        id="weekly_review",
        misfire_grace_time=3600,  # still run if the fire time was missed by up to 1h
    )
    scheduler.add_job(
        lambda: run_scheduled_reminder(config),
        trigger=reminder_trigger,
        id="weight_reminder",
        misfire_grace_time=3600,
    )

    scheduler.start()
    logger.info(
        "scheduler started (weekly cron: %s, weight reminder cron: %s, timezone: %s)",
        config.weekly_cron,
        config.weight_reminder_cron,
        tz,
    )
    return scheduler


def reschedule(scheduler: BackgroundScheduler, config: Config, tz: tzinfo) -> None:
    """Rebuild both cron jobs against a new timezone (e.g. after /timezone), so the
    change takes effect immediately instead of waiting for a restart."""
    weekly_trigger, reminder_trigger = _triggers(config, tz)
    scheduler.reschedule_job("weekly_review", trigger=weekly_trigger)
    scheduler.reschedule_job("weight_reminder", trigger=reminder_trigger)
    logger.info("scheduler rescheduled to timezone: %s", tz)
