"""Tests for scheduler timezone wiring: start_scheduler reads the stored (or default)
timezone, and reschedule() rebuilds both cron jobs against a new one."""

from pathlib import Path
from zoneinfo import ZoneInfo

from xirtun.config import Config
from xirtun.scheduler import reschedule, start_scheduler
from xirtun.storage import db


def _config(tmp_path: Path) -> Config:
    return Config(
        telegram_token="t",
        telegram_chat_id="1",
        gemini_api_key="k",
        cheap_model="m",
        strong_model="m",
        data_dir=tmp_path,
        weekly_cron="0 17 * * *",
        weight_reminder_cron="0 8 * * *",
        timezone=ZoneInfo("UTC"),
    )


def test_start_scheduler_uses_stored_timezone(conn, tmp_path):
    db.set_timezone(conn, "Europe/Ljubljana")
    scheduler = start_scheduler(_config(tmp_path), conn)
    try:
        assert str(scheduler.get_job("weekly_review").trigger.timezone) == "Europe/Ljubljana"
        assert str(scheduler.get_job("weight_reminder").trigger.timezone) == "Europe/Ljubljana"
    finally:
        scheduler.shutdown(wait=False)


def test_start_scheduler_falls_back_to_config_default(conn, tmp_path):
    scheduler = start_scheduler(_config(tmp_path), conn)  # nothing stored yet
    try:
        assert str(scheduler.get_job("weekly_review").trigger.timezone) == "UTC"
    finally:
        scheduler.shutdown(wait=False)


def test_reschedule_rebuilds_both_jobs_with_new_timezone(conn, tmp_path):
    config = _config(tmp_path)
    scheduler = start_scheduler(config, conn)
    try:
        reschedule(scheduler, config, ZoneInfo("America/New_York"))
        assert str(scheduler.get_job("weekly_review").trigger.timezone) == "America/New_York"
        assert str(scheduler.get_job("weight_reminder").trigger.timezone) == "America/New_York"
    finally:
        scheduler.shutdown(wait=False)
