"""Birth and death facts for every player, from Wikidata.

WHAT IT WRITES. data/players/player_bio.json, keyed by the player name the
career database uses:

    "Kobe Bryant": {
      "birth_date": "1978-08-23",       # date precision is kept: a year-only
      "death_date": "2020-01-26",       # Wikidata date stays "1947", never
      "birth_place": "Philadelphia",    # becomes "1947-01-01"
      "death_place": "Calabasas",
      "wikidata_id": "Q41421",
      "source": "wikidata",
      "checked": "2026-09-24"
    }

HOW IT GETS THERE. Two hops, both batched:

  1. en.wikipedia.org/w/api.php?action=query&prop=pageprops — 50 article
     titles a request, following redirects, reading the wikibase_item each
     article carries. That is the only reliable Wikipedia -> Wikidata link.
  2. query.wikidata.org/sparql — one query per chunk of item IDs, asking for
     P569 (birth date), P570 (death date), P19 (place of birth) and P20
     (place of death). The dates are read off the statement's VALUE NODE
     (psv:), not the truthy wdt: shortcut, because only the value node
     carries wikibase:timePrecision -- and without the precision a date
     Wikidata only knows to the year comes back looking like January 1st.

     SPARQL rather than wbgetentities: a full entity is hundreds of KB of
     claims we would throw away, and the label service hands over the place
     names in the same round trip.

WHAT IT REFUSES TO DO. If the API cannot be reached -- DNS, a proxy, an
outage, a 429 -- the run prints the failure and exits non-zero WITHOUT
writing. An empty player_bio.json would quietly strip the birth line off
every page on the site, which is a worse outcome than a red workflow run.

INCREMENTAL BY DEFAULT. A run resolves the players missing from
player_bio.json, and re-checks living players whose record is more than
--refresh-days old so a death is picked up within the week. --refresh-limit
caps how many of those a single run re-checks, which spreads the sweep over
several days instead of re-reading five thousand records every Monday.

NOTHING IS OVERWRITTEN SILENTLY. A re-check fills blanks and updates
death_date/death_place; it never replaces a birth date that is already on
record. If Wikidata now says something different, the old and new values go
to data/players/bio_needs_review.json and the record keeps what it had. Pass
--full to re-fetch everyone and let the fresh values win.

CROSS-CHECK. Birth dates are compared against Basketball-Reference (via
sumitrodatta/bball-reference-datasets). Disagreements, and players with no
Wikidata birth date at all, are written to data/players/bio_needs_review.json
for a human to settle. The cross-check never edits player_bio.json.

Run:
    python3 scripts/fetch_bio_wikidata.py                 # incremental
    python3 scripts/fetch_bio_wikidata.py --limit 500     # bounded backfill
    python3 scripts/fetch_bio_wikidata.py --full          # re-fetch everyone
    python3 scripts/fetch_bio_wikidata.py --fixtures tests/fixtures  # offline
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import normkey, title_from_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
BIO = ROOT / "data" / "players" / "player_bio.json"
REVIEW = ROOT / "data" / "players" / "bio_needs_review.json"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
# Basketball-Reference career info, mirrored as a CSV. One row per player,
# with the birth date we cross-check against.
BREF_CSV = ("https://raw.githubusercontent.com/sumitrodatta/"
            "bball-reference-datasets/master/Data/Player%20Career%20Info.csv")

# Wikimedia's User-Agent policy (https://foundation.wikimedia.org/wiki/
# Policy:Wikimedia_Foundation_User-Agent_Policy) wants a descriptive agent
# naming the tool and a way to reach whoever runs it. The Wikidata Query
# Service enforces it harder than the Wikipedia API does: a generic agent is
# answered with a 403.
USER_AGENT = ("nba-career-map-bio/1.0 (https://hoopsmatic.com/nba-career-map; "
              "https://github.com/jsierrahoopshype/nba-career-map; "
              "player birth/death facts) python-urllib")

TITLE_BATCH = 50      # the pageprops API's own cap
QID_CHUNK = 250       # item IDs per SPARQL query
REFRESH_DAYS = 7
REFRESH_LIMIT = 1200  # living players re-checked for a death per run

# wikibase:timePrecision: 11 = day, 10 = month, 9 = year, 8 = decade.
PREC_DAY, PREC_MONTH, PREC_YEAR = 11, 10, 9

_QID = re.compile(r"^Q\d+$")


class BioFetchError(RuntimeError):
    """The source could not be read. Never swallowed: the run fails."""


# --- transports -------------------------------------------------------------
class HttpTransport:
    """Live HTTP, with the polite delay every Wikimedia client owes."""

    def __init__(self, delay: float = 1.0, timeout: int = 60):
        self.delay, self.timeout = delay, timeout
        self._last = 0.0
        self.requests = 0

    def _throttle(self) -> None:
        if self._last:
            gap = time.time() - self._last
            if gap < self.delay:
                time.sleep(self.delay - gap)

    def _open(self, req: urllib.request.Request) -> bytes:
        self._throttle()
        self.requests += 1
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            raise BioFetchError(f"{req.full_url.split('?')[0]}: {exc}") from exc
        finally:
            self._last = time.time()

    def get_json(self, url: str, params: dict) -> dict:
        full = url + "?" + urllib.parse.urlencode(params)
        raw = self._open(urllib.request.Request(full))
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise BioFetchError(f"{url}: response was not JSON ({exc})") from exc

    def sparql(self, query: str) -> dict:
        # POST: a 5,000-player VALUES clause does not fit in a URL, and the
        # query service prefers it.
        body = urllib.parse.urlencode({"query": query}).encode("utf-8")
        req = urllib.request.Request(SPARQL_ENDPOINT, data=body)
        req.add_header("Accept", "application/sparql-results+json")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        raw = self._open(req)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise BioFetchError(
                f"query.wikidata.org: response was not JSON ({exc})") from exc

    def get_text(self, url: str) -> str:
        return self._open(urllib.request.Request(url)).decode("utf-8")


class FixtureTransport:
    """Saved responses, for tests and offline dry runs.

    Reads <dir>/pageprops-1.json, pageprops-2.json ... in call order, the
    same for sparql-N.json, and <dir>/bref.csv for the cross-check. Running
    past the saved responses is an error rather than an empty answer, so a
    test cannot pass by accident.
    """

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self._n = {"pageprops": 0, "sparql": 0}
        self.requests = 0

    def _next(self, kind: str) -> dict:
        self._n[kind] += 1
        self.requests += 1
        path = self.dir / f"{kind}-{self._n[kind]}.json"
        if not path.exists():
            raise BioFetchError(f"no fixture {path.name} in {self.dir}")
        return json.loads(path.read_text(encoding="utf-8"))

    def get_json(self, url: str, params: dict) -> dict:
        return self._next("pageprops")

    def sparql(self, query: str) -> dict:
        return self._next("sparql")

    def get_text(self, url: str) -> str:
        path = self.dir / "bref.csv"
        if not path.exists():
            raise BioFetchError(f"no fixture {path} for the cross-check")
        return path.read_text(encoding="utf-8")


# --- wikipedia -> wikidata --------------------------------------------------
def resolve_qids(transport, titles: list[str],
                 batch: int = TITLE_BATCH) -> dict[str, str]:
    """{requested title: item ID} for the titles that carry one.

    Redirects and normalization are followed, so the answer is keyed back to
    the title that was asked for.
    """
    out: dict[str, str] = {}
    titles = [t for t in dict.fromkeys(titles) if t]
    for i in range(0, len(titles), batch):
        chunk = titles[i:i + batch]
        data = transport.get_json(WIKIPEDIA_API, {
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "titles": "|".join(chunk), "redirects": 1,
            "format": "json", "formatversion": "2",
        })
        if "error" in data:
            raise BioFetchError(f"wikipedia API: {data['error']}")
        q = data.get("query", {})
        hop: dict[str, str] = {}
        for kind in ("normalized", "redirects"):
            for h in q.get(kind, []) or []:
                hop[h["from"]] = h["to"]
        by_title = {p.get("title"): (p.get("pageprops") or {}).get("wikibase_item")
                    for p in q.get("pages", []) or [] if not p.get("missing")}
        for t in chunk:
            cur, seen = t, set()
            while cur in hop and cur not in seen:
                seen.add(cur)
                cur = hop[cur]
            qid = by_title.get(cur)
            if qid and _QID.match(qid):
                out[t] = qid
    return out


# --- wikidata facts ---------------------------------------------------------
SPARQL_TEMPLATE = """SELECT ?item ?birth ?bprec ?death ?dprec ?bplaceLabel ?dplaceLabel WHERE {
  VALUES ?item { %s }
  OPTIONAL { ?item p:P569/psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?bprec ] }
  OPTIONAL { ?item p:P570/psv:P570 [ wikibase:timeValue ?death ; wikibase:timePrecision ?dprec ] }
  OPTIONAL { ?item wdt:P19 ?bplace }
  OPTIONAL { ?item wdt:P20 ?dplace }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
}"""


def format_date(value: str, precision) -> str | None:
    """Wikidata time value + precision -> the string we store.

    Precision is the whole point: day -> "1978-08-23", month -> "1978-08",
    year (or coarser) -> "1978". A year-only date must never be dressed up as
    January 1st, on the page or in the JSON-LD.
    """
    if not value:
        return None
    m = re.match(r"^([+-]?)(\d{4,})-(\d{2})-(\d{2})", str(value))
    if not m or m.group(1) == "-":   # BCE: not a thing for NBA players
        return None
    sign, year, month, day = m.groups()
    try:
        prec = int(precision)
    except (TypeError, ValueError):
        prec = PREC_DAY
    if prec >= PREC_DAY and month != "00" and day != "00":
        return f"{year}-{month}-{day}"
    if prec == PREC_MONTH and month != "00":
        return f"{year}-{month}"
    if prec >= PREC_YEAR:
        return year
    return None   # decade or coarser: not a date worth printing


def _label(row: dict, key: str) -> str:
    """A label from the results row, dropping the bare-QID fallback the
    label service returns when an item has no English label."""
    val = ((row.get(key) or {}).get("value") or "").strip()
    return "" if _QID.match(val) else val


def fetch_facts(transport, qids: list[str],
                chunk: int = QID_CHUNK) -> dict[str, dict]:
    """{item ID: {birth_date, death_date, birth_place, death_place}}."""
    out: dict[str, dict] = {}
    qids = [q for q in dict.fromkeys(qids) if q and _QID.match(q)]
    for i in range(0, len(qids), chunk):
        part = qids[i:i + chunk]
        query = SPARQL_TEMPLATE % " ".join(f"wd:{q}" for q in part)
        data = transport.sparql(query)
        rows = (data.get("results") or {}).get("bindings")
        if rows is None:
            raise BioFetchError("query.wikidata.org: no results in the response")
        for row in rows:
            uri = ((row.get("item") or {}).get("value") or "")
            qid = uri.rsplit("/", 1)[-1]
            if not _QID.match(qid):
                continue
            rec = out.setdefault(qid, {"birth_date": None, "death_date": None,
                                       "birth_place": "", "death_place": ""})
            birth = format_date((row.get("birth") or {}).get("value"),
                                (row.get("bprec") or {}).get("value"))
            death = format_date((row.get("death") or {}).get("value"),
                                (row.get("dprec") or {}).get("value"))
            # An item can carry several statements for the same property
            # (disputed dates, deprecated ranks). First non-empty wins; the
            # cross-check below is what catches a wrong one.
            rec["birth_date"] = rec["birth_date"] or birth
            rec["death_date"] = rec["death_date"] or death
            rec["birth_place"] = rec["birth_place"] or _label(row, "bplaceLabel")
            rec["death_place"] = rec["death_place"] or _label(row, "dplaceLabel")
    return out


# --- selection --------------------------------------------------------------
def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _days_since(stamp: str) -> int:
    try:
        then = dt.date.fromisoformat(str(stamp)[:10])
    except ValueError:
        return 10 ** 6
    return (dt.datetime.now(dt.timezone.utc).date() - then).days


def select_targets(players: list, bio: dict, *, full: bool, limit: int | None,
                   refresh_days: int, refresh_limit: int) -> tuple[list, list]:
    """(new players to resolve, living players due a death re-check).

    Both lists hold the career-database records themselves, so the caller has
    the Wikipedia URL as well as the name.
    """
    if full:
        rows = players if limit is None else players[:limit]
        return list(rows), []

    missing = [p for p in players if p.get("player") not in bio]
    if limit is not None:
        missing = missing[:limit]

    due = []
    for p in players:
        rec = bio.get(p.get("player"))
        if not rec or rec.get("death_date"):
            continue          # unknown yet (above), or a death already on file
        if _days_since(rec.get("checked", "")) >= refresh_days:
            due.append(p)
    due.sort(key=lambda p: bio[p["player"]].get("checked", ""))
    return missing, due[:refresh_limit]


def _title_of(player: dict) -> str:
    url = player.get("wikipedia_url") or ""
    return title_from_url(url) or (player.get("display_name")
                                   or player.get("player") or "")


# --- merge ------------------------------------------------------------------
def merge(bio: dict, player: dict, qid: str, facts: dict, *,
          full: bool, changes: list) -> None:
    """Fold one player's fresh facts into the stored record.

    A blank is filled and a death is always taken (that is the fact this
    re-reads for). An existing birth date/place is never quietly replaced:
    the disagreement is recorded and the stored value stands, unless --full
    says to take the fresh one. Returns True if anything but the `checked`
    stamp moved.
    """
    name = player["player"]
    rec = bio.get(name) or {}
    before = dict(rec)
    fresh = {
        "birth_date": facts.get("birth_date"),
        "death_date": facts.get("death_date"),
        "birth_place": facts.get("birth_place") or "",
        "death_place": facts.get("death_place") or "",
    }
    for field in ("birth_date", "death_date", "birth_place", "death_place"):
        new, old = fresh[field], rec.get(field)
        if not new:
            continue
        if not old or full or field.startswith("death_"):
            rec[field] = new
        elif new != old:
            changes.append({"player": name, "field": field,
                            "stored": old, "wikidata": new,
                            "wikidata_id": qid,
                            "note": "kept the stored value; "
                                    "re-run with --full to take Wikidata's"})
    rec.setdefault("birth_date", None)
    rec.setdefault("death_date", None)
    rec.setdefault("birth_place", "")
    rec.setdefault("death_place", "")
    rec["wikidata_id"] = qid
    rec["source"] = "wikidata" if qid else "unresolved"
    rec["checked"] = _today()
    bio[name] = rec
    return rec != before


# --- cross-check ------------------------------------------------------------
# The CSV writes an unknown birth date as R's "NA", which is not a date and
# must never be compared against one.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_bref(transport) -> dict[str, str]:
    """{normalized player name: birth date} from Basketball-Reference."""
    text = transport.get_text(BREF_CSV)
    out: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("player") or "").strip()
        born = (row.get("birth_date") or "").strip()
        if not name or not _ISO_DATE.fullmatch(born):
            continue
        # Two players can share a normalized name; a row that disagrees with
        # one already seen is dropped rather than guessed at.
        key = normkey(name)
        if key in out and out[key] != born:
            out[key] = ""
        else:
            out.setdefault(key, born)
    return {k: v for k, v in out.items() if v}


def same_date(ours: str, theirs: str) -> bool:
    """Compare at the precision we actually hold.

    A Wikidata date known only to the year agrees with any Basketball-
    Reference date in that year -- it is less precise, not wrong.
    """
    if not ours or not theirs:
        return True
    ours, theirs = ours.strip(), theirs.strip()
    return theirs[:len(ours)] == ours


def cross_check(bio: dict, bref: dict[str, str]) -> dict:
    mismatches, missing = [], []
    for name, rec in sorted(bio.items()):
        ours = rec.get("birth_date")
        theirs = bref.get(normkey(name))
        if not ours:
            missing.append({"player": name,
                            "wikidata_id": rec.get("wikidata_id") or "",
                            "basketball_reference": theirs or ""})
            continue
        if theirs and not same_date(ours, theirs):
            mismatches.append({"player": name, "wikidata": ours,
                               "basketball_reference": theirs,
                               "wikidata_id": rec.get("wikidata_id") or ""})
    return {"mismatches": mismatches, "missing_birth_date": missing}


# --- io ---------------------------------------------------------------------
def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                                     sort_keys=True) + "\n", encoding="utf-8")


def coverage(bio: dict) -> dict:
    return {
        "players": len(bio),
        "with_birth_date": sum(1 for r in bio.values() if r.get("birth_date")),
        "with_death_date": sum(1 for r in bio.values() if r.get("death_date")),
        "with_birth_place": sum(1 for r in bio.values() if r.get("birth_place")),
        "with_death_place": sum(1 for r in bio.values() if r.get("death_place")),
        "year_only_birth_date": sum(1 for r in bio.values()
                                    if len(str(r.get("birth_date") or "")) == 4),
    }


def run(args, transport) -> dict:
    players = _read_json(args.careers, [])
    if not players:
        raise BioFetchError(f"{args.careers}: no players to work from")
    bio = _read_json(args.out, {})
    if not isinstance(bio, dict):
        raise BioFetchError(f"{args.out}: expected an object keyed by player name")

    new, due = select_targets(players, bio, full=args.full, limit=args.limit,
                              refresh_days=args.refresh_days,
                              refresh_limit=args.refresh_limit)
    targets = new + due
    print(f"players in the database: {len(players)}")
    print(f"already on file: {len(bio)}")
    print(f"to resolve: {len(new)} new, {len(due)} living re-checks")

    changes: list = []
    if not targets:
        print("nothing to fetch this run")
    else:
        # Reuse the item ID we already resolved; only the ones we have never
        # seen cost a Wikipedia round trip.
        known = {p["player"]: bio[p["player"]]["wikidata_id"]
                 for p in targets
                 if bio.get(p["player"], {}).get("wikidata_id")}
        unknown = [p for p in targets if p["player"] not in known]
        title_of = {p["player"]: _title_of(p) for p in unknown}
        resolved = resolve_qids(transport, [t for t in title_of.values() if t])
        for name, title in title_of.items():
            qid = resolved.get(title)
            if qid:
                known[name] = qid
        print(f"item IDs: {len(known)} of {len(targets)} players "
              f"({len(targets) - len(known)} with no Wikidata item)")

        facts = fetch_facts(transport, sorted(set(known.values())))
        if known and not facts:
            raise BioFetchError(
                "the query service answered, but with no facts at all for "
                f"{len(known)} item(s) -- refusing to write that")
        updated = 0
        for p in targets:
            qid = known.get(p["player"])
            # A player Wikidata has no item for still gets a record, with no
            # item ID: without one he is "missing from player_bio.json" and
            # every run to come pays to look him up again. The weekly
            # re-check is what tries him again, once a week rather than daily.
            updated += merge(bio, p, qid or "", facts.get(qid, {}) if qid else {},
                             full=args.full, changes=changes)
        print(f"records written or changed: {updated}")

    bref = {} if args.skip_bref else load_bref(transport)
    review = cross_check(bio, bref) if bref else {
        "mismatches": [], "missing_birth_date": [
            {"player": n, "wikidata_id": r.get("wikidata_id") or "",
             "basketball_reference": ""}
            for n, r in sorted(bio.items()) if not r.get("birth_date")]}
    review["generated"] = _today()
    review["cross_check_source"] = "" if args.skip_bref else BREF_CSV
    review["changed_upstream"] = changes
    review["counts"] = {"mismatches": len(review["mismatches"]),
                        "missing_birth_date": len(review["missing_birth_date"]),
                        "changed_upstream": len(changes)}
    cov = coverage(bio)

    if args.dry_run:
        print("dry run — nothing written")
    else:
        _write_json(args.out, bio)
        _write_json(args.review, review)
        print(f"wrote {args.out}")
        print(f"wrote {args.review}")

    print("\ncoverage: " + json.dumps(cov))
    print(f"review: {review['counts']['mismatches']} birth-date mismatch(es) vs "
          f"Basketball-Reference, {review['counts']['missing_birth_date']} "
          f"player(s) with no Wikidata birth date, "
          f"{review['counts']['changed_upstream']} upstream change(s) held back")
    for row in review["mismatches"][:10]:
        print(f"  MISMATCH {row['player']}: wikidata {row['wikidata']} vs "
              f"bbref {row['basketball_reference']}")
    return {"coverage": cov, "review": review, "bio": bio}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None,
                    help="max players to resolve this run (default: all missing)")
    ap.add_argument("--refresh-days", type=int, default=REFRESH_DAYS,
                    help=f"re-check a living player this many days after the "
                         f"last look (default {REFRESH_DAYS})")
    ap.add_argument("--refresh-limit", type=int, default=REFRESH_LIMIT,
                    help=f"max living players re-checked for a death in one "
                         f"run (default {REFRESH_LIMIT})")
    ap.add_argument("--full", action="store_true",
                    help="re-fetch every player and let Wikidata's values win")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between requests (default 1.0)")
    ap.add_argument("--fixtures", type=Path, default=None,
                    help="read saved responses from this directory instead of "
                         "the network (offline testing)")
    ap.add_argument("--skip-bref", action="store_true",
                    help="skip the Basketball-Reference cross-check")
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and report, write nothing")
    ap.add_argument("--careers", type=Path, default=CAREERS)
    ap.add_argument("--out", type=Path, default=BIO)
    ap.add_argument("--review", type=Path, default=REVIEW)
    args = ap.parse_args()

    transport = (FixtureTransport(args.fixtures) if args.fixtures
                 else HttpTransport(delay=args.delay))
    try:
        run(args, transport)
    except BioFetchError as exc:
        # Loud and non-zero: a silent empty file would strip the birth line
        # off every page the next time the site is built.
        print(f"::error::bio fetch failed, nothing written: {exc}",
              file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
