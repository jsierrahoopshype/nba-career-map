"""Re-canonicalise the Seville club to "Real Betis", and fold in every variant.

Round 6 merged "Real Betis" and "Cajasol" INTO "Caja San Fernando", because the
canonical name was picked by usage and Caja San Fernando carried the most
stints. Usage picked a sponsor name over the club's own enduring one, so the
direction is reversed here: everything now collapses to "Real Betis", which is
what readers know the club as.

Reversing the arrow means the old alias "Real Betis" -> "Caja San Fernando"
must be DELETED, not just overridden. apply_aliases resolves a name in a single
pass, so leaving it in place alongside "Caja San Fernando" -> "Real Betis"
would be a two-cycle: whichever the data happened to hold would be renamed to
the other and back on alternate runs, and the merge would never settle.

The four sponsor variants left out of round 6 are folded in at the same time:
Cajasol Sevilla, Cajasol Banca Civica, Coosur Real Betis and Real Betis Energia
Plus. All seven names are the same club.

Idempotent. Run:  python3 scripts/merge_real_betis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"

CANONICAL = "Real Betis"
VARIANTS = [
    "Caja San Fernando",
    "Cajasol",
    "Cajasol Sevilla",
    "Cajasol Banca Cívica",
    "Coosur Real Betis",
    "Real Betis Energía Plus",
]
# The club's home, carried onto the canonical name so every merged stint plots.
CANONICAL_LOCATION = {"team": CANONICAL, "city": "Seville", "state": "",
                      "country": "Spain", "league": ""}


def main() -> None:
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))
    aliases = alias_doc["aliases"]

    # Point every variant at the canonical name, and make sure the canonical
    # name is not itself an alias of anything (see the two-cycle note above).
    added = 0
    for name in VARIANTS:
        if aliases.get(name) != CANONICAL:
            aliases[name] = CANONICAL
            added += 1
    stale = aliases.pop(CANONICAL, None)
    alias_doc["aliases"] = dict(sorted(aliases.items()))
    ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    # Imported after the table is written: apply_aliases reads it at import time.
    import apply_aliases  # noqa: E402

    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    tally: dict = {}
    renamed = apply_aliases._apply(careers, tally) + apply_aliases._apply(ready, {})

    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    locations[CANONICAL] = CANONICAL_LOCATION
    for name in VARIANTS:
        locations.pop(name, None)

    # Every merged stint takes the canonical club's location; several variants
    # had no location at all, so this is what makes them plottable.
    located = 0
    for db in (careers, ready):
        for p in db:
            for s in p.get("career_history", []):
                if s.get("team") != CANONICAL:
                    continue
                if s.get("city") and s.get("country"):
                    continue
                s["city"] = CANONICAL_LOCATION["city"]
                s["state"] = CANONICAL_LOCATION["state"]
                s["country"] = CANONICAL_LOCATION["country"]
                located += 1

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    removed = 0
    for name in VARIANTS + [CANONICAL]:
        if name in review:
            del review[name]
            removed += 1

    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    alumni = {p["player"] for p in careers
              for s in p.get("career_history", []) if s.get("team") == CANONICAL}
    stints = sum(1 for p in careers
                 for s in p.get("career_history", []) if s.get("team") == CANONICAL)

    print(f"aliases pointed at {CANONICAL!r}: {added}")
    if stale:
        print(f"removed the reversed alias {CANONICAL!r} -> {stale!r} (would have cycled)")
    print(f"stints renamed: {renamed}")
    for k, v in sorted(tally.items()):
        print(f"    x{v}  {k}")
    print(f"stints given the club's location: {located}")
    print(f"names removed from review: {removed}")
    print(f"{CANONICAL!r}: {len(alumni)} alumni across {stints} stints")


if __name__ == "__main__":
    main()
