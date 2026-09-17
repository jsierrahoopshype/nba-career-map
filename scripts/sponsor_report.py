"""Find club names that are another club's name plus a commercial token.

The containment guard already handles a club whose name grew a DESCRIPTOR
("Al Riyadi" -> "Al Riyadi Club Beirut"). This is the other half: a club whose
name grew a SPONSOR. "Baskonia" -> "Kosner Baskonia", "Valencia" -> "Valencia
Basket" -> "Valencia Hoja del Lunes". Nothing about the shape of the string
says which, so the only honest way to tell a sponsor from a distinguishing word
is to name the sponsors.

This script does not decide anything. It produces the candidate list, with the
evidence for each pair, for a human to read. SPONSOR_PAIRS in
merge_club_renames.py is the decided list; this is what it gets built from.

The candidates are containment pairs where the extra tokens are NOT
descriptors, NOT places either club plays in, and NOT reserve markers -- so the
rename guard has already refused them -- filtered to those where both sides
sit in the same city and country. Same city is necessary and nowhere near
sufficient: Beijing has four different clubs, Larissa three, Manama two.

Run:  python3 scripts/sponsor_report.py            # the candidate list
      python3 scripts/sponsor_report.py --decided  # what is actually merged
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY  # noqa: E402
from era_correct_teams import ERA_TABLE  # noqa: E402
from merge_club_renames import index  # noqa: E402
from rosters import NBA_TEAMS  # noqa: E402
from team_normalizer import (GENERIC_TOKENS, RESERVE_TOKENS,  # noqa: E402
                             spelling_tokens)

_NBA = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA.add(_n)


def candidates(dbs: list) -> list:
    names, places, _cities = index(dbs)
    counts: Counter = Counter()
    for db in dbs:
        for p in db:
            for st in p.get("career_history") or []:
                t = (st.get("team") or "").strip()
                if t:
                    counts[t] += 1
    toks = {n: set(spelling_tokens(n)) for n in names}

    def place_toks(n):
        ci, co = places.get(n, ("", ""))
        return set(spelling_tokens(ci)) | set(spelling_tokens(co))

    # How many DIFFERENT clubs each bare name sits inside, which is what makes
    # "Beijing" or "Larisa" unusable as a merge target.
    supersets: dict = defaultdict(list)
    for a in names:
        for b in names:
            if a != b and toks[a] and toks[b] and toks[a] < toks[b]:
                supersets[a].append(b)

    out = []
    for a in names:
        for b in supersets[a]:
            if "/" in a or "/" in b or a in _NBA or b in _NBA:
                continue
            extra = toks[b] - toks[a]
            if extra & RESERVE_TOKENS:
                continue
            shared = place_toks(a) | place_toks(b)
            unknown = sorted(t for t in extra
                             if t not in GENERIC_TOKENS and t not in shared)
            if not unknown:
                continue          # the rename guard already merges these
            ca, cb = places.get(a, ("", "")), places.get(b, ("", ""))
            if not (ca[0] and cb[0]):
                continue          # no location on one side: nothing to check
            if (set(spelling_tokens(ca[0])) != set(spelling_tokens(cb[0]))
                    or set(spelling_tokens(ca[1]))
                    != set(spelling_tokens(cb[1]))):
                continue          # different cities: two clubs, not a sponsor
            out.append({"bare": a, "extended": b, "extra": unknown,
                        "bare_stints": counts[a], "ext_stints": counts[b],
                        "city": ca[0], "country": ca[1],
                        "bare_inside": len(supersets[a])})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--decided", action="store_true",
                    help="list the pairs merge_club_renames will actually act on")
    a = ap.parse_args()

    dbs = [json.loads(CAREERS.read_text(encoding="utf-8")),
           json.loads(READY.read_text(encoding="utf-8"))]
    if a.decided:
        from merge_club_renames import SPONSOR_PAIRS
        print(f"decided sponsor pairs: {len(SPONSOR_PAIRS)}")
        for pair, why in sorted(SPONSOR_PAIRS.items(), key=lambda kv: sorted(kv[0])):
            print(f"  {sorted(pair)}\n      {why}")
        return 0

    cand = candidates(dbs)
    # Grouped by bare name: a club with five sponsor names over its history is
    # one decision, not five.
    by_bare: dict = defaultdict(list)
    for c in cand:
        by_bare[c["bare"]].append(c)
    clean = {k: v for k, v in by_bare.items() if v[0]["bare_inside"] == len(v)}
    mixed = {k: v for k, v in by_bare.items() if k not in clean}

    print(f"candidate pairs: {len(cand)} across {len(by_bare)} bare names\n")
    print(f"=== bare name sits inside ONLY these ({len(clean)} names) ===")
    for bare in sorted(clean, key=lambda k: -max(c["ext_stints"] for c in clean[k])):
        v = clean[bare]
        print(f"  {bare!r} ({v[0]['bare_stints']}) -- {v[0]['city']}, "
              f"{v[0]['country']}")
        for c in sorted(v, key=lambda c: -c["ext_stints"]):
            print(f"      + {'/'.join(c['extra']):24s} {c['extended']!r} "
                  f"({c['ext_stints']})")
    print(f"\n=== bare name ALSO sits inside something else "
          f"({len(mixed)} names, read with care) ===")
    for bare in sorted(mixed):
        v = mixed[bare]
        print(f"  {bare!r} ({v[0]['bare_stints']}) inside "
              f"{v[0]['bare_inside']} names, {len(v)} look commercial")
        for c in sorted(v, key=lambda c: -c["ext_stints"]):
            print(f"      + {'/'.join(c['extra']):24s} {c['extended']!r} "
                  f"({c['ext_stints']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
