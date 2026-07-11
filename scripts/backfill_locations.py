"""Backfill locations for the most-repeated location-less teams (idempotent).

~1,392 stints across ~1,089 teams have empty city AND country. This fills the
confidently-placeable head of that distribution (the LOC_FIX map below) on every
matching stint in the career data, mirrors the values into team_locations.json
so future fetches enrich correctly, and drops those teams from the review queue.

Every other team that still has a location-less stint (the long tail we cannot
confidently place) is added to teams_needing_review.json instead of guessed.

Run:  python3 scripts/backfill_locations.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
REVIEW = ROOT / "data" / "teams" / "teams_needing_review.json"

# team -> (city, state, country). International clubs have no state.
LOC_FIX: dict[str, tuple[str, str, str]] = {
    "Xinjiang Flying Tigers": ("Ürümqi", "", "China"),
    "Guangdong Southern Tigers": ("Dongguan", "", "China"),
    "Bilbao": ("Bilbao", "", "Spain"),
    "Varese": ("Varese", "", "Italy"),
    "UCAM Murcia": ("Murcia", "", "Spain"),
    "Cantù": ("Cantù", "", "Italy"),
    "Napoli": ("Naples", "", "Italy"),
    "Aquila Trento": ("Trento", "", "Italy"),
    "Beşiktaş Icrypex": ("Istanbul", "", "Turkey"),
    "Büyükçekmece": ("Istanbul", "", "Turkey"),
    "Borac Čačak": ("Čačak", "", "Serbia"),
    "Olitalia Forlì": ("Forlì", "", "Italy"),
    "Lazio": ("Rome", "", "Italy"),
    "Al Sadd": ("Doha", "", "Qatar"),
    "Omonia": ("Nicosia", "", "Cyprus"),
    "Los Barrios": ("Los Barrios", "", "Spain"),
    "Uberlândia": ("Uberlândia", "", "Brazil"),
    "COC Ribeirão Preto": ("Ribeirão Preto", "", "Brazil"),
    "Reggio Calabria": ("Reggio Calabria", "", "Italy"),
    "Bourg-en-Bresse": ("Bourg-en-Bresse", "", "France"),
    "Panthers Fürstenfeld": ("Fürstenfeld", "", "Austria"),
    "Prometey": ("Slobozhanske", "", "Ukraine"),
}


def _located(stint: dict) -> bool:
    return bool((stint.get("city") or "").strip() and (stint.get("country") or "").strip())


def _fill(players: list) -> int:
    changed = 0
    for p in players:
        for s in p.get("career_history", []):
            fix = LOC_FIX.get(s.get("team", ""))
            if not fix or _located(s):
                continue
            city, state, country = fix
            if (s.get("city"), s.get("state"), s.get("country")) != (city, state, country):
                s["city"], s["state"], s["country"] = city, state, country
                changed += 1
    return changed


def _missing_location_teams(players: list) -> set[str]:
    """Teams with at least one stint missing city AND country."""
    out = set()
    for p in players:
        for s in p.get("career_history", []):
            if not (s.get("city") or "").strip() and not (s.get("country") or "").strip():
                t = (s.get("team") or "").strip()
                if t:
                    out.add(t)
    return out


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    n1 = _fill(careers)
    n2 = _fill(ready)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # mirror into team_locations.json so future fetches enrich correctly
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    loc_updated = 0
    for team, (city, state, country) in LOC_FIX.items():
        cur = locations.get(team, {})
        entry = {"team": team, "city": city, "state": state, "country": country,
                 "league": cur.get("league", "")}
        if cur != entry:
            locations[team] = entry
            loc_updated += 1
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # review queue: fixed teams leave it; every remaining location-less team joins
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    removed = 0
    for team in LOC_FIX:
        if team in review:
            del review[team]
            removed += 1
    still_missing = _missing_location_teams(careers)
    added = 0
    for team in sorted(still_missing):
        if team not in review:
            review[team] = {"team": team, "reason": "missing city and/or country",
                            "city": "", "country": ""}
            added += 1
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"teams removed from review (now fixed): {removed}")
    print(f"teams added to review (long tail, unplaceable): {added}")
    print(f"teams still location-less total: {len(still_missing)}")


if __name__ == "__main__":
    main()
