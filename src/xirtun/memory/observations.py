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


_NUMBERS_START = "<!-- app-numbers:start -->"
_NUMBERS_END = "<!-- app-numbers:end -->"


def strip_numbers_block(content: str) -> str:
    """The agent's own prose, with any app-written numbers block removed."""
    if _NUMBERS_START not in content:
        return content
    head, _, rest = content.partition(_NUMBERS_START)
    _, _, tail = rest.partition(_NUMBERS_END)
    return (head.rstrip() + "\n" + tail.lstrip()).strip()


def write_numbers_block(path: Path, block: str) -> None:
    """Replace the app-owned numbers section of observations.md.

    The agent rewrites this file wholesale each week, which means every figure in it is
    retyped from memory — and a mistyped average becomes next week's established fact.
    So the app owns the numbers: it strips whatever block it wrote last time and appends
    freshly computed ones. The agent's prose is left untouched, and it can still read the
    figures back; it just can't be the thing that transcribes them.
    """
    prose = strip_numbers_block(read(path))
    body = f"{prose}\n\n" if prose else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{body}{_NUMBERS_START}\n{block.strip()}\n{_NUMBERS_END}\n", encoding="utf-8"
    )


def _numbers_block(content: str) -> str:
    """The app-written block as stored, or "" if there isn't one."""
    if _NUMBERS_START not in content or _NUMBERS_END not in content:
        return ""
    _, _, rest = content.partition(_NUMBERS_START)
    block, _, _ = rest.partition(_NUMBERS_END)
    return block.strip()


def append(path: Path, note: str) -> None:
    """Append a note (e.g. weekly Q&A answers) for the agent to read and fold into
    its own rewritten summary next run — additive, unlike write()'s full replace.

    The note joins the prose, above any app-written numbers block, so that block stays
    last and stays whole."""
    content = read(path)
    block = _numbers_block(content)
    prose = strip_numbers_block(content).rstrip()
    prose = f"{prose}\n\n{note}\n" if prose else f"{note}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if block:
        prose = f"{prose.rstrip()}\n\n{_NUMBERS_START}\n{block}\n{_NUMBERS_END}\n"
    path.write_text(prose, encoding="utf-8")
