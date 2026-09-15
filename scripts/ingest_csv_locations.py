"""Apply club locations supplied by a hand-checked CSV export.

The bulk-locate rounds left a tail of clubs they would not guess at, and they
were right not to: "Al Arabi" is not in Ethiopia, and "Barangay Ginebra" is in
Araneta City, not Geneva. Those answers came back checked by hand, so this
ingests them rather than re-deriving anything.

One row is corrected rather than taken as given: see CSV_OVERRIDE.

The CSV is a per-stint export for the 25+ stop players, with a `missing` column
marking the rows whose club had no location. Only those rows are read, and only
for clubs that STILL have no location -- a club located since the export was
made keeps what it has. Nothing here overwrites.

Club names go through the alias table first, so a CSV row naming a spelling
that has since been merged away lands on the surviving club.

Locations live in two places: the team_locations index AND a copy stamped onto
each career-history stint at write time. Both are updated, or the stints that
prompted this would stay unplottable.

Idempotent. Run:  python3 scripts/ingest_csv_locations.py --csv <path>
                  python3 scripts/ingest_csv_locations.py --csv <path> --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402
from team_normalizer import TeamNormalizer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

# Rows where the CSV's place is wrong and the right one is known. The CSV is
# hand-checked but not infallible, and this is the very error class it exists to
# fix, so the row is corrected rather than ingested or dropped.
# {club: (city, state, country, why)}
CSV_OVERRIDE = {
    "Trouville": (
        "Montevideo", "", "Uruguay",
        "CSV said Trouville, France. This is Club Trouville of Montevideo: the "
        "only player with the stint is Esteban Batista, a Uruguayan whose stops "
        "either side are Club Nacional de Football and Club Atlético Welcome, "
        "both Montevideo. Same class of error as Ginebra/Geneva"
    ),
}


def _js_object(name: str) -> dict:
    """Pull a top-level object literal out of index.html (COORDS / FALLBACK)."""
    text = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(rf"const {name}\s*=\s*", text)
    if not m:
        return {}
    i = text.index("{", m.end())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return json.loads(text[i:j + 1])


def plots(city: str, state: str, country: str, coords: dict, fallback: dict) -> str:
    """How getCoords() would resolve this place: the same order the app uses."""
    if not city:
        return ""
    if f"{city}|{state or ''}|{country or ''}" in coords:
        return "COORDS exact"
    if f"{city}||{country or ''}" in coords:
        return "COORDS city+country"
    if country and country in fallback:
        return f"FALLBACK[{country}]"
    return ""


def read_plan(csv_path: Path, locations: dict, tn: TeamNormalizer) -> tuple[dict, dict]:
    """Return ({club: (city, state, country)}, skipped) from the flagged rows."""
    plan, skipped = {}, {}
    with csv_path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if (row.get("missing") or "").strip().upper() != "YES":
                continue
            city = (row.get("city") or "").strip()
            country = (row.get("country") or "").strip()
            if not (city and country):
                continue
            club = tn.normalize((row.get("team") or "").strip())
            if not club:
                continue
            have = locations.get(club)
            if have and have.get("city") and have.get("country"):
                skipped[club] = (have.get("city"), have.get("country"))
                continue
            over = CSV_OVERRIDE.get(club)
            place = ((over[0], over[1], over[2]) if over
                     else (city, (row.get("state") or "").strip(), country))
            if club in plan and plan[club] != place:
                raise SystemExit(f"CSV disagrees with itself about {club!r}: "
                                 f"{plan[club]} vs {place}")
            plan[club] = place
    return plan, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="the hand-checked export")
    ap.add_argument("--apply", action="store_true", help="write (default: report)")
    args = ap.parse_args()

    tn = TeamNormalizer()
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    plan, skipped = read_plan(Path(args.csv), locations, tn)
    if CSV_OVERRIDE:
        print("corrected against the CSV:")
        for club, (city, _st, country, why) in sorted(CSV_OVERRIDE.items()):
            print(f"  {club} -> {city}, {country}: {why}")
        print()
    coords, fallback = _js_object("COORDS"), _js_object("FALLBACK")

    stints = sum(1 for p in careers for s in p.get("career_history", [])
                 if s.get("team") in plan)
    unplottable = sum(1 for p in careers for s in p.get("career_history", [])
                      if s.get("team") in plan
                      and not (s.get("city") and s.get("country")))

    print(f"clubs to locate: {len(plan)}   "
          f"(skipped, already located: {len(skipped)})")
    print(f"stints at those clubs: {stints}, of which {unplottable} "
          f"currently have no place\n")

    wont_plot = []
    for club, (city, state, country) in sorted(plan.items()):
        how = plots(city, state, country, coords, fallback)
        if not how:
            wont_plot.append(club)
        where = f"{city}{', ' + state if state else ''}, {country}"
        print(f"  {club:34} {where:38} {how or '** WOULD NOT PLOT **'}")
    if wont_plot:
        print(f"\n  {len(wont_plot)} would not resolve to coordinates: {wont_plot}")

    if not args.apply:
        print("\n(report only — pass --apply to write)")
        return

    for club, (city, state, country) in plan.items():
        entry = locations.get(club) or {"team": club, "league": ""}
        entry.update({"team": club, "city": city, "state": state,
                      "country": country})
        locations[club] = entry

    stamped = 0
    for db in (careers, ready):
        for p in db:
            for s in p.get("career_history", []):
                place = plan.get(s.get("team"))
                if not place:
                    continue
                if (s.get("city"), s.get("state"), s.get("country")) != place:
                    s["city"], s["state"], s["country"] = place
                    stamped += 1

    cleared = sum(1 for club in plan if review.pop(club, None) is not None)

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

    now_plottable = sum(1 for p in careers for s in p.get("career_history", [])
                        if s.get("team") in plan
                        and plots(s.get("city", ""), s.get("state", ""),
                                  s.get("country", ""), coords, fallback))
    print(f"\nclubs located:            {len(plan)}")
    print(f"stint rows stamped:       {stamped} (both career databases)")
    print(f"stints now plottable:     {now_plottable} (were {stints - unplottable})")
    print(f"review entries cleared:   {cleared}")


if __name__ == "__main__":
    main()
