"""Resolve combined "old-name / current-name" NBA stint strings by majority years.

Some stints store a franchise's relocation as a combined name (e.g.
"New Orleans / Utah Jazz") because the player's tenure spanned the move, and the
location defaulted to whichever name appeared last -- often to nothing at all,
which drops the stint from the career map entirely. For each such stint that maps
to a franchise in the relocation reference table, compute how many of the stint's
own years fall in each era (using the era boundaries) and set team +
city/state/country to the majority era. Even split -> the later/current era.

Combined strings whose franchise is NOT in the reference table (G League, ABA,
international: "Denver Rockets / Nuggets", "U/Tex Wranglers") have no
authoritative split year and are left unchanged (reported).

This runs AUTOMATICALLY from the pipeline -- update_careers._persist() calls
resolve_players() on every run, because Wikipedia re-writes combined names on
every re-fetch and a one-off cleanup regenerates within days. The CLI entry
point below stays for ad-hoc runs over the files on disk.

Idempotent. Run:  python3 scripts/split_combined_teams.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"
CURRENT_YEAR = 2026

# franchise -> [(era_start_year, era_name), ...] ascending (from the era-names fix)
ERA_TABLE = {
    "Atlanta Hawks": [(0, "Tri-Cities Blackhawks"), (1951, "Milwaukee Hawks"),
                      (1955, "St. Louis Hawks"), (1968, "Atlanta Hawks")],
    "Detroit Pistons": [(0, "Fort Wayne Pistons"), (1957, "Detroit Pistons")],
    "Sacramento Kings": [(0, "Rochester Royals"), (1957, "Cincinnati Royals"),
                         (1972, "Kansas City-Omaha Kings"), (1975, "Kansas City Kings"),
                         (1985, "Sacramento Kings")],
    "Los Angeles Lakers": [(0, "Minneapolis Lakers"), (1960, "Los Angeles Lakers")],
    "Golden State Warriors": [(0, "Philadelphia Warriors"), (1962, "San Francisco Warriors"),
                              (1971, "Golden State Warriors")],
    "Washington Wizards": [(0, "Chicago Packers"), (1962, "Chicago Zephyrs"),
                           (1963, "Baltimore Bullets"), (1973, "Capital Bullets"),
                           (1974, "Washington Bullets"), (1997, "Washington Wizards")],
    "Philadelphia 76ers": [(0, "Syracuse Nationals"), (1963, "Philadelphia 76ers")],
    "Houston Rockets": [(0, "San Diego Rockets"), (1971, "Houston Rockets")],
    "LA Clippers": [(0, "Buffalo Braves"), (1978, "San Diego Clippers"), (1984, "LA Clippers")],
    "Brooklyn Nets": [(0, "New York Nets"), (1977, "New Jersey Nets"), (2012, "Brooklyn Nets")],
    "Utah Jazz": [(0, "New Orleans Jazz"), (1979, "Utah Jazz")],
    "Memphis Grizzlies": [(0, "Vancouver Grizzlies"), (2001, "Memphis Grizzlies")],
    "Oklahoma City Thunder": [(0, "Seattle SuperSonics"), (2008, "Oklahoma City Thunder")],
    "New Orleans Pelicans": [(0, "New Orleans Hornets"), (2013, "New Orleans Pelicans")],
    "Charlotte Hornets": [(0, "Charlotte Hornets"), (2004, "Charlotte Bobcats"), (2014, "Charlotte Hornets")],
}

LOC = {
    "New Orleans Jazz": ("New Orleans", "Louisiana", "USA"),
    "Utah Jazz": ("Salt Lake City", "Utah", "USA"),
    "Seattle SuperSonics": ("Seattle", "Washington", "USA"),
    "Oklahoma City Thunder": ("Oklahoma City", "Oklahoma", "USA"),
    "New York Nets": ("New York", "New York", "USA"),
    "New Jersey Nets": ("East Rutherford", "New Jersey", "USA"),
    "Brooklyn Nets": ("Brooklyn", "New York", "USA"),
    "Vancouver Grizzlies": ("Vancouver", "British Columbia", "Canada"),
    "Memphis Grizzlies": ("Memphis", "Tennessee", "USA"),
    "Charlotte Hornets": ("Charlotte", "North Carolina", "USA"),
    "New Orleans Hornets": ("New Orleans", "Louisiana", "USA"),
    "New Orleans Pelicans": ("New Orleans", "Louisiana", "USA"),
    "Charlotte Bobcats": ("Charlotte", "North Carolina", "USA"),
    "Tri-Cities Blackhawks": ("Moline", "Illinois", "USA"),
    "Milwaukee Hawks": ("Milwaukee", "Wisconsin", "USA"),
    "St. Louis Hawks": ("St. Louis", "Missouri", "USA"),
    "Atlanta Hawks": ("Atlanta", "Georgia", "USA"),
    "Fort Wayne Pistons": ("Fort Wayne", "Indiana", "USA"),
    "Detroit Pistons": ("Detroit", "Michigan", "USA"),
    "Rochester Royals": ("Rochester", "New York", "USA"),
    "Cincinnati Royals": ("Cincinnati", "Ohio", "USA"),
    "Kansas City-Omaha Kings": ("Kansas City", "Missouri", "USA"),
    "Kansas City Kings": ("Kansas City", "Missouri", "USA"),
    "Sacramento Kings": ("Sacramento", "California", "USA"),
    "Minneapolis Lakers": ("Minneapolis", "Minnesota", "USA"),
    "Los Angeles Lakers": ("Los Angeles", "California", "USA"),
    "Philadelphia Warriors": ("Philadelphia", "Pennsylvania", "USA"),
    "San Francisco Warriors": ("San Francisco", "California", "USA"),
    "Golden State Warriors": ("San Francisco", "California", "USA"),
    "Chicago Packers": ("Chicago", "Illinois", "USA"),
    "Chicago Zephyrs": ("Chicago", "Illinois", "USA"),
    "Baltimore Bullets": ("Baltimore", "Maryland", "USA"),
    "Capital Bullets": ("Landover", "Maryland", "USA"),
    "Washington Bullets": ("Washington", "D.C.", "USA"),
    "Washington Wizards": ("Washington", "D.C.", "USA"),
    "Syracuse Nationals": ("Syracuse", "New York", "USA"),
    "Philadelphia 76ers": ("Philadelphia", "Pennsylvania", "USA"),
    "San Diego Rockets": ("San Diego", "California", "USA"),
    "Houston Rockets": ("Houston", "Texas", "USA"),
    "Buffalo Braves": ("Buffalo", "New York", "USA"),
    "San Diego Clippers": ("San Diego", "California", "USA"),
    "LA Clippers": ("Los Angeles", "California", "USA"),
}

# era name -> (franchise, index in its ERA_TABLE list)
_ERA_INDEX: dict[str, tuple[str, int]] = {}
for _fr, _eras in ERA_TABLE.items():
    for _i, (_b, _n) in enumerate(_eras):
        _ERA_INDEX.setdefault(_n, (_fr, _i))

_ALIASES = json.loads(ALIASES.read_text(encoding="utf-8")).get("aliases", {})


# Combined names arrive in several shapes -- " / ", "/", and city-only or
# nickname-only shorthand -- so the separator match can't be the literal " / "
# it once was. That single-form assumption is why the earlier pass resolved
# only 8 of the 190 stints now present.
_SPLIT = re.compile(r"\s*/\s*")


def _segments(combined: str) -> list[str]:
    return [seg.strip().lstrip(",").strip()
            for seg in _SPLIT.split(combined) if seg.strip()]


def _era_candidates(combined: str) -> list[str]:
    """Every era name a combined string could be naming.

    Verbatim segments first, then the two shorthand shapes that appear in the
    data: a city-only prefix borrowing the last segment's nickname
    ("Vancouver/Memphis Grizzlies" -> "Vancouver Grizzlies") and a
    nickname-only suffix borrowing the first segment's city
    ("New Orleans Hornets/Pelicans" -> "New Orleans Pelicans").
    """
    segs = _segments(combined)
    cands = list(segs)
    if len(segs) >= 2:
        nickname = segs[-1].split()[-1]
        cands += [f"{seg} {nickname}" for seg in segs[:-1]]
        city = " ".join(segs[0].split()[:-1])
        if city:
            cands += [f"{city} {seg}" for seg in segs[1:]]
    return cands


def _identify(combined: str, bounds) -> str | None:
    """Franchise for a combined string, or None to leave it alone.

    A candidate only counts when the era it names actually OVERLAPS the stint's
    own years. Without that check, "Ontario / San Diego Clippers" (the Clippers'
    G League affiliate, 2019-) matches the NBA "San Diego Clippers" era of
    1978-84 and the stint gets rewritten to an NBA city it has nothing to do
    with -- caught while testing this.
    """
    start, end = bounds
    for cand in _era_candidates(combined):
        cand = _ALIASES.get(cand, cand)
        hit = _ERA_INDEX.get(cand)
        if not hit:
            continue
        franchise, i = hit
        eras = ERA_TABLE[franchise]
        era_start = eras[i][0]
        era_end = eras[i + 1][0] if i + 1 < len(eras) else 10_000
        if min(end, era_end) - max(start, era_start) > 0:
            return franchise
    return None


def _year_bounds(years: str):
    yrs = str(years or "")
    nums = re.findall(r"\d{4}", yrs)
    if not nums:
        return None
    start = int(nums[0])
    end = CURRENT_YEAR if re.search(r"present", yrs, re.I) else int(nums[-1])
    return start, end


def _majority_era(franchise: str, start: int, end: int) -> str | None:
    """Era of `franchise` with the most overlap with [start, end]; ties -> later."""
    # A single-year stint ("1977") spans that one season; give it width 1 so a
    # stint landing exactly on the relocation boundary counts toward the era that
    # contains the year (the later/current name) instead of scoring 0 on both.
    end = max(end, start + 1)
    eras = ERA_TABLE[franchise]
    best_name, best_overlap = None, 0
    for i, (b, name) in enumerate(eras):
        nxt = eras[i + 1][0] if i + 1 < len(eras) else 10_000
        overlap = min(end, nxt) - max(start, b)
        if overlap >= best_overlap:  # >= so a later era wins an even split
            best_overlap, best_name = overlap, name
    return best_name if best_overlap > 0 else None


def _resolve(players: list, changes: list, skipped: dict) -> int:
    n = 0
    for p in players:
        for s in p.get("career_history", []):
            team = s.get("team", "")
            if "/" not in team:
                continue
            bounds = _year_bounds(s.get("years", ""))
            fr = _identify(team, bounds) if bounds else None
            if not fr or not bounds:
                skipped[team] = skipped.get(team, 0) + 1
                continue
            era = _majority_era(fr, *bounds)
            if not era:
                skipped[team] = skipped.get(team, 0) + 1
                continue
            city, state, country = LOC[era]
            if (team, s.get("city"), s.get("state"), s.get("country")) != \
                    (era, city, state, country):
                changes.append((p["player"], team, s.get("years"), era))
                s["team"], s["city"], s["state"], s["country"] = era, city, state, country
                n += 1
    return n


def resolve_players(players: list) -> tuple[int, list, dict]:
    """Split combined names in an in-memory player list, in place.

    This is what the pipeline calls (update_careers._persist), before anything
    is written, so the careers file and every file derived from it get the same
    resolved values in one pass. Returns (changed, changes, skipped).
    """
    changes: list = []
    skipped: dict = {}
    n = _resolve(players, changes, skipped)
    return n, changes, skipped


def main() -> None:
    changes: list = []
    skipped: dict = {}
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    n1 = _resolve(careers, changes, skipped)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ready = json.loads(READY.read_text(encoding="utf-8"))
    n2 = _resolve(ready, [], {})
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json combined stints resolved: {n1}")
    print(f"READY.json  combined stints resolved: {n2}")
    print(f"skipped (not in relocation reference table): {sum(skipped.values())} stints, "
          f"{len(skipped)} distinct names")
    print("\n=== ALL RESOLVED CHANGES (player | combined -> era) ===")
    for player, old, yrs, new in sorted(changes):
        print(f"  {player} | {old!r} ({yrs}) -> {new!r}")
    print("\n=== SKIPPED combined names (no reference split year) ===")
    for name, cnt in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  x{cnt}  {name!r}")


if __name__ == "__main__":
    main()
