"""Merge club names that are the same club described at more length.

"Al Riyadi" and "Al Riyadi Club Beirut" are one club. A Wikipedia editor
expanded the article's name to its formal form, every stint scraped afterwards
carried the longer string, and the diff between two scrapes read as a transfer.
The spelling guard cannot catch it -- the letters are not the same, the name
GAINED words -- so this is the containment case, handled on tokens.

WHAT COUNTS AS THE SAME CLUB. One name's token set wholly inside the other's,
and every extra token either a descriptor (Club, BC, KK, SC, CD, CB, Basket,
Basketball, an article) or a place already attested for that country. Those
sets live in team_normalizer alongside the rule itself, so the move guard in
update_careers.py and this merger cannot disagree about what a rename is.

WHAT DOES NOT, and this is the half that matters:

  Reserve sides.   "Real Madrid" / "Real Madrid Castilla" and "Barcelona" /
                   "Barcelona B" are a first team and its farm team, and
                   players move between them for real. Any extra token in
                   RESERVE_TOKENS vetoes the match before anything else is
                   considered. Castilla is refused on a second ground too: it
                   is not a descriptor and not a Spanish city in this dataset,
                   so it reads as a distinguishing word.

  Shared names.    "Al Ahly" sits inside Al Ahly Cairo, Al Ahly Benghazi AND
                   Al Ahly Ly; "Al-Ahli" inside eight names across four
                   countries. These are different clubs that share a name, and
                   which one a bare stint meant is not knowable from here. A
                   name with more than one superset is merged ONLY when those
                   supersets are themselves all one club; otherwise it goes to
                   review.

  Sponsor names.   "Kosner Baskonia" and "HDI Sigorta Afyon Belediye" carry a
                   sponsor, which is neither a descriptor nor a place, so they
                   are reported rather than merged. Detecting sponsors needs a
                   list of sponsors; guessing would merge real clubs.

  Two countries.   Al Nasr Dubai and Al-Nasr Benghazi.

Canonical is picked by usage, exactly as in the spelling rounds, and the writes
go through merge_spelling_variants.apply_merges so both mergers touch the same
files the same way.

Idempotent. Run:  python3 scripts/merge_club_renames.py            # report
                  python3 scripts/merge_club_renames.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS  # noqa: E402
from era_correct_teams import ERA_TABLE  # noqa: E402
from update_careers import _is_slash_joined  # noqa: E402
from merge_spelling_variants import (_diacritic_count, _usage,  # noqa: E402
                                     apply_merges)
from rosters import NBA_TEAMS  # noqa: E402
from team_normalizer import (KNOWN_DISTINCT, rename_containment,  # noqa: E402
                             spelling_key, spelling_tokens, strip_diacritics)

# Pairs the automatic rule reports rather than merges, confirmed by hand.
# Each is a sponsor prefix on an otherwise identical name.
FORCE_MERGE = {
    frozenset({"Baskonia", "Kosner Baskonia"}):
        "Kosner was a shirt sponsor; one stint against 130",
    # The rule refuses this one because the club's location rows say Manara,
    # a Beirut district, so "Beirut" is not among its own place tokens. It is
    # the club the whole containment rule was written for.
    frozenset({"Al Riyadi", "Al Riyadi Beirut"}):
        "same Lebanese club; Manara is a district of Beirut",
}

_NBA_NAMES = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA_NAMES.add(_n)


def _country(places: dict, name: str) -> str:
    return "".join(spelling_tokens((places.get(name) or ("", ""))[-1]))


def index(dbs: list) -> tuple[list, dict, dict]:
    """Club names, where each plays, and the cities attested per country."""
    counts: dict = defaultdict(int)
    seen: dict = defaultdict(lambda: defaultdict(int))
    cities: dict = defaultdict(set)
    for db in dbs:
        for p in db:
            for s in p.get("career_history") or []:
                team = (s.get("team") or "").strip()
                if not team:
                    continue
                counts[team] += 1
                city = (s.get("city") or "").strip()
                country = (s.get("country") or "").strip()
                if city or country:
                    seen[team][(city, country)] += 1
                if city and country:
                    cities["".join(spelling_tokens(country))].update(
                        spelling_tokens(city))
    places = {t: max(v.items(), key=lambda kv: kv[1])[0]
              for t, v in seen.items()}
    return sorted(counts), places, cities


def edges(names: list, places: dict, cities: dict) -> tuple[dict, dict, list]:
    """(accepted subset -> [supersets], every subset -> [supersets], held)."""
    ok: dict = defaultdict(list)
    every: dict = defaultdict(list)
    held: list = []
    toks = {n: set(spelling_tokens(n)) for n in names}
    for a in names:
        for b in names:
            if a == b or not toks[a] or not toks[b] or not toks[a] < toks[b]:
                continue
            # Recorded before any filter: the ambiguity guard has to see EVERY
            # club a bare name sits inside, not just the ones that survived the
            # country and descriptor checks. Counting only the survivors is
            # what let "Al Ahly" merge into Al Ahly Cairo while Al Ahly
            # Benghazi and Al Ahly Ly were quietly filtered out first.
            every[a].append(b)
            pair = frozenset({strip_diacritics(a).casefold().strip(),
                              strip_diacritics(b).casefold().strip()})
            if pair in KNOWN_DISTINCT:
                held.append(((a, b), "pinned as distinct clubs"))
                continue
            # A slash usually marks a relocated or merged franchise
            # ("Pittsburgh / Minnesota Pipers", "Baltimore/Rockford
            # Lightning"), and folding one side in throws away the other half
            # of its history. A few are harmless sponsor slashes
            # ("Kalev/Cramo"), but telling them apart is exactly the judgement
            # call that belongs to a human, so all of them are reported.
            if "/" in a or "/" in b:
                held.append(((a, b), "relocation / merged-franchise name"))
                continue
            same, why = rename_containment(
                a, b, place_a=places.get(a, ()), place_b=places.get(b, ()))
            if same is True:
                ok[a].append(b)
            elif same is None or why:
                held.append(((a, b), why))
    return ok, every, held


def plan(dbs: list) -> tuple[list, list]:
    """(merges, held_back). Each merge is (canonical, [variants...])."""
    names, places, cities = index(dbs)
    ok, every, held = edges(names, places, cities)
    toks = {n: set(spelling_tokens(n)) for n in names}

    # A name inside SEVERAL others is only safe when those others are one club
    # between themselves -- Al Riyadi Beirut and Al Riyadi Club Beirut are,
    # Al Ahly Cairo and Al Ahly Benghazi are not.
    accepted: list = []
    for sub, sups in ok.items():
        allsups = every.get(sub, [])
        if len(allsups) > 1:
            chain = all(toks[a] < toks[b] or toks[b] < toks[a]
                        for i, a in enumerate(allsups) for b in allsups[i + 1:])
            if not chain:
                held.append(((sub, " / ".join(sorted(allsups))),
                             f"name shared by {len(allsups)} clubs"))
                continue
        accepted.extend((sub, s) for s in sups)

    forced = {tuple(sorted(p)) for p in FORCE_MERGE}
    for a, b in ({tuple(sorted(p)) for p in forced} - {tuple(sorted(e))
                                                       for e in accepted}):
        if a in names and b in names:
            accepted.append((a, b))

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in accepted:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups: dict = defaultdict(set)
    for n in list(parent):
        groups[find(n)].add(n)

    merges = []
    for members in groups.values():
        members = sorted(members)
        if any(m in _NBA_NAMES for m in members):
            held.append((tuple(members), "NBA franchise / era name"))
            continue
        # Union-find is transitive; containment is not. Las Vegas Silvers and
        # Albuquerque Silvers both sit inside "Las Vegas/Albuquerque Silvers"
        # and neither sits inside the other, so the chain dragged two clubs in
        # two cities into one group. A group has to be a chain: every pair
        # ordered by containment, or it is not one club getting longer.
        pairs = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
        if not all(toks[a] < toks[b] or toks[b] < toks[a] for a, b in pairs):
            held.append((tuple(members), "not a containment chain"))
            continue
        # KNOWN_DISTINCT pins pairs, and a third name must not be allowed to
        # reunite them: "Al Nassr" and "Al-Nasr" are pinned apart but both sit
        # inside "Al-Nasr SC".
        pinned = [(a, b) for a, b in pairs
                  if frozenset({strip_diacritics(a).casefold().strip(),
                                strip_diacritics(b).casefold().strip()})
                  in KNOWN_DISTINCT]
        if pinned:
            held.append((tuple(members),
                         f"pinned as distinct clubs ({pinned[0][0]} / "
                         f"{pinned[0][1]})"))
            continue
        canonical = sorted(
            members,
            key=lambda n: (-_usage(dbs, n)[0], -_usage(dbs, n)[1],
                           -_diacritic_count(n), n))[0]
        merges.append((canonical, [m for m in members if m != canonical]))
    return sorted(merges), held


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the merges")
    args = ap.parse_args()

    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    dbs = [careers, ready]

    merges, held = plan(dbs)
    print(f"=== merges: {len(merges)} ===")
    for canonical, variants in merges:
        st, al = _usage(dbs, canonical)
        detail = ", ".join(f"{v!r} ({_usage(dbs, v)[0]})" for v in variants)
        print(f"  {canonical!r} ({st} stints, {al} alumni)  <-  {detail}")

    buckets: dict = defaultdict(list)
    for members, why in held:
        buckets[why].append(members)
    print(f"\n=== held back: {len(held)} ===")
    for why, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {why}: {len(items)}")
        for m in sorted(items)[:6]:
            print(f"      {m}")
        if len(items) > 6:
            print(f"      ... and {len(items) - 6} more")

    if not args.apply:
        print("\n(report only — pass --apply to write)")
        return 0
    apply_merges(merges, careers, ready, locations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
