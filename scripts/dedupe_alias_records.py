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

# most active wins when two rows disagree
_RANK = {"nba_active": 3, "overseas_active": 2, "retired": 1}


def groups(players: list, report: dict | None) -> tuple[list, list]:
    """Group records by the article they resolve to.

    With a sweep report, the articles are Wikipedia's own answers. Without one,
    fall back to the stored URLs -- enough to find the seed's duplicates, since
    those store the same article under two names.
    """
    resolved = (report or {}).get("resolved") or {}
    condemned = {r["player"] for kind in ("bad_source", "wrong_person")
                 for r in (report or {}).get(kind, [])}
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
        ready.append({"article": article, "players": keys})
    return ready, blocked


def _survivor(keys: list, article: str) -> str:
    """The key to keep: the one that reads like the article's own title."""
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

    stints = {(s.get("years", ""), s.get("team", "")): s
              for s in rec.get("career_history") or []}
    added = 0
    for other_key in dropped:
        other = db.by_name[other_key]
        for s in other.get("career_history") or []:
            sig = (s.get("years", ""), s.get("team", ""))
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
    rec["display_name"] = rec.get("display_name") or article
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

    merged = []
    for g in ready:
        merged.append(_merge(db, g["article"], g["players"]) if args.apply
                      else {"article": g["article"], "kept":
                            _survivor(g["players"], g["article"]),
                            "dropped": [k for k in g["players"]
                                        if k != _survivor(g["players"], g["article"])]})
    for row in merged:
        print(f"{row['kept']!r} <- {', '.join(repr(d) for d in row['dropped'])}"
              f"   [{row['article']}]"
              + (f"  +{row['stints_gained']} stints" if args.apply else ""))
    for b in blocked:
        print(f"SKIPPED {b['players']}: {b['why']}")
    print(f"\n{len(merged)} group(s) {'merged' if args.apply else 'to merge'}, "
          f"{len(blocked)} skipped")

    if args.apply and merged:
        uc._persist(db, {"date": uc.today(), "mode": "alias-dedupe",
                         "players_updated": [r["kept"] for r in merged],
                         "new_players": [], "new_teams": [], "team_moves": [],
                         "status_changes": [], "newly_overseas": [],
                         "newly_retired": [], "requests": 0,
                         "budget_exhausted": False})
        print("database written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
