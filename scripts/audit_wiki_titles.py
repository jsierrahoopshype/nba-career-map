"""Ask Wikipedia what every record's article actually is, and report the liars.

Identical careers gave away six records built from the wrong article, but that
only works when the fabrication happens to collide with the real player it was
copied from. A wrong article nobody else shares looks like ordinary data. So
this asks the source directly: resolve every stored article title, and judge it
against the person the record claims to be.

  sweep   resolve every title and classify (a hundred requests for five
          thousand records -- the API takes fifty titles at a time)
  fix     for the records the sweep condemns, go and find the right article,
          and replace the invented career with the real one

Run:  python3 scripts/audit_wiki_titles.py sweep
      python3 scripts/audit_wiki_titles.py fix [--player NAME ...] [--dry-run]
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
from names import canonical_url, title_from_url  # noqa: E402
from wiki_parser import parse_player  # noqa: E402
from wiki_person import candidate_titles, same_person  # noqa: E402
from wikipedia_api import WikipediaClient  # noqa: E402
from player_status import classify_status  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "wiki_title_audit.json"


def stored_title(rec: dict) -> str:
    """The article this record says it was built from."""
    return title_from_url(rec.get("wikipedia_url", ""))


def sweep(client: WikipediaClient, players: list, limit: int = 0) -> dict:
    """Resolve what every record's NAME reaches, and classify what comes back.

    The name, not the stored URL: the name is what the pipeline asks Wikipedia
    for on the next run, so resolving it says what the pipeline is about to do.
    The stored URL is checked too, but only to report the ones that no longer
    agree -- the seed wrote a guessed URL for many records (all three Charles
    Joneses point at the bare title), and a guessed URL is not evidence about
    the career the record actually holds.
    """
    recs = players[:limit] if limit else players
    resolved = client.resolve_titles([r["player"] for r in recs]
                                     + [t for t in (stored_title(r) for r in recs) if t])

    report = {"checked": len(recs), "wrong_person": [], "missing": [],
              "soft": [], "url_disagrees": [], "duplicates": [], "resolved": {}}
    by_article = defaultdict(list)
    for rec in recs:
        key = rec["player"]
        got = resolved.get(key)
        report["resolved"][key] = got or ""
        stored = stored_title(rec)
        row = {"player": key, "stored": stored, "resolved": got or "",
               "stints": len(rec.get("career_history") or [])}
        if not got:
            report["missing"].append(row)
            continue
        by_article[got].append(key)
        ok, why = same_person(key, got)
        row["reason"] = why
        if not ok:
            report["wrong_person"].append(row)
        elif why:
            report["soft"].append(row)
        if stored and resolved.get(stored) and resolved[stored] != got:
            report["url_disagrees"].append(
                {**row, "url_resolves_to": resolved[stored]})

    for article, keys in sorted(by_article.items()):
        if len(keys) > 1:
            report["duplicates"].append({"article": article,
                                         "players": sorted(keys)})
    return report


def _print_sweep(report: dict) -> None:
    print(f"checked {report['checked']} records")
    print(f"  wrong person : {len(report['wrong_person'])}")
    for r in report["wrong_person"]:
        print(f"      {r['player']!r} -> {r['resolved']!r}  ({r['reason']}, "
              f"{r['stints']} stints)")
    print(f"  no article   : {len(report['missing'])}")
    print(f"  stored URL points somewhere else: {len(report['url_disagrees'])}")
    print(f"  same article shared by 2+ records: {len(report['duplicates'])}")
    for r in report["duplicates"]:
        print(f"      {r['article']!r}: {', '.join(r['players'])}")
    print(f"  respelled (allowed, listed for eyeballing): {len(report['soft'])}")


# --- fix ---------------------------------------------------------------------

def _replace_career(db, rec: dict, title: str, wt: str, client,
                    current_year: int) -> dict:
    """Overwrite a record from the article that is actually about this person.

    A straight replacement, not a merge: what is there was built from somebody
    else's article, so there is nothing in it worth keeping. _richer() exists to
    stop a thin parse clobbering good data, and would stop this.
    """
    fresh = parse_player(wt, rec["player"], db.normalizer)
    fresh.pop("_raw_teams", None)
    before = [f"{s.get('years')} {s.get('team')}"
              for s in rec.get("career_history") or []]
    rec["career_history"] = fresh.get("career_history", [])
    rec["current_team"] = fresh.get("current_team", "")
    rec["wikipedia_url"] = canonical_url(title)
    rec["parse_status"] = fresh.pop("status", "success")
    for f in ("position", "number", "birth_date", "birth_place", "death_date",
              "death_place", "high_school", "college", "draft", "nationality",
              "all_star"):
        if fresh.get(f):
            rec[f] = fresh[f]
    new_teams = []
    for stint in rec["career_history"]:
        if stint["team"] not in db.locations:
            new_teams.append(stint["team"])
        db.enrich_stint(stint, client, {})
    rec["status"] = classify_status(rec, on_nba_roster=False,
                                    current_year=current_year,
                                    retirement_announced=rec.get(
                                        "retirement_announced", False))
    rec["last_updated"] = uc.today()
    return {"player": rec["player"], "article": title, "before": before,
            "after": [f"{s.get('years')} {s.get('team')}"
                      for s in rec["career_history"]],
            "new_teams": new_teams, "status": rec["status"]}


def fix(client: WikipediaClient, db, names: list, current_year: int) -> dict:
    out = {"fixed": [], "unresolved": []}
    for name in names:
        rec = db.by_name.get(name)
        if not rec:
            out["unresolved"].append({"player": name, "why": "no such record"})
            continue
        tried = []
        for cand in [name] + candidate_titles(name, uc._birth_year(rec)):
            wt, title = client.get_wikitext_and_title(cand)
            tried.append({"title": cand, "resolved": title or ""})
            if not (wt and title and same_person(name, title)[0]):
                continue
            out["fixed"].append(_replace_career(db, rec, title, wt, client,
                                                current_year))
            break
        else:
            out["unresolved"].append({"player": name, "tried": tried})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["sweep", "fix"])
    ap.add_argument("--player", action="append", default=[],
                    help="fix only these records (default: everything the "
                         "sweep report condemns)")
    ap.add_argument("--players-csv", default="",
                    help="same as --player, comma-separated (for CI inputs)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-requests", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = WikipediaClient(delay=args.delay, max_requests=args.max_requests)
    db = uc.Database()
    players = [db.by_name[n] for n in db.order]
    current_year = int(uc.today()[:4])

    if args.action == "sweep":
        report = sweep(client, players, args.limit)
        _print_sweep(report)
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                          encoding="utf-8")
        print(f"\nwrote {REPORT.relative_to(ROOT)}  "
              f"({client.requests_made} requests)")
        return 0

    names = list(args.player) + [n.strip() for n in args.players_csv.split(",")
                                 if n.strip()]
    if not names:
        if not REPORT.exists():
            sys.exit("no sweep report; run `sweep` first or pass --player")
        names = [r["player"] for r in
                 json.loads(REPORT.read_text(encoding="utf-8"))["wrong_person"]]
    result = fix(client, db, names, current_year)
    for row in result["fixed"]:
        print(f"\n{row['player']!r} <- {row['article']!r}  [{row['status']}]")
        print(f"    was: {' | '.join(row['before']) or '(nothing)'}")
        print(f"    now: {' | '.join(row['after']) or '(nothing)'}")
        if row["new_teams"]:
            print(f"    new teams: {', '.join(row['new_teams'])}")
    for row in result["unresolved"]:
        print(f"\n{row['player']!r}: no article found "
              f"({row.get('why') or row.get('tried')})")
    print(f"\nfixed {len(result['fixed'])}, unresolved "
          f"{len(result['unresolved'])} ({client.requests_made} requests)")
    if result["fixed"] and not args.dry_run:
        uc._persist(db, {"date": uc.today(), "mode": "article-fix",
                         "players_updated": [r["player"] for r in result["fixed"]],
                         "new_players": [], "new_teams": [], "team_moves": [],
                         "status_changes": [], "newly_overseas": [],
                         "newly_retired": [], "requests": client.requests_made,
                         "budget_exhausted": False})
        print("database written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
