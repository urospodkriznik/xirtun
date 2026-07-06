"""diet.md — the agent-managed user profile (allergies, conditions, diet, goals).

100% agent-owned (ADR-008): the agent writes it; the user doesn't hand-edit it.
Because wholesale LLM rewrites are lossy, we snapshot the previous version to
data/diet.history/ before every overwrite, so a bad rewrite is visible and
recoverable.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def read_diet(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def is_empty(path: Path) -> bool:
    return not read_diet(path).strip()


NOTE_HEADING = "## Notes from me"


def append_note(path: Path, text: str, *, now: datetime | None = None) -> None:
    """Append a user note under a dedicated heading in diet.md.

    Append-only (no snapshot): notes are additive and low-risk. The weekly run
    later reads them and folds them into the profile proper. The full timestamp
    (not just the date) is stored in brackets so /undo can compare a note's
    recency against other diary entries.
    """
    now = now or datetime.now().astimezone()
    content = read_diet(path)
    if NOTE_HEADING not in content:
        content = content.rstrip() + f"\n\n{NOTE_HEADING}\n"
    content = content.rstrip() + f"\n- [{now.isoformat()}] {text}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def parse_note_line(line: str) -> dict[str, Any] | None:
    """Parse a '- [ISO-timestamp] text' note line. Returns None for lines from
    before timestamps were bracketed (date-only) — they're still shown in
    /lastnotes, just not precise enough for /undo to rank."""
    line = line.strip()
    if not (line.startswith("- [") and "]" in line):
        return None
    raw_ts, _, text = line[3:].partition("]")
    try:
        occurred_at = datetime.fromisoformat(raw_ts)
    except ValueError:
        return None
    return {"occurred_at": occurred_at, "text": text.strip()}


def recent_notes(path: Path, limit: int = 3) -> list[str]:
    """The most recent note lines (raw, as stored), oldest-to-newest."""
    content = read_diet(path)
    if NOTE_HEADING not in content:
        return []
    section = content.split(NOTE_HEADING, 1)[1]
    notes = [line.strip() for line in section.splitlines() if line.strip().startswith("- ")]
    return notes[-limit:]


def last_note(path: Path) -> dict[str, Any] | None:
    """The most recently appended note as {occurred_at, text}, or None if there
    are no notes or the last one predates timestamped notes."""
    notes = recent_notes(path, limit=1)
    return parse_note_line(notes[0]) if notes else None


def remove_last_note(path: Path) -> str | None:
    """Delete the most recently appended note line. Returns its text, or None if
    there was nothing to remove."""
    content = read_diet(path)
    if NOTE_HEADING not in content:
        return None
    head, _, section = content.partition(NOTE_HEADING)
    lines = section.splitlines()
    note_indices = [i for i, line in enumerate(lines) if line.strip().startswith("- ")]
    if not note_indices:
        return None
    last_idx = note_indices[-1]
    parsed = parse_note_line(lines[last_idx])
    removed_text = parsed["text"] if parsed else lines[last_idx].strip().lstrip("- ").strip()
    del lines[last_idx]
    new_content = head + NOTE_HEADING + "\n".join(lines)
    path.write_text(new_content.rstrip() + "\n", encoding="utf-8")
    return removed_text


def write_diet(path: Path, content: str, *, now: datetime | None = None) -> None:
    now = now or datetime.now().astimezone()
    
    # Snapshot the previous version only if one exists and isn't blank.
    if path.exists() and path.read_text(encoding="utf-8").strip():
        history = path.parent / "diet.history"
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, history / f"diet-{now:%Y%m%dT%H%M%S}.md")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
