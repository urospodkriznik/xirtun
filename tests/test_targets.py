"""Tests for deterministic calorie/protein targets and metric storage."""

from xirtun import targets

FULL = {"sex": "male", "birth_year": 1994, "height_cm": 180, "weight_kg": 80, "activity": "moderate"}


def test_compute_full_metrics():
    t = targets.compute(FULL)
    assert t is not None
    assert t["protein_g"] == round(1.6 * 80)
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
