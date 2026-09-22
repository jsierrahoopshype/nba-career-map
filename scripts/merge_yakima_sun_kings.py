"""Fold "Yakama Sun Kings" into "Yakima Sun Kings".

One club, two spellings of the city it plays in. Yakima is the city and the
club; Yakama is the Native American nation the city is named for, and the
misspelling reached seven stints against Yakima's 104. Both records already
sit in Yakima, so nothing moves on the map: this only stops one club appearing
as two on the club pages and in the alumni counts.

Idempotent. Run:  python3 scripts/merge_yakima_sun_kings.py [--apply]
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

CANONICAL = "Yakima Sun Kings"
VARIANT = "Yakama Sun Kings"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    home = loc.get(CANONICAL) or {}
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))

    moved = 0
    dbs = [(CAREERS, json.loads(CAREERS.read_text(encoding="utf-8")))]
    if READY.exists():
        dbs.append((READY, json.loads(READY.read_text(encoding="utf-8"))))
    movers = []
    for path, db in dbs:
        for p in db:
            for s in p.get("career_history") or []:
                if (s.get("team") or "").strip() != VARIANT:
                    continue
                s["team"] = CANONICAL
                s["city"] = home.get("city", "")
                s["state"] = home.get("state", "")
                s["country"] = home.get("country", "")
                if path == CAREERS:
                    moved += 1
                    movers.append((p["player"], s.get("years")))

    print(f"{VARIANT!r} -> {CANONICAL!r}")
    for who, years in movers:
        print(f"    {who:22} {years}")
    print(f"\n{moved} stint(s) {'moved' if args.apply else 'to move'}; "
          f"both already sat in {home.get('city')!r}, so no stint changes place")

    if args.apply:
        alias_doc["aliases"][VARIANT] = CANONICAL
        alias_doc["aliases"] = dict(sorted(alias_doc["aliases"].items()))
        ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        loc.pop(VARIANT, None)
        LOCATIONS.write_text(json.dumps(dict(sorted(loc.items())),
                                        ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        for path, db in dbs:
            path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
