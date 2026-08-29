"""Clean HTML-comment fragments out of stored team names and merge them into
their canonical teams.

THE BUG (fixed in wiki_parser.strip_html_comments): editors comment out blocks
of infobox parameters, and parse_infobox_fields split the infobox on "|" before
comments were removed. The "|" characters inside the comment became field
separators, so the opening marker landed on one field's value and the closing
marker on another:

    |team10  = [[Detroit Pistons]]<!--
    |years11 = 2025
    |team11  = [[Motor City Cruise]]-->

  ->  team "Detroit Pistons <!--"  and  team "Motor City Cruise-->"

_clean_text's generic <[^>]+> tag strip could not catch either half ("<!--" has
no closing ">", "-->" has no opening "<"), so both reached storage. The parser
fix stops new ones; this script cleans the records already on disk.

Every polluted name is a duplicate of a team that already exists with a real
location, so cleaning is a rename-and-merge: strip the markers, collapse the
whitespace, then fold the stint into the canonical team. If the player already
holds an identical (team, years) stint the duplicate is dropped rather than
doubled.

Idempotent: a second run finds nothing to do.

Run:  python3 scripts/clean_comment_fragments.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

_MARKERS = re.compile(r"<!--|-->")


def canonical(name: str) -> str:
    """The team name with comment markers removed and whitespace collapsed."""
    return re.sub(r"\s+", " ", _MARKERS.sub(" ", str(name or ""))).strip()


def is_polluted(name: str) -> bool:
    return bool(_MARKERS.search(str(name or "")))


def clean_players(players: list) -> tuple[int, int, dict]:
    """Rename polluted stints to their canonical team, dropping any that become
    an exact duplicate. Returns (renamed, dropped, name -> canonical)."""
    renamed = dropped = 0
    mapping: dict[str, str] = {}
    for p in players:
        hist = p.get("career_history") or []
        if not any(is_polluted(s.get("team")) or is_polluted(s.get("team_raw"))
                   for s in hist):
            continue
        seen: set[tuple[str, str]] = set()
        out = []
        for s in hist:
            team = s.get("team", "")
            if is_polluted(team):
                good = canonical(team)
                mapping[team] = good
                s["team"] = good
                renamed += 1
            # team_raw keeps the pre-normalisation name. The parser now strips
            # comments before the field is ever read, so a re-fetch would write
            # a clean value here too -- match that rather than leaving markers
            # sitting in the record.
            if is_polluted(s.get("team_raw", "")):
                s["team_raw"] = canonical(s["team_raw"])
            key = (s.get("team", ""), str(s.get("years", "")))
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            out.append(s)
        p["career_history"] = out
    return renamed, dropped, mapping


def clean_table(path: Path) -> int:
    """Drop polluted keys from a team-keyed table. The canonical entry already
    exists in every case, and it carries the real location -- the polluted one
    is an empty shell -- so the polluted key is removed, never merged over the
    good one."""
    table = json.loads(path.read_text(encoding="utf-8"))
    removed = 0
    for key in [k for k in table if is_polluted(k)]:
        good = canonical(key)
        entry = table.pop(key)
        removed += 1
        if good not in table:
            # Nothing canonical to fall back on: keep the entry under the clean
            # name rather than losing it.
            entry["team"] = good
            table[good] = entry
    if removed:
        path.write_text(json.dumps(dict(sorted(table.items())),
                                   ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return removed


def _fill_locations(players: list, locations: dict, teams: set) -> int:
    """Give the newly-cleaned stints the canonical team's location. Scoped to
    the teams this merge actually touched -- filling anything else would be a
    location backfill wearing this script's name."""
    n = 0
    for p in players:
        for s in p.get("career_history") or []:
            if s.get("team", "") not in teams:
                continue
            if s.get("city") and s.get("country"):
                continue
            loc = locations.get(s.get("team", ""))
            if loc and loc.get("city") and loc.get("country"):
                s["city"] = loc.get("city", "")
                s["state"] = loc.get("state", "")
                s["country"] = loc.get("country", "")
                n += 1
    return n


def main() -> None:
    loc_removed = clean_table(LOCATIONS)
    rev_removed = clean_table(REVIEW)
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))

    totals = {}
    mapping: dict[str, str] = {}
    for path in (CAREERS, READY):
        players = json.loads(path.read_text(encoding="utf-8"))
        renamed, dropped, m = clean_players(players)
        mapping.update(m)
        located = _fill_locations(players, locations, set(m.values()))
        path.write_text(json.dumps(players, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        totals[path.name] = (renamed, dropped, located)

    for name, (renamed, dropped, located) in totals.items():
        print(f"{name}: renamed {renamed} stint(s), dropped {dropped} duplicate(s), "
              f"located {located}")
    print(f"team_locations.json: removed {loc_removed} polluted key(s)")
    print(f"teams_needing_review.json: removed {rev_removed} polluted key(s)")
    if mapping:
        print("merged:")
        for bad, good in sorted(mapping.items()):
            print(f"  {bad!r} -> {good!r}")


if __name__ == "__main__":
    main()
