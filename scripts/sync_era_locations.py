"""Sync each stint's city/state/country to match its (era-accurate) team name.

After the era-name correction, a stint's location must reflect the team it names
(e.g. a "New Orleans Jazz" stint must say New Orleans, not Salt Lake City). This
overwrites city/state/country for the franchise-era teams below, only where the
stored value differs. Idempotent; only the listed teams are touched.

Run:  python3 scripts/sync_era_locations.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"

# team name -> (city, state, country)
LOC: dict[str, tuple[str, str, str]] = {
    "New Orleans Jazz": ("New Orleans", "Louisiana", "USA"),
    "Utah Jazz": ("Salt Lake City", "Utah", "USA"),
    "Seattle SuperSonics": ("Seattle", "Washington", "USA"),
    "Oklahoma City Thunder": ("Oklahoma City", "Oklahoma", "USA"),
    "New York Nets": ("New York", "New York", "USA"),
    "New Jersey Nets": ("East Rutherford", "New Jersey", "USA"),
    "Brooklyn Nets": ("Brooklyn", "New York", "USA"),
    "Vancouver Grizzlies": ("Vancouver", "British Columbia", "Canada"),
    "Memphis Grizzlies": ("Memphis", "Tennessee", "USA"),
    "Charlotte Hornets": ("Charlotte", "North Carolina", "USA"),
    "New Orleans Hornets": ("New Orleans", "Louisiana", "USA"),
    "New Orleans Pelicans": ("New Orleans", "Louisiana", "USA"),
    "Charlotte Bobcats": ("Charlotte", "North Carolina", "USA"),
    "Tri-Cities Blackhawks": ("Moline", "Illinois", "USA"),
    "Milwaukee Hawks": ("Milwaukee", "Wisconsin", "USA"),
    "St. Louis Hawks": ("St. Louis", "Missouri", "USA"),
    "Atlanta Hawks": ("Atlanta", "Georgia", "USA"),
    "Fort Wayne Pistons": ("Fort Wayne", "Indiana", "USA"),
    "Detroit Pistons": ("Detroit", "Michigan", "USA"),
    "Rochester Royals": ("Rochester", "New York", "USA"),
    "Cincinnati Royals": ("Cincinnati", "Ohio", "USA"),
    "Kansas City-Omaha Kings": ("Kansas City", "Missouri", "USA"),
    "Kansas City Kings": ("Kansas City", "Missouri", "USA"),
    "Sacramento Kings": ("Sacramento", "California", "USA"),
    "Minneapolis Lakers": ("Minneapolis", "Minnesota", "USA"),
    "Los Angeles Lakers": ("Los Angeles", "California", "USA"),
    "Philadelphia Warriors": ("Philadelphia", "Pennsylvania", "USA"),
    "San Francisco Warriors": ("San Francisco", "California", "USA"),
    "Golden State Warriors": ("San Francisco", "California", "USA"),
    "Chicago Packers": ("Chicago", "Illinois", "USA"),
    "Chicago Zephyrs": ("Chicago", "Illinois", "USA"),
    "Baltimore Bullets": ("Baltimore", "Maryland", "USA"),
    "Capital Bullets": ("Landover", "Maryland", "USA"),
    "Washington Bullets": ("Washington", "D.C.", "USA"),
    "Washington Wizards": ("Washington", "D.C.", "USA"),
    "Syracuse Nationals": ("Syracuse", "New York", "USA"),
    "Philadelphia 76ers": ("Philadelphia", "Pennsylvania", "USA"),
    "San Diego Rockets": ("San Diego", "California", "USA"),
    "Houston Rockets": ("Houston", "Texas", "USA"),
    "Buffalo Braves": ("Buffalo", "New York", "USA"),
    "San Diego Clippers": ("San Diego", "California", "USA"),
    "LA Clippers": ("Los Angeles", "California", "USA"),
}


def _sync(players: list) -> int:
    changed = 0
    for p in players:
        for s in p.get("career_history", []):
            loc = LOC.get(s.get("team"))
            if not loc:
                continue
            city, state, country = loc
            if (s.get("city"), s.get("state"), s.get("country")) != (city, state, country):
                s["city"], s["state"], s["country"] = city, state, country
                changed += 1
    return changed


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    n1 = _sync(careers)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ready = json.loads(READY.read_text(encoding="utf-8"))
    n2 = _sync(ready)
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints re-located: {n1}")
    print(f"READY.json  stints re-located: {n2}")


if __name__ == "__main__":
    main()
