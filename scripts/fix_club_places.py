"""Correct club locations that came in wrong with the seed, one at a time.

Every entry here was checked by hand against the club it names. None of them
came from our own alias or merge steps -- all are present verbatim in the seed
import (7a38c4e), which is why they are corrected by an explicit table rather
than by re-running location discovery over the whole club list. A blanket
re-lookup would churn 3,500 clubs to fix twenty and could reintroduce the very
failure that caused these (see _discover_location's guard).

The table is the record of what was decided and why. Applying it writes the
location AND every stint that carries it, because the two are stored
separately and a stint is what actually plots on the map.

Run:  python3 scripts/fix_club_places.py          # show what would change
      python3 scripts/fix_club_places.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"

# club -> (city, state, country), with the wrong value it replaces.
PLACES = {
    # The article the seed read was Libertas the Irish political party, whose
    # infobox gives its registered office. The club is in Forlì.
    "Libertas Forlì": ("Forlì", "Emilia-Romagna", "Italy"),           # was Registered at Moyne Park / Ireland

    # Huesca is in Aragon. "Newfoundland and Labrador, Canada" is not a
    # plausible misreading of anything about this club.
    "Peñas Huesca": ("Huesca", "Aragon", "Spain"),                    # was Newfoundland and Labrador / Canada

    # "Emilio Frugoni 924" is the club's street address in Montevideo.
    "Club Atlético Welcome": ("Montevideo", "", "Uruguay"),           # was Emilio Frugoni 924 / Uruguay

    # Not Chile. Felipe Lopez's only stint here sits among La Vega, Santo
    # Domingo and Puerto Plata, and the city's own name is Santiago de los
    # Caballeros -- which is what the club is named after.
    "Caballeros de Santiago": ("Santiago de los Caballeros", "",
                               "Dominican Republic"),                 # was Spain / Chile

    "Lazio Basket": ("Rome", "Lazio", "Italy"),                       # was America and the Caribbean / USA

    # Atenas is the Córdoba in Argentina, not the one in Veracruz. The same
    # club is already correct under "Atenas de Córdoba".
    "Atenas Cordoba": ("Córdoba", "", "Argentina"),                   # was Veracruz / Mexico

    # Aguada is a Montevideo barrio; "Club Atlético Aguada" already says so.
    "Atlético Aguada": ("Montevideo", "", "Uruguay"),                 # was Gualala / Honduras

    "Canturina Cantù": ("Cantù", "Lombardy", "Italy"),                # was Turin / Italy
    "Banca Nuova Trapani": ("Trapani", "Sicily", "Italy"),            # was Palermo / Italy

    # The Fly Dragons play in Chongqing; Beijing is where the franchise came
    # from before the move, and is where both spellings were pointed.
    "Chongqing Fly Dragons": ("Chongqing", "", "China"),              # was Beijing / China
    "Chongqing Flying Dragons": ("Chongqing", "", "China"),           # was Beijing / China

    # Mitteldeutscher BC is in Weißenfels; the bare name was given Leipzig.
    "Mitteldeutscher": ("Weißenfels", "Saxony-Anhalt", "Germany"),    # was Leipzig / Germany

    # Budućnost is Podgorica, Montenegro. No stints today, corrected so the
    # next one to arrive plots somewhere real.
    "Buducnost Voli": ("Podgorica", "", "Montenegro"),                # was Valjevo / Serbia

    # --- the country field holding a US state ------------------------------
    # Six clubs had country "Georgia" meaning the state. Six others have
    # country "Georgia" meaning the country (Dinamo Tbilisi, BC Armia, VITA
    # Tbilisi, BC Rustavi, BC Sokhumi, Kolkha) and are left exactly as they
    # are: that ambiguity is presumably how this got through in the first
    # place. Only the country and state move here; no city is touched.
    "College Park Skyhawks": ("College Park", "Georgia", "USA"),
    "Detroit Spirits": ("Savannah", "Georgia", "USA"),
    "Savannah Spirits": ("Savannah", "Georgia", "USA"),
    "Atlanta Trojans": ("Atlanta", "Georgia", "USA"),
    "Atlanta Crackers": ("Atlanta", "Georgia", "USA"),
    "Marietta Storm": ("Marietta", "Georgia", "USA"),
}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    loc = _load(LOCATIONS)
    dbs = [(CAREERS, _load(CAREERS))]
    if READY.exists():
        dbs.append((READY, _load(READY)))

    moved = 0
    missing = []
    for club, (city, state, country) in PLACES.items():
        was = loc.get(club)
        if was is None:
            missing.append(club)
            continue
        n = 0          # rows in the career database, which is what plots
        for path, db in dbs:
            for p in db:
                for s in p.get("career_history") or []:
                    if (s.get("team") or "").strip() != club:
                        continue
                    if (s.get("city"), s.get("state"), s.get("country")) == (city, state, country):
                        continue
                    s["city"], s["state"], s["country"] = city, state, country
                    if path == CAREERS:
                        n += 1
        print(f"{club!r}")
        print(f"    was {was.get('city')!r} / {was.get('state')!r} / {was.get('country')!r}")
        print(f"    now {city!r} / {state!r} / {country!r}     {n} stint(s)")
        loc[club] = {"team": club, "city": city, "state": state,
                     "country": country, "league": was.get("league", "")}
        moved += n

    if missing:
        print(f"\nnot in the location table (nothing done): {missing}")
    print(f"\n{len(PLACES) - len(missing)} club(s), {moved} stint(s) "
          f"{'moved' if args.apply else 'to move'} (the map file is kept in step)")
    if args.apply:
        _write(LOCATIONS, dict(sorted(loc.items())))
        for path, db in dbs:
            _write(path, db)
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
