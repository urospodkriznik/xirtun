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
