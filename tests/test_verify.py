"""Tests for checking the agent's calorie claims against computed values, and for the
app-owned numbers block in the agent's memory file."""

from datetime import datetime, timedelta

from xirtun.agent import tools, verify
from xirtun.memory import observations
from xirtun.storage import diary

NOW = datetime(2026, 7, 8, 17, 0)


def _meal(conn, occurred_at, kcal):
    diary.save_meal(conn, "m", {"occurred_at": occurred_at, "notes": None,
                                "items": [{"name": "x", "calories": kcal, "protein_g": 50}]})


def test_flags_a_figure_the_diary_does_not_support():
    """The real failure: a report claimed 1899 kcal for a week the diary puts at 2098,
    and that number then entered the agent's memory as established fact."""
    report = "Your week averaged 1899 kcal/day, down from 2400."
    assert verify.unverified_calorie_figures(report, {2098, 2400}) == [1899]


def test_accepts_figures_that_match_within_rounding():
    report = "You averaged 2098 kcal/day (2400 kcal target)."
    assert verify.unverified_calorie_figures(report, {2099, 2400}) == []


def test_ignores_numbers_that_are_not_intake_claims():
    """A recommendation cites numbers legitimately — flagging those would make the
    check useless noise, so only plausible daily-intake figures are examined."""
    report = "Add a 30g scoop of protein and a 200 kcal snack; aim for a 500 kcal deficit."
    assert verify.unverified_calorie_figures(report, {2098}) == []


def test_reports_each_bad_figure_once_in_order():
    report = "1899 kcal, then 1899 kcal again, and 3500 kcal."
    assert verify.unverified_calorie_figures(report, {2098}) == [1899, 3500]


def test_verified_values_come_from_daily_and_weekly_arithmetic(conn):
    _meal(conn, (NOW - timedelta(days=1)).isoformat(), 2000)
    _meal(conn, (NOW - timedelta(days=2)).isoformat(), 2200)

    values = tools.verified_values(conn, NOW)
    assert 2000 in values and 2200 in values    # per-day totals
    assert 2100 in values                       # the week's average over logged days


def test_numbers_block_is_written_by_the_app_and_replaced_each_run(conn, tmp_path):
    path = tmp_path / "observations.md"
    observations.write(path, "The user is eating more vegetables.")
    _meal(conn, (NOW - timedelta(days=1)).isoformat(), 2000)

    observations.write_numbers_block(path, tools.format_weekly_numbers(conn, NOW))
    first = path.read_text()
    assert "The user is eating more vegetables." in first    # the agent's prose survives
    assert "Verified weekly numbers" in first
    assert "| This week | 1 | 2000 |" in first

    # A second run replaces the block rather than stacking another one.
    _meal(conn, (NOW - timedelta(days=2)).isoformat(), 2200)
    observations.write_numbers_block(path, tools.format_weekly_numbers(conn, NOW))
    second = path.read_text()
    assert second.count("Verified weekly numbers") == 1
    assert "| This week | 2 | 2100 |" in second
    assert "The user is eating more vegetables." in second


def test_agent_rewriting_its_memory_cannot_leave_a_stale_block(conn, tmp_path):
    path = tmp_path / "observations.md"
    _meal(conn, (NOW - timedelta(days=1)).isoformat(), 2000)
    observations.write_numbers_block(path, tools.format_weekly_numbers(conn, NOW))

    # The agent replaces the whole file, block included — as it does every week.
    observations.write(path, "Fresh summary with no numbers.")
    assert "Verified weekly numbers" not in path.read_text()

    observations.write_numbers_block(path, tools.format_weekly_numbers(conn, NOW))
    assert path.read_text().count("Verified weekly numbers") == 1


def test_appended_notes_stay_above_the_numbers_block(conn, tmp_path):
    path = tmp_path / "observations.md"
    observations.write(path, "Prior summary.")
    _meal(conn, (NOW - timedelta(days=1)).isoformat(), 2000)
    observations.write_numbers_block(path, tools.format_weekly_numbers(conn, NOW))

    observations.append(path, "Q&A: user says they log oils inconsistently.")

    content = path.read_text()
    assert content.index("inconsistently") < content.index("Verified weekly numbers")
    assert content.count("Verified weekly numbers") == 1
    assert content.rstrip().endswith("-->")
