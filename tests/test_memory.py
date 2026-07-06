"""Tests for the agent-managed diet.md file, including the snapshot safeguard."""

from datetime import datetime, timezone

from xirtun.memory import diet as memory


def test_read_and_is_empty(tmp_path):
    p = tmp_path / "diet.md"
    assert memory.is_empty(p)            # missing file
    p.write_text("   \n")
    assert memory.is_empty(p)            # blank
    p.write_text("# Profile")
    assert not memory.is_empty(p)


def test_write_creates_file(tmp_path):
    p = tmp_path / "diet.md"
    memory.write_diet(p, "# Profile v1")
    assert p.read_text() == "# Profile v1"


def test_write_snapshots_previous_version(tmp_path):
    p = tmp_path / "diet.md"
    memory.write_diet(p, "# v1", now=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc))
    memory.write_diet(p, "# v2", now=datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc))

    assert p.read_text() == "# v2"
    snapshots = list((tmp_path / "diet.history").glob("diet-*.md"))
    assert len(snapshots) == 1
    assert snapshots[0].read_text() == "# v1"


def test_append_note(tmp_path):
    p = tmp_path / "diet.md"
    p.write_text("# Profile\n")
    memory.append_note(p, "gain muscle", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    memory.append_note(p, "wants more lutein", now=datetime(2026, 1, 2, tzinfo=timezone.utc))

    text = p.read_text()
    assert text.count(memory.NOTE_HEADING) == 1     # one heading, not one per note
    assert "gain muscle" in text and "wants more lutein" in text


def test_last_note_returns_most_recent_with_timestamp(tmp_path):
    p = tmp_path / "diet.md"
    p.write_text("# Profile\n")
    assert memory.last_note(p) is None

    memory.append_note(p, "gain muscle", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    memory.append_note(p, "wants more lutein", now=datetime(2026, 1, 2, tzinfo=timezone.utc))

    last = memory.last_note(p)
    assert last["text"] == "wants more lutein"
    assert last["occurred_at"] == datetime(2026, 1, 2, tzinfo=timezone.utc)


def test_remove_last_note_removes_only_the_last_one(tmp_path):
    p = tmp_path / "diet.md"
    p.write_text("# Profile\n")
    memory.append_note(p, "gain muscle", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    memory.append_note(p, "wants more lutein", now=datetime(2026, 1, 2, tzinfo=timezone.utc))

    removed = memory.remove_last_note(p)
    assert removed == "wants more lutein"

    text = p.read_text()
    assert "gain muscle" in text
    assert "wants more lutein" not in text
    assert memory.last_note(p)["text"] == "gain muscle"


def test_remove_last_note_empty_is_noop(tmp_path):
    p = tmp_path / "diet.md"
    p.write_text("# Profile\n")
    assert memory.remove_last_note(p) is None
