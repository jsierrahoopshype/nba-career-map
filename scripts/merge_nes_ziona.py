"""Merge the two spellings of the Ness Ziona club into one canonical name.

The signings feed logged "Jacob Wiley: Ironi Nes Ziona -> Ironi Ness Ziona" as
a transfer. One club, one S. Neither spelling was in team_aliases.json, so both
sides of _is_real_move normalized to themselves, differed, and a phantom move
went to the ledger.

Canonical is picked by usage, as in the earlier rounds: "Ironi Ness Ziona"
carries 19 stints across 17 alumni against 8 stints across 8 for "Ironi Nes
Ziona". (Worth knowing for later: the one-S form is the closer transliteration
of נס ציונה, and round 6 -> merge_real_betis is the precedent for reversing a
usage-picked canonical when the name readers know differs. Reverse it by
swapping CANONICAL and VARIANTS here and DELETING the stale reversed alias,
never by leaving both directions in place -- that cycles.)

No player carries both spellings, so the merge cannot produce a duplicate
stint.

Run prune_phantom_moves.py FIRST. This script rewrites the variant spelling in
past ledger rows, which turns the phantom into "X -> X" and loses the record of
which spelling the feed actually reported.

Idempotent. Run:  python3 scripts/merge_nes_ziona.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"

CANONICAL = "Ironi Ness Ziona"
VARIANTS = ["Ironi Nes Ziona"]
# Both spellings already carried the same location; it is restated on the
# canonical name so every merged stint still plots.
CANONICAL_LOCATION = {"team": CANONICAL, "city": "Ness Ziona", "state": "",
                      "country": "Israel", "league": ""}


def main() -> None:
    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))
    aliases = alias_doc["aliases"]

    added = 0
    for name in VARIANTS:
        if aliases.get(name) != CANONICAL:
            aliases[name] = CANONICAL
            added += 1
    # Guard against the two-cycle: the canonical name must not itself be an
    # alias of one of the variants.
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

    # current_team is a separate field from the stint list and is what the
    # move detector compares, so it has to be renamed too or the next run
    # re-fires the same phantom.
    current_fixed = 0
    for db in (careers, ready):
        for p in db:
            if p.get("current_team") in VARIANTS:
                p["current_team"] = CANONICAL
                current_fixed += 1

    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    locations[CANONICAL] = CANONICAL_LOCATION
    for name in VARIANTS:
        locations.pop(name, None)

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

    # The ledger is append-only, but a variant spelling left in a PAST entry
    # would render a club page that no longer exists. Rename in place; the
    # phantom row itself is removed separately by prune_phantom_moves.py.
    ledger = json.loads(TRANSACTIONS.read_text(encoding="utf-8"))
    txns = ledger["transactions"] if isinstance(ledger, dict) else ledger
    ledger_fixed = 0
    for t in txns:
        for side in ("from_team", "to_team"):
            if t.get(side) in VARIANTS:
                t[side] = CANONICAL
                ledger_fixed += 1
    if ledger_fixed:
        TRANSACTIONS.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

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
    print(f"current_team fields renamed: {current_fixed}")
    print(f"stints given the club's location: {located}")
    print(f"names removed from review: {removed}")
    print(f"ledger team names renamed: {ledger_fixed}")
    print(f"{CANONICAL!r}: {len(alumni)} alumni across {stints} stints")


if __name__ == "__main__":
    main()
