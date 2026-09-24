"""Find the real Wikipedia article for the players resolved to a namesake.

THE PROBLEM. data/players/bio_needs_review.json lists, under
`wikipedia_url_wrong_person`, the records whose stored Wikipedia article is not
about the NBA player at all -- David Duke the Klansman, Jack White the
guitarist, Ace Bailey the ice hockey player, Mike Lynn the Vikings general
manager. The career history in those records was parsed off those articles, so
the Career Map has been showing visitors an empty career, or somebody else's.

WHAT THIS DOES. For each flagged player it assembles the articles that COULD be
his, resolves every one of them to a Wikidata item, and accepts an article only
when Wikidata vouches for it:

    P106 (occupation) = Q3665646 (basketball player)
  AND a birth year within 1 of Basketball-Reference's for this player

Exactly one accepted article -> it is written to
data/players/player_url_overrides.json, which the pipeline then uses forever
(see scripts/player_urls.py). Zero, or more than one -> the player is LISTED
for a human, with everything the run learned about each candidate. It never
guesses: two basketball players of the same name born the same year is not an
answer.

WHERE THE CANDIDATES COME FROM.
  * the pre-filled guess in the `candidates` section of the overrides file,
    tried first -- Wikipedia's conventions are predictable enough to guess
    ("Ron Holland II", "A. J. Green (basketball)") but not to trust
  * "<name> (basketball)"          the standard disambiguator
  * "<name> Jr."                   a suffix the career key dropped
  * "<name> (basketball, born YYYY)" with Basketball-Reference's birth year
  * the Wikipedia search API, "<name> basketball" -- the net under the
    conventions, and the only one that finds a title our spelling cannot
    generate ("Mamadou N'Diaye" from a key that lower-cases the D)

The article the record already points at is excluded: it is the known-wrong one.

A COLLISION IS NOT A FIX. An article that is already some other record's is
refused and listed, because pointing two records at one article makes a
duplicate instead of a repair.

Actions:
    audit     offline. What each flagged record's career history currently
              shows, so the damage is on the record before it is repaired.
              -> logs/wrong_person_career_audit.json
    resolve   the search above. Read-only unless --apply.
              -> logs/player_url_resolution.json (+ --apply writes the
                 overrides file)

Run:
    python3 scripts/resolve_player_urls.py audit
    python3 scripts/resolve_player_urls.py resolve                  # dry run
    python3 scripts/resolve_player_urls.py resolve --apply
    python3 scripts/resolve_player_urls.py resolve --player "Jack White"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import player_urls  # noqa: E402
from fetch_bio_wikidata import (BioFetchError, HttpTransport, WIKIPEDIA_API,  # noqa: E402
                                _label, _row_qid, format_date, load_bref,
                                resolve_qids, QID_CHUNK)
from names import canonical_url, normkey, title_from_url  # noqa: E402
from prerender import franchise_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
REVIEW = ROOT / "data" / "players" / "bio_needs_review.json"
OVERRIDES = player_urls.OVERRIDES
AUDIT_OUT = ROOT / "logs" / "wrong_person_career_audit.json"
RESOLUTION_OUT = ROOT / "logs" / "player_url_resolution.json"

# The acceptance test, spelled out once so the report can quote it.
Q_BASKETBALL_PLAYER = "Q3665646"   # P106 occupation: basketball player
BIRTH_YEAR_TOLERANCE = 1
SEARCH_RESULTS = 8       # Wikipedia search hits considered per player

# Strict by design, and stricter than the bio fetcher's gate: that one accepts
# P106 = basketball player OR P641 = basketball, which lets in a coach whose
# sport is basketball. An article we are about to scrape a PLAYING career off
# has to be a player. P641 is still read, for the report.
STRICT_TEMPLATE = """SELECT ?item ?itemLabel ?itemDescription ?p106 ?p641 ?birth ?bprec ?article WHERE {
  VALUES ?item { %%s }
  BIND(EXISTS { ?item wdt:P106 wd:%s } AS ?p106)
  BIND(EXISTS { ?item wdt:P641 wd:Q5372 } AS ?p641)
  OPTIONAL { ?item p:P569 ?bst . ?bst psv:P569 [ wikibase:timeValue ?birth ;
             wikibase:timePrecision ?bprec ] .
             FILTER NOT EXISTS { ?bst wikibase:rank wikibase:DeprecatedRank } }
  OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}""" % Q_BASKETBALL_PLAYER


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _year(value) -> int | None:
    m = re.match(r"^(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def flagged_players(review: dict) -> list[dict]:
    """The `wikipedia_url_wrong_person` rows, as they stand in the review file."""
    rows = review.get("wikipedia_url_wrong_person") or []
    return [r for r in rows if isinstance(r, dict) and r.get("player")]


# --- the offline audit ------------------------------------------------------
def audit_careers(rows: list[dict], players: list[dict]) -> dict:
    """What each flagged record's career history shows right now.

    Four outcomes, and they are not equally bad. A biography with no club table
    (the swimmer, the Klansman) parses to nothing, so the page shows the player
    no career at all. An article about somebody with a career shows THAT
    career. And a record whose seeded NBA career survived -- _richer() in
    update_careers.py refuses to replace a real history with a thinner one --
    still looks right while being frozen: every daily run re-reads the
    namesake's article, so nothing the player has done since the seed can
    reach the page.
    """
    by_name = {p.get("player"): p for p in players}
    out = []
    for row in sorted(rows, key=lambda r: r["player"]):
        name = row["player"]
        rec = by_name.get(name) or {}
        history = rec.get("career_history") or []
        stints = [{"years": s.get("years", ""), "team": s.get("team", ""),
                   "city": s.get("city", ""), "country": s.get("country", "")}
                  for s in history]
        teams = " \u00b7 ".join(s["team"] for s in stints)
        # A current or historical NBA franchise anywhere in the history. Read
        # through franchise_of so an era name counts: Minneapolis Lakers and
        # St. Louis Hawks are NBA stints, and a bare franchise-name test misses
        # every player who retired before his team moved.
        nba = [s["team"] for s in stints
               if franchise_of(s["team"], s["years"])]
        if not rec:
            kind, verdict = "no_record", "no record in the career database"
        elif not stints:
            kind = "empty"
            verdict = ("EMPTY -- the namesake's article has no club table, so "
                       "the page shows this player no career at all")
        elif not nba:
            kind = "namesake_career"
            verdict = ("THE NAMESAKE'S CAREER -- no NBA franchise appears in "
                       "it, so these teams are almost certainly another "
                       "person's (Mike Lynn's are the Minnesota Vikings')")
        else:
            kind = "frozen"
            verdict = ("FROZEN -- the seeded NBA career survived (it is richer "
                       "than the namesake's article parses to), so the page "
                       "looks right, but every run re-reads the wrong article "
                       "and nothing new can reach it")
        out.append({
            "player": name,
            "stored_wikipedia_url": row.get("wikipedia_url", ""),
            "stored_wikipedia_title": row.get("wikipedia_title", ""),
            "rejected_wikidata_id": row.get("rejected_wikidata_id", ""),
            "status": rec.get("status", ""),
            "current_team": rec.get("current_team", ""),
            "stint_count": len(stints),
            "nba_teams_shown": nba,
            "teams_shown": teams,
            "career_history": stints,
            "kind": kind,
            "verdict": verdict,
        })
    counts = {}
    for r in out:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    return {"generated": _today(), "players": len(out), "counts": counts,
            "audit": out}


# --- candidate articles -----------------------------------------------------
def candidate_titles(row: dict, career: dict, bref_date: str,
                     prefilled: str = "") -> list[str]:
    """Every article that could be this player's, best guess first.

    The article the record already holds is left out: the review file exists
    because that one is somebody else's.
    """
    name = row["player"]
    stored = (row.get("wikipedia_title") or "").strip()
    bases, seen = [], set()
    for base in (name, (career or {}).get("display_name") or ""):
        base = (base or "").strip()
        if base and base not in seen:
            seen.add(base)
            bases.append(base)

    out: list[str] = []
    if prefilled:
        out.append(prefilled)
    for base in bases:
        out.append(f"{base} (basketball)")
        out.append(f"{base} Jr.")
        year = _year(bref_date)
        if year:
            out.append(f"{base} (basketball, born {year})")
    # dedupe, keep order, drop the known-wrong article
    final, done = [], set()
    for t in out:
        t = t.strip()
        key = t.casefold()
        if not t or key in done or t == stored:
            continue
        done.add(key)
        final.append(t)
    return final


def search_titles(transport, name: str, limit: int = SEARCH_RESULTS) -> list[str]:
    """Wikipedia's own answer to "<name> basketball"."""
    data = transport.get_json(WIKIPEDIA_API, {
        "action": "query", "list": "search",
        "srsearch": f"{name} basketball", "srlimit": limit,
        "srnamespace": 0, "format": "json", "formatversion": "2",
    })
    if "error" in data:
        raise BioFetchError(f"wikipedia search API: {data['error']}")
    hits = ((data.get("query") or {}).get("search") or [])
    return [h.get("title", "") for h in hits if h.get("title")]


def fetch_strict(transport, qids: list[str],
                 chunk: int = QID_CHUNK) -> dict[str, dict]:
    """{item ID: what the acceptance test needs to know about it}."""
    out: dict[str, dict] = {}
    qids = [q for q in dict.fromkeys(qids) if q]
    for i in range(0, len(qids), chunk):
        part = qids[i:i + chunk]
        data = transport.sparql(STRICT_TEMPLATE % " ".join(f"wd:{q}"
                                                           for q in part))
        rows = (data.get("results") or {}).get("bindings")
        if rows is None:
            raise BioFetchError("query.wikidata.org: no results in the response")
        for row in rows:
            qid = _row_qid(row)
            if not qid:
                continue
            rec = out.setdefault(qid, {"qid": qid, "label": "",
                                       "description": "", "p106": False,
                                       "p641": False, "birth_date": None,
                                       "article": ""})
            rec["label"] = rec["label"] or _label(row, "itemLabel")
            rec["description"] = rec["description"] or _label(row,
                                                              "itemDescription")
            if ((row.get("p106") or {}).get("value") or "").lower() == "true":
                rec["p106"] = True
            if ((row.get("p641") or {}).get("value") or "").lower() == "true":
                rec["p641"] = True
            rec["birth_date"] = rec["birth_date"] or format_date(
                (row.get("birth") or {}).get("value"),
                (row.get("bprec") or {}).get("value"))
            rec["article"] = rec["article"] or (
                (row.get("article") or {}).get("value") or "")
    return out


def _article_url(item: dict, fallback_title: str) -> str:
    """The canonical article URL for an accepted item.

    The Wikidata sitelink is the authority (it is the article title as
    Wikipedia holds it today, not the redirect we happened to ask for); it is
    re-built through canonical_url so it is spelled the way every other URL in
    the database is.
    """
    title = title_from_url(item.get("article") or "") or fallback_title
    return canonical_url(title)


def why_rejected(item: dict | None, bref_date: str) -> str:
    if item is None:
        return "no Wikidata item"
    if not item.get("p106"):
        got = item.get("description") or item.get("label") or "no description"
        return f"not a basketball player on Wikidata ({got})"
    wd_year, bref_year = _year(item.get("birth_date")), _year(bref_date)
    if bref_year is None:
        return "no Basketball-Reference birth date to check the item against"
    if wd_year is None:
        return "the item has no birth date, so it cannot be checked"
    return (f"born {wd_year}, Basketball-Reference says {bref_year} "
            f"({abs(wd_year - bref_year)} years apart)")


def accepts(item: dict | None, bref_date: str) -> bool:
    """P106 = basketball player, and born when the NBA player was."""
    if not item or not item.get("p106"):
        return False
    wd_year, bref_year = _year(item.get("birth_date")), _year(bref_date)
    if wd_year is None or bref_year is None:
        return False
    return abs(wd_year - bref_year) <= BIRTH_YEAR_TOLERANCE


def resolve(transport, rows: list[dict], players: list[dict],
            prefills: dict[str, dict], *,
            search_limit: int = SEARCH_RESULTS) -> dict:
    """Work out each flagged player's real article. Writes nothing."""
    by_name = {p.get("player"): p for p in players}
    # Which record already owns which article -- the collision guard.
    owner_of = {}
    for p in players:
        t = title_from_url(p.get("wikipedia_url") or "")
        if t:
            owner_of.setdefault(t.casefold(), p["player"])

    bref = load_bref(transport)

    # One pass to collect every title, so the title -> item lookup is batched
    # across all players instead of per player.
    per_player: dict[str, dict] = {}
    all_titles: list[str] = []
    for row in rows:
        name = row["player"]
        bref_date = bref.get(normkey(name), "")
        prefilled = title_from_url(
            (prefills.get(name) or {}).get("wikipedia_url") or "")
        titles = candidate_titles(row, by_name.get(name) or {}, bref_date,
                                  prefilled)
        found = search_titles(transport, name, search_limit)
        stored = (row.get("wikipedia_title") or "").strip()
        for t in found:
            if t != stored and t.casefold() not in {x.casefold() for x in titles}:
                titles.append(t)
        per_player[name] = {"row": row, "bref_date": bref_date,
                            "prefilled": prefilled, "titles": titles,
                            "searched": found}
        all_titles.extend(titles)

    title_to_qid = resolve_qids(transport, all_titles)
    items = fetch_strict(transport, list(title_to_qid.values()))

    resolved, unresolved = {}, []
    for name, info in per_player.items():
        row, bref_date = info["row"], info["bref_date"]
        rejected_qid = row.get("rejected_wikidata_id") or ""
        considered, accepted = [], {}
        for title in info["titles"]:
            qid = title_to_qid.get(title, "")
            item = items.get(qid) if qid else None
            ok = bool(qid) and qid != rejected_qid and accepts(item, bref_date)
            considered.append({
                "title": title,
                "wikidata_id": qid,
                "label": (item or {}).get("label", ""),
                "description": (item or {}).get("description", ""),
                "p106_basketball_player": bool((item or {}).get("p106")),
                "birth_date": (item or {}).get("birth_date") or "",
                "accepted": ok,
                "why_not": "" if ok else (
                    "no article" if not qid else
                    "this is the namesake the record already pointed at"
                    if qid == rejected_qid else why_rejected(item, bref_date)),
            })
            if ok:
                accepted.setdefault(qid, title)

        base = {"player": name,
                "basketball_reference_birth_date": bref_date,
                "stored_wikipedia_url": row.get("wikipedia_url", ""),
                "rejected_wikidata_id": rejected_qid,
                "searched": info["searched"],
                "candidates": considered}

        if len(accepted) != 1:
            base["why"] = ("no candidate article is a basketball player born "
                           "in the right year"
                           if not accepted else
                           f"{len(accepted)} different basketball players of "
                           f"this name were born in the right year, so the "
                           f"article cannot be chosen automatically")
            unresolved.append(base)
            continue

        qid, title = next(iter(accepted.items()))
        item = items[qid]
        url = _article_url(item, title)
        holder = owner_of.get((title_from_url(url) or "").casefold())
        if holder and holder != name:
            base["why"] = (f"that article is already {holder}'s record; "
                           f"pointing both at it would make a duplicate")
            unresolved.append(base)
            continue

        resolved[name] = {
            "wikipedia_url": url,
            "wikidata_id": qid,
            "wikidata_label": item.get("label", ""),
            "wikidata_description": item.get("description", ""),
            "wikidata_birth_date": item.get("birth_date") or "",
            "basketball_reference_birth_date": bref_date,
            "replaces": row.get("wikipedia_url", ""),
            "replaces_wikidata_id": rejected_qid,
            "matched_candidate": title,
            "verified": _today(),
            "verified_by": ("P106 = %s and a birth year within %d of "
                            "Basketball-Reference's"
                            % (Q_BASKETBALL_PLAYER, BIRTH_YEAR_TOLERANCE)),
        }

    return {"generated": _today(),
            "acceptance_test": {
                "occupation": f"P106 = {Q_BASKETBALL_PLAYER} (basketball player)",
                "birth_year_tolerance": BIRTH_YEAR_TOLERANCE,
                "cross_check": "Basketball-Reference career info CSV"},
            "counts": {"flagged": len(rows), "verified": len(resolved),
                       "needs_a_human": len(unresolved)},
            "verified": resolved,
            "needs_a_human": unresolved}


def apply_overrides(report: dict, path: Path = OVERRIDES) -> dict:
    """Fold the verified articles into the overrides file.

    A verified player's pre-filled candidate is removed: it has served its
    purpose and leaving it would suggest the answer is still open.
    """
    doc = _read_json(path, {}) or {}
    if not isinstance(doc, dict):
        doc = {}
    overrides = doc.get("overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    cands = doc.get("candidates")
    if not isinstance(cands, dict):
        cands = {}
    for name, rec in report.get("verified", {}).items():
        overrides[name] = rec
        cands.pop(name, None)
    doc["overrides"] = dict(sorted(overrides.items()))
    doc["candidates"] = dict(sorted(cands.items()))
    doc["generated"] = _today()
    doc.setdefault("verified_by", "scripts/resolve_player_urls.py")
    _write_json(path, doc)
    player_urls.reset_cache()
    return doc


# --- reporting --------------------------------------------------------------
LABELS = {
    "empty": "no career at all (the namesake's article has no club table)",
    "namesake_career": "the namesake's own career (no NBA franchise in it)",
    "frozen": "an NBA career that survived, but frozen on the wrong article",
    "no_record": "no record in the career database",
}


def print_audit(doc: dict) -> None:
    print(f"## What the {doc['players']} flagged records show today\n")
    for kind, label in LABELS.items():
        n = doc["counts"].get(kind)
        if n:
            print(f"- **{label}**: {n}")
    print()
    print("| Player | Stored article | Stints | What the page shows | Verdict |")
    print("| --- | --- | --- | --- | --- |")
    for r in doc["audit"]:
        teams = r["teams_shown"] or "_nothing_"
        if len(teams) > 90:
            teams = teams[:87] + "..."
        print(f"| {r['player']} | {r['stored_wikipedia_title']} | "
              f"{r['stint_count']} | {teams} | {r['kind']} |")


def print_resolution(report: dict) -> None:
    c = report["counts"]
    print("## Wikipedia URL overrides\n")
    print(f"- **flagged**: {c['flagged']}")
    print(f"- **verified**: {c['verified']}")
    print(f"- **needs a human**: {c['needs_a_human']}\n")
    if report["verified"]:
        print("### Verified\n")
        print("| Player | Article | Wikidata | Born (WD / BR) |")
        print("| --- | --- | --- | --- |")
        for name, r in sorted(report["verified"].items()):
            print(f"| {name} | {title_from_url(r['wikipedia_url'])} | "
                  f"{r['wikidata_id']} | {r['wikidata_birth_date']} / "
                  f"{r['basketball_reference_birth_date']} |")
        print()
    if report["needs_a_human"]:
        print("### Needs a human\n")
        for r in report["needs_a_human"]:
            print(f"- **{r['player']}** (Basketball-Reference birth date: "
                  f"{r['basketball_reference_birth_date'] or 'none'}) — "
                  f"{r['why']}")
            for cand in r["candidates"]:
                mark = "accepted" if cand["accepted"] else cand["why_not"]
                print(f"  - `{cand['title']}` → "
                      f"{cand['wikidata_id'] or '(no item)'} — {mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["audit", "resolve"])
    ap.add_argument("--player", action="append", default=[],
                    help="only these players (default: everything flagged)")
    ap.add_argument("--players-csv", default="",
                    help="same as --player, comma-separated (for CI inputs)")
    ap.add_argument("--apply", action="store_true",
                    help="write the verified articles to the overrides file")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--search-limit", type=int, default=SEARCH_RESULTS)
    args = ap.parse_args()

    review = _read_json(REVIEW, {})
    players = _read_json(CAREERS, [])
    if not isinstance(players, list):
        print("data/players/nba_players_careers.json is not a list", file=sys.stderr)
        return 1
    rows = flagged_players(review)
    wanted = [n.strip() for n in
              (args.player + args.players_csv.split(",")) if n.strip()]
    if wanted:
        keys = {normkey(n) for n in wanted}
        rows = [r for r in rows if normkey(r["player"]) in keys]
    if not rows:
        print("nothing flagged in data/players/bio_needs_review.json "
              "under wikipedia_url_wrong_person")
        return 0

    if args.action == "audit":
        doc = audit_careers(rows, players)
        _write_json(AUDIT_OUT, doc)
        print_audit(doc)
        print(f"\nwrote {AUDIT_OUT.relative_to(ROOT)}")
        return 0

    transport = HttpTransport(delay=args.delay)
    try:
        report = resolve(transport, rows, players,
                         player_urls.candidates(),
                         search_limit=args.search_limit)
    except BioFetchError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    _write_json(RESOLUTION_OUT, report)
    print_resolution(report)
    if args.apply and report["verified"]:
        apply_overrides(report)
        print(f"\nwrote {len(report['verified'])} override(s) to "
              f"{OVERRIDES.relative_to(ROOT)}")
    elif args.apply:
        print("\nnothing verified, so the overrides file is unchanged")
    else:
        print("\ndry run: the overrides file was not written (pass --apply)")
    print(f"wrote {RESOLUTION_OUT.relative_to(ROOT)}  "
          f"({transport.requests} requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
