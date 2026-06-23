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


def read_diet(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def is_empty(path: Path) -> bool:
    return not read_diet(path).strip()


NOTE_HEADING = "## Notes from me"


def append_note(path: Path, text: str, *, now: datetime | None = None) -> None:
    """Append a user note under a dedicated heading in diet.md.

    Append-only (no snapshot): notes are additive and low-risk. The weekly run
    later reads them and folds them into the profile proper.
    """
    now = now or datetime.now().astimezone()
    content = read_diet(path)
    if NOTE_HEADING not in content:
        content = content.rstrip() + f"\n\n{NOTE_HEADING}\n"
    content = content.rstrip() + f"\n- {now:%Y-%m-%d}: {text}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_diet(path: Path, content: str, *, now: datetime | None = None) -> None:
    now = now or datetime.now().astimezone()
    
    # Snapshot the previous version only if one exists and isn't blank.
    if path.exists() and path.read_text(encoding="utf-8").strip():
        history = path.parent / "diet.history"
        history.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, history / f"diet-{now:%Y%m%dT%H%M%S}.md")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
