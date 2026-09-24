"""Correct four mis-placed clubs, and settle on ONE label for UK clubs.

WHY team_locations.json IS THE FIX. Database.enrich_stint() overwrites a
stint's city/state/country from data/teams/team_locations.json on every run
for every team it already knows, so that file -- not the stints -- is where a
location correction has to land. The stints are rewritten here as well, so the
site is right before the next pipeline run rather than after it.

THE FOUR CORRECTIONS. Three clubs carried country "USA" with a city that is
not in the United States (Skopje, Anyang, St. John's) -- the sports-club
location guard had matched the wrong article. The fourth is a label, not a
place: Newcastle Eagles sat under "England" while most UK clubs sit under
"United Kingdom".

THE UK LABEL. "United Kingdom" wins on two counts, so there is no judgement
call to record: it is what the majority of UK clubs already carry, and it is
what geo.COUNTRY_ALIASES has always folded England/Scotland/Wales into, so it
is the label every newly-discovered UK club gets anyway. The three stragglers
predate that. Every UK-nation label is swept, not just the ones present today,
so a future stray "Scotland" is caught by the same pass.

Idempotent. Run:  python3 scripts/fix_club_countries.py [--apply]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
REVIEW = ROOT / "data" / "teams" / "teams_needing_review.json"

# club -> (city, state, country, why)
CORRECTIONS = {
    "Rabotnički": (
        "Skopje", "", "North Macedonia",
        "Macedonian club; country read as USA, which put it on the USA page "
        "and split the Skopje city page in two",
    ),
    "Anyang KGC": (
        "Anyang", "", "South Korea",
        "Korean club; the 'Anyang SBS' spelling it absorbed carried USA",
    ),
    "Newfoundland Growlers": (
        "St. John's", "", "Canada",
        "St. John's, Newfoundland; country read as USA",
    ),
    "Newcastle Eagles": (
        "Newcastle upon Tyne", "", "United Kingdom",
        "label only: moved onto the UK convention below",
    ),
}

# Every label that means the United Kingdom, and the one this site uses.
UK_LABEL = "United Kingdom"
UK_ALIASES = {"England", "Scotland", "Wales", "Northern Ireland",
              "Great Britain", "Britain", "GB", "UK", "U.K."}


def _fix_stints(dbs, team: str, city: str, state: str, country: str) -> int:
    n = 0
    for _path, db in dbs:
        for p in db:
            for s in p.get("career_history") or []:
                if (s.get("team") or "").strip() != team:
                    continue
                if (s.get("city"), s.get("state"), s.get("country")) == (city, state, country):
                    continue
                s["city"], s["state"], s["country"] = city, state, country
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    dbs = [(CAREERS, json.loads(CAREERS.read_text(encoding="utf-8")))]
    if READY.exists():
        dbs.append((READY, json.loads(READY.read_text(encoding="utf-8"))))

    print("--- place corrections ---")
    for team, (city, state, country, why) in CORRECTIONS.items():
        before = loc.get(team) or {}
        n = _fix_stints(dbs, team, city, state, country)
        loc[team] = {"team": team, "city": city, "state": state,
                     "country": country, "league": before.get("league", "")}
        print(f"  {team!r}: {before.get('city', '?')}/{before.get('country', '?')}"
              f" -> {city}/{country}   ({n} stint(s))")
        print(f"      {why}")

    print(f"\n--- UK label -> {UK_LABEL!r} ---")
    swept = 0
    for team, entry in loc.items():
        if (entry.get("country") or "").strip() in UK_ALIASES:
            was = entry["country"]
            entry["country"] = UK_LABEL
            n = _fix_stints(dbs, team, entry.get("city", ""),
                            entry.get("state", ""), UK_LABEL)
            swept += 1
            print(f"  {team!r}: {was!r} -> {UK_LABEL!r}   ({n} stint(s))")
    # A stint can carry a UK-nation label even where its club's location entry
    # does not (a club merged away, a stint enriched before the entry changed),
    # so sweep the stored stints directly too.
    stint_only = 0
    for _path, db in dbs:
        for p in db:
            for s in p.get("career_history") or []:
                if (s.get("country") or "").strip() in UK_ALIASES:
                    s["country"] = UK_LABEL
                    stint_only += 1
    print(f"  {swept} location entr(ies) relabelled, plus {stint_only} stint(s) "
          f"no location entry covered")

    if not args.apply:
        print("\n(dry run — pass --apply to write)")
        return 0

    LOCATIONS.write_text(
        json.dumps(dict(sorted(loc.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    if REVIEW.exists():
        review = json.loads(REVIEW.read_text(encoding="utf-8"))
        cleared = [k for k in CORRECTIONS if k in review]
        for k in cleared:
            review.pop(k, None)
        REVIEW.write_text(
            json.dumps(dict(sorted(review.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"teams_needing_review: {len(cleared)} corrected club(s) cleared")
    for path, db in dbs:
        path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
