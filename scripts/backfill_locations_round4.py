"""Fourth location backfill pass: two ad-hoc single-team fixes requested
directly (not from the tiered teams_needing_review.json sweep), reusing
backfill_locations.py's _fill / _missing_location_teams helpers verbatim,
same idempotent mechanism as rounds 2 and 3.

Botafogo de Futebol e Regatas -> Rio de Janeiro, Brazil: certain (the club's
home city is unambiguous real-world knowledge).

Selenge Bodons -> Mongolia, city Sükhbaatar: the country is certain (the
club plays in the Mongolian National Basketball Association), but the city
is a probable inference (the Mongolian franchise most commonly associated
with that name plays out of Sükhbaatar) rather than a confirmed fact --
applied to unblock the affected records (this also fixes DeMarcus Cousins's
"-" country in the dashboard's Most Well-Traveled widget, since a stint
missing country entirely doesn't count as a located country), but flagged
review-tier in logs/location_review_proposals.txt rather than presented as
equally certain to the country.

Run:  python3 scripts/backfill_locations_round4.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import (  # noqa: E402
    CAREERS, READY, LOCATIONS, REVIEW, _fill, _missing_location_teams,
)
import json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "location_review_proposals.txt"

LOC_FIX: dict[str, tuple[str, str, str]] = {
    'Botafogo de Futebol e Regatas': ('Rio de Janeiro', '', 'Brazil'),
    'Selenge Bodons': ('Sükhbaatar', '', 'Mongolia'),
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

    log_entry = f"""
================================================================================
AD-HOC FIX -- ROUND 4 (2 teams, requested directly, not from the tiered sweep)
================================================================================
  'Botafogo de Futebol e Regatas' -> city='Rio de Janeiro', state='—', country='Brazil'
    CERTAIN (both city and country) -- real-world knowledge of the club's home city
  'Selenge Bodons' -> city='Sükhbaatar', state='—', country='Mongolia'
    country CERTAIN (Mongolian National Basketball Association club)
    city REVIEW-TIER / PROBABLE -- best-available inference, not independently confirmed;
    applied to the data (unblocks DeMarcus Cousins's "-" country in the Most
    Well-Traveled widget) but flagged here for future verification, unlike
    every other entry in this log which is presented as equally-confident.
"""
    with LOG.open("a", encoding="utf-8") as f:
        f.write(log_entry)

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"teams removed from review (now fixed): {removed}")
    print(f"teams still location-less total: {len(still_missing)}")


if __name__ == "__main__":
    main()
