"""Tests for deterministic calorie/protein targets and metric storage."""

from xirtun import targets

FULL = {"sex": "male", "birth_year": 1994, "height_cm": 180, "weight_kg": 80, "activity": "moderate"}


def test_compute_full_metrics():
    t = targets.compute(FULL)
    assert t is not None
    # moderate activity → 1.4–1.6 g/kg range
    assert t["protein_min_g"] == round(1.4 * 80)
    assert t["protein_max_g"] == round(1.6 * 80)
    assert t["calories"] > 1500  # sanity: a plausible maintenance figure


def test_compute_incomplete_returns_none():
    assert targets.compute({"sex": "male", "birth_year": 1994}) is None


def test_metrics_roundtrip_and_weight_update(conn):
    targets.write_metrics(conn, dict(FULL))
    assert targets.read_metrics(conn)["weight_kg"] == 80

    targets.update_weight(conn, 75)
    assert targets.read_metrics(conn)["weight_kg"] == 75
    assert targets.read_metrics(conn)["height_cm"] == 180  # other fields preserved


def test_format_targets_missing_metrics():
    assert "don't have" in targets.format_targets({})


# --- calibrated working targets ---

def test_set_calibrated_persists_and_shows(conn):
    targets.write_metrics(conn, dict(FULL))
    result = targets.set_calibrated(
        conn, calories=2300, protein_min_g=110, protein_max_g=130,
        rationale="ankle injury, sedentary weeks, reports feeling too full",
    )
    assert "2300" in result

    cal = targets.read_calibrated(conn)
    assert cal["calories"] == 2300
    assert cal["protein_min_g"] == 110

    combined = targets.format_all_targets(conn)
    assert "Formula estimate" in combined
    assert "2300" in combined
    assert "too full" in combined       # rationale surfaced


def test_set_calibrated_clamps_dangerous_values(conn):
    targets.write_metrics(conn, dict(FULL))
    formula = targets.compute(targets.read_metrics(conn))

    result = targets.set_calibrated(
        conn, calories=800, protein_min_g=10, protein_max_g=500, rationale="bad idea",
    )
    assert "clamped" in result

    cal = targets.read_calibrated(conn)
    assert cal["calories"] >= 1500                    # never below BMR
    assert cal["calories"] <= round(formula["calories"] * 1.5)
    assert cal["protein_min_g"] >= round(0.8 * 80)    # 0.8 g/kg floor
    assert cal["protein_max_g"] <= round(2.2 * 80)    # 2.2 g/kg cap


def test_set_calibrated_requires_rationale_and_metrics(conn):
    assert "ERROR" in targets.set_calibrated(
        conn, calories=2300, protein_min_g=110, protein_max_g=130, rationale="x",
    )  # no metrics yet

    targets.write_metrics(conn, dict(FULL))
    assert "ERROR" in targets.set_calibrated(
        conn, calories=2300, protein_min_g=110, protein_max_g=130, rationale="  ",
    )  # blank rationale
    assert targets.read_calibrated(conn) is None      # nothing stored


def test_format_calibrated_when_unset(conn):
    assert "No calibrated target" in targets.format_calibrated(conn)


# --- weight-trend staleness ---

def _log(conn, when, kg):
    targets.update_weight(conn, kg, now=when)


def test_weight_trend_flags_stale_latest_entry(conn):
    """Regression (prod 2026-08-13): the review ran with the newest weight 7 days old,
    so the 35-day decline covered none of the reviewed week — but the trend text gave no
    staleness signal, so the agent called it 'this week' and raised the target on it."""
    from datetime import datetime, timedelta

    now = datetime(2026, 8, 13, 17, 0)
    _log(conn, now - timedelta(days=42), 85.0)
    _log(conn, now - timedelta(days=7), 81.3)      # newest entry predates the week

    out = targets.format_weight_trend(conn, now=now)
    assert "85kg → 81.3kg" in out                  # history still reported
    assert "STALE" in out
    assert "7 days old" in out
    assert "NO data from the week" in out
    assert "do NOT recalibrate" in out.replace("Do NOT", "do NOT")


def test_weight_trend_marks_recent_entry_as_covering_the_week(conn):
    from datetime import datetime, timedelta

    now = datetime(2026, 8, 13, 17, 0)
    _log(conn, now - timedelta(days=20), 85.0)
    _log(conn, now - timedelta(days=1), 83.0)

    out = targets.format_weight_trend(conn, now=now)
    assert "STALE" not in out
    assert "1d old" in out
    assert "does cover the week" in out


def test_single_weight_reports_its_age(conn):
    from datetime import datetime, timedelta

    now = datetime(2026, 8, 13, 17, 0)
    _log(conn, now - timedelta(days=9), 82.0)
    out = targets.format_weight_trend(conn, now=now)
    assert "9d ago" in out and "Not enough for a trend" in out


# --- waist (logged alongside weight; nothing computes from it) ---

def test_waist_trend_needs_two_measurements(conn):
    from datetime import datetime, timedelta

    now = datetime(2026, 8, 24, 9, 0)
    assert "No waist measurements" in targets.format_waist_trend(conn, now=now)

    targets.add_waist(conn, 86.0, now=now - timedelta(days=30))
    assert "one measurement" in targets.format_waist_trend(conn, now=now)

    targets.add_waist(conn, 84.0, now=now)
    trend = targets.format_waist_trend(conn, now=now)
    assert "86cm → 84cm" in trend
    assert "-2.0cm" in trend and "down" in trend
    assert len(targets.waist_history(conn)) == 2


def test_waist_is_kept_out_of_the_calorie_formula(conn):
    """Waist answers a question weight can't, but nothing computes from it — a target
    must not silently move because a tape measure did."""
    from datetime import datetime

    targets.write_metrics(conn, dict(FULL))
    before = targets.compute(targets.read_metrics(conn))
    targets.add_waist(conn, 84.0, now=datetime(2026, 8, 24, 9, 0))
    assert targets.compute(targets.read_metrics(conn)) == before
    assert "waist" not in str(targets.read_metrics(conn)).lower()
