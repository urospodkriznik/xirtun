"""Tests for the remove-a-person walk-through's pure helpers."""

import pytest

from xirtun.removeuser import delete, removal_targets


def _set_up(root):
    (root / ".env").write_text("GEMINI_API_KEY=k\n")
    (root / ".env.ana").write_text("TELEGRAM_TOKEN=t\n")
    (root / ".env.uros").write_text("TELEGRAM_TOKEN=u\n")
    for person in ("ana", "uros"):
        (root / "data" / person / "diet.history").mkdir(parents=True)
        (root / "data" / person / "xirtun.db").write_text("")
        (root / "data" / person / "xirtun.db-wal").write_text("")


def test_removes_only_that_person(tmp_path):
    _set_up(tmp_path)

    targets = removal_targets("ana", tmp_path)
    delete(targets)

    assert targets == [tmp_path / ".env.ana", tmp_path / "data" / "ana"]
    assert not (tmp_path / ".env.ana").exists()
    assert not (tmp_path / "data" / "ana").exists()
    assert (tmp_path / ".env").exists()                          # shared settings stay
    assert (tmp_path / ".env.uros").exists()                     # everyone else stays
    assert (tmp_path / "data" / "uros" / "xirtun.db-wal").exists()


def test_partial_setup_lists_what_exists(tmp_path):
    (tmp_path / ".env.ana").write_text("TELEGRAM_TOKEN=t\n")    # no data yet
    assert removal_targets("ana", tmp_path) == [tmp_path / ".env.ana"]
    assert removal_targets("nobody", tmp_path) == []


@pytest.mark.parametrize("name", ["../data", "", "Ana", ".", "data/uros", "a"])
def test_refuses_names_that_could_escape(tmp_path, name):
    with pytest.raises(ValueError):
        removal_targets(name, tmp_path)
