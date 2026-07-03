"""One-time import of the existing career database into the /data structure.

Reads the existing ``nba_players_careers_READY.json`` (~5k players, each stint
carrying team/city/state/country), then:

  * normalizes every team name via team_aliases.json
  * builds data/players/nba_players_careers.json (canonical database)
  * derives data/teams/team_locations.json (canonical team -> location)
  * flags teams with missing city/country in teams_needing_review.json
  * assigns each player a tracking status (nba_active / overseas_active /
    retired) and writes active_players.json (split by NBA vs overseas) and
    retired_players.json
  * keeps the root nba_players_careers_READY.json in sync so index.html keeps
    working unchanged

The seed classifies with a lenient retirement gap (SEED_RETIRE_GAP) so that
borderline players still land in the overseas re-check queue instead of being
stranded as ``retired`` and never revisited; live runs then apply the strict
2-year rule against fresh Wikipedia data.

Run:  python3 scripts/seed_import.py
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from team_normalizer import TeamNormalizer
from player_status import (classify_status, is_nba_team, NBA_ACTIVE,
                           OVERSEAS_ACTIVE, RETIRED)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "nba_players_careers_READY.json"
DATA = ROOT / "data"
PLAYERS_DIR = DATA / "players"
TEAMS_DIR = DATA / "teams"

# Lenient gap for the initial seed (see module docstring). Live runs use 2.
SEED_RETIRE_GAP = 3


def main() -> None:
    current_year = dt.datetime.now(dt.timezone.utc).year
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
        # the source "status" is a parse outcome; keep it under parse_status so
        # it does not collide with the tracking status assigned below.
        np["parse_status"] = np.pop("status", "success")
        np["career_history"] = new_history
        # no "present" markers in the seed data, so the most recent stint's team
        # is the best available "current team".
        np["current_team"] = new_history[-1]["team"] if new_history else ""
        np["status"] = classify_status(np, on_nba_roster=False,
                                       current_year=current_year,
                                       retire_gap=SEED_RETIRE_GAP)
        normalized_players.append(np)

    # locations file
    locations_out = {
        team: {
            "team": team,
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "country": loc.get("country", ""),
            "league": "NBA" if (loc.get("country") == "USA" and is_nba_team(team)) else "",
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

    # tracking-status split
    nba_active = sorted(p["player"] for p in normalized_players
                        if p["status"] == NBA_ACTIVE)
    overseas = sorted(p["player"] for p in normalized_players
                      if p["status"] == OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in normalized_players
                     if p["status"] == RETIRED)

    PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    TEAMS_DIR.mkdir(parents=True, exist_ok=True)

    _write(PLAYERS_DIR / "nba_players_careers.json", normalized_players)
    _write(PLAYERS_DIR / "active_players.json",
           {"count": len(nba_active) + len(overseas),
            "nba_active": nba_active, "overseas_active": overseas})
    _write(PLAYERS_DIR / "retired_players.json",
           {"count": len(retired), "players": retired})
    _write(TEAMS_DIR / "team_locations.json", locations_out)
    _write(TEAMS_DIR / "teams_needing_review.json", review)

    # keep the map's data file in sync (same normalized content)
    _write(ROOT / "nba_players_careers_READY.json", normalized_players)

    print(f"players            : {len(normalized_players)}")
    print(f"nba_active         : {len(nba_active)}")
    print(f"overseas_active    : {len(overseas)}")
    print(f"retired            : {len(retired)}")
    print(f"unique teams       : {len(locations_out)}")
    print(f"teams need review  : {len(review)}")
    print(f"location conflicts : {len(location_conflicts)} "
          f"(logged, first value kept)")


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


if __name__ == "__main__":
    main()
