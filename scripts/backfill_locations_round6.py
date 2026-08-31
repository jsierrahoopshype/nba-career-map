"""Sixth location pass: three locations, and one club merged out of three names.

Same idempotent mechanism as rounds 2-5: reuses backfill_locations' _fill /
_located helpers for the locations, and the existing team_aliases.json +
apply_aliases machinery for the merge, so a re-fetch cannot undo either.

LOCATIONS. Three clubs whose home city is unambiguous. "Reims CAUFA" had been
recorded in "Champagne" with no country -- the region, not the city, and not
enough to plot -- so it is corrected rather than trusted.

THE MERGE. "Real Betis", "Caja San Fernando" and "Cajasol" are one Seville club
under successive sponsor names. Canonical name is chosen BY USAGE, as in prior
merge rounds: Caja San Fernando carries 19 stints against Real Betis's 12 and
Cajasol's 1, so it wins. (Worth a human's eye: usage picks a sponsor name over
the club's own enduring one. The rule was applied as specified rather than
overridden here.)

FOUR MORE VARIANTS OF THE SAME CLUB were found and deliberately NOT merged,
because they were not in the request: "Cajasol Sevilla" (3 stints), "Cajasol
Banca Cívica" (1), "Coosur Real Betis" (1) and "Real Betis Energía Plus" (1).
Folding those in as well would take the club to 36 alumni across 38 stints.

Run:  python3 scripts/backfill_locations_round6.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import (  # noqa: E402
    CAREERS, READY, LOCATIONS, REVIEW, _fill,
)

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"

# team -> (city, state, country)
LOC_FIX: dict[str, tuple[str, str, str]] = {
    "Zunder Palencia": ("Palencia", "", "Spain"),
    "Reims CAUFA": ("Reims", "", "France"),          # was: "Champagne", the region
    "North Gold Coast Seahawks": ("Gold Coast", "Queensland", "Australia"),
    # Not in the request, but the Slack reword's own worked example depends on
    # it: "Gifu Swoops of Japan" needs a country, and this entry had a city and
    # an empty country -- the round-5 partial-entry class, of which 86 remain.
    "Gifu Swoops": ("Gifu", "", "Japan"),
}

# Same club, successive sponsor names -> canonical (most-used) name.
MERGE_CANONICAL = "Caja San Fernando"
MERGE_ALIASES = ["Real Betis", "Cajasol"]


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    import backfill_locations as bl
    bl.LOC_FIX = LOC_FIX
    n1 = _fill(careers)
    n2 = _fill(ready)

    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    loc_updated = 0
    for team, (city, state, country) in LOC_FIX.items():
        cur = locations.get(team, {})
        entry = {"team": team, "city": city, "state": state, "country": country,
                 "league": cur.get("league", "")}
        if cur != entry:
            locations[team] = entry
            loc_updated += 1

    # --- the merge: register the aliases, then let apply_aliases do the rename
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))
    added = 0
    for name in MERGE_ALIASES:
        if alias_doc["aliases"].get(name) != MERGE_CANONICAL:
            alias_doc["aliases"][name] = MERGE_CANONICAL
            added += 1
    alias_doc["aliases"] = dict(sorted(alias_doc["aliases"].items()))
    ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    # Imported AFTER the alias file is written: apply_aliases reads the table at
    # import time, so the new entries have to be on disk first.
    import apply_aliases  # noqa: E402
    tally: dict = {}
    renamed = apply_aliases._apply(careers, tally) + apply_aliases._apply(ready, {})

    # The merged-away names leave the club index; the canonical name keeps the
    # location (it already carries Seville, Spain).
    for name in MERGE_ALIASES:
        locations.pop(name, None)

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    removed = 0
    for team in list(LOC_FIX) + MERGE_ALIASES:
        if team in review:
            del review[team]
            removed += 1

    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    alumni = {p["player"] for p in careers
              for s in p.get("career_history", []) if s.get("team") == MERGE_CANONICAL}
    stints = sum(1 for p in careers
                 for s in p.get("career_history", []) if s.get("team") == MERGE_CANONICAL)

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"aliases added: {added}")
    print(f"stints renamed by the merge: {renamed}")
    for k, v in sorted(tally.items()):
        print(f"    x{v}  {k}")
    print(f"teams removed from review: {removed}")
    print(f"merged club {MERGE_CANONICAL!r}: {len(alumni)} alumni across {stints} stints")


if __name__ == "__main__":
    main()
