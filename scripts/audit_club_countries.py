"""Report clubs whose country looks wrong. Reports only -- writes nothing.

TWO CHECKS.

  1. CITY DISAGREEMENT. Clubs that share a city name but not a country. Some
     of these are real -- Valencia is a city in Spain and another in Venezuela,
     León one in Spain and another in Mexico -- so the report separates the
     city names already established as genuine collisions from the rest, which
     is where a mis-placed club shows up.

  2. "USA" OVER A CITY THAT IS NOT IN THE US. Reported on evidence, never on a
     guess about the name: a club is flagged only when its own city is attested
     with a non-USA country somewhere else in the project -- another club's
     location entry, or the COORDS table index.html draws pins from -- and
     never attested with USA. That is how Rabotnički (Skopje), Anyang SBS
     (Anyang) and Newfoundland Growlers (St. John's) were found.

Run:  python3 scripts/audit_club_countries.py
"""
from __future__ import annotations

import collections
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
INDEX_HTML = ROOT / "index.html"

# City names this project has already established really do name two different
# places. Listing them keeps them out of the way of check 1's real findings.
KNOWN_TWO_CITY_NAMES = {
    "Valencia", "León", "Lima", "San Carlos", "Santiago", "Tripoli",
    "Worcester",
}


def fold(name: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", name or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def coords_city_countries() -> dict:
    """city -> {country, ...} from the COORDS table in index.html (read only)."""
    out = collections.defaultdict(set)
    try:
        text = INDEX_HTML.read_text(encoding="utf-8")
    except OSError:
        return out
    m = re.search(r"const COORDS\s*=\s*", text)
    if not m:
        return out
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
    for key in json.loads(text[i:j + 1]):
        parts = key.split("|")
        if len(parts) == 3 and parts[0] and parts[2]:
            out[fold(parts[0])].add(parts[2])
    return out


def main() -> int:
    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    db = json.loads(CAREERS.read_text(encoding="utf-8"))

    # Only clubs that actually appear in a career stint: the location file
    # carries entries for names nothing references any more, and a report full
    # of those is a report nobody reads.
    used = {(s.get("team") or "").strip()
            for p in db for s in p.get("career_history", [])}
    clubs = {k: v for k, v in loc.items()
             if k in used and (v.get("city") or "").strip()
             and (v.get("country") or "").strip()}

    # --- check 1: same city name, different countries ---------------------
    by_city = collections.defaultdict(list)
    for name, v in clubs.items():
        by_city[fold(v["city"])].append((name, v["city"], v["country"]))

    genuine, suspect = [], []
    for _key, rows in by_city.items():
        countries = {c for _n, _c, c in rows}
        if len(countries) < 2:
            continue
        display = sorted({c for _n, c, _co in rows})[0]
        (genuine if display in KNOWN_TWO_CITY_NAMES else suspect).append(
            (display, sorted(rows, key=lambda r: (r[2], r[0]))))

    print("=" * 72)
    print("CHECK 1  clubs sharing a city name but not a country")
    print("=" * 72)
    print(f"\n-- {len(suspect)} city name(s) where the split is NOT a known "
          f"two-city name --\n")
    for city, rows in sorted(suspect):
        print(f"  {city}")
        for name, c, country in rows:
            print(f"      {country:20} {name}   (city recorded as {c!r})")
    print(f"\n-- {len(genuine)} city name(s) already established as two real "
          f"cities (expected) --\n")
    for city, rows in sorted(genuine):
        print(f"  {city}: " + ", ".join(
            f"{name} [{country}]" for name, _c, country in rows))

    # --- check 2: "USA" over a city the project places elsewhere ----------
    city_countries = collections.defaultdict(set)
    for name, v in clubs.items():
        city_countries[fold(v["city"])].add(v["country"])
    from_coords = coords_city_countries()
    # COORDS is a pin table, not a country list, and a few of its keys carry a
    # province or a truncated name where the country goes ("St. John's||NL",
    # "Skopje||North"). Only a value this project uses as a real country counts
    # as evidence, so the report does not accuse a club on the strength of a
    # typo in a different file.
    real_countries = {(v.get("country") or "").strip() for v in loc.values()}
    real_countries.discard("")

    print()
    print("=" * 72)
    print('CHECK 2  clubs labelled "USA" whose city is attested elsewhere as '
          "non-US")
    print("=" * 72)
    hits = []
    for name, v in sorted(clubs.items()):
        if v["country"] != "USA":
            continue
        key = fold(v["city"])
        evidence = set()
        for other, ov in clubs.items():
            if other != name and fold(ov["city"]) == key and ov["country"] != "USA":
                evidence.add(f"{ov['country']} (club: {other})")
        others_usa = any(other != name and fold(ov["city"]) == key
                         and ov["country"] == "USA"
                         for other, ov in clubs.items())
        coord_countries = {c for c in from_coords.get(key, set())
                           if c in real_countries}
        if coord_countries and "USA" not in coord_countries:
            evidence |= {f"{c} (COORDS)" for c in sorted(coord_countries)}
        elif "USA" in coord_countries:
            others_usa = True
        if evidence and not others_usa:
            hits.append((name, v["city"], sorted(evidence)))
    if not hits:
        print("\n  none -- every USA-labelled club's city is attested as US "
              "somewhere, or nowhere else at all\n")
    for name, city, evidence in hits:
        print(f"\n  {name}   city {city!r}, country 'USA'")
        for e in evidence:
            print(f"      attested elsewhere as: {e}")
    print(f"\n  {len(hits)} club(s) flagged.")
    print("\n(report only — nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
