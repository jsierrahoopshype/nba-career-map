"""Fold four near-duplicate club records into the record that keeps the name.

Each pair below was looked at by hand, stint by stint and year by year, and
confirmed to be one club written two ways -- not two clubs that happen to
share a name. The direction of each merge was chosen deliberately and is what
the table records; it is not always the record with more stints (CB Peñas
Huesca carries one stint against Peñas Huesca's three, and is still the club's
actual name).

Pairs that looked like this and turned out NOT to be one club are pinned in
team_normalizer.KNOWN_DISTINCT instead, so nothing later fuses them. Libertas
Forlì / Fulgor Libertas Forlì is the open case -- see docs/unresolved-clubs.md.

The canonical record keeps its own location, taking any field the variant
filled in and it left empty (state, mostly). The variant's location entry is
dropped and an alias written, so the name still resolves.

Idempotent. Run:  python3 scripts/merge_club_pairs.py [--apply]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"

# variant -> (canonical, why)
MERGES = {
    "Atenas Cordoba": (
        "Atenas de Córdoba",
        "same club, the accent dropped; both already sit in Córdoba, Argentina",
    ),
    "Atlético Aguada": (
        "Club Atlético Aguada",
        "the club's short name; both already sit in Montevideo, Uruguay",
    ),
    "Peñas Huesca": (
        "CB Peñas Huesca",
        "CB Peñas Huesca is the club's name; both already sit in Huesca, Spain",
    ),
    "Lazio Basket": (
        "Lazio",
        "the basketball section of S.S. Lazio; both already sit in Rome, Italy",
    ),
}

# Clubs whose country field was left empty by the seed. City and state are
# right; only the missing country is filled.
COUNTRY_FILL = {
    "Washington Bullets": "USA",
    "Chicago Packers": "USA",
}


def merge_place(keep: dict, drop: dict) -> dict:
    """Canonical location wins; the variant supplies only what it left empty."""
    out = dict(keep)
    for field in ("city", "state", "country", "league"):
        if not (out.get(field) or "").strip():
            out[field] = drop.get(field, "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))

    dbs = [(CAREERS, json.loads(CAREERS.read_text(encoding="utf-8")))]
    if READY.exists():
        dbs.append((READY, json.loads(READY.read_text(encoding="utf-8"))))

    homes: dict[str, dict] = {}
    for variant, (canonical, _why) in MERGES.items():
        homes[canonical] = merge_place(loc.get(canonical) or {}, loc.get(variant) or {})
        homes[canonical]["team"] = canonical

    total = 0
    for variant, (canonical, why) in MERGES.items():
        home = homes[canonical]
        movers = []
        for path, db in dbs:
            for p in db:
                for s in p.get("career_history") or []:
                    if (s.get("team") or "").strip() != variant:
                        continue
                    s["team"] = canonical
                    s["city"] = home.get("city", "")
                    s["state"] = home.get("state", "")
                    s["country"] = home.get("country", "")
                    if path == CAREERS:
                        movers.append((p["player"], s.get("years")))
        total += len(movers)
        print(f"{variant!r} -> {canonical!r}  ({why})")
        for who, years in movers:
            print(f"    {who:24} {years}")
        print(f"    place: {home.get('city')}, {home.get('state') or '-'}, "
              f"{home.get('country')}")

    filled = []
    for club, country in COUNTRY_FILL.items():
        entry = loc.get(club)
        if entry is None or (entry.get("country") or "").strip():
            continue
        entry["country"] = country
        n = 0
        for path, db in dbs:
            for p in db:
                for s in p.get("career_history") or []:
                    if (s.get("team") or "").strip() == club and not (s.get("country") or "").strip():
                        s["country"] = country
                        if path == CAREERS:
                            n += 1
        filled.append((club, country, n))
    for club, country, n in filled:
        print(f"country fill: {club!r} -> {country} ({n} stint(s))")

    print(f"\n{total} stint(s) {'moved' if args.apply else 'to move'} across "
          f"{len(MERGES)} pair(s); every pair already shared a city, so no "
          f"stint changes place")

    if not args.apply:
        return 0

    for variant, (canonical, _why) in MERGES.items():
        loc[canonical] = homes[canonical]
        loc.pop(variant, None)
        alias_doc["aliases"][variant] = canonical
    alias_doc["aliases"] = dict(sorted(alias_doc["aliases"].items()))
    ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    LOCATIONS.write_text(json.dumps(dict(sorted(loc.items())), ensure_ascii=False,
                                    indent=2) + "\n", encoding="utf-8")
    for path, db in dbs:
        path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
