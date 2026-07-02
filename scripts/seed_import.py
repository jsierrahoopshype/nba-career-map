"""One-time import of the existing career database into the /data structure.

Reads the existing ``nba_players_careers_READY.json`` (~5k players, each stint
carrying team/city/state/country), then:

  * normalizes every team name via team_aliases.json
  * builds data/players/nba_players_careers.json (canonical database)
  * derives data/teams/team_locations.json (canonical team -> location)
  * flags teams with missing city/country in teams_needing_review.json
  * splits players into active_players.json / retired_players.json using a
    last-season heuristic (refined later by the roster step)
  * keeps the root nba_players_careers_READY.json in sync so index.html keeps
    working unchanged

Run:  python3 scripts/seed_import.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from team_normalizer import TeamNormalizer

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "nba_players_careers_READY.json"
DATA = ROOT / "data"
PLAYERS_DIR = DATA / "players"
TEAMS_DIR = DATA / "teams"

# Players whose most recent stint ends in or after this year are treated as
# still-active candidates (NBA or overseas). Refined by the roster step.
ACTIVE_SINCE_YEAR = 2024


def last_year(career_history: list[dict]) -> int:
    latest = 0
    for stint in career_history:
        yrs = str(stint.get("years", ""))
        if "present" in yrs.lower():
            return 9999
        for y in re.findall(r"\d{4}", yrs):
            latest = max(latest, int(y))
        if re.search(r"[–\-]\s*$", yrs):  # open-ended range
            latest = max(latest, ACTIVE_SINCE_YEAR)
    return latest


def main() -> None:
    players = json.loads(SOURCE.read_text(encoding="utf-8"))
    tn = TeamNormalizer()

    # canonical team -> best-known location (first non-empty wins)
    locations: dict[str, dict] = {}
    location_conflicts: dict[str, set] = {}

    normalized_players = []
    for p in players:
        new_history = []
        for stint in p.get("career_history", []):
            raw_team = stint.get("team", "")
            canonical = tn.normalize(raw_team)
            city = (stint.get("city") or "").strip()
            state = (stint.get("state") or "").strip()
            country = (stint.get("country") or "").strip()

            entry = {"years": stint.get("years", ""), "team": canonical,
                     "city": city, "state": state, "country": country}
            new_history.append(entry)

            if canonical:
                loc = {"city": city, "state": state, "country": country}
                if canonical not in locations:
                    locations[canonical] = loc
                elif (city or country):
                    existing = locations[canonical]
                    if not (existing.get("city") or existing.get("country")):
                        locations[canonical] = loc
                    elif city and existing.get("city") and city != existing["city"]:
                        location_conflicts.setdefault(canonical, set()).add(
                            f"{existing.get('city')}|{city}")

        np = {k: v for k, v in p.items() if k != "career_history"}
        np["career_history"] = new_history
        normalized_players.append(np)

    # locations file
    locations_out = {
        team: {
            "team": team,
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "country": loc.get("country", ""),
            "league": "NBA" if (loc.get("country") == "USA" and _looks_nba(team)) else "",
        }
        for team, loc in sorted(locations.items())
    }

    # teams needing review: missing city or country
    review = {
        team: {
            "team": team,
            "reason": "missing city and/or country",
            "city": info["city"],
            "country": info["country"],
        }
        for team, info in locations_out.items()
        if not info["city"] or not info["country"]
    }

    # active / retired split
    active, retired = [], []
    for p in normalized_players:
        (active if last_year(p.get("career_history", [])) >= ACTIVE_SINCE_YEAR
         else retired).append(p["player"])

    PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)

    _write(PLAYERS_DIR / "nba_players_careers.json", normalized_players)
    _write(PLAYERS_DIR / "active_players.json",
           {"count": len(active), "players": sorted(active)})
    _write(PLAYERS_DIR / "retired_players.json",
           {"count": len(retired), "players": sorted(retired)})
    _write(TEAMS_DIR / "team_locations.json", locations_out)
    _write(TEAMS_DIR / "teams_needing_review.json", review)

    # keep the map's data file in sync (same normalized content)
    _write(ROOT / "nba_players_careers_READY.json", normalized_players)

    print(f"players            : {len(normalized_players)}")
    print(f"active (heuristic) : {len(active)}")
    print(f"retired (heuristic): {len(retired)}")
    print(f"unique teams       : {len(locations_out)}")
    print(f"teams need review  : {len(review)}")
    print(f"location conflicts : {len(location_conflicts)} "
          f"(logged, first value kept)")


def _looks_nba(team: str) -> bool:
    nba = {
        "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets",
        "Chicago Bulls", "Cleveland Cavaliers", "Dallas Mavericks",
        "Denver Nuggets", "Detroit Pistons", "Golden State Warriors",
        "Houston Rockets", "Indiana Pacers", "LA Clippers", "Los Angeles Lakers",
        "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
        "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
        "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers",
        "Phoenix Suns", "Portland Trail Blazers", "Sacramento Kings",
        "San Antonio Spurs", "Toronto Raptors", "Utah Jazz", "Washington Wizards",
    }
    return team in nba


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


if __name__ == "__main__":
    main()
