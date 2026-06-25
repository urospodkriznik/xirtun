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


def test_recent_meals_report(conn):
    diary.save_meal(conn, "x", _meal([{"name": "banana", "calories": 100}], NOW.isoformat()))
    out = reports.recent_meals_report(conn)
    assert "Last 1 meals" in out and "banana" in out and "2026-06-23" in out


def test_recent_workouts_report(conn):
    diary.save_exercise(conn, "ran", {
        "occurred_at": NOW.isoformat(), "type": "running", "duration_min": 30,
        "intensity": None, "calories_burned": 300, "distance_km": None, "notes": None, "tags": [],
    })
    out = reports.recent_exercises_report(conn)
    assert "running" in out and "300" in out


def test_recent_notes_report(tmp_path):
    from xirtun.memory import diet as memory
    p = tmp_path / "diet.md"
    p.write_text("# Profile")
    memory.append_note(p, "gain muscle", now=NOW)
    assert "gain muscle" in reports.recent_notes_report(p)
