"""Fold records that are the same player under two spellings into one.

"Pat Ewing" and "Patrick Ewing" are one man with two rows; so are Jakob Poeltl
and Jakob Pöltl. They came in from the seed, which stored the name it was given
rather than the article it reached, so the dedupe index never saw them meet.

Two records are the same player when Wikipedia resolves both to the same
article. The stints are unioned rather than one row being picked: identical
today, but a row that carries a stint the other lacks must not lose it.

Order matters. A record built from the WRONG article also shares that article
with its victim (Scotty Pippen Jr and his father both resolve to Scottie
Pippen), and merging those two would bury the bug rather than fix it. So any
group holding a record the audit condemned is skipped, loudly, until
`audit_wiki_titles.py fix` has been run.

Run:  python3 scripts/dedupe_alias_records.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_careers as uc  # noqa: E402
from names import canonical_url, title_from_url, url_key  # noqa: E402
from wiki_person import same_person  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "wiki_title_audit.json"


# --- decided by hand ---------------------------------------------------------
#
# The automatic rule refuses a suffix difference, because Jr and Sr are
# different people and a father's career written into his son's record is the
# bug this whole pass exists for. These four were checked one at a time: each
# "Sr" key holds the FATHER's career, correctly, under a suffix Wikipedia does
# not use in the article title. They are the bare record twice over, not two
# people, and the careers are identical stint for stint.
FORCE_MERGE = (
    ("Jabari Smith", "Jabari Smith Sr"),
    ("Gerald Henderson", "Gerald Henderson Sr"),
    ("Walker Russell", "Walker Russell Sr"),
)

# Derrick Alston Sr has no bare record to merge into: he IS the only Derrick
# Alston here, keyed with a suffix his article does not carry. The equivalent
# of merging him is keying him the way every other single-record player is.
RENAME = {
    "Derrick Alston Sr": "Derrick Alston",
}

# A record whose career belonged to somebody else and for which no article of
# his own exists. Mike Gibson keeps the thirteen stints it was copied from; an
# empty page under a name nothing can source is worse than no page.
DROP = {
    "Michael Wilson": "career was Mike Gibson's; no article of his own parses",
}


# most active wins when two rows disagree
_RANK = {"nba_active": 3, "overseas_active": 2, "retired": 1}


def _sig(stint: dict) -> tuple:
    """A stint's identity, dash-insensitive.

    One row writes "2022-2023" and the other "2022–2023"; the same stint under
    two dashes would union into two.
    """
    years = re.sub(r"[\u2010-\u2015]", "-", stint.get("years", "") or "")
    return (years.strip(), (stint.get("team", "") or "").strip())


def _stints(rec: dict) -> set:
    return {_sig(s) for s in rec.get("career_history") or []}


def compatible(a: dict, b: dict) -> tuple[bool, str]:
    """Could these two records be one player?

    A shared article is not evidence. Cliff Robinson and Clifford Robinson both
    resolve to the same title and are two different players; so are Bob, Rob and
    Robert Williams. What Wikipedia is saying is that one of them has no article
    of its own, not that they are the same man.

    The careers decide. One player's two rows describe the same career, so they
    share stints; two players' rows do not. A row with no career at all is a
    stub and folds into the real one, provided the names can be the same
    person's.
    """
    if not same_person(a["player"], b["player"])[0]:
        # Both may answer to one article and still be two people: the two
        # Freddie Lewises, born twenty-two years apart, have the same career in
        # both rows because one of them was copied from the other.
        return False, f"{a['player']} is not {b['player']}"
    sa, sb = _stints(a), _stints(b)
    if not sa or not sb:
        # An empty record is missing data, not a duplicate. John Lucas has no
        # stints and his son has nineteen; folding the father into the son
        # would lose a player rather than merge one.
        return False, "one of them has no career at all"
    if sa == sb:
        return True, "identical careers"
    if sa & sb:
        return True, f"{len(sa & sb)} shared stint(s)"
    return False, "careers do not overlap"


def clusters(members: list, by_name: dict) -> tuple[list, list]:
    """Split one article's records into who is actually who."""
    keys = sorted(members)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    reasons, apart = {}, []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ok, why = compatible(by_name[a], by_name[b])
            if ok:
                parent[find(a)] = find(b)
                reasons[(a, b)] = why
            else:
                apart.append((a, b, why))
    out = defaultdict(list)
    for k in keys:
        out[find(k)].append(k)
    joined = [sorted(v) for v in out.values() if len(v) > 1]
    return joined, apart


def groups(players: list, report: dict | None) -> tuple[list, list]:
    """Group records by the article they resolve to, then by who they are.

    Two records that resolve to one article are candidates, not duplicates;
    `compatible` decides. A group holding a record the audit condemned is set
    aside entirely -- merging Scotty Pippen Jr into his father would make the
    duplicate go away and the fabrication permanent.
    """
    resolved = (report or {}).get("resolved") or {}
    condemned = {r["player"] for kind in ("bad_source", "wrong_person")
                 for r in (report or {}).get(kind, [])}
    by_name = {r["player"]: r for r in players}
    by_article = defaultdict(list)
    for rec in players:
        key = rec["player"]
        art = resolved.get(key) or title_from_url(rec.get("wikipedia_url", ""))
        if art:
            by_article[url_key(canonical_url(art))].append((art, key))

    ready, blocked = [], []
    for _k, members in sorted(by_article.items()):
        if len(members) < 2:
            continue
        article = max((a for a, _ in members), key=len)
        keys = sorted(k for _a, k in members)
        if condemned & set(keys):
            blocked.append({"article": article, "players": keys,
                            "why": "a member was built from the wrong article"})
            continue
        bad = [k for k in keys if not same_person(k, article)[0]]
        if bad:
            blocked.append({"article": article, "players": keys,
                            "why": f"not the same person: {', '.join(bad)}"})
            continue
        joined, apart = clusters(keys, by_name)
        for cluster in joined:
            ready.append({"article": article, "players": cluster,
                          "why": compatible(by_name[cluster[0]],
                                            by_name[cluster[1]])[1]})
        for a, b, why in apart:
            if not any(a in c and b in c for c in joined):
                blocked.append({"article": article, "players": [a, b],
                                "why": why})
    return ready, blocked


def _survivor(keys: list, article: str) -> str:
    """The key to keep.

    The primary key is what the map and the quiz index on and what a player's
    page is addressed by, and the repo keeps those ASCII on purpose -- the
    diacritics live in display_name. So an ASCII key wins over an accented one
    ("Jakob Poeltl" over "Jakob Pöltl"); otherwise the article's own title
    decides.
    """
    plain = [k for k in keys if k.isascii()]
    if plain and len(plain) < len(keys):
        keys = plain
    bare = re.sub(r"\s*\(.*?\)", "", article).strip()
    for k in keys:
        if k == article:
            return k
    for k in keys:
        if k == bare:
            return k
    return sorted(keys, key=lambda k: (-len(k.split()), len(k), k))[0]


def _merge(db, article: str, keys: list) -> dict:
    keep = _survivor(keys, article)
    rec = db.by_name[keep]
    dropped = [k for k in keys if k != keep]

    stints = {_sig(s): s for s in rec.get("career_history") or []}
    added = 0
    for other_key in dropped:
        other = db.by_name[other_key]
        for s in other.get("career_history") or []:
            sig = _sig(s)
            if sig not in stints:
                stints[sig] = s
                added += 1
        # scalars: keep what we have, take what we lack
        for f, v in other.items():
            if f in ("player", "display_name", "aliases", "career_history",
                     "current_team", "status", "all_star_count"):
                continue
            if not rec.get(f) and v:
                rec[f] = v
        if other.get("all_star_count") is not None:
            rec["all_star_count"] = max(rec.get("all_star_count") or 0,
                                        other["all_star_count"])
        if _RANK.get(other.get("status"), 0) > _RANK.get(rec.get("status"), 0):
            rec["status"] = other["status"]
            rec["current_team"] = other.get("current_team") or rec.get("current_team")

    rec["career_history"] = list(stints.values())
    rec["wikipedia_url"] = canonical_url(article)
    # the article's title, not its disambiguator: the frontend prints this,
    # and nobody is called "Johnny Davis (NBA)"
    rec["display_name"] = (rec.get("display_name")
                           or re.sub(r"\s*\(.*?\)", "", article).strip())
    aliases = set(rec.get("aliases") or [])
    for k in dropped:
        aliases.add(k)
        other = db.by_name[k]
        if other.get("display_name"):
            aliases.add(other["display_name"])
        aliases.update(other.get("aliases") or [])
        db.by_name.pop(k, None)
        if k in db.order:
            db.order.remove(k)
    rec["aliases"] = sorted(a for a in aliases if a and a != keep)
    rec["last_updated"] = uc.today()
    return {"article": article, "kept": keep, "dropped": dropped,
            "stints_gained": added, "stints": len(rec["career_history"]),
            "status": rec.get("status")}



def forced(db) -> list:
    """The hand-decided merges, in the same shape as the automatic ones."""
    out = []
    for keep, drop in FORCE_MERGE:
        if keep in db.by_name and drop in db.by_name:
            out.append({"article": keep, "players": sorted([keep, drop]),
                        "why": "decided by hand", "keep": keep})
    return out


def rename(db, old: str, new: str) -> dict:
    """Re-key a record, keeping the old key as an alias."""
    rec = db.by_name.pop(old)
    db.order[db.order.index(old)] = new
    rec["player"] = new
    rec["aliases"] = sorted(set(rec.get("aliases") or []) | {old})
    rec["last_updated"] = uc.today()
    db.by_name[new] = rec
    return {"from": old, "to": new,
            "stints": len(rec.get("career_history") or [])}


def drop(db, key: str) -> dict:
    rec = db.by_name.pop(key)
    db.order.remove(key)
    return {"player": key, "stints": len(rec.get("career_history") or [])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = uc.Database()
    players = [db.by_name[n] for n in db.order]
    report = (json.loads(REPORT.read_text(encoding="utf-8"))
              if REPORT.exists() else None)
    if report is None:
        print("no sweep report: grouping by stored URL, which the seed often "
              "guessed. Preview only -- run `audit_wiki_titles.py sweep` "
              "before applying.")
        if args.apply:
            return 2
    ready, blocked = groups(players, report)
    ready = forced(db) + ready

    merged = []
    for g in ready:
        merged.append(_merge(db, g["article"], g["players"]) if args.apply
                      else {"article": g["article"], "kept":
                            _survivor(g["players"], g["article"]),
                            "dropped": [k for k in g["players"]
                                        if k != _survivor(g["players"], g["article"])]})
    for g, row in zip(ready, merged):
        print(f"{row['kept']!r} <- {', '.join(repr(d) for d in row['dropped'])}"
              f"   [{g['article']}: {g['why']}]"
              + (f"  +{row['stints_gained']} stints" if args.apply else ""))
    for b in blocked:
        print(f"SKIPPED {b['players']}: {b['why']}")
    print(f"\n{len(merged)} group(s) {'merged' if args.apply else 'to merge'}, "
          f"{len(blocked)} skipped")

    for old, new in RENAME.items():
        if old not in db.by_name:
            continue
        if new in db.by_name:
            print(f"SKIPPED rename {old!r} -> {new!r}: {new!r} already exists")
            continue
        row = rename(db, old, new) if args.apply else {"from": old, "to": new}
        print(f"{row['from']!r} re-keyed as {row['to']!r}"
              + ("" if args.apply else "  (dry run)"))
        merged.append(row)

    for key, why in DROP.items():
        if key not in db.by_name:
            continue
        rec = db.by_name[key]
        if rec.get("career_history"):
            print(f"SKIPPED drop {key!r}: it has a career now "
                  f"({len(rec['career_history'])} stints) -- look again")
            continue
        row = drop(db, key) if args.apply else {"player": key}
        print(f"{key!r} dropped -- {why}" + ("" if args.apply else "  (dry run)"))
        merged.append(row)

    if args.apply and merged:
        uc._persist(db, {"date": uc.today(), "mode": "alias-dedupe",
                         "players_updated": [r.get("kept") or r.get("to")
                                             or r.get("player") for r in merged],
                         "new_players": [], "new_teams": [], "team_moves": [],
                         "status_changes": [], "newly_overseas": [],
                         "newly_retired": [], "requests": 0,
                         "budget_exhausted": False})
        print("database written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
