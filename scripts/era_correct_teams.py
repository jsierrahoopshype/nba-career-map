"""Restore era-accurate NBA franchise names in the stored data.

Earlier normalization collapsed every historical NBA franchise name to its
current name (Philadelphia Warriors -> Golden State Warriors, etc.). This
reverses that FOR NBA FRANCHISES ONLY, using each stint's start year to pick the
name the franchise actually used during that stint. International / non-NBA club
normalization (Tau Cerámica -> Baskonia, …) is untouched.

Only the `team` field is changed; city/state/country are left as-is (already
era-correct per verification). Idempotent. Run:

    python3 scripts/era_correct_teams.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from wiki_parser import _select_current_team

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"

# current name -> [(start_year_of_era, era_name), ...] ascending. An era covers
# [start_year, next_start_year); the last runs to present. Chosen by the stint's
# START year. Sourced from the provided Wikipedia reference table.
ERA_TABLE: dict[str, list[tuple[int, str]]] = {
    "Atlanta Hawks": [(0, "Tri-Cities Blackhawks"), (1951, "Milwaukee Hawks"),
                      (1955, "St. Louis Hawks"), (1968, "Atlanta Hawks")],
    "Detroit Pistons": [(0, "Fort Wayne Pistons"), (1957, "Detroit Pistons")],
    "Sacramento Kings": [(0, "Rochester Royals"), (1957, "Cincinnati Royals"),
                         (1972, "Kansas City-Omaha Kings"), (1975, "Kansas City Kings"),
                         (1985, "Sacramento Kings")],
    "Los Angeles Lakers": [(0, "Minneapolis Lakers"), (1960, "Los Angeles Lakers")],
    "Golden State Warriors": [(0, "Philadelphia Warriors"), (1962, "San Francisco Warriors"),
                              (1971, "Golden State Warriors")],
    "Washington Wizards": [(0, "Chicago Packers"), (1962, "Chicago Zephyrs"),
                           (1963, "Baltimore Bullets"), (1973, "Capital Bullets"),
                           (1974, "Washington Bullets"), (1997, "Washington Wizards")],
    "Philadelphia 76ers": [(0, "Syracuse Nationals"), (1963, "Philadelphia 76ers")],
    "Houston Rockets": [(0, "San Diego Rockets"), (1971, "Houston Rockets")],
    "LA Clippers": [(0, "Buffalo Braves"), (1978, "San Diego Clippers"),
                    (1984, "LA Clippers")],
    "Brooklyn Nets": [(0, "New York Nets"), (1977, "New Jersey Nets"),
                      (2012, "Brooklyn Nets")],
    "Utah Jazz": [(0, "New Orleans Jazz"), (1979, "Utah Jazz")],
    "Memphis Grizzlies": [(0, "Vancouver Grizzlies"), (2001, "Memphis Grizzlies")],
    "Oklahoma City Thunder": [(0, "Seattle SuperSonics"), (2008, "Oklahoma City Thunder")],
    # This lineage's history belongs to the Pelicans.
    "New Orleans Pelicans": [(0, "New Orleans Hornets"), (2013, "New Orleans Pelicans")],
    # Current Charlotte franchise: original Hornets (1988-2002) display as
    # "Charlotte Hornets", Bobcats era (2004-2014) as "Charlotte Bobcats",
    # reclaimed Hornets (2014-present) as "Charlotte Hornets".
    "Charlotte Hornets": [(0, "Charlotte Hornets"), (2004, "Charlotte Bobcats"),
                          (2014, "Charlotte Hornets")],
}


def era_name(current: str, years: str) -> str:
    table = ERA_TABLE.get(current)
    if not table:
        return current
    m = re.search(r"\d{4}", str(years or ""))
    if not m:
        return current  # no parseable year -> leave as the current name
    start = int(m.group())
    name = table[0][1]
    for boundary, era in table:
        if start >= boundary:
            name = era
        else:
            break
    return name


def _apply(players: list) -> int:
    changed = 0
    examples = []
    for p in players:
        for s in p.get("career_history", []):
            new = era_name(s.get("team", ""), s.get("years", ""))
            if new != s.get("team"):
                if len(examples) < 20:
                    examples.append((p["player"], s.get("team"), s.get("years"), new))
                s["team"] = new
                changed += 1
    _apply.examples = examples
    return changed


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    n1 = _apply(careers)
    ex = _apply.examples
    # keep current_team consistent with the era-corrected stints (careers.json
    # only; the READY map file has no current_team field). Does not change
    # status: recency is unchanged and active players' current stint is a
    # current-era name that was not renamed.
    ct_fixed = 0
    for p in careers:
        new_ct = _select_current_team(p.get("career_history", []))
        if new_ct and new_ct != p.get("current_team"):
            p["current_team"] = new_ct
            ct_fixed += 1
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ready = json.loads(READY.read_text(encoding="utf-8"))
    n2 = _apply(ready)
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints corrected: {n1}")
    print(f"READY.json  stints corrected: {n2}")
    print(f"current_team fields realigned: {ct_fixed}")
    print("examples (player | old -> new | years):")
    for player, old, yrs, new in ex:
        print(f"  {player}: {old!r} -> {new!r} ({yrs})")


if __name__ == "__main__":
    main()
