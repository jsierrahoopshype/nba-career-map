"""Correct two franchise-era location records the daily pass invented.

`assign_location` writes `self.locations[team]` from `_discover_location`
whenever the location table has no usable entry for a name it sees. For a
franchise-era name that is a trap: Wikipedia answers "Chicago Packers" with
the article for the franchise as it exists today, the Washington Wizards, and
the discovery heuristic reads "based in Washington, D.C." off it. The result
is a location record for a 1961 Chicago team that says Washington, and one
stint plotted 700 miles from where it was played.

The franchise-era teams already have an authority for where they played:
sync_era_locations.LOC. This does not invent a second one -- it names the two
records to correct and asserts them against that table, so the two can never
drift apart.

Every record here disagreed with LOC, and the stints carrying them are
corrected with them. Five of the eight give a location to a stint that had
none or had the wrong city, so a route gains or moves a stop -- which moves
players through the quiz pool and can invalidate rendered clips. That is why
each entry is written out with what it said and why, and why the pool is
diffed before any of this is merged.

Idempotent. Run:  python3 scripts/fix_shadow_locations.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_era_locations import LOC  # noqa: E402  the authority for these places

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"

# team -> what the shadow record said, for the record of what was wrong.
FIXING = {
    "Chicago Packers": "Washington, D.C — the CURRENT home of the franchise "
                       "this 1961 team became, read off the Wizards' article",
    "Washington Bullets": "Washington, D.C — the city and country right, the "
                          "state written 'D.C' where every other stint says 'D.C.'",

    # The same failure, six more times. Each is a franchise-era name whose
    # record came from somewhere other than the era table: either the
    # franchise's later home, or a discovery that stopped before the country.
    "Kansas City Kings": "Sacramento, California — where the franchise plays "
                         "NOW, forty years after it left Kansas City",
    "New York Nets": "Uniondale, New York — one of the several arenas the "
                     "team used; the era table names the city, not the arena",
    "New Jersey Nets": "nothing at all — an empty record, so every stint "
                       "carrying it had no place and never reached the map",
    "Philadelphia Warriors": "nothing at all — the same empty record",
    "Seattle SuperSonics": "Seattle with no state and no country, which is "
                           "not enough to resolve to coordinates",
    "Vancouver Grizzlies": "Vancouver with no state and no country — and two "
                           "countries have a Vancouver",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    missing = [t for t in FIXING if t not in LOC]
    if missing:
        print(f"not in sync_era_locations.LOC, refusing to guess: {missing}")
        return 1

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    dbs = [(CAREERS, json.loads(CAREERS.read_text(encoding="utf-8")))]
    if READY.exists():
        dbs.append((READY, json.loads(READY.read_text(encoding="utf-8"))))

    moved = 0
    for team, was in FIXING.items():
        city, state, country = LOC[team]
        entry = loc.get(team) or {}
        print(f"{team!r}")
        # The note says what the record originally held. Once applied the
        # record reads correctly, so it is printed separately from the note
        # rather than as "was", which would contradict itself on a re-run.
        print(f"    originally  {was}")
        print(f"    record      {entry.get('city')!r}, {entry.get('state')!r}, "
              f"{entry.get('country')!r}")
        print(f"    era table   {city!r}, {state!r}, {country!r}")
        for path, db in dbs:
            for p in db:
                for s in p.get("career_history") or []:
                    if (s.get("team") or "").strip() != team:
                        continue
                    if (s.get("city"), s.get("state"), s.get("country")) == (city, state, country):
                        continue
                    if path == CAREERS:
                        print(f"    stint  {p['player']} {s.get('years')}: "
                              f"{s.get('city')!r},{s.get('state')!r},{s.get('country')!r}"
                              f" -> {city!r},{state!r},{country!r}")
                        moved += 1
                    s["city"], s["state"], s["country"] = city, state, country
        loc[team] = {"team": team, "city": city, "state": state, "country": country,
                     "league": entry.get("league", "")}

    print(f"\n{moved} stint(s) {'corrected' if args.apply else 'to correct'}")

    # Everything else of the same shape, reported and not touched.
    rest = []
    for team, want in sorted(LOC.items()):
        if team in FIXING:
            continue
        entry = loc.get(team)
        if entry is None:
            continue                      # no record at all is not a wrong record
        have = (entry.get("city"), entry.get("state"), entry.get("country"))
        if have != want:
            rest.append((team, have, want))
    if rest:
        print(f"\nleft alone, {len(rest)} more record(s) disagreeing with the era table:")
        for team, have, want in rest:
            print(f"    {team:24} {have} -> {want}")

    if args.apply:
        LOCATIONS.write_text(json.dumps(dict(sorted(loc.items())), ensure_ascii=False,
                                        indent=2) + "\n", encoding="utf-8")
        for path, db in dbs:
            path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
