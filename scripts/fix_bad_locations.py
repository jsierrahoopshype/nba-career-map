"""Correct five wrong club location records, and re-stamp the stints they fed.

These were filed by flag_bad_locations.py after the spelling-variant merge
round: each club exists under two spellings whose location records disagree,
and in each case one side is simply a bad geocode while the OTHER spelling of
the same club already holds the right answer. The corrected values below are
taken from those sibling records, not invented here.

Correcting by hand rather than leaving them to review mode is deliberate.
_run_review re-runs the same "based in <City>, <Region>" heuristic that
produced the bad values, so it could reproduce one and drop the entry from
review as resolved -- "Vilnius on Tuesday" is an artefact of exactly that
parse. Once corrected, the entries are removed from the review file, because
they are no longer in question.

The stints matter as much as the records: city/state/country are COPIED onto
each career-history stint at write time, not read live from team_locations, so
correcting the table alone would leave every existing stint plotting in the
wrong place. Both are updated here.

Idempotent. Run:  python3 scripts/fix_bad_locations.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402

# team -> the corrected place, taken from the club's other spelling.
CORRECT = {
    # was "Vilnius on Tuesday", USA -- a parse artefact, not a place.
    "Lietuvos rytas": {"city": "Vilnius", "state": "", "country": "Lithuania"},
    # was Thessaloniki, North Macedonia. Thessaloniki is in Greece.
    "P.A.O.K.": {"city": "Thessaloniki", "state": "", "country": "Greece"},
    "PAOK Thessaloníki": {"city": "Thessaloniki", "state": "", "country": "Greece"},
    # was Erie, Pennsylvania, USA. The club is Venezuelan.
    "Guaiqueries de Margarita": {"city": "Porlamar", "state": "",
                                 "country": "Venezuela"},
    # was Rapid City, South Dakota, USA.
    "Florida Beach Dogs": {"city": "West Palm Beach", "state": "Florida",
                           "country": "USA"},
}


def main() -> None:
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    fixed_records = 0
    for team, place in CORRECT.items():
        entry = locations.get(team)
        if entry is None:
            print(f"  SKIP {team!r}: no location record")
            continue
        before = (entry.get("city", ""), entry.get("state", ""),
                  entry.get("country", ""))
        after = (place["city"], place["state"], place["country"])
        if before != after:
            entry.update(place)
            entry["team"] = team
            locations[team] = entry
            fixed_records += 1
            print(f"  {team!r}: {before[0]}/{before[2]} -> {after[0]}/{after[2]}")

    # Corrected, so no longer in question.
    unflagged = sum(1 for t in CORRECT if review.pop(t, None) is not None)

    restamped = 0
    for db in (careers, ready):
        for p in db:
            for s in p.get("career_history", []):
                place = CORRECT.get(s.get("team"))
                if not place:
                    continue
                if (s.get("city"), s.get("state"), s.get("country")) != \
                        (place["city"], place["state"], place["country"]):
                    s["city"] = place["city"]
                    s["state"] = place["state"]
                    s["country"] = place["country"]
                    restamped += 1

    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")

    print(f"\nlocation records corrected: {fixed_records}")
    print(f"review entries cleared:     {unflagged}")
    print(f"stints re-stamped:          {restamped}")


if __name__ == "__main__":
    main()
