"""APScheduler setup: fires the weekly review on the configured cron schedule.

Runs in-process (a background thread inside the bot), so a single artifact behaves
identically on a laptop and on the VM. Robustness comes from a restart policy plus
the idempotency guard in run_weekly_review (see docs/decisions.md ADR-006).
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from xirtun.config import Config
from xirtun.run_weekly import run_scheduled_review

logger = logging.getLogger(__name__)


def start_scheduler(config: Config) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=config.timezone)
    trigger = CronTrigger.from_crontab(config.weekly_cron, timezone=config.timezone)
    scheduler.add_job(
        lambda: run_scheduled_review(config, force=False),
        trigger=trigger,
        id="weekly_review",
        misfire_grace_time=3600,  # still run if the fire time was missed by up to 1h
    )
    scheduler.start()
    logger.info("scheduler started (weekly cron: %s)", config.weekly_cron)
    return scheduler
