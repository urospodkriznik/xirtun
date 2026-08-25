"""Checking the agent's calorie claims against arithmetic it cannot fudge.

The weekly agent is told, at length, to take every figure from `get_intake_summary`
and never to sum meal items itself. It did anyway: a report once carried 1899 kcal
for a week the diary puts at 2098, and that number then entered its memory as fact
and shaped the following week's advice. An instruction that is already being ignored
cannot be strengthened by repeating it — so the numbers get checked.

Deliberately narrow. Only calorie figures in a plausible daily-intake range are
examined, because that is where a wrong number does real damage; a recommendation
("add a 30g scoop") must not be flagged as a false claim. This catches invented
weekly averages, not every possible slip.
"""

from __future__ import annotations

import re

# Below this, a number is almost certainly a portion, a deficit or a single food;
# above it, nothing plausible about daily intake.
_MIN_KCAL = 800
_MAX_KCAL = 6000
# A rounded average may legitimately differ from the stored figure by a little.
_TOLERANCE = 3

_KCAL = re.compile(r"(\d[\d,]*)\s*(?:kcal|calories)\b", re.IGNORECASE)


def unverified_calorie_figures(report: str, allowed: set[int]) -> list[int]:
    """Calorie figures in `report` that no computed value supports, in order of
    appearance. An empty list means every checkable number traces back to the diary."""
    suspect = []
    for match in _KCAL.finditer(report):
        value = int(match.group(1).replace(",", ""))
        if not _MIN_KCAL <= value <= _MAX_KCAL:
            continue
        if any(abs(value - ok) <= _TOLERANCE for ok in allowed):
            continue
        if value not in suspect:
            suspect.append(value)
    return suspect
