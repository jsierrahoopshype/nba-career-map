"""File five demonstrably wrong location records for review.

These surfaced while merging spelling-variant club names: each of these clubs
exists under two spellings, and the two spellings disagree about where the club
plays. The disagreement is not evidence of two clubs -- it is one club with one
bad geocode, and in every case the OTHER spelling of the same club already
carries the right answer, which is what makes these safe to call wrong rather
than merely suspicious.

The alias table is deliberately left alone. These groups stay unmerged; the fix
belongs in the location data, so that is where the flag goes.

Filing them here is not just a note: update_careers' review mode re-runs
location discovery over every entry in this file, so these are queued for a
fresh attempt. Two caveats worth knowing, both reported rather than acted on:

  - Discovery uses the same "based in <City>, <Region>" heuristic that produced
    the bad values, so it can reproduce one of them and quietly drop the entry
    from review as resolved. "Vilnius on Tuesday" is a heuristic artefact and
    the likeliest to recur.
  - The wrong city and country are also COPIED onto each stint at write time,
    so 42 stints currently plot in the wrong place and will keep doing so until
    the location is corrected and the stints re-stamped. Correcting the records
    by hand is the reliable fix; this script does not touch them.

Idempotent. Run:  python3 scripts/flag_bad_locations.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, LOCATIONS, REVIEW  # noqa: E402

# team -> why the stored record is wrong. Each names the sibling spelling that
# already holds the right value, so a reviewer can see the fix without digging.
BAD = {
    "Lietuvos rytas": (
        "recorded city is 'Vilnius on Tuesday' in USA — an artefact of the "
        "'based in <City>' parse, not a place; the other spelling "
        "'Lietuvos Rytas' correctly reads Vilnius, Lithuania"
    ),
    "P.A.O.K.": (
        "country recorded as North Macedonia; Thessaloniki is in Greece, and "
        "the other spelling 'PAOK' correctly reads Greece"
    ),
    "PAOK Thessaloníki": (
        "country recorded as North Macedonia; Thessaloniki is in Greece, and "
        "the sibling spellings 'PAOK Thessaloniki' and 'P.A.O.K. Thessaloniki' "
        "both correctly read Greece"
    ),
    "Guaiqueries de Margarita": (
        "recorded as Erie, Pennsylvania, USA; the club is Venezuelan, and the "
        "other spelling 'Guaiqueríes de Margarita' correctly reads Porlamar, "
        "Venezuela"
    ),
    "Florida Beach Dogs": (
        "recorded as Rapid City, South Dakota, USA; the other spelling "
        "'Florida Beachdogs' correctly reads West Palm Beach, Florida"
    ),
}


def main() -> None:
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))

    added = 0
    for team, reason in BAD.items():
        loc = locations.get(team)
        if loc is None:
            print(f"  SKIP {team!r}: no location record (already corrected?)")
            continue
        entry = {"team": team, "reason": reason,
                 "city": loc.get("city", ""), "country": loc.get("country", "")}
        if review.get(team) != entry:
            review[team] = entry
            added += 1

    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    affected = sum(1 for p in careers for s in p.get("career_history", [])
                   if s.get("team") in BAD)
    print(f"records flagged: {added} (review file now {len(review)} entries)")
    print(f"stints currently plotting from a bad record: {affected}")


if __name__ == "__main__":
    main()
