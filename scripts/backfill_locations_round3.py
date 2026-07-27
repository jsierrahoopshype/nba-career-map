"""Third location backfill pass, generated from a tiered review of
teams_needing_review.json (idempotent). Reuses backfill_locations.py's
_fill / _missing_location_teams helpers verbatim.

This is the remainder of the same tiered candidate set as
backfill_locations_round2.py -- same two HIGH-confidence rules (reuse of a
located stint for the exact team-name string, or an unambiguous city-name
match not preceded by an article/preposition and not slash-joined), same
manual real-world-knowledge exclusions. These 105 entries were held back at
round 2's ~150-change checkpoint; see logs/location_review_proposals.txt
for the full tiered list and reasoning.

Run:  python3 scripts/backfill_locations_round3.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import (  # noqa: E402
    CAREERS, READY, LOCATIONS, REVIEW, _fill, _missing_location_teams,
)
import json  # noqa: E402

LOC_FIX: dict[str, tuple[str, str, str]] = {
    'Memphis Rockers': ('Memphis', 'Tennessee', 'USA'),
    'Meridiano Alicante': ('Alicante', 'Valencian Community', 'Spain'),
    'Mestre 1958': ('Mestre', '', 'Italy'),
    'Montana Forlì': ('Forlì', '', 'Italy'),
    'Montegranaro': ('Montegranaro', '', 'Italy'),
    'Montpellier Basket': ('Montpellier', '', 'France'),
    'Montpellier Paillade': ('Montpellier', '', 'France'),
    'Montpellier Paillade Basket': ('Montpellier', '', 'France'),
    'Mornar Bar': ('Bar', '', 'Montenegro'),
    'Mumbai Titans': ('Mumbai', '', 'India'),
    'Muskegon Panthers': ('Muskegon', 'Michigan', 'USA'),
    'Nacional Montevideo': ('Montevideo', '', 'Uruguay'),
    'New Basket Caserta': ('Caserta', '', 'Italy'),
    'New Haven Elms': ('New Haven', 'Connecticut', 'USA'),
    'New Orleans Hornets': ('New Orleans', 'Louisiana', 'USA'),
    'North Gold Coast Seahawks': ('Gold Coast', 'Queensland', 'Australia'),
    'Novipiù Casale Monferrato': ('Casale Monferrato', '', 'Italy'),
    'Nuova Gorizia': ('Gorizia', '', 'Italy'),
    'Nymburk': ('Nymburk', '', 'Czech Republic'),
    "Oakland Blue n' Gold Atlas": ('Oakland', 'California', 'USA'),
    'Ockelbo Basket': ('Ockelbo', '', 'Sweden'),
    'Odesa': ('Odesa', '', 'Ukraine'),
    'Omonia Nicosia': ('Nicosia', '', 'Cyprus'),
    'Otto Caserta': ('Caserta', '', 'Italy'),
    'Overense Aerosoles Ovar': ('Ovar', '', 'Portugal'),
    'Pardubice': ('Pardubice', '', 'Czech Republic'),
    'Pavia': ('Pavia', '', 'Italy'),
    'Pescanova Ferrol': ('Ferrol', '', 'Spain'),
    'Pezoporikos Larnaca': ('Larnaca', '', 'Cyprus'),
    'Pittsburgh Corbetts': ('Pittsburgh', 'Pennsylvania', 'USA'),
    'Pittsburgh Pirates': ('Pittsburgh', 'Pennsylvania', 'USA'),
    'Pivovarna Laško': ('Laško', '', 'Slovenia'),
    'Prienai': ('Prienai', '', 'Lithuania'),
    'Proaguas Costablanca Alicante': ('Alicante', 'Valencian Community', 'Spain'),
    'Puleva Granada': ('Granada', '', 'Spain'),
    'RIG Luleå': ('Luleå', '', 'Sweden'),
    'Racing Mechelen': ('Mechelen', '', 'Belgium'),
    'Raleigh Bullfrogs': ('Raleigh', 'North Carolina', 'USA'),
    'Raleigh Knights': ('Raleigh', 'North Carolina', 'USA'),
    'Regatas San Nicolás': ('San Nicolás', '', 'Argentina'),
    'Reims CAUFA': ('Reims', '', 'France'),
    'Rimini Crabs': ('Rimini', '', 'Italy'),
    'Riyadi Beirut': ('Beirut', '', 'Lebanon'),
    'Roanne': ('Roanne', '', 'France'),
    'Roma SPQR': ('Roma', '', 'Italy'),
    'Rotterdam-Zuid': ('Rotterdam', '', 'Netherlands'),
    'Râmnicu Vâlcea': ('Râmnicu Vâlcea', '', 'Romania'),
    'S. Benedetto Gorizia': ('Gorizia', '', 'Italy'),
    'S. Bennedetto Gorizia': ('Gorizia', '', 'Italy'),
    'SSV Hagen': ('Hagen', '', 'Germany'),
    'Sacramento Prospectors': ('Sacramento', 'California', 'USA'),
    'Sagesse Club Beirut': ('Beirut', '', 'Lebanon'),
    'Sakarya': ('Sakarya', '', 'Turkey'),
    'Sakarya Isik Koleji': ('Sakarya', '', 'Turkey'),
    'San Diego Clippers': ('San Diego', 'California', 'USA'),
    'San Pedro de Macorís': ('San Pedro de Macorís', '', 'Dominican Republic'),
    'San Sebastián': ('San Sebastián', '', 'Spain'),
    'Satria Muda Bandung': ('Bandung', '', 'Indonesia'),
    'Scafati': ('Scafati', '', 'Italy'),
    'Schenectady Comets': ('Schenectady', 'New York', 'USA'),
    'Seattle Aviators': ('Seattle', 'Washington', 'USA'),
    'Seattle Super Hawks': ('Seattle', 'Washington', 'USA'),
    'Silverstone Brescia': ('Brescia', '', 'Italy'),
    'Slovan Bratislava': ('Bratislava', '', 'Slovakia'),
    'Sparta Prague': ('Prague', '', 'Czech Republic'),
    'Spotter Leuven': ('Leuven', '', 'Belgium'),
    'Standard Liège': ('Liège', '', 'Belgium'),
    'Starogard Gdański': ('Starogard Gdański', '', 'Poland'),
    'Sunbury Mercurys': ('Sunbury', 'Pennsylvania', 'USA'),
    'Supernova Montegranaro': ('Montegranaro', '', 'Italy'),
    'Surne Bilbao Basket': ('Bilbao', '', 'Spain'),
    'TSG Ehingen': ('Ehingen', '', 'Germany'),
    'TSV Quakenbrück': ('Quakenbrück', '', 'Germany'),
    'Tabiat Tehran': ('Tehran', '', 'Iran'),
    'Tampa Bay Sunblasters': ('Tampa', 'Florida', 'USA'),
    'Tampa Bay Windjammers': ('Tampa', 'Florida', 'USA'),
    'Tarragona': ('Tarragona', '', 'Spain'),
    'Telemarket Brescia': ('Brescia', '', 'Italy'),
    'Telemarket Forlì': ('Forlì', '', 'Italy'),
    'Teramo': ('Teramo', '', 'Italy'),
    'Tijuana Dragons': ('Tijuana', '', 'Mexico'),
    'Tizona Burgos': ('Burgos', '', 'Spain'),
    'Tortona': ('Tortona', '', 'Italy'),
    'Townsville Heat': ('Townsville', 'Queensland', 'Australia'),
    'Trento': ('Trento', '', 'Italy'),
    'United Byblos Amchit': ('Byblos', '', 'Lebanon'),
    'Universitatea Cluj-Napoca': ('Cluj-Napoca', '', 'Romania'),
    'Universitetas-Irvinga Klaipėda': ('Klaipėda', '', 'Lithuania'),
    'Verona': ('Verona', '', 'Italy'),
    'Vichy': ('Vichy', '', 'France'),
    'Waterloo Revolution': ('Waterloo', 'Iowa', 'USA'),
    'Waterloo Rockets': ('Waterloo', 'Iowa', 'USA'),
    'Willebroek': ('Willebroek', '', 'Belgium'),
    'Wilmington Wave Rockers': ('Wilmington', 'Delaware', 'USA'),
    'Wonju TG Xers': ('Wonju', '', 'South Korea'),
    'Wuhan Kunpeng': ('Wuhan', '', 'China'),
    'Xacobeo 99 Ourense': ('Ourense', '', 'Spain'),
    'Yenisey Krasnoyarsk': ('Krasnoyarsk', '', 'Russia'),
    'Youngstown Cubs': ('Youngstown', 'Ohio', 'USA'),
    'Zabok': ('Zabok', '', 'Croatia'),
    'Zara Imballaggi Fabriano': ('Fabriano', '', 'Italy'),
    'Élan Béarnais Pau-Lacq-Orthez': ('Pau', '', 'France'),
    'Élan Béarnais Pau-Orthez': ('Pau', '', 'France'),
    'Ādaži Rīga': ('Rīga', '', 'Latvia'),
    'Šiauliai–Casino Admiral': ('Šiauliai', '', 'Lithuania'),
}


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    import backfill_locations as bl
    bl.LOC_FIX = LOC_FIX

    n1 = _fill(careers)
    n2 = _fill(ready)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    removed = 0
    for team in LOC_FIX:
        if team in review:
            del review[team]
            removed += 1
    still_missing = _missing_location_teams(careers)
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"teams removed from review (now fixed): {removed}")
    print(f"teams still location-less total: {len(still_missing)}")


if __name__ == "__main__":
    main()
