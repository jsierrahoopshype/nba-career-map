"""Birth and death facts for every player, from Wikidata and Basketball-Reference.

WHAT IT WRITES. data/players/player_bio.json, keyed by the player name the
career database uses:

    "Kobe Bryant": {
      "birth_date": "1978-08-23",       # date precision is kept: a year-only
      "death_date": "2020-01-26",       # Wikidata date stays "1947", never
      "birth_place": "Philadelphia",    # becomes "1947-01-01"
      "death_place": "Calabasas",
      "wikidata_id": "Q41421",
      "source": "basketball-reference", # where birth_date came from
      "checked": "2026-09-24"
    }

`source` names the origin of `birth_date` and nothing else:
"basketball-reference", "wikidata", or "unresolved" when neither had one.
`wikidata_id` is the item the places and the death date came from -- empty
when no item passed the identity gate below. A record that failed the gate
also carries `rejected_wikidata_id`, so a later run (and a human) can see
which namesake it had been resolved to.

THE IDENTITY GATE. A player's Wikipedia article resolves to a Wikidata item,
and for a name like "Ace Bailey", "Michael Phelps" or "Reggie Jackson" that
item is routinely a NAMESAKE -- an ice hockey player who died in 1992, a
swimmer, a baseball Hall of Famer. An item is only believed when it carries
P106 (occupation) = basketball player (Q3665646) or P641 (sport) = basketball
(Q5372). An item that fails has ALL of its dates and places discarded; we
then search Wikidata for a basketball player of the same name whose birth
year is within a year of Basketball-Reference's, and take it only when the
search comes back with exactly one candidate.

HUMAN-VERIFIED ARTICLES. A player whose override a person signed off on
(`"human_verified": true` in data/players/player_url_overrides.json, see
player_urls.py) is always read through that article, and its item is taken as
this player even when it lacks P106/P641 -- Ray Ellefson's item has no
occupation at all. The birth-year comparison with Basketball-Reference still
runs and still lands in `date_disagreement`, but as a warning: it does not
cost the item its death date or places, and --reapply does not throw it away.

SOURCE PRECEDENCE. Basketball-Reference is keyed to the actual NBA player, so
it decides the birth date whenever it has one; the gated Wikidata value is the
fallback. The death date and both places are Wikidata's alone, and are only
taken from a gated item whose birth year is within a year of
Basketball-Reference's -- a right-occupation item with the wrong birth year is
still likely to be a different basketball player, and his home town is not
this player's.

HOW IT GETS THERE. Three batched hops:

  1. en.wikipedia.org/w/api.php?action=query&prop=pageprops — 50 article
     titles a request, following redirects, reading the wikibase_item each
     article carries. That is the only reliable Wikipedia -> Wikidata link.
  2. query.wikidata.org/sparql — one query per chunk of item IDs, asking for
     P569 (birth date), P570 (death date), P19 (place of birth), P20 (place
     of death), the item's label and description, and the gate flag itself.
     The dates are read off the statement's VALUE NODE (psv:), not the truthy
     wdt: shortcut, because only the value node carries
     wikibase:timePrecision -- and without the precision a date Wikidata only
     knows to the year comes back looking like January 1st. Statements
     Wikidata has marked deprecated (its way of saying "this value is known
     to be wrong") are skipped.

     SPARQL rather than wbgetentities: a full entity is hundreds of KB of
     claims we would throw away, and the label service hands over the place
     names in the same round trip.
  3. query.wikidata.org/sparql again, for the players whose item failed the
     gate: label/alias lookup restricted to P106 = basketball player.

WHAT IT REFUSES TO DO. If the API cannot be reached -- DNS, a proxy, an
outage, a 429 -- the run prints the failure and exits non-zero WITHOUT
writing. An empty player_bio.json would quietly strip the dates off every
page's structured data, which is a worse outcome than a red workflow run.

INCREMENTAL BY DEFAULT. A run resolves the players missing from
player_bio.json, and re-checks living players whose record is more than
--refresh-days old so a death is picked up within the week. --refresh-limit
caps how many of those a single run re-checks, which spreads the sweep over
several days instead of re-reading five thousand records every Monday.
--full re-reads and REWRITES every record from scratch under the rules above,
which is what to run after the rules themselves change.

THE REVIEW FILE. data/players/bio_needs_review.json is the human's queue, in
three lists: `wrong_entity` (the gate failed -- what it had resolved to, and
whether a replacement was found), `date_disagreement` (the same person, but
Wikidata and Basketball-Reference state different birth dates -- and which
one the record uses), and `still_missing` (no birth date from either source).
It also carries `wikipedia_url_wrong_person`: the wrong-entity players whose
stored Wikipedia URL is the namesake's article, so their CLUB HISTORY may be
wrong too. Nothing in the review file edits the career database. The fix for
that list is data/players/player_url_overrides.json -- a curated article per
player, written by scripts/resolve_player_urls.py and consulted here (see
_title_of) before the stored URL, so a run cannot resolve back to the namesake.
A player with a verified override is left off `wikipedia_url_wrong_person`:
the override is the repair, whether or not his bio record has been re-read.

Run:
    python3 scripts/fetch_bio_wikidata.py                 # incremental
    python3 scripts/fetch_bio_wikidata.py --limit 500     # bounded backfill
    python3 scripts/fetch_bio_wikidata.py --full          # rewrite everyone
    python3 scripts/fetch_bio_wikidata.py --reapply       # offline: re-apply
                                                          # the rules to what
                                                          # is already on file
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
import player_urls  # noqa: E402
from names import normkey, title_from_url  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
BIO = ROOT / "data" / "players" / "player_bio.json"
REVIEW = ROOT / "data" / "players" / "bio_needs_review.json"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
# Basketball-Reference career info, mirrored as a CSV. One row per player,
# with the birth date that decides ours.
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
NAME_CHUNK = 40       # names per replacement-search query
REFRESH_DAYS = 7
REFRESH_LIMIT = 1200  # living players re-checked for a death per run

# The identity gate. An item is this player only if Wikidata says it plays the
# sport; everything else with the name is a namesake.
Q_BASKETBALL_PLAYER = "Q3665646"   # P106 occupation: basketball player
Q_BASKETBALL = "Q5372"             # P641 sport: basketball
# How far a gated item's birth year may sit from Basketball-Reference's before
# its places and death date stop being this player's. One year absorbs the
# genuine off-by-one records without absorbing a different man.
BIRTH_YEAR_TOLERANCE = 1
# How a wrong-entity row in the review file was reached. The offline one is a
# stand-in: --reapply has no network, so it can only go on the birth year.
GATE_CHECK = "wikidata identity gate (P106/P641)"
OFFLINE_CHECK = ("offline birth-year check -- the real P106/P641 gate runs on "
                 "the next live --full run, which can hand the item back")
# --reapply has no gate to consult, so it goes on the birth year alone, and it
# is deliberately more forgiving than BIRTH_YEAR_TOLERANCE about it: two or
# three years between Wikidata and Basketball-Reference is an ordinary dispute
# over a player born in the 1920s, while five is a different man. The item
# still loses its places at BIRTH_YEAR_TOLERANCE either way -- what this
# decides is whether the item ID is thrown away with them.
OFFLINE_REJECT_YEARS = 5

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
# ?bball IS the identity gate, answered per item in the same round trip: true
# when the item's occupation is basketball player or its sport is basketball.
# ?itemLabel/?itemDescription are what the review file reports a rejected item
# as, so a human reading it sees "Canadian ice hockey player" rather than a
# bare Q-number.
#
# The deprecated-rank filter: Wikidata marks a statement deprecated to say the
# value is known to be wrong (a birth year a later source disproved). Reading
# it would hand a correctly-identified player a birth year years off his real
# one, and under the rules below that costs him his birth place.
_FACT_PATTERNS = """
  OPTIONAL { ?item p:P569 ?bst . ?bst psv:P569 [ wikibase:timeValue ?birth ;
             wikibase:timePrecision ?bprec ] .
             FILTER NOT EXISTS { ?bst wikibase:rank wikibase:DeprecatedRank } }
  OPTIONAL { ?item p:P570 ?dst . ?dst psv:P570 [ wikibase:timeValue ?death ;
             wikibase:timePrecision ?dprec ] .
             FILTER NOT EXISTS { ?dst wikibase:rank wikibase:DeprecatedRank } }
  OPTIONAL { ?item wdt:P19 ?bplace }
  OPTIONAL { ?item wdt:P20 ?dplace }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
"""

SPARQL_TEMPLATE = """SELECT ?item ?itemLabel ?itemDescription ?bball ?birth ?bprec ?death ?dprec ?bplaceLabel ?dplaceLabel WHERE {
  VALUES ?item { %%s }
  BIND(EXISTS { { ?item wdt:P106 wd:%s } UNION { ?item wdt:P641 wd:%s } } AS ?bball)
%s}""" % (Q_BASKETBALL_PLAYER, Q_BASKETBALL, _FACT_PATTERNS)

# The replacement search: items that answer to the name, by label or by alias,
# and that ARE basketball players. The name goes first and as a UNION rather
# than an rdfs:label|skos:altLabel path, because that is the shape the query
# service resolves off the literal index -- the other way round it walks every
# basketball player on Wikidata before it looks at a single name.
SEARCH_TEMPLATE = """SELECT ?name ?item ?itemLabel ?itemDescription ?birth ?bprec ?death ?dprec ?bplaceLabel ?dplaceLabel WHERE {
  VALUES ?name { %%s }
  { ?item rdfs:label ?name } UNION { ?item skos:altLabel ?name }
  ?item wdt:P106 wd:%s .
%s}""" % (Q_BASKETBALL_PLAYER, _FACT_PATTERNS)


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


def _blank_item(qid: str) -> dict:
    return {"qid": qid, "birth_date": None, "death_date": None,
            "birth_place": "", "death_place": "", "label": "",
            "description": "", "basketball": False}


def _absorb(rec: dict, row: dict) -> None:
    """Fold one results row into an item record.

    An item can carry several statements for the same property (disputed
    dates, several citizenships). First non-empty wins; the Basketball-
    Reference comparison below is what catches a wrong one.
    """
    birth = format_date((row.get("birth") or {}).get("value"),
                        (row.get("bprec") or {}).get("value"))
    death = format_date((row.get("death") or {}).get("value"),
                        (row.get("dprec") or {}).get("value"))
    rec["birth_date"] = rec["birth_date"] or birth
    rec["death_date"] = rec["death_date"] or death
    rec["birth_place"] = rec["birth_place"] or _label(row, "bplaceLabel")
    rec["death_place"] = rec["death_place"] or _label(row, "dplaceLabel")
    rec["label"] = rec["label"] or _label(row, "itemLabel")
    rec["description"] = rec["description"] or _label(row, "itemDescription")
    if ((row.get("bball") or {}).get("value") or "").lower() == "true":
        rec["basketball"] = True


def _row_qid(row: dict) -> str:
    uri = (row.get("item") or {}).get("value") or ""
    qid = uri.rsplit("/", 1)[-1]
    return qid if _QID.match(qid) else ""


def fetch_facts(transport, qids: list[str],
                chunk: int = QID_CHUNK) -> dict[str, dict]:
    """{item ID: item record}, including the `basketball` gate flag."""
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
            qid = _row_qid(row)
            if not qid:
                continue
            _absorb(out.setdefault(qid, _blank_item(qid)), row)
    return out


def passes_gate(item: dict | None) -> bool:
    """Does Wikidata say this item plays basketball?"""
    return bool(item and item.get("basketball"))


def _sparql_literal(text: str) -> str:
    body = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{body}"@en'


def search_basketball_items(transport, names: list[str],
                            chunk: int = NAME_CHUNK) -> dict[str, list[dict]]:
    """{name: [item record, ...]} over items Wikidata calls basketball players.

    Every candidate is returned; the caller is the one that insists on a
    single unambiguous hit.
    """
    out: dict[str, dict[str, dict]] = {}
    names = [n for n in dict.fromkeys(names) if n and n.strip()]
    for i in range(0, len(names), chunk):
        part = names[i:i + chunk]
        query = SEARCH_TEMPLATE % " ".join(_sparql_literal(n) for n in part)
        data = transport.sparql(query)
        rows = (data.get("results") or {}).get("bindings")
        if rows is None:
            raise BioFetchError(
                "query.wikidata.org: no results in the name search")
        for row in rows:
            qid = _row_qid(row)
            name = ((row.get("name") or {}).get("value") or "").strip()
            if not qid or not name:
                continue
            bucket = out.setdefault(name, {})
            rec = bucket.setdefault(qid, _blank_item(qid))
            rec["basketball"] = True   # the query asked for nothing else
            _absorb(rec, row)
    return {name: list(items.values()) for name, items in out.items()}
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
    """The article to read this player's item off.

    A curated article (data/players/player_url_overrides.json) comes first, so
    a bio run that happens before the career pipeline has re-scraped the record
    still reads the right item instead of re-deriving the namesake's -- and so
    a run can never hand the rejected item back.
    """
    forced = player_urls.override_title(player.get("player") or "")
    if forced:
        return forced
    url = player.get("wikipedia_url") or ""
    return title_from_url(url) or (player.get("display_name")
                                   or player.get("player") or "")


def search_names(player: dict) -> list[str]:
    """The names to look a replacement item up under, best first."""
    seen, out = set(), []
    for n in (player.get("display_name"), player.get("player")):
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# --- the rules --------------------------------------------------------------
def _year(value) -> int | None:
    m = re.match(r"^(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def birth_years_agree(a, b, tolerance: int = BIRTH_YEAR_TOLERANCE) -> bool:
    """Are these two birth dates close enough to be the same man?

    Missing on either side is not a disagreement -- there is nothing to
    compare, and the facts get the benefit of the doubt.
    """
    ya, yb = _year(a), _year(b)
    if ya is None or yb is None:
        return True
    return abs(ya - yb) <= tolerance


def pick_replacement(candidates: list[dict], bref_date: str) -> dict | None:
    """The one basketball player of this name born when the NBA player was.

    Only a single unambiguous hit is accepted: two "Michael Porter"s both born
    in 1998 is not an answer, it is a coin toss.
    """
    if not bref_date:
        return None        # nothing to disambiguate against
    hits = [c for c in candidates
            if _year(c.get("birth_date")) is not None
            and birth_years_agree(c.get("birth_date"), bref_date)]
    return hits[0] if len(hits) == 1 else None


def compose(item: dict | None, bref_date: str, *, checked: str,
            rejected_qid: str = "", trusted: bool = False) -> dict:
    """One player's record, built from the gated item and Basketball-Reference.

    birth_date is Basketball-Reference's whenever it has one, because that file
    is keyed to the NBA player and cannot be a namesake. The death date and
    both places are Wikidata's alone, and only from an item whose birth year
    lands within BIRTH_YEAR_TOLERANCE of Basketball-Reference's -- unless the
    item is `trusted` (a human verified the article), where a disagreeing
    year is left for the review file to report and blocks nothing.
    """
    wd_birth = (item or {}).get("birth_date")
    birth = bref_date or wd_birth or None
    rec = {
        "birth_date": birth,
        "death_date": None,
        "birth_place": "",
        "death_place": "",
        "wikidata_id": (item or {}).get("qid", "") or "",
        "source": ("basketball-reference" if bref_date
                   else ("wikidata" if wd_birth else "unresolved")),
        "checked": checked,
    }
    if item and (trusted or birth_years_agree(wd_birth, bref_date)):
        rec["death_date"] = item.get("death_date") or None
        rec["birth_place"] = item.get("birth_place") or ""
        rec["death_place"] = item.get("death_place") or ""
    # Basketball-Reference wins, but the value it beat is kept so the review
    # file can still report the disagreement on a later, incremental run --
    # by then the Wikidata value is no longer in birth_date to compare.
    if wd_birth and bref_date and not same_date(wd_birth, bref_date):
        rec["wikidata_birth_date"] = wd_birth
    if rejected_qid:
        rec["rejected_wikidata_id"] = rejected_qid
    return rec
# --- basketball-reference ---------------------------------------------------
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

    A date known only to the year agrees with any other date in that year --
    it is less precise, not wrong.
    """
    if not ours or not theirs:
        return True
    ours, theirs = ours.strip(), theirs.strip()
    return theirs[:len(ours)] == ours


# --- the review file --------------------------------------------------------
def _item_summary(item: dict | None) -> dict | None:
    if not item:
        return None
    return {"wikidata_id": item.get("qid") or "",
            "label": item.get("label") or "",
            "description": item.get("description") or "",
            "birth_date": item.get("birth_date") or "",
            "death_date": item.get("death_date") or ""}


def build_review(bio: dict, careers: list, bref: dict[str, str],
                 details: dict, previous: dict | None = None) -> dict:
    """The human's queue, in three lists plus the Wikipedia-URL warning.

    `details` is what this run learned about the items it rejected
    ({player: {"rejected": item, "replacement": item|None, "candidates": n}}).
    A player rejected by an EARLIER run and not re-read this time keeps the
    description the previous review file recorded, so an incremental run never
    silently shortens the list.
    """
    # The override, when there is one, is the article this player is actually
    # read from -- so a repaired player stops being reported as pointing at the
    # namesake even before the career pipeline has written the new URL back.
    url_of = {p.get("player"): (player_urls.override_url(p.get("player") or "")
                                or p.get("wikipedia_url") or "")
              for p in careers}
    carried = {row.get("player"): row
               for row in ((previous or {}).get("wrong_entity") or [])}
    # A verified override IS the repair: the pipeline reads that article before
    # anything else, so the player stops counting as pointing at the namesake
    # the moment it is written -- not only once his bio record is re-read.
    repaired = {p.get("player") for p in careers
                if player_urls.override_for(p.get("player") or "")}

    wrong, disagreement, missing, bad_url = [], [], [], []
    for name, rec in sorted(bio.items()):
        bref_date = bref.get(normkey(name), "")
        detail = details.get(name) or {}
        rejected_qid = rec.get("rejected_wikidata_id") or ""

        if rejected_qid:
            old = carried.get(name) or {}
            resolved = (_item_summary(detail.get("rejected"))
                        or old.get("resolved_to")
                        or {"wikidata_id": rejected_qid, "label": "",
                            "description": "", "birth_date": "",
                            "death_date": ""})
            replacement = (_item_summary(detail.get("replacement"))
                           if "replacement" in detail
                           else old.get("replacement"))
            if not rec.get("wikidata_id"):
                replacement = None
            detected = (detail.get("detected_by")
                        or old.get("detected_by") or GATE_CHECK)
            url = url_of.get(name, "")
            searched = detected != OFFLINE_CHECK
            if rec.get("wikidata_id"):
                note = ("re-pointed at a basketball player of the same name "
                        "born in the right year")
            elif searched:
                note = ("no basketball player of this name was found on "
                        "Wikidata; the dates and places that item carried "
                        "were discarded")
            else:
                note = ("the dates and places that item carried were "
                        "discarded; no replacement was looked for, because "
                        "the offline pass cannot search Wikidata")
            wrong.append({
                "player": name,
                "rejected_wikidata_id": rejected_qid,
                "detected_by": detected,
                "resolved_to": resolved,
                "replacement_found": bool(rec.get("wikidata_id")),
                "replacement": replacement,
                "basketball_reference": bref_date,
                "birth_date_used": rec.get("birth_date") or "",
                "wikipedia_url": url,
                "note": note,
            })
            if url and name not in repaired:
                bad_url.append({
                    "player": name,
                    "wikipedia_url": url,
                    "wikipedia_title": title_from_url(url) or "",
                    "rejected_wikidata_id": rejected_qid,
                    "note": "this article is the namesake's, so the club "
                            "history scraped from it may be his too",
                })
            continue

        wd_birth = rec.get("wikidata_birth_date") or ""
        if wd_birth and bref_date and not same_date(wd_birth, bref_date):
            apart = abs((_year(wd_birth) or 0) - (_year(bref_date) or 0))
            disagreement.append({
                "player": name,
                "wikidata": wd_birth,
                "basketball_reference": bref_date,
                "wikidata_id": rec.get("wikidata_id") or "",
                "used": rec.get("birth_date") or "",
                "used_source": rec.get("source") or "",
                "years_apart": apart,
                "places_kept": (apart <= BIRTH_YEAR_TOLERANCE
                                or player_urls.is_human_verified(name)),
            })

        if not rec.get("birth_date"):
            missing.append({"player": name,
                            "wikidata_id": rec.get("wikidata_id") or "",
                            "basketball_reference": bref_date})

    return {
        "generated": _today(),
        "cross_check_source": BREF_CSV,
        "identity_gate": {
            "occupation": f"P106 = {Q_BASKETBALL_PLAYER} (basketball player)",
            "sport": f"P641 = {Q_BASKETBALL} (basketball)",
            "birth_year_tolerance": BIRTH_YEAR_TOLERANCE,
        },
        "counts": {"wrong_entity": len(wrong),
                   "wrong_entity_replaced": sum(1 for r in wrong
                                                if r["replacement_found"]),
                   "date_disagreement": len(disagreement),
                   "still_missing": len(missing),
                   "wikipedia_url_wrong_person": len(bad_url)},
        "wrong_entity": wrong,
        "date_disagreement": disagreement,
        "still_missing": missing,
        "wikipedia_url_wrong_person": bad_url,
    }
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
        "from_basketball_reference": sum(
            1 for r in bio.values() if r.get("source") == "basketball-reference"),
        "rejected_wikidata_item": sum(
            1 for r in bio.values() if r.get("rejected_wikidata_id")),
    }


# --- gathering --------------------------------------------------------------
def gather_items(transport, targets: list, bio: dict,
                 bref: dict[str, str]) -> tuple[dict, dict]:
    """({player: gated item or None}, {player: what this run learned}).

    Three steps: map the ones with no item ID on file through Wikipedia, read
    every item's facts and gate flag, then go looking for a replacement for
    each item the gate turned down.
    """
    # A human-verified article is always re-read, never short-cut through the
    # item ID on file: that ID may be a replacement the name search picked, and
    # the person's choice of article is the one that decides.
    trusted = {p["player"] for p in targets
               if player_urls.is_human_verified(p["player"])}
    known = {p["player"]: bio[p["player"]]["wikidata_id"]
             for p in targets
             if p["player"] not in trusted
             and bio.get(p["player"], {}).get("wikidata_id")}
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

    items: dict[str, dict | None] = {}
    details: dict[str, dict] = {}
    for p in targets:
        name = p["player"]
        qid = known.get(name)
        item = facts.get(qid) if qid else None
        if qid and not passes_gate(item) and name not in trusted:
            details[name] = {"rejected": item or _blank_item(qid),
                             "player": p}
            items[name] = None
        else:
            items[name] = item

    if details:
        for detail in details.values():
            detail["detected_by"] = GATE_CHECK
        wanted: dict[str, list[str]] = {}
        for name, detail in details.items():
            if bref.get(normkey(name)):
                wanted[name] = search_names(detail["player"])
        print(f"identity gate: {len(details)} item(s) are not basketball "
              f"players; searching for {len(wanted)} replacement(s)")
        try:
            found = search_basketball_items(
                transport, [n for names in wanted.values() for n in names])
        except BioFetchError as exc:
            # The gate has already done the job that matters -- the namesake's
            # dates are out. A label search that times out costs us the
            # replacements, not the run, and the players stay in the review
            # file for the next one to try again.
            print(f"::warning::replacement search failed, no replacements "
                  f"this run: {exc}", file=sys.stderr)
            found = {}
        for name, aliases in wanted.items():
            candidates: dict[str, dict] = {}
            for alias in aliases:
                for cand in found.get(alias, []):
                    candidates[cand["qid"]] = cand
            pick = pick_replacement(list(candidates.values()),
                                    bref.get(normkey(name), ""))
            details[name]["replacement"] = pick
            details[name]["candidates"] = len(candidates)
            if pick:
                items[name] = pick
        for name, detail in details.items():
            detail.setdefault("replacement", None)
            detail.pop("player", None)
    return items, details


def reapply(bio: dict, bref: dict[str, str]) -> tuple[dict, dict]:
    """Re-run the rules over what is already on file, without the network.

    The P106/P641 gate cannot be checked offline, so this uses the strongest
    signal the stored record still carries: a Wikidata birth year
    OFFLINE_REJECT_YEARS or more away from Basketball-Reference's is a
    namesake, not a disputed date. Everything else the rules say --
    Basketball-Reference deciding the birth date, places surviving only an
    item within BIRTH_YEAR_TOLERANCE -- applies exactly as it does online.
    A live --full run re-checks all of it against the real gate, and can hand
    an item back that this threw away.
    """
    out, details = {}, {}
    for name, rec in bio.items():
        bref_date = bref.get(normkey(name), "")
        item = _stored_item(rec)
        rejected_qid = rec.get("rejected_wikidata_id") or ""
        trusted = player_urls.is_human_verified(name)
        if item and not trusted and not birth_years_agree(
                item["birth_date"], bref_date, OFFLINE_REJECT_YEARS - 1):
            details[name] = {"rejected": item, "replacement": None,
                             "candidates": 0, "detected_by": OFFLINE_CHECK}
            rejected_qid = item["qid"]
            item = None
        out[name] = compose(item, bref_date,
                            checked=rec.get("checked") or _today(),
                            rejected_qid=rejected_qid, trusted=trusted)
    return out, details


def _stored_item(rec: dict) -> dict | None:
    """The Wikidata item a stored record still describes, or None."""
    qid = rec.get("wikidata_id") or ""
    if not qid:
        return None
    wd_birth = rec.get("wikidata_birth_date")
    if wd_birth is None and rec.get("source") != "basketball-reference":
        wd_birth = rec.get("birth_date")
    return {"qid": qid, "birth_date": wd_birth,
            "death_date": rec.get("death_date"),
            "birth_place": rec.get("birth_place") or "",
            "death_place": rec.get("death_place") or "",
            "label": "", "description": "", "basketball": True}


def run(args, transport) -> dict:
    players = _read_json(args.careers, [])
    if not players:
        raise BioFetchError(f"{args.careers}: no players to work from")
    bio = _read_json(args.out, {})
    if not isinstance(bio, dict):
        raise BioFetchError(f"{args.out}: expected an object keyed by player name")
    previous = _read_json(args.review, {})

    # Basketball-Reference first: it decides the birth dates and it is what
    # the identity gate measures a candidate item against, so a run cannot
    # compose a single record without it.
    bref = {} if args.skip_bref else load_bref(transport)

    if args.reapply:
        print(f"re-applying the rules to {len(bio)} record(s) already on file")
        bio, details = reapply(bio, bref)
        print(f"records rewritten: {len(bio)}")
    else:
        new, due = select_targets(players, bio, full=args.full, limit=args.limit,
                                  refresh_days=args.refresh_days,
                                  refresh_limit=args.refresh_limit)
        targets = new + due
        print(f"players in the database: {len(players)}")
        print(f"already on file: {len(bio)}")
        print(f"to resolve: {len(new)} new, {len(due)} living re-checks")

        details = {}
        if not targets:
            print("nothing to fetch this run")
        else:
            items, details = gather_items(transport, targets, bio, bref)
            for p in targets:
                name = p["player"]
                item = items.get(name)
                rejected = ((details.get(name) or {}).get("rejected") or {})
                bio[name] = compose(item, bref.get(normkey(name), ""),
                                    checked=_today(),
                                    rejected_qid=rejected.get("qid") or "",
                                    trusted=player_urls.is_human_verified(name))
            print(f"records written: {len(targets)}")

    review = build_review(bio, players, bref, details, previous)
    review["cross_check_source"] = "" if args.skip_bref else BREF_CSV
    cov = coverage(bio)

    if args.dry_run:
        print("dry run - nothing written")
    else:
        _write_json(args.out, bio)
        _write_json(args.review, review)
        print(f"wrote {args.out}")
        print(f"wrote {args.review}")

    print("\ncoverage: " + json.dumps(cov))
    c = review["counts"]
    print(f"review: {c['wrong_entity']} wrong entity "
          f"({c['wrong_entity_replaced']} re-pointed at the right player), "
          f"{c['date_disagreement']} date disagreement(s), "
          f"{c['still_missing']} still missing, "
          f"{c['wikipedia_url_wrong_person']} Wikipedia URL(s) on the wrong "
          f"person")
    for row in review["wrong_entity"][:10]:
        got = row["resolved_to"]
        print(f"  WRONG ENTITY {row['player']}: {row['rejected_wikidata_id']} "
              f"{got.get('label') or ''} "
              f"({got.get('description') or 'no description'})")
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
                    help="re-read every player and rewrite every record from "
                         "scratch under the current rules")
    ap.add_argument("--reapply", action="store_true",
                    help="re-apply the rules to the records already on file, "
                         "without touching Wikipedia or Wikidata")
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

    if args.reapply and args.full:
        ap.error("--reapply works offline on what is on file; --full re-reads "
                 "everyone from Wikidata. Pick one.")

    transport = (FixtureTransport(args.fixtures) if args.fixtures
                 else HttpTransport(delay=args.delay))
    try:
        run(args, transport)
    except BioFetchError as exc:
        # Loud and non-zero: a silent empty file would strip the dates out of
        # every player page's structured data the next time the site is built.
        print(f"::error::bio fetch failed, nothing written: {exc}",
              file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
