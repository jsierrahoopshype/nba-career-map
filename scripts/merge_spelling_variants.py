"""Merge every remaining spelling-variant club-name collision into one name.

The Ness Ziona round fixed one pair. This sweeps the rest: each group of club
names that reduce to the same spelling_key (same letters, differing only in
case, diacritics, punctuation/spacing or doubled letters) becomes one club.
Every one of these is a phantom transfer waiting for a player to move between
the two spellings, and two club pages on the site in the meantime.

Canonical is picked by usage, as in the earlier rounds: most stints wins. Ties
break on alumni count, then on the spelling carrying MORE diacritics (the house
style -- "Beşiktaş" over "Besiktas", "Náuticos" over "Nauticos"), then
alphabetically, so a re-run always picks the same winner.

CANONICAL_OVERRIDE is the one place that rule is set aside. Usage counts how
often a spelling was scraped, not whether it is right, and on a handful of
clubs the misspelling is simply the one that got scraped more. Enshrining
"S. Bennedetto Gorizia" as a club's name because a typo out-scraped the correct
spelling 4-2 is not a result worth having. Each override is listed with its
reason; remove an entry to fall back to pure usage.

HELD BACK rather than merged:
  - Pairs in team_normalizer.KNOWN_DISTINCT. Al Nasr plays in Dubai and
    Al Nassr in Riyadh; they collide on spelling_key but are two clubs.
  - Groups whose members sit in different cities or countries once those are
    themselves spelling-normalized (so 'Evreux'/'Évreux' is not a conflict but
    Veracruz/Córdoba is). Location data in this repo is noisy enough that a
    disagreement is a reason for a human to look, not to merge or to split.
  - Groups touching an NBA franchise or era name. Those are era-table
    business, not alias business.

Idempotent. Run:  python3 scripts/merge_spelling_variants.py            # report
                  python3 scripts/merge_spelling_variants.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402
from era_correct_teams import ERA_TABLE  # noqa: E402
from rosters import NBA_TEAMS  # noqa: E402
from team_normalizer import (KNOWN_DISTINCT, TRANSPOSITION_MIN_KEY_LEN,  # noqa: E402
                             TeamNormalizer, _one_adjacent_transposition,
                             spelling_key, strip_diacritics)

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"

# Groups the location guard would veto, where the disagreement is a bad geocode
# on the minority spelling rather than evidence of two clubs. Each is a single
# scrape typo one transposition from a well-attested name, and the variant's
# location row is dropped by the merge anyway. {frozenset(names): reason}
FORCE_MERGE = {
    frozenset({"Homenetmen Beirut", "Homentemen Beirut"}):
        "one stint against eight, and Mezher is a Beirut suburb, not a "
        "different club's home",
    frozenset({"Hunstville Flight", "Huntsville Flight"}):
        "one stint against eighteen; the D-League club is in Huntsville and "
        "the Cleveland geocode sits on the typo",
}

# Clubs where usage picks a spelling that is simply wrong. Value = the name to
# canonicalise on; it must be one of the group's own members.
CANONICAL_OVERRIDE = {
    # "Benedetto" (San Benedetto, the Italian sponsor). The doubled n is a
    # scrape typo that happens to out-number the correct spelling 4-2.
    "sbenedetogorizia": "S. Benedetto Gorizia",
    # Melilla is the Spanish city. "Melila" is a typo, and the two tie on
    # usage, so the tie-break alone would pick it on alphabetical order.
    "melilabaloncesto": "Melilla Baloncesto",
    # Báez is a proper name; the unaccented form leads 4-2 on scrapes only.
    "metropolitanosdemauriciobaez": "Metropolitanos de Mauricio Báez",
    # Three spellings, all tied on usage: the accent belongs on the a
    # ("Náuticos"). "Naúticos" puts it on the u and would win on sort order.
    "nauticosdemazatlan": "Náuticos de Mazatlán",
}

_NBA_NAMES = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA_NAMES.add(_n)


def _diacritic_count(name: str) -> int:
    """How many characters carry a mark. More marks = closer to house style."""
    return sum(1 for a, b in zip(name, strip_diacritics(name)) if a != b) \
        + max(0, len(name) - len(strip_diacritics(name)))


def _usage(dbs: list, name: str) -> tuple[int, int]:
    """(stints, alumni) for a club name across the career databases."""
    stints = sum(1 for db in dbs for p in db
                 for s in p.get("career_history", []) if s.get("team") == name)
    alumni = len({(id(db), p["player"]) for db in dbs for p in db
                  for s in p.get("career_history", []) if s.get("team") == name})
    return stints, alumni


def _place(locations: dict, name: str) -> tuple[str, str] | None:
    e = locations.get(name)
    if not e:
        return None
    city, country = e.get("city", ""), e.get("country", "")
    if not city and not country:
        return None
    return spelling_key(city), spelling_key(country)


def plan(dbs: list, locations: dict) -> tuple[list, list]:
    """Return (merges, held_back). Each merge is (canonical, [variants...])."""
    names = sorted({s["team"] for db in dbs for p in db
                    for s in p.get("career_history", []) if s.get("team")})
    by_key = defaultdict(list)
    for n in names:
        by_key[spelling_key(n)].append(n)

    # Keys that differ by ONE adjacent transposition are the same club written
    # two ways ("Guruyu Watson" / "Guruyú Waston"), so their groups join.
    # Short keys are excluded: see TRANSPOSITION_MIN_KEY_LEN.
    parent = {k: k for k in by_key}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    long_keys = [k for k in by_key if len(k) >= TRANSPOSITION_MIN_KEY_LEN]
    key_set = set(long_keys)
    for k in long_keys:
        for i in range(len(k) - 1):
            if k[i] == k[i + 1]:
                continue
            t = k[:i] + k[i + 1] + k[i] + k[i + 2:]
            if t in key_set and _one_adjacent_transposition(k, t):
                union(k, t)

    groups = defaultdict(list)
    for k, members in by_key.items():
        groups[find(k)].extend(members)

    merges, held = [], []
    for key, members in sorted(groups.items()):
        if len(members) < 2 or not key:
            continue

        nba = [m for m in members if m in _NBA_NAMES]
        if nba:
            held.append((members, f"NBA franchise / era name: {nba}"))
            continue

        folded = {strip_diacritics(m).casefold().strip() for m in members}
        if any(pair <= folded for pair in KNOWN_DISTINCT):
            held.append((members, "KNOWN_DISTINCT: different clubs that share a key"))
            continue

        places = {m: _place(locations, m) for m in members}
        known = {p for p in places.values() if p}
        if len(known) > 1 and frozenset(members) not in FORCE_MERGE:
            detail = ", ".join(f"{m}={locations.get(m, {}).get('city', '')}/"
                               f"{locations.get(m, {}).get('country', '')}"
                               for m in members)
            held.append((members, f"locations disagree ({detail})"))
            continue

        ranked = sorted(
            members,
            key=lambda m: (-_usage(dbs, m)[0], -_usage(dbs, m)[1],
                           -_diacritic_count(m), m))
        canonical = ranked[0]
        forced = CANONICAL_OVERRIDE.get(key)
        if forced:
            if forced not in members:
                held.append((members,
                             f"CANONICAL_OVERRIDE names {forced!r}, which is not "
                             f"in this group — stale override, not merged"))
                continue
            canonical = forced
        variants = [m for m in members if m != canonical]
        merges.append((canonical, variants))
    return merges, held


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the merges")
    args = ap.parse_args()

    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    dbs = [careers, ready]

    merges, held = plan(dbs, locations)

    print(f"=== merges: {len(merges)} ===")
    overridden = []
    for canonical, variants in merges:
        st, al = _usage(dbs, canonical)
        detail = ", ".join(f"{v!r} ({_usage(dbs, v)[0]})" for v in variants)
        flag = ""
        if CANONICAL_OVERRIDE.get(spelling_key(canonical)) == canonical:
            flag = "   [override: usage favoured the other spelling]"
            overridden.append((canonical, variants))
        print(f"  {canonical!r} ({st} stints, {al} alumni)  <-  {detail}{flag}")
    if overridden:
        print(f"\n  ({len(overridden)} canonical name(s) set against usage "
              f"— see CANONICAL_OVERRIDE)")
    print(f"\n=== held back: {len(held)} ===")
    for members, why in held:
        print(f"  {members}\n      {why}")

    if not args.apply:
        print("\n(report only — pass --apply to write)")
        return

    alias_doc = json.loads(ALIASES.read_text(encoding="utf-8"))
    aliases = alias_doc["aliases"]
    added = removed = 0
    for canonical, variants in merges:
        for v in variants:
            if aliases.get(v) != canonical:
                aliases[v] = canonical
                added += 1
        # never leave the canonical pointing back at one of its own variants
        if canonical in aliases:
            del aliases[canonical]
            removed += 1
    # Flatten chains. TeamNormalizer.normalize() resolves in a SINGLE pass, so
    # an older row pointing at a name this round just demoted to a variant
    # ("CD Cajamadrid" -> "Cajamadrid", now itself a variant of "Caja Madrid")
    # would resolve to a club that no longer exists. Repoint every such row at
    # the end of its chain.
    flattened = 0
    for k in list(aliases):
        seen, target = {k}, aliases[k]
        while target in aliases and aliases[target] != target and target not in seen:
            seen.add(target)
            target = aliases[target]
        if target != aliases[k]:
            aliases[k] = target
            flattened += 1

    alias_doc["aliases"] = dict(sorted(aliases.items()))
    ALIASES.write_text(json.dumps(alias_doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    if not merges and not flattened:
        print("\nnothing to merge")
        return

    import apply_aliases  # noqa: E402  (reads the table at import time)
    tally: dict = {}
    renamed = apply_aliases._apply(careers, tally) + apply_aliases._apply(ready, {})

    variant_to_canon = {v: c for c, vs in merges for v in vs}
    current_fixed = 0
    for db in dbs:
        for p in db:
            c = variant_to_canon.get(p.get("current_team"))
            if c:
                p["current_team"] = c
                current_fixed += 1

    # The canonical keeps its own location; each variant's entry is dropped.
    loc_dropped = 0
    for canonical, variants in merges:
        for v in variants:
            if v in locations:
                if canonical not in locations:
                    locations[canonical] = {**locations[v], "team": canonical}
                del locations[v]
                loc_dropped += 1

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    rev_dropped = 0
    for v in variant_to_canon:
        if v in review:
            del review[v]
            rev_dropped += 1

    ledger = json.loads(TRANSACTIONS.read_text(encoding="utf-8"))
    txns = ledger["transactions"] if isinstance(ledger, dict) else ledger
    ledger_fixed = 0
    for t in txns:
        for side in ("from_team", "to_team"):
            c = variant_to_canon.get(t.get(side))
            if c:
                t[side] = c
                ledger_fixed += 1

    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if ledger_fixed:
        TRANSACTIONS.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")

    clubs = {s["team"] for p in careers for s in p.get("career_history", [])
             if s.get("team") and s["team"] not in _NBA_NAMES}
    print(f"\nalias rows added: {added}"
          f"{f' (removed {removed} reversed)' if removed else ''}")
    print(f"alias chains flattened: {flattened}")
    print(f"stints renamed: {renamed}")
    print(f"current_team fields renamed: {current_fixed}")
    print(f"variant location rows dropped: {loc_dropped}")
    print(f"names removed from review: {rev_dropped}")
    print(f"ledger team names renamed: {ledger_fixed}")
    print(f"distinct non-NBA clubs remaining: {len(clubs)}")


if __name__ == "__main__":
    main()
