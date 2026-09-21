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
PROPOSALS = ROOT / "logs" / "article_proposals.json"


def stored_title(rec: dict) -> str:
    """The article this record says it was built from."""
    return title_from_url(rec.get("wikipedia_url", ""))


def sweep(client: WikipediaClient, players: list, limit: int = 0) -> dict:
    """Resolve what every record reaches, from both ends, and classify it.

    Two different questions, and the six known fabrications answer them
    differently:

      the NAME     is what the pipeline will ask for on the next run, so
                   resolving it says what the pipeline is about to do
      the URL      is where the career in the record came from, so resolving it
                   says whether what is already stored is somebody else's

    Scotty Pippen Jr fails only the second: his name reaches his own article
    today, while the career in his record was taken from his father's.
    """
    recs = players[:limit] if limit else players
    stored = {r["player"]: stored_title(r) for r in recs}
    resolved = client.resolve_titles([r["player"] for r in recs]
                                     + [t for t in stored.values() if t])

    report = {"checked": len(recs), "bad_source": [], "wrong_person": [],
              "missing": [], "soft": [], "url_disagrees": [], "duplicates": [],
              "resolved": {}, "resolved_url": {}}
    by_article = defaultdict(list)
    for rec in recs:
        key = rec["player"]
        got, url_got = resolved.get(key), resolved.get(stored[key])
        report["resolved"][key] = got or ""
        report["resolved_url"][key] = url_got or ""
        row = {"player": key, "stored": stored[key], "resolved": got or "",
               "url_resolves_to": url_got or "",
               "stints": len(rec.get("career_history") or [])}

        # what the record was built from
        if url_got and not same_person(key, url_got)[0]:
            report["bad_source"].append(
                {**row, "reason": same_person(key, url_got)[1]})

        # what the next run would do
        if not got:
            report["missing"].append(row)
            continue
        by_article[got].append(key)
        ok, why = same_person(key, got)
        row = {**row, "reason": why}
        if not ok:
            report["wrong_person"].append(row)
        elif why:
            report["soft"].append(row)
        if url_got and url_got != got:
            report["url_disagrees"].append(row)

    for article, keys in sorted(by_article.items()):
        if len(keys) > 1:
            report["duplicates"].append({"article": article,
                                         "players": sorted(keys)})
    return report


def _print_sweep(report: dict) -> None:
    print(f"checked {report['checked']} records")
    print(f"  built from somebody else's article: {len(report['bad_source'])}")
    for r in report["bad_source"]:
        print(f"      {r['player']!r} <- {r['url_resolves_to']!r}  "
              f"({r['reason']}, {r['stints']} stints)")
    print(f"  name now reaches somebody else: {len(report['wrong_person'])}")
    for r in report["wrong_person"]:
        print(f"      {r['player']!r} -> {r['resolved']!r}  ({r['reason']}, "
              f"{r['stints']} stints)")
    print(f"  no article   : {len(report['missing'])}")
    print(f"  stored URL points somewhere else: {len(report['url_disagrees'])}")
    print(f"  same article shared by 2+ records: {len(report['duplicates'])}")
    for r in report["duplicates"]:
        print(f"      {r['article']!r}: {', '.join(r['players'])}")
    print(f"  respelled (allowed, listed for eyeballing): {len(report['soft'])}")



# --- propose -----------------------------------------------------------------

def _overlap(rec: dict, parsed: dict) -> tuple[int, int, int]:
    here, there = _stints(rec), _stints(parsed)
    return len(here & there), len(here), len(there)


def propose(client: WikipediaClient, db, names: list) -> dict:
    """For a record pointing at a disambiguation page, find the real article.

    A disambiguation page is Wikipedia saying "which one?", and the answer is
    usually one search away -- but "Joe Smith (basketball)" is not necessarily
    our Joe Smith, so every candidate is parsed and its career compared with
    what the record already holds. Shared stints are the evidence; the count is
    reported rather than assumed.
    """
    out = {"proposals": [], "none": []}
    for name in names:
        rec = db.by_name.get(name)
        if not rec:
            out["none"].append({"player": name, "why": "no such record"})
            continue
        tried, best = [], None
        seen = set()
        ladder = candidate_titles(name, uc._birth_year(rec))
        try:
            ladder += [t for t in client.search(f"{name} basketball", limit=6)]
        except Exception as exc:  # noqa: BLE001
            print(f"[propose] {name}: search failed ({exc})")
        for cand in ladder:
            if cand in seen:
                continue
            seen.add(cand)
            wt, title = client.get_wikitext_and_title(cand)
            if not (wt and title):
                continue
            ok, why = same_person(name, title)
            if not ok:
                tried.append({"title": cand, "resolved": title, "why": why})
                continue
            parsed = parse_player(wt, name, db.normalizer)
            shared, here, there = _overlap(rec, parsed)
            row = {"title": title, "shared": shared, "stored": here,
                   "article": there, "reason": why,
                   "teams": [s.get("team") for s in
                             (parsed.get("career_history") or [])][:6]}
            tried.append(row)
            if best is None or (shared, there) > (best["shared"], best["article"]):
                best = row
        if best and best["shared"]:
            out["proposals"].append({"player": name, **best, "tried": tried})
        else:
            out["none"].append({"player": name, "why": "nothing shares a stint",
                                "tried": tried})
    return out


def _print_proposals(doc: dict) -> None:
    print(f"\n{len(doc['proposals'])} proposal(s):")
    for r in doc["proposals"]:
        print(f"  {r['player']!r:18} -> {r['title']!r:38} "
              f"{r['shared']}/{r['stored']} stored stints shared "
              f"({r['article']} in the article)")
    print(f"\n{len(doc['none'])} with nothing confident:")
    for r in doc["none"]:
        print(f"  {r['player']!r:18} {r['why']}")


# --- fix ---------------------------------------------------------------------

def _stints(rec_or_parse: dict) -> set:
    return {(re.sub(r"[\u2010-\u2015]", "-", (s.get("years") or "")).strip(),
             (s.get("team") or "").strip())
            for s in rec_or_parse.get("career_history") or []}


def _replace_career(db, rec: dict, title: str, wt: str, client,
                    current_year: int, *, replace: bool) -> dict:
    """Rebuild a record from the article that is actually about this person.

    Two different jobs. A record built from somebody else's article holds
    nothing worth keeping, so it is replaced outright -- _richer() exists to
    stop a thin parse clobbering good data, and would stop exactly this repair.
    A record that merely points at a disambiguation page probably holds a fine
    career from the seed, so its new article has to agree with what is already
    there before anything is overwritten: a "Joe Smith (basketball)" who shares
    no stint with our Joe Smith is a different Joe Smith.
    """
    fresh = parse_player(wt, rec["player"], db.normalizer)
    fresh.pop("_raw_teams", None)
    before = [f"{s.get('years')} {s.get('team')}"
              for s in rec.get("career_history") or []]
    here, there = _stints(rec), _stints(fresh)
    if here and not there:
        # An article that parses to nothing is not an improvement on a career,
        # even a wrong one: Michael Wilson's thirteen stints were another man's,
        # and replacing them with an empty list lost the record instead of
        # repairing it.
        return {"player": rec["player"], "article": title, "before": before,
                "after": None, "new_teams": [],
                "conflict": "the article parsed to nothing",
                "status": rec.get("status")}
    if not replace:
        if here and there and not (here & there):
            return {"player": rec["player"], "article": title,
                    "before": before, "after": None, "new_teams": [],
                    "conflict": "shares no stint with the article",
                    "status": rec.get("status")}
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
            "new_teams": new_teams, "conflict": "", "status": rec["status"]}


def fix(client: WikipediaClient, db, names: list, current_year: int,
        replace: set | None = None, extra: dict | None = None) -> dict:
    """Repair records. `replace` names the ones whose careers are known bad."""
    replace = set(names) if replace is None else replace
    extra = extra or {}
    out = {"fixed": [], "unresolved": [], "conflicts": []}
    for name in names:
        rec = db.by_name.get(name)
        if not rec:
            out["unresolved"].append({"player": name, "why": "no such record"})
            continue
        tried = []
        ladder = [name] + candidate_titles(name, uc._birth_year(rec))
        if extra.get(name):
            # the article a proposal settled on, tried before the guesses
            ladder.insert(1, extra[name])
        for cand in ladder:
            wt, title = client.get_wikitext_and_title(cand)
            tried.append({"title": cand, "resolved": title or ""})
            if not (wt and title and same_person(name, title)[0]):
                continue
            row = _replace_career(db, rec, title, wt, client, current_year,
                                  replace=name in replace)
            (out["conflicts"] if row["conflict"] else out["fixed"]).append(row)
            break
        else:
            out["unresolved"].append({"player": name, "tried": tried})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["sweep", "propose", "fix"])
    ap.add_argument("--from-proposals", action="store_true",
                    help="fix the records named in logs/article_proposals.json")
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
    replace = None
    if args.action == "propose":
        if not names:
            if not REPORT.exists():
                sys.exit("no sweep report; run `sweep` first or pass --player")
            doc = json.loads(REPORT.read_text(encoding="utf-8"))
            names = [r["player"] for r in doc.get("wrong_person", [])]
        found = propose(client, db, names)
        _print_proposals(found)
        PROPOSALS.parent.mkdir(parents=True, exist_ok=True)
        PROPOSALS.write_text(json.dumps(found, indent=2, ensure_ascii=False),
                             encoding="utf-8")
        print(f"\nwrote {PROPOSALS.relative_to(ROOT)}  "
              f"({client.requests_made} requests)")
        return 0
    if args.from_proposals and not names:
        doc = json.loads(PROPOSALS.read_text(encoding="utf-8"))
        names = [r["player"] for r in doc["proposals"]]
        replace = set()          # careful mode: the stored career is good
    if not names:
        if not REPORT.exists():
            sys.exit("no sweep report; run `sweep` first or pass --player")
        doc = json.loads(REPORT.read_text(encoding="utf-8"))
        # a record built from the wrong article is replaced; one that merely
        # points at a disambiguation page is only repointed, and only if the
        # article agrees with the career already stored
        replace = {r["player"] for r in doc.get("bad_source", [])}
        names = list(dict.fromkeys(
            sorted(replace) + [r["player"] for r in doc.get("wrong_person", [])]))
    chosen = {}
    if args.from_proposals and PROPOSALS.exists():
        chosen = {r["player"]: r["title"] for r in
                  json.loads(PROPOSALS.read_text(encoding="utf-8"))["proposals"]}
        replace = set()
    result = fix(client, db, names, current_year,
                 replace if (args.from_proposals or not
                             (args.player or args.players_csv)) else None,
                 extra=chosen)
    for row in result["fixed"]:
        print(f"\n{row['player']!r} <- {row['article']!r}  [{row['status']}]")
        print(f"    was: {' | '.join(row['before']) or '(nothing)'}")
        print(f"    now: {' | '.join(row['after']) or '(nothing)'}")
        if row["new_teams"]:
            print(f"    new teams: {', '.join(row['new_teams'])}")
    for row in result["conflicts"]:
        print(f"\n{row['player']!r}: left alone -- {row['conflict']}")
        print(f"    stored:  {' | '.join(row['before']) or '(nothing)'}")
        print(f"    article: {row['article']!r}")
    for row in result["unresolved"]:
        print(f"\n{row['player']!r}: no article found "
              f"({row.get('why') or row.get('tried')})")
    print(f"\nfixed {len(result['fixed'])}, left alone "
          f"{len(result['conflicts'])}, unresolved "
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
