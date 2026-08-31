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

import json
import re
from pathlib import Path

from g_league_affiliates import AFFILIATE_PARENT

_INDEX = Path(__file__).resolve().parent.parent / "data" / "nba_team_index.json"


def _load_index() -> tuple[set, dict]:
    """Current franchise names, and era name -> current franchise. Read from the
    generated index so there is one source of truth; an empty index just means
    the parent-club rule below never fires, never that it fires wrongly."""
    try:
        idx = json.loads(_INDEX.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), {}
    return set(idx.get("franchises") or []), dict(idx.get("eras") or {})


_FRANCHISES, _ERAS = _load_index()


def _current_franchise(team: str) -> str:
    """The franchise a team name denotes today, or "" if it isn't an NBA name."""
    if team in _FRANCHISES:
        return team
    return _ERAS.get(team, "")

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


def _career_keys(career: list) -> list:
    """Sort keys for a whole career: (start, end, rank, original index).

    Two refinements sit on top of the plain (start, end) rule, and both need to
    see the WHOLE career, which is why the keys are computed together:

    THE PARENT-CLUB RULE. When a player is assigned to his NBA club's own G
    League affiliate, both stints start in the same year and both are ranges,
    so ordering by end year alone puts whichever ended first at the front --
    Aaron Wiggins read as Oklahoma City Blue (2021-2023) before Oklahoma City
    Thunder (2021-2026), as though he played for the affiliate before the club
    that assigned him there. So an affiliate stint that shares its start year
    with a range stint at its own parent club borrows the PARENT's end year and
    takes rank 1, which lands it immediately after the parent and nowhere else.

    This is done by rewriting keys rather than by special-casing the comparison
    of two stints, because a pairwise "parent first" override is not
    transitive: with Thunder (2021-2026), Blue (2021-2023) and some third club
    (2021-2024), pairwise rules give third < Thunder, Thunder < Blue and
    Blue < third -- a cycle, and an undefined sort. Keys cannot cycle.

    WHAT IS DELIBERATELY LEFT ALONE. The bare-year rule wins over this one: a
    bare "2007" still precedes a "2007-2011" starting the same year, affiliate
    or not, because a bare year means the player was there within that year and
    left. And a same-start tie where neither side is an NBA franchise (A.J.
    Wynder's four CBA clubs, all 1995-1996) has no parent to anchor it, so it
    keeps the order it already had.
    """
    stints = list(career or [])
    raw = [bounds((s or {}).get("years")) for s in stints]

    # start year -> {current franchise name: end year} for NBA RANGE stints.
    # Bare-year NBA stints are excluded: the bare-year rule governs those.
    nba_ranges: dict[int, dict[str, int]] = {}
    for (start, end), s in zip(raw, stints):
        if start == end:
            continue
        franchise = _current_franchise((s or {}).get("team", ""))
        if franchise:
            nba_ranges.setdefault(start, {})[franchise] = end

    keys = []
    for i, ((start, end), s) in enumerate(zip(raw, stints)):
        rank = 0
        if start != end:                       # ranges only; bare years opt out
            parent = AFFILIATE_PARENT.get((s or {}).get("team", ""))
            parent_end = nba_ranges.get(start, {}).get(parent) if parent else None
            if parent_end is not None:
                end, rank = parent_end, 1      # sit directly behind the parent
        keys.append((start, end, rank, i))
    return keys


def sort_career(career: list) -> list:
    """Career stints in canonical order. Returns a new list."""
    stints = list(career or [])
    return [s for _, s in sorted(zip(_career_keys(stints), stints),
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
