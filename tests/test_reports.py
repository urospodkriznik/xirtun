"""Tests for the deterministic /today and /week reports."""

from datetime import datetime, timezone

from xirtun import reports
from xirtun.storage import diary

NOW = datetime(2026, 6, 23, 20, 0, tzinfo=timezone.utc)


def _meal(items, occurred_at):
    return {"occurred_at": occurred_at, "items": items, "notes": None}


def test_today_report_with_meals(conn):
    diary.save_meal(
        conn, "lunch",
        _meal([{"name": "banana", "calories": 100, "protein_g": 1}], NOW.replace(hour=12).isoformat()),
    )
    out = reports.today_report(conn, NOW)
    assert "1 meal" in out and "100" in out and "banana" in out


def test_today_report_empty(conn):
    assert "No meals" in reports.today_report(conn, NOW)


def test_week_report(conn):
    diary.save_meal(conn, "rice", _meal([{"name": "rice", "calories": 200}], NOW.isoformat()))
    out = reports.week_report(conn, NOW)
    assert "Past 7 days" in out and "200" in out
