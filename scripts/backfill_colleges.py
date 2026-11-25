"""Fill the `college` field for records that have none, from the player's own
Wikipedia article.

WHY. `college` comes from the Wikipedia infobox, and only records the pipeline
has re-parsed carry it. Most retired players were imported once and are never
re-read, so a record like Allen Iverson's can sit without a college while his
article lists Georgetown. Those records lose the "from <college> to <last
team>" close of their meta description (see build_meta_description in
scripts/prerender.py).

SOURCE. The Career Map's own source and parser: the article the pipeline reads
for this player (the curated one in data/players/player_url_overrides.json
first, then the record's `wikipedia_url`), fetched through the MediaWiki API
50 articles a request, and the infobox's `college` field cleaned by
scripts/wiki_parser.py exactly as the nightly parse cleans it. nba-player-data
has no college field, so there is nothing to take from it. No other source is
used.

WHICH ARTICLES ARE TRUSTED. Not every stored article is the player's:
  * a record listed under `wikipedia_url_wrong_person` in
    data/players/bio_needs_review.json points at a namesake, and is skipped
    unless it has a curated override;
  * the article must carry {{Infobox basketball biography}};
  * when the infobox has a birth date and the player has an NBA birth date in
    data/players/player_bio.json (nba-player-data's bio.json, then NBA.com),
    the two years must be within one of each other -- the same identity check
    the bio fetcher uses.
An infobox with no college (straight from high school, international) leaves
the record empty on purpose.

A record that already has a college is never touched, and the nightly keeps a
stored college when a fresh Wikipedia parse has none (merge_player in
update_careers.py), so this does not get undone.

Run:  python3 scripts/backfill_colleges.py [--apply] [--max-requests N]
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
import wiki_parser  # noqa: E402
from names import title_from_url  # noqa: E402
from wikipedia_api import RequestBudgetExceeded, WikipediaClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
BIO = ROOT / "data" / "players" / "player_bio.json"
REVIEW = ROOT / "data" / "players" / "bio_needs_review.json"
LOG = ROOT / "logs" / "college_backfill.json"
TITLE_BATCH = 50
BIRTH_YEAR_TOLERANCE = 1
NBA_SOURCES = ("nba-player-data", "nba.com")


def has_college(p: dict) -> bool:
    return bool((p.get("college") or "").strip())


def _year(value) -> int | None:
    m = re.match(r"^(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def article_title(p: dict, wrong_person: set[str]) -> str:
    """The article to read this player's college off, or "" when none is his."""
    forced = player_urls.override_title(p.get("player") or "")
    if forced:
        return forced
    if p.get("player") in wrong_person:
        return ""
    return title_from_url(p.get("wikipedia_url") or "") or ""


def college_from_article(text: str, nba_birth: str) -> tuple[str, str]:
    """(college, why not) from one article's wikitext."""
    fields = wiki_parser.parse_infobox_fields(text or "")
    if not fields:
        return "", "no basketball infobox"
    born = _year(wiki_parser._parse_date_template(fields.get("birth_date", "")))
    nba_year = _year(nba_birth)
    if born and nba_year and abs(born - nba_year) > BIRTH_YEAR_TOLERANCE:
        return "", f"infobox born {born}, NBA birth date {nba_year}"
    college = wiki_parser._clean_text(fields.get("college", ""))
    return college, ("" if college else "the infobox lists no college")


def fetch_wikitexts(client, titles: list[str]) -> dict[str, str]:
    """{requested title: wikitext}, following redirects, 50 titles a request."""
    out: dict[str, str] = {}
    titles = [t for t in dict.fromkeys(titles) if t]
    for i in range(0, len(titles), TITLE_BATCH):
        chunk = titles[i:i + TITLE_BATCH]
        data = client._get({"action": "query", "prop": "revisions",
                            "rvprop": "content", "rvslots": "main",
                            "titles": "|".join(chunk), "redirects": 1})
        q = data.get("query", {})
        hop = {}
        for kind in ("normalized", "redirects"):
            for h in q.get(kind, []) or []:
                hop[h["from"]] = h["to"]
        text_of = {}
        for page in q.get("pages", []) or []:
            try:
                text_of[page["title"]] = page["revisions"][0]["slots"]["main"]["content"]
            except (KeyError, IndexError, TypeError):
                continue
        for t in chunk:
            cur, seen = t, set()
            while cur in hop and cur not in seen:
                seen.add(cur)
                cur = hop[cur]
            if cur in text_of:
                out[t] = text_of[cur]
    return out


def backfill(players: list[dict], bio: dict, wrong_person: set[str],
             fetch) -> dict:
    """Work out each college. `fetch(titles) -> {title: wikitext}`; when it
    raises RequestBudgetExceeded or a network error the rest wait for the
    next run."""
    todo, no_article = [], []
    for p in players:
        if has_college(p):
            continue
        title = article_title(p, wrong_person)
        if title:
            todo.append((p, title))
        else:
            no_article.append({"player": p["player"],
                               "why": "no article known to be his"})
    texts: dict[str, str] = {}
    titles = [t for _, t in todo]
    waiting = []
    for i in range(0, len(titles), TITLE_BATCH):
        try:
            texts.update(fetch(titles[i:i + TITLE_BATCH]))
        except (RequestBudgetExceeded, OSError) as exc:
            # The budget, or Wikipedia refusing (a 429): keep what was read,
            # and the rest waits for the next run.
            if not isinstance(exc, RequestBudgetExceeded):
                print(f"::warning::Wikipedia stopped answering, the rest waits "
                      f"for the next run: {exc}", file=sys.stderr)
            waiting = titles[i:]
            break
    waiting_set = set(waiting)

    filled, no_college, skipped = [], [], []
    for p, title in todo:
        if title in waiting_set:
            continue
        if title not in texts:
            skipped.append({"player": p["player"], "article": title,
                            "why": "article not found"})
            continue
        rec = bio.get(p["player"]) or {}
        nba_birth = (rec.get("birth_date") if rec.get("source") in NBA_SOURCES
                     else "")
        college, why = college_from_article(texts[title], nba_birth or "")
        if college:
            filled.append({"player": p["player"], "college": college,
                           "article": title})
        elif why == "the infobox lists no college":
            no_college.append({"player": p["player"], "article": title})
        else:
            skipped.append({"player": p["player"], "article": title, "why": why})
    return {"filled": filled, "no_college_in_infobox": no_college,
            "skipped": skipped + no_article,
            "not_read_yet": sorted(p["player"] for p, t in todo
                                   if t in waiting_set)}


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-requests", type=int, default=200,
                    help="Wikipedia request budget (50 articles a request)")
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    bio = _read_json(BIO, {})
    review = _read_json(REVIEW, {})
    wrong_person = {r.get("player") for r in
                    (review.get("wikipedia_url_wrong_person") or [])}
    before = sum(1 for p in players if not has_college(p))

    client = WikipediaClient(delay=args.delay, max_requests=args.max_requests)
    result = backfill(players, bio, wrong_person,
                      lambda titles: fetch_wikitexts(client, titles))
    got = {f["player"]: f["college"] for f in result["filled"]}
    if args.apply:
        for p in players:
            if p["player"] in got and not has_college(p):
                p["college"] = got[p["player"]]
    after = before - len(got)

    print(f"records: {len(players)}")
    print(f"no college BEFORE: {before}")
    print(f"  filled from the player's Wikipedia infobox: {len(got)}")
    print(f"  infobox lists no college:                    "
          f"{len(result['no_college_in_infobox'])}")
    print(f"  skipped (no article of his / wrong person):   "
          f"{len(result['skipped'])}")
    print(f"  not read this run (request budget):           "
          f"{len(result['not_read_yet'])}")
    print(f"no college AFTER{'' if args.apply else ' (dry run)'}: {after}")
    print(f"Wikipedia requests: {client.requests_made}")

    if args.apply:
        CAREERS.write_text(json.dumps(players, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        log = _read_json(LOG, {})
        # `filled` adds up across runs (a budget-limited run fills a slice);
        # the other lists describe this run.
        result["filled"] = list({f["player"]: f for f in
                                 (log.get("filled") or []) + result["filled"]}.values())
        log.update({
            "source": "the player's own Wikipedia article (infobox `college`, "
                      "scripts/wiki_parser.py)",
            "generated": dt.date.today().isoformat(),
            "before": before, "after": after, **result})
        LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"written: {CAREERS.relative_to(ROOT)}, {LOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
