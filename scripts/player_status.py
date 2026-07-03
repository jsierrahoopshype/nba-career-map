"""Player tracking-status classification.

Three tracking statuses (stored in each player record's ``status`` field):

  nba_active       currently on an NBA roster (or their Wikipedia current team
                   is an NBA franchise)
  overseas_active  no longer in the NBA but still playing (overseas league,
                   G League, etc.) within the recency window
  retired          no team for ``retire_gap`` or more years

Note: the *parse* outcome (success / no_career_data / error) is stored
separately in ``parse_status`` so it never collides with tracking status.

Live runs classify with the strict spec rule (retire_gap=2). The seed import
uses a more lenient gap so borderline players still enter the overseas
re-check queue and are converged to the correct status by later runs (rather
than being stranded as ``retired`` and never re-checked).
"""
from __future__ import annotations

import re

from rosters import NBA_TEAMS

NBA_ACTIVE = "nba_active"
OVERSEAS_ACTIVE = "overseas_active"
RETIRED = "retired"

PRESENT = 9999  # sentinel: an open-ended / "present" stint

_NBA_TEAM_SET = set(NBA_TEAMS)


def last_active_year(history: list[dict]) -> int:
    """Most recent end-year across a career history.

    Returns PRESENT for any open-ended ("2023–present" / "2023–") stint, and 0
    when no year can be parsed at all.
    """
    latest = 0
    for stint in history:
        yrs = str(stint.get("years", ""))
        if "present" in yrs.lower() or re.search(r"[–\-]\s*$", yrs):
            return PRESENT
        for y in re.findall(r"\d{4}", yrs):
            latest = max(latest, int(y))
    return latest


def is_nba_team(team: str) -> bool:
    return team in _NBA_TEAM_SET


def classify_status(record: dict, on_nba_roster: bool, current_year: int,
                    retire_gap: int = 2) -> str:
    """Classify a player record into one of the three tracking statuses.

    Roster membership (``on_nba_roster``) is only a *candidate* signal used to
    decide who to fetch; it does NOT by itself confer ``nba_active`` — otherwise
    coaches and staff listed on roster templates would be marked active. The
    decision is made from the fetched page:

      * A record with no parseable playing career (empty career_history) is a
        non-player (coach/staff) -> retired, never nba_active.
      * nba_active requires an actual *current NBA stint*: a recent (within
        retire_gap years, or open-ended "present") stint whose current team is
        an NBA franchise.
      * A recent stint on a non-NBA team -> overseas_active.
      * Otherwise (gap of retire_gap+ years) -> retired.

    ``on_nba_roster`` is retained in the signature for callers but intentionally
    not used in the positive decision.
    """
    current_team = record.get("current_team", "") or ""
    history = record.get("career_history", []) or []

    # A non-player (no career at all) can never be active. This is the guard
    # that keeps pure coaches / categories / schools out of nba_active.
    if not history:
        return RETIRED

    ly = last_active_year(history)

    # Recency gate first: not played in retire_gap+ years -> retired, even if
    # their last team was an NBA franchise (a former NBA player now coaching).
    if ly == 0:
        return RETIRED
    if ly != PRESENT and (current_year - ly) >= retire_gap:
        return RETIRED

    # Recently active. NBA franchise as current team = confirmed NBA player;
    # anything else = still playing, just not in the NBA.
    if is_nba_team(current_team):
        return NBA_ACTIVE
    return OVERSEAS_ACTIVE
