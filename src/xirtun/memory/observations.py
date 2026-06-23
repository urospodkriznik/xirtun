"""observations.md — the weekly agent's own long-term memory (ADR-010).

A compact running summary the agent rewrites each week. Read + full-rewrite (the
agent is responsible for keeping it compact); unlike diet.md we don't snapshot it,
since it's the agent's regenerable scratchpad rather than user-authored facts.
"""

from __future__ import annotations

from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
