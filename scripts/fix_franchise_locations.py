"""Fix stale CURRENT-franchise cities in the location lookup + stored stints.

Bug: team_locations.json still mapped some current NBA franchise names to their
PRE-relocation city (Utah Jazz -> New Orleans, Memphis Grizzlies -> Vancouver,
Brooklyn Nets -> East Rutherford). The earlier stored-data sync fixed old stints
but not the LOOKUP table, so freshly-fetched stints got poisoned locations.

This:
  1. Sets every current NBA franchise name in team_locations.json to its CURRENT
     city (historical era names keep their historical cities — untouched).
  2. Adds the "San Diego Clippers" entry (San Diego, CA, USA) so the modern G
     League club of the same name enriches correctly (same city as the NBA era).
  3. Scans stored stints (careers + READY) for any current-franchise stint whose
     city doesn't match the franchise's current city and corrects it.

Idempotent. Run:  python3 scripts/fix_franchise_locations.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rosters import NBA_TEAMS  # noqa: E402
from sync_era_locations import LOC  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"

# Current city for each of the 30 current franchise names. LOC (era-accurate)
# already carries the correct CURRENT city for the relocated franchises; the
# never-relocated ones are read from the existing (correct) team_locations.
_tl = json.loads(LOCATIONS.read_text(encoding="utf-8"))
CURRENT_LOC: dict[str, tuple[str, str, str]] = {}
for fr in NBA_TEAMS:
    if fr in LOC:
        CURRENT_LOC[fr] = LOC[fr]
    else:
        e = _tl.get(fr, {})
        CURRENT_LOC[fr] = (e.get("city", ""), e.get("state", ""), e.get("country", ""))

# The G-League / NBA-era shared name maps to San Diego for both.
EXTRA = {"San Diego Clippers": ("San Diego", "California", "USA")}


def _fix_stints(players: list) -> tuple[int, list]:
    n, ex = 0, []
    for p in players:
        for s in p.get("career_history", []):
            loc = CURRENT_LOC.get(s.get("team", ""))
            if not loc:
                continue
            if (s.get("city"), s.get("state"), s.get("country")) != loc:
                if len(ex) < 20:
                    ex.append((p["player"], s.get("team"), s.get("years"),
                               (s.get("city"), s.get("country")), loc, p.get("last_updated")))
                s["city"], s["state"], s["country"] = loc
                n += 1
    return n, ex


def main() -> None:
    # 1 + 2: fix the lookup table
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    updated = []
    for team, (city, state, country) in {**CURRENT_LOC, **EXTRA}.items():
        cur = locations.get(team, {})
        entry = {"team": team, "city": city, "state": state, "country": country,
                 "league": cur.get("league", "")}
        if cur != entry:
            locations[team] = entry
            updated.append(team)
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 3: fix stored stints
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    n1, ex = _fix_stints(careers)
    n2, _ = _fix_stints(ready)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"team_locations.json entries corrected: {len(updated)} -> {sorted(updated)}")
    print(f"careers.json stints re-located: {n1}")
    print(f"READY.json  stints re-located: {n2}")
    for player, team, yrs, was, now, lu in ex:
        print(f"  {player} | {team} ({yrs}) {was} -> {now}  [last_updated {lu}]")


if __name__ == "__main__":
    main()
