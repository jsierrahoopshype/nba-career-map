"""Canonical ordering of a player's career stints.

THE RULE: start year ascending, then END year ascending, then original
position (stable).

The end-year tie-break is what puts stints in sequence when two of them start
in the same year. A bare "2003" ends in 2003 -- the player was there within
that year and left -- while "2003-2004" runs past it, so the bare year comes
FIRST. Gary Payton 2003 is the canonical case: Sonics (1990-2003), then Bucks
("2003"), then Lakers ("2003-2004").

This replaces a duration-DESCENDING tie-break that put the longer stint first,
which read Lakers before Bucks. That old rule existed to keep a parent club
ahead of a same-year loan or G League assignment (Rockets before Rio Grande
Valley Vipers). Ordering strictly by when a stint ENDED reverses those, because
the assignment really did end first. The two cannot be told apart from years
alone -- "Rockets 2007-2011 / Vipers 2007" and "Sonics.../Bucks 2003 / Lakers
2003-2004" have identical shape -- so chronology wins and the affiliate sorts
first.

This module is the Python side of the rule; index.html's careerBounds() /
sortCareer() implement the same comparator for display. Keeping the stored
career_history in this order means positional reads ([0] for the first stop,
[-1] for the last) are correct by construction for every consumer.
"""
from __future__ import annotations

import re

_YEAR = re.compile(r"\d{4}")
_OPEN_ENDED = re.compile(r"present", re.I)
_TRAILING_DASH = re.compile(r"[–-]\s*$")

# Sorts after any real year, for "present"/open-ended spans and unparseable
# ones. Matches the 9999 sentinel index.html uses.
OPEN_END = 9999


def bounds(years) -> tuple[int, int]:
    """(start, end) for a year span. A bare year ends in that same year; an
    open-ended span ends at OPEN_END so it sorts last."""
    yrs = str(years or "")
    nums = _YEAR.findall(yrs)
    start = int(nums[0]) if nums else OPEN_END
    if _OPEN_ENDED.search(yrs) or _TRAILING_DASH.search(yrs):
        end = OPEN_END
    elif len(nums) > 1:
        end = int(nums[1])
    else:
        end = start
    return start, end


def stint_key(stint: dict, i: int = 0) -> tuple[int, int, int]:
    start, end = bounds((stint or {}).get("years"))
    return (start, end, i)


def sort_career(career: list) -> list:
    """Career stints in canonical order. Returns a new list."""
    return [s for _, s in sorted(
        ((stint_key(s, i), s) for i, s in enumerate(career or [])),
        key=lambda t: t[0])]


def year_span_key(years) -> tuple[int, int]:
    """Sort key for a bare year-span string (used where spans are joined for
    display, e.g. "2002-2004, 2019")."""
    return bounds(years)


def order_players(players: list) -> int:
    """Reorder every player's career_history in place. Returns the number of
    players whose order actually changed."""
    changed = 0
    for p in players:
        hist = p.get("career_history")
        if not hist or len(hist) < 2:
            continue
        ordered = sort_career(hist)
        if any(a is not b for a, b in zip(hist, ordered)):
            p["career_history"] = ordered
            changed += 1
    return changed
