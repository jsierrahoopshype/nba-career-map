"""Main orchestrator for the automated career-database update.

Modes (see --mode):
  incremental   : default. Add players newly appearing on NBA rosters; re-check
                  players who dropped off an NBA roster (they may have moved
                  overseas rather than retired); re-check ALL overseas_active
                  players to catch team changes (e.g. Patty Mills in Australia);
                  then refresh least-recently-updated nba_active players. All
                  within the request budget; continues next run.
  full          : refresh every active player (NBA + overseas), budget-bounded.
  full_overseas : re-check ALL overseas_active players (intended to run monthly
                  so long-time overseas players stay current even between the
                  daily incremental passes).
  single        : refresh one player by name (--player "First Last").
  review        : try to resolve locations for teams in teams_needing_review.json
                  by reading their Wikipedia lead extract.

Each player carries a tracking ``status`` (nba_active / overseas_active /
retired); see player_status.classify_status. A player who leaves an NBA roster
but whose Wikipedia shows a current overseas team becomes overseas_active (not
retired); one with no team for 2+ years becomes retired.

Rate limiting: WikipediaClient enforces --delay seconds between requests and a
hard --max-requests budget per run (roster fetches count toward it). The delay
is measured start-to-start, so network latency is absorbed into it and each
request costs ~max(delay, latency) ~= 1.0s of wall time at the default delay.

Budget sizing: the incremental queue is the whole active rotation (~1,295
players as of Aug 2026) and real runs average ~1.46 requests per player
(measured over 12 consecutive daily runs), so a full sweep costs ~1,900
requests. The default 650/run therefore refreshes ~445 players/day and cycles
the rotation in ~3 days, at ~11 minutes of wall time -- comfortably inside the
job timeout. The previous default of 100 refreshed only ~68 players/day, a
19-day cycle, which meant a signing could sit unnoticed for weeks.

Outputs are written under /data and /logs; the root
nba_players_careers_READY.json is kept in sync so index.html keeps working.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import split_combined_teams
import stint_order
from wikipedia_api import WikipediaClient, RequestBudgetExceeded
from team_normalizer import (TeamNormalizer, edit_distance,
                             is_spelling_variant, rename_containment,
                             spelling_key, spelling_tokens, strip_diacritics)
from wiki_parser import parse_player
from rosters import fetch_all_rosters, NBA_TEAMS
from era_correct_teams import ERA_TABLE
from sync_era_locations import LOC as ERA_LOCATIONS
from player_status import (classify_status, last_active_year, PRESENT,
                           NBA_ACTIVE, OVERSEAS_ACTIVE,
                           RETIRED as RETIRED_STATUS)  # RETIRED name is the file path below
from geo import resolve_location
from names import (normkey, url_key, canonical_url, title_from_url,
                   exact_article_key)
from wiki_person import same_person, candidate_titles

ROOT = Path(__file__).resolve().parent.parent
DATA, LOGS = ROOT / "data", ROOT / "logs"
CAREERS = DATA / "players" / "nba_players_careers.json"
ACTIVE = DATA / "players" / "active_players.json"
RETIRED = DATA / "players" / "retired_players.json"
LOCATIONS = DATA / "teams" / "team_locations.json"
REVIEW = DATA / "teams" / "teams_needing_review.json"
UPDATE_LOG = LOGS / "update_log.json"
CHANGELOG = LOGS / "changelog.md"
# Append-only transaction ledger: one record per real current_team change,
# captured going forward (past runs' previous values weren't kept, so this
# cannot be backfilled). Feeds the dashboard "latest_signings" widget.
TRANSACTIONS = DATA / "logs" / "transactions.json"
SPELLING_REVIEW = DATA / "logs" / "spelling_review.json"
PLACE_REVIEW = DATA / "logs" / "wrong_place_review.json"
ARTICLE_REVIEW = DATA / "logs" / "wrong_article_review.json"
ROOT_MAP_FILE = ROOT / "nba_players_careers_READY.json"
# Cursor into TRANSACTIONS: how many ledger entries have already been posted
# to Slack. Same append-only-ledger pattern as TRANSACTIONS itself; tracking
# a count (not content) means a re-run naturally resumes from the right spot
# and never backfills or double-posts. See _notify_slack.
SLACK_MARKER = DATA / "logs" / "slack_posted_marker.json"


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _is_slash_joined(team: str) -> bool:
    """True for names like "Sheffield Forgers / Sharks" (slash padded by spaces),
    which usually means two team names got merged. A slash without surrounding
    spaces (e.g. "Hapoel Gilboa/Afula") is a legitimate single name."""
    return bool(re.search(r"\S\s+/\s+\S", team or ""))


class Database:
    def __init__(self):
        players = load_json(CAREERS, [])
        self.by_name = {p["player"]: p for p in players}
        self.order = [p["player"] for p in players]
        self.locations = load_json(LOCATIONS, {})
        self._pin_era_locations()
        self.review = load_json(REVIEW, {})
        self.normalizer = TeamNormalizer()
        # dedupe indexes: normalized name / aliases, and canonical Wikipedia URL
        self.norm_index: dict[str, str] = {}
        self.url_index: dict[str, str] = {}
        # exact article titles, suffix and all -- see owner_of_article
        self.article_index: dict[str, str] = {}
        for p in players:
            self._index(p)

    def _pin_era_locations(self) -> None:
        """Give every franchise-era name its place before discovery can guess.

        Wikipedia answers a defunct team's name with the article for the
        franchise as it exists today: "Chicago Packers" reaches the Washington
        Wizards, and _discover_location reads "based in Washington, D.C." off
        it. A 1961 Chicago team then gets a location record in Washington, and
        the stint plots 700 miles from where it was played. Eight records had
        already gone that way, three of them holding nothing at all.

        The sports-club guard cannot catch this -- the Wizards article IS
        about a basketball team. But these teams have an authority for where
        they played, so consult it first and the guess never happens. Pinned
        rather than merely seeded, because a record that drifted once can
        drift again.
        """
        for team, (city, state, country) in ERA_LOCATIONS.items():
            entry = self.locations.get(team) or {"team": team, "league": ""}
            if (entry.get("city"), entry.get("state"),
                    entry.get("country")) == (city, state, country):
                continue
            entry.update({"team": team, "city": city, "state": state,
                          "country": country})
            self.locations[team] = entry

    # -- dedupe index -------------------------------------------------------

    def _index(self, rec: dict) -> None:
        """Register a record's name, aliases and URL in the dedupe indexes."""
        name = rec["player"]
        for key in (name, rec.get("display_name", ""), *rec.get("aliases", [])):
            k = normkey(key)
            if k:
                self.norm_index.setdefault(k, name)
        u = url_key(rec.get("wikipedia_url", ""))
        if u:
            self.url_index.setdefault(u, name)
        a = exact_article_key(title_from_url(rec.get("wikipedia_url", "")))
        if a:
            self.article_index.setdefault(a, name)

    def owner_of_article(self, title: str) -> str | None:
        """Which record, if any, is already built from this exact article.

        Exact: "Kenyon Martin Jr." is not "Kenyon Martin", however alike the
        two fold. Asking the name-based index instead told the scraper that
        KJ Martin's own article belonged to his father, and his record stopped
        updating for three days.
        """
        return self.article_index.get(exact_article_key(title))

    def resolve_by_name(self, candidate: str) -> str | None:
        """Existing player matching a candidate by normalized name, else None."""
        return self.norm_index.get(normkey(candidate))

    def resolve_canonical(self, canonical_title: str) -> str | None:
        """Existing player matching a resolved Wikipedia article (URL or name)."""
        if not canonical_title:
            return None
        return (self.url_index.get(url_key(canonical_url(canonical_title)))
                or self.norm_index.get(normkey(canonical_title)))

    # -- team locations -----------------------------------------------------

    def location_for(self, team: str) -> dict:
        return self.locations.get(team, {})

    def enrich_stint(self, stint: dict, client: WikipediaClient,
                     discovered: dict) -> None:
        """Attach city/state/country to a parsed stint from known locations,
        discovering + flagging unknown teams."""
        team = stint["team"]

        # A slash-joined name (e.g. "Sheffield Forgers / Sharks") is almost
        # always two historical names run together by a misparse. Flag it for
        # manual review instead of trying to geolocate it.
        if _is_slash_joined(team):
            stint["city"] = stint["state"] = stint["country"] = ""
            self.review[team] = {"team": team,
                                 "reason": "slash-joined name — likely misparse of two teams",
                                 "city": "", "country": ""}
            return

        loc = self.locations.get(team)
        if loc and (loc.get("city") or loc.get("country")):
            stint["city"] = loc.get("city", "")
            stint["state"] = loc.get("state", "")
            stint["country"] = loc.get("country", "")
        else:
            # unknown / incomplete team -> try discovery, then flag for review
            found = discovered.get(team)
            if found is None:
                found = self._discover_location(team, client)
                discovered[team] = found
            stint["city"] = found.get("city", "")
            stint["state"] = found.get("state", "")
            stint["country"] = found.get("country", "")
            self.locations[team] = {"team": team, **found,
                                    "league": self.locations.get(team, {}).get("league", "")}
            if not found.get("city") or not found.get("country"):
                self.review[team] = {"team": team,
                                     "reason": "auto-added; needs location confirmation",
                                     "city": found.get("city", ""),
                                     "country": found.get("country", "")}

        # A slash without surrounding spaces (e.g. "Birmingham/Laketown Squadron")
        # may still be two merged names — flag for verification without discarding
        # the location we found. (Space-padded slashes are handled above.)
        if "/" in team:
            self.review.setdefault(team, {
                "team": team, "reason": "contains '/', verify not two merged teams",
                "city": stint.get("city", ""), "country": stint.get("country", "")})

    def _discover_location(self, team: str, client: WikipediaClient) -> dict:
        # Ask what article this name actually reaches before reading anything
        # off it. Wikipedia answers a name it does not have by redirecting to
        # the nearest thing it does, and for a club that is usually its
        # successor: "Chicago Packers" reaches the Washington Wizards, whose
        # article is about a basketball team and so sails past the sports-club
        # guard below. A title that redirected away from the name requested is
        # a different subject, whatever it is about.
        if not same_article(team, resolved := _resolve_quietly(team, client)):
            REFUSED_PLACE.append({"team": team, "date": today(),
                                  "reason": "the title redirected to another article",
                                  "extract": f"-> {resolved!r}"})
            return {"city": "", "state": "", "country": ""}
        try:
            extract = client.get_extract(team)
        except RequestBudgetExceeded:
            raise
        except Exception:  # noqa: BLE001
            extract = None
        if not extract:
            return {"city": "", "state": "", "country": ""}
        # The same guard the article fetch has, for the same reason. Wikipedia
        # answers a club name it does not have with the nearest thing it does:
        # "Libertas" is an Irish political party, and the seed took its
        # registered office as the club's home, putting nine stints of Italian
        # basketball in County Galway. An extract that says nothing about a
        # sport is not about a club, whatever the name matched.
        if not SPORTS_CLUB.search(extract):
            REFUSED_PLACE.append({"team": team, "date": today(),
                                  "reason": "the article is not about a sports club",
                                  "extract": extract[:180]})
            return {"city": "", "state": "", "country": ""}
        # light heuristic: "... based in <City>, <Region-or-Country>"
        m = re.search(r"based in ([A-Z][\w.\- ]+?)(?:,\s*([A-Z][\w.\- ]+?))?[.,]",
                      extract)
        if m:
            city = m.group(1).strip()
            # resolve region/US-state names (Lazio, Georgia, …) to a country,
            # passing the city so ambiguous "Georgia" disambiguates; unknown
            # tokens keep the country blank so review flags it.
            state, country = resolve_location(m.group(2) or "", city)
            return {"city": city, "state": state, "country": country}
        return {"city": "", "state": "", "country": ""}


def _resolve_quietly(team: str, client: WikipediaClient) -> str | None:
    """The article a club name reaches, or None if the lookup cannot say.

    A lookup that fails for any reason other than the budget returns None,
    which same_article() reads as "cannot tell" and lets through: a guard that
    refuses on a network error would silently stop discovering anything.
    """
    try:
        return client.resolve_title(team)
    except RequestBudgetExceeded:
        raise
    except Exception:  # noqa: BLE001
        return None


# Words that say what KIND of thing a club is, not WHICH club it is. A shared
# "BC" or "basketball" means nothing; a shared "Cantù" or "Seahorses" is the
# club's identity surviving a rename.
GENERIC_CLUB_WORDS = {
    "bc", "kk", "cb", "fc", "sc", "ac", "as", "bk", "cd", "ca", "sk", "bbc",
    "club", "basket", "basketball", "baloncesto", "pallacanestro", "basquete",
    "sports", "sport", "society", "association", "team", "nba", "league",
    "spor", "kulubu", "the", "de", "of", "and",
}


def significant_words(name: str) -> set:
    """The words in a club name that identify the club, diacritics folded."""
    s = re.sub(r"\(.*?\)", " ", strip_diacritics(name or "").casefold())
    return {w for w in re.split(r"[^a-z0-9]+", s)
            if len(w) > 2 and w not in GENERIC_CLUB_WORDS}


def same_article(requested: str, resolved: str | None) -> bool:
    """True when a club name reached an article that is still about that club.

    Two things count as reaching it. The title is the one asked for, allowing
    for the rewriting Wikipedia does to every title -- underscores, leading
    case, whitespace. Or the title it redirected to keeps a word that NAMES
    the club: "Acqua S.Bernardo Cantù" reaching "Pallacanestro Cantù" is a
    sponsor prefix falling away, and "Aisin Seahorses" reaching "SeaHorses
    Mikawa" is a club that moved city keeping its name.

    What that leaves refused is the failure this guard exists for: a defunct
    club's name redirecting to its successor, which shares nothing with it.
    "Chicago Packers" reaches "Washington Wizards"; "Anaheim Arsenal" reaches
    "Grand Rapids Gold"; "Asheville Altitude" reaches "Oklahoma City Blue".
    Each of those articles is about a real basketball team in a real city, so
    the sports-club guard passes them and only the title objects.

    Requiring the titles to match outright instead would refuse 990 of the
    2,254 club locations we already hold, against 272 for this rule -- and
    almost all of those 718 are a sponsor or short name reaching its own club.
    A club refused here keeps its blank field and goes to review, where a
    person settles it.
    """
    if resolved is None:            # nothing was learned, so nothing is refused
        return True
    a = (requested or "").replace("_", " ").strip()
    b = (resolved or "").replace("_", " ").strip()
    if not b:
        return False
    if a == b or a[:1].upper() + a[1:] == b[:1].upper() + b[1:]:
        return True
    return bool(significant_words(a) & significant_words(b))


def _richer(a: list, b: list) -> bool:
    """True if career history `a` is at least as rich as `b` (more stints)."""
    return len(a or []) >= len(b or [])


# How close two club names may be before a transfer between them is merely
# FLAGGED for review. Suppression is never distance-based (see below); this
# only decides what a human gets asked to look at.
NEAR_MISS_DISTANCE = 2


def place_index(players: list) -> tuple[dict, dict]:
    """Where each club plays, and which cities each country is known to have.

    Both come from the careers data itself rather than a gazetteer: the second
    is what lets the rename guard recognise "Beirut" in "Al Riyadi Club Beirut"
    as a place rather than a word that distinguishes two clubs.
    """
    from collections import Counter, defaultdict
    seen: dict = defaultdict(Counter)
    cities: dict = defaultdict(set)
    for p in players:
        for st in p.get("career_history") or []:
            team = (st.get("team") or "").strip()
            city = (st.get("city") or "").strip()
            country = (st.get("country") or "").strip()
            if team and (city or country):
                seen[team][(city, country)] += 1
            if city and country:
                cities[_country_key(country)].update(spelling_tokens(city))
    club = {t: c.most_common(1)[0][0] for t, c in seen.items()}
    return club, cities


def _country_key(country: str) -> str:
    return "".join(spelling_tokens(country))


def classify_move(normalizer: TeamNormalizer, prev: str, new: str, *,
                  club_places: dict | None = None,
                  cities_by_country: dict | None = None) -> tuple[bool, str]:
    """Decide whether prev -> new is a transfer, and say why.

    Returns (is_real, reason). Reasons:
      "incomplete"        one side is missing, nothing to compare
      "same-club"         both sides resolve to the same canonical club
      "spelling-variant"  the alias table does not know the pair, but the two
                          names are the same name spelled differently
      "club-rename"       one side is the other with descriptive words added,
                          which is an editor lengthening a club's name rather
                          than a player going anywhere
      "rename-review"     one name contains the other but the pair is not safe
                          to merge -- a sponsor, a reserve side, or two clubs
                          that share a name. Not posted either: a pair we will
                          not merge is still not a signing
      "near-miss"         a real move whose two clubs are suspiciously close
      "real"              a real move

    The spelling-variant arm is the one that catches what the alias table has
    not been taught yet. "Ironi Nes Ziona" -> "Ironi Ness Ziona" is one club
    and one S, but with neither spelling in team_aliases both sides normalize
    to themselves, they differ, and a phantom transfer gets logged.

    Suppression deliberately stops at spelling noise and does NOT extend to a
    small edit distance. In this dataset Palencia/Valencia, Palma/Parma,
    Iraklio/Iraklis and Chicago Rockers/Rockets are each ONE edit apart and
    each a genuinely different club, so a distance-based rule would silently
    swallow real transfers. Distance only raises a flag, and the move is still
    logged.
    """
    if not prev or not new:
        return False, "incomplete"
    a, b = normalizer.normalize(prev), normalizer.normalize(new)
    if a == b:
        return False, "same-club"
    if is_spelling_variant(a, b):
        return False, "spelling-variant"
    # A club that gains words is usually a club whose article was renamed, not
    # a player who moved. Guarded hard on the other side: a reserve-side marker
    # vetoes it outright, and two clubs in different countries that merely
    # share a name are reported rather than merged.
    places = club_places or {}
    pa, pb = places.get(a, ()), places.get(b, ())
    country = _country_key(pa[-1] if pa else (pb[-1] if pb else ""))
    same, why = rename_containment(
        a, b, place_a=pa, place_b=pb,
        known_cities=(cities_by_country or {}).get(country, frozenset()))
    # A non-empty reason means containment fired in SOME form -- the same club
    # renamed, a sponsor added, a reserve side, or two clubs that share a name.
    # None of those is a signing, so none of them is posted. The merger is a
    # much stricter judge than this: it has to be sure enough to fold two club
    # pages into one, and being unsure there is not a reason to announce a
    # transfer here. Every case is still written to the review file.
    if why:
        return False, "club-rename" if same is True else "rename-review"
    if edit_distance(spelling_key(a), spelling_key(b),
                     cap=NEAR_MISS_DISTANCE) <= NEAR_MISS_DISTANCE:
        return True, "near-miss"
    return True, "real"


def _is_real_move(normalizer: TeamNormalizer, prev: str, new: str) -> bool:
    """True only when `prev` and `new` are different clubs, not the same club
    written two ways.

    A raw string diff can fire on a club-name normalization rather than an
    actual transfer — e.g. a cached pre-alias spelling ("Beşiktaş Gain") next
    to a freshly-normalized one ("Beşiktaş"), or a club's infobox name
    changing without the player moving. Both sides go through the same
    team_aliases-backed normalizer before comparing, and a pair the alias
    table has never seen still gets caught if the two names differ only by
    case, diacritics, punctuation or doubled letters.
    """
    return classify_move(normalizer, prev, new)[0]


# Words that say an article is about a sport, any sport: a club, a team, a
# league, a competition. Deliberately broad -- the job is to exclude political
# parties, companies and villages, not to identify basketball.
SPORTS_CLUB = re.compile(
    r"\b(basketball|football|soccer|volleyball|handball|sports?|athletic|"
    r"basket|club|team|league|championship|division|arena|players?)\b", re.I)

# Location lookups refused by that guard. Cleared per run and written to
# data/logs/wrong_place_review.json, so a club with no location because its
# article was about something else is visible rather than merely blank.
REFUSED_PLACE: list[dict] = []


# Fetches refused because the article was about somebody else. Cleared at the
# start of every run(); read back into the summary and written to the review
# log, because a refusal means a record went un-updated and somebody should
# know which.
REFUSED: list[dict] = []


def _birth_year(rec: dict) -> int | None:
    m = re.search(r"\b(1[89]\d\d|20\d\d)\b", rec.get("birth_date", "") or "")
    return int(m.group()) if m else None


def right_article(db: "Database", name: str,
                  client: WikipediaClient) -> tuple[str | None, str | None, dict | None]:
    """Fetch the article for `name`, or refuse it.

    Wikipedia answers every request with something, and for a name it does not
    have it answers with the nearest thing it does: "Scotty Pippen Jr" lands on
    his father, whose career then becomes the son's. So the answer is checked
    against the question, and when it names somebody else we go looking for the
    disambiguated title instead ("Scotty Pippen Jr." -- the period is the whole
    difference) before giving up.

    Returns (wikitext, canonical_title, refusal). A refusal means no record is
    written at all: stale data beats invented data.
    """
    # The article this record was built from, when it has one and it is about
    # this person. Fourteen records are keyed on a name Wikipedia answers with
    # a disambiguation page -- "Joe Smith" is forty people -- so asking by name
    # would refuse them every run and freeze them, which for an active player
    # like Ben Sheppard means his record stops following his career. The stored
    # article is the answer we already worked out; ask for it first.
    stored = title_from_url(db.by_name.get(name, {}).get("wikipedia_url", ""))
    first = name
    if stored and stored != name and same_person(name, stored)[0]:
        first = stored

    wt, title = client.get_wikitext_and_title(first)
    tried = [{"title": first, "resolved": title}]
    ok, why = same_person(name, title) if title else (False, "no article")
    if not ok and first != name:
        # the stored article stopped being about him (renamed, merged away):
        # fall back to the name and let the usual checks judge the answer
        wt, title = client.get_wikitext_and_title(name)
        tried.append({"title": name, "resolved": title})
        ok, why = same_person(name, title) if title else (False, "no article")
    if ok and why == "suffix-added":
        # Our key catching up with the article ("Craig Porter" ->
        # "Craig Porter Jr."), unless another record already IS that article,
        # in which case the suffix belongs to the son and this is his father.
        owner = db.owner_of_article(title)
        if owner and owner != name and owner != db.resolve_by_name(name):
            ok, why = False, f"{title} already belongs to {owner}"
    if ok:
        return wt, title, None

    for cand in candidate_titles(name, _birth_year(db.by_name.get(name, {}))):
        wt2, title2 = client.get_wikitext_and_title(cand)
        tried.append({"title": cand, "resolved": title2})
        if title2 and same_person(name, title2)[0]:
            return wt2, title2, None

    kind = "missing" if not title else "wrong-person"
    return None, title, {"player": name, "resolved": title or "", "kind": kind,
                         "reason": why, "tried": tried, "date": today()}


def merge_player(db: Database, name: str, client: WikipediaClient,
                 discovered: dict, roster_players: set[str],
                 current_year: int) -> tuple[dict | None, list[str], bool]:
    """Fetch + parse a player, dedupe against existing records by canonical
    Wikipedia article, enrich locations, classify status, and upsert.

    Returns (record, new_teams, is_new). A candidate that resolves to an
    existing record (diacritics, suffix, nickname, redirect) is MERGED into it
    rather than inserted as a duplicate.
    """
    wt, canonical_title, refusal = right_article(db, name, client)
    if refusal:
        # Nothing to merge. A name Wikipedia simply does not have is the old,
        # quiet skip; an article about somebody else is a refusal, and that one
        # goes in the review log because a record went un-updated.
        if refusal["kind"] != "missing":
            REFUSED.append(refusal)
            print(f"[update] {name}: refused -> {refusal['resolved']} "
                  f"({refusal['reason']})")
        return None, [], False, None, None
    if not wt:
        # page not found (e.g. a "(1990)" disambiguated title with no matching
        # article/redirect) — skip cleanly. Return the full 5-tuple so the
        # caller's unpack never raises "expected 5, got 3".
        return None, [], False, None, None
    fresh = parse_player(wt, name, db.normalizer)
    fresh.pop("_raw_teams", None)
    fresh["parse_status"] = fresh.pop("status", "success")
    fresh_valid = bool(fresh.get("career_history"))
    curl = canonical_url(canonical_title) if canonical_title else canonical_url(name)

    # Resolve to an existing record: same key, same canonical article (URL or
    # normalized title), or same normalized display name.
    existing_name = (name if name in db.by_name
                     else db.resolve_canonical(canonical_title)
                     or db.resolve_by_name(name))
    # ...but only if that record is about this same person. The name index
    # folds Jr and Sr away, so "Jabari Smith Jr." matches his father's record;
    # letting that stand would write the son's career over the father's. Two
    # different articles are two different people, whatever the names fold to.
    if existing_name and canonical_title:
        held = db.by_name.get(existing_name, {})
        prev_title = title_from_url(held.get("wikipedia_url", "")) or existing_name
        if not same_person(prev_title, canonical_title, strict_suffix=True)[0]:
            existing_name = None
    is_new = existing_name is None
    base = db.by_name.get(existing_name, {}) if existing_name else {}
    prev_status = base.get("status")
    prev_current = base.get("current_team")

    # Choose the history source: the freshly-fetched page when it parsed and is
    # at least as rich, otherwise the existing record (so a failed/empty parse
    # like the "A. J. Green" disambiguation page never clobbers real data).
    use_fresh = fresh_valid and _richer(fresh.get("career_history"),
                                        base.get("career_history"))
    primary = fresh if use_fresh else (base or fresh)

    rec = dict(base)  # start from existing to preserve fields we don't refresh
    rec["career_history"] = primary.get("career_history", [])
    rec["current_team"] = primary.get("current_team", "")
    # scalar fields: prefer the chosen source, fall back to the other
    for f in ("position", "number", "birth_date", "birth_place", "death_date",
              "death_place", "high_school", "college", "draft",
              "nationality", "all_star"):
        val = primary.get(f) or fresh.get(f) or base.get(f)
        if val:
            rec[f] = val
    rec["parse_status"] = fresh.get("parse_status", base.get("parse_status", "success"))

    # primary key + display name + aliases
    if is_new:
        key = canonical_title or name
    else:
        key = existing_name  # keep existing (frontend-load-bearing) primary key
    # never create/persist a record with an empty name (guards against junk
    # rows like the blank-name seed artifact)
    if not str(key or "").strip():
        return None, [], False, None, None
    rec["player"] = key
    rec["display_name"] = canonical_title or rec.get("display_name") or key
    aliases = set(base.get("aliases", []))
    for alt in (name, canonical_title, base.get("player")):
        if alt and alt != key:
            aliases.add(alt)
    if aliases:
        rec["aliases"] = sorted(aliases)
    rec["wikipedia_url"] = curl or base.get("wikipedia_url", "")

    # locations
    new_teams = []
    for stint in rec["career_history"]:
        if stint["team"] not in db.locations:
            new_teams.append(stint["team"])
        db.enrich_stint(stint, client, discovered)

    # Explicit-retirement-announcement signal (bug fix): sticky once detected —
    # a transient regex miss on a later re-fetch (e.g. the prose gets copy-
    # edited) must not un-retire someone we already confirmed. The one
    # exception is a genuine comeback: if this run's fresh, richer history
    # shows a stint dated after the recorded retirement year, the retirement
    # no longer holds and the flag clears.
    fresh_retired = bool(fresh.get("retirement_announced"))
    prev_retired = bool(base.get("retirement_announced"))
    comeback = False
    if prev_retired and not fresh_retired and use_fresh:
        ret_year = re.search(r"\d{4}", base.get("retirement_date", "") or "")
        ly = last_active_year(rec["career_history"])
        if ret_year and ly and (ly == PRESENT or ly > int(ret_year.group())):
            comeback = True
    if fresh_retired or (prev_retired and not comeback):
        rec["retirement_announced"] = True
        rd = fresh.get("retirement_date") or base.get("retirement_date", "")
        if rd:
            rec["retirement_date"] = rd
    else:
        rec.pop("retirement_announced", None)
        rec.pop("retirement_date", None)

    rec["status"] = classify_status(
        rec, on_nba_roster=name in roster_players or key in roster_players,
        current_year=current_year, retirement_announced=rec.get("retirement_announced", False))
    rec["last_updated"] = today()

    # upsert: if we merged into a different existing key, drop the queue name
    if not is_new and key != name and name in db.by_name:
        db.by_name.pop(name, None)
    db.by_name[key] = rec
    if key not in db.order:
        db.order.append(key)
    db._index(rec)
    return rec, new_teams, is_new, prev_status, prev_current


def _dedupe(seq) -> list[str]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def _by_status(db: Database, status: str) -> list[str]:
    """DB players with the given tracking status, least-recently-updated first."""
    names = [n for n, r in db.by_name.items() if r.get("status") == status]
    return sorted(names, key=lambda n: db.by_name[n].get("last_updated", "0000-00-00"))


def build_queue(db: Database, mode: str, player: str | None,
                roster_players: set[str]) -> list[str]:
    if mode == "single":
        return [player] if player else []

    overseas = _by_status(db, OVERSEAS_ACTIVE)
    if mode == "full_overseas":
        return overseas

    nba_active = _by_status(db, NBA_ACTIVE)
    # Map roster candidates to existing records by canonical name key, so a
    # roster spelling that differs from the DB spelling (Şengün vs Sengun) is
    # recognized as the same player rather than a newcomer.
    on_roster = {db.resolve_by_name(c) for c in roster_players}
    on_roster.discard(None)
    # roster newcomers not yet tracked (rookies / signings)
    new_players = _dedupe(sorted(c for c in roster_players
                                 if db.resolve_by_name(c) is None))
    # NBA-active players not matched by any roster candidate: re-check whether
    # they moved overseas (-> overseas_active) or retired. Canonical matching
    # stops variant-spelling players (Alperen Sengun) being re-fetched as
    # "dropped" every run while their diacritic spelling is added as "new".
    dropped = [n for n in nba_active if n not in on_roster]
    stale_nba = [n for n in nba_active if n in on_roster]

    if mode == "full":
        # every active player (NBA + overseas) plus any roster newcomers
        pool = new_players + dropped + stale_nba + overseas
    else:  # incremental
        pool = new_players + dropped + overseas + stale_nba
    return _dedupe(pool)


def run(mode: str, player: str | None, delay: float, max_requests: int) -> dict:
    db = Database()
    client = WikipediaClient(delay=delay, max_requests=max_requests)
    current_year = dt.datetime.now(dt.timezone.utc).year
    REFUSED.clear()
    REFUSED_PLACE.clear()
    summary = {"date": today(), "mode": mode, "players_updated": [],
               "new_players": [], "new_teams": [], "team_moves": [],
               "spelling_review": [],
               "status_changes": [], "newly_overseas": [], "newly_retired": [],
               "requests": 0, "budget_exhausted": False,
               "queue_size": 0, "queue_completed": False}

    if mode == "review":
        _run_review(db, client, summary)
    else:
        roster_players: set[str] = set()
        if mode in ("incremental", "full"):
            rosters = fetch_all_rosters(client)
            for team_players in rosters.values():
                roster_players.update(team_players)
        queue = build_queue(db, mode, player, roster_players)
        # Built once from the database as it stands: the rename guard needs to
        # know where a club plays and which cities a country is known to have.
        club_places, cities_by_country = place_index(
            [db.by_name[n] for n in db.order])
        summary["queue_size"] = len(queue)
        summary["queue_completed"] = True   # cleared below if the budget cuts the run short
        discovered: dict = {}
        for name in queue:
            try:
                rec, new_teams, is_new, prev_status, prev_current = merge_player(
                    db, name, client, discovered, roster_players, current_year)
            except RequestBudgetExceeded:
                summary["budget_exhausted"] = True
                summary["queue_completed"] = False
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[update] {name}: failed ({exc})")
                continue
            if rec is None:
                continue
            key = rec["player"]
            if is_new:
                summary["new_players"].append(key)
            summary["players_updated"].append(key)
            summary["new_teams"].extend(new_teams)
            new_current = rec.get("current_team")
            is_move, why = classify_move(db.normalizer, prev_current,
                                         new_current,
                                         club_places=club_places,
                                         cities_by_country=cities_by_country)
            if is_move:
                summary["team_moves"].append(
                    {"player": key, "from": prev_current, "to": new_current})
            # A pair that looks like one club spelled two ways never reaches the
            # ledger, but it is not discarded either: it goes to the review file
            # so the alias table can be taught the pair. "near-miss" DID post.
            if why in ("spelling-variant", "near-miss", "club-rename",
                       "rename-review"):
                summary["spelling_review"].append(
                    {"player": key, "from": prev_current, "to": new_current,
                     "reason": why, "posted": is_move})
            new_status = rec.get("status")
            if prev_status and new_status and prev_status != new_status:
                summary["status_changes"].append(
                    {"player": key, "from": prev_status, "to": new_status})
                if new_status == OVERSEAS_ACTIVE:
                    summary["newly_overseas"].append(key)
                elif new_status == RETIRED_STATUS:
                    summary["newly_retired"].append(key)

    summary["wrong_article"] = list(REFUSED)
    summary["wrong_place"] = list(REFUSED_PLACE)
    summary["requests"] = client.requests_made
    summary["new_teams"] = sorted(set(summary["new_teams"]))
    _persist(db, summary)
    return summary


def _run_review(db: Database, client: WikipediaClient, summary: dict) -> None:
    resolved = []
    for team in list(db.review):
        try:
            found = db._discover_location(team, client)
        except RequestBudgetExceeded:
            summary["budget_exhausted"] = True
            break
        if found.get("city") and found.get("country"):
            db.locations[team] = {"team": team, **found,
                                  "league": db.locations.get(team, {}).get("league", "")}
            db.review.pop(team, None)
            resolved.append(team)
    summary["resolved_teams"] = resolved


def _persist(db: Database, summary: dict) -> None:
    # defensively drop any empty-name record so junk rows never reach disk
    for empty in [n for n in list(db.order) if not str(n or "").strip()]:
        db.by_name.pop(empty, None)
        db.order.remove(empty)
    players = [db.by_name[n] for n in db.order if str(n or "").strip()]

    # Split combined era names ("New Orleans Hornets/Pelicans") into the single
    # era their years actually fall in, BEFORE anything is written -- so the
    # careers file and every file derived from it below get the same resolved
    # values in one pass.
    #
    # This has to run every time, not once: Wikipedia writes these combined
    # strings, so each re-fetch regenerates them. They matter because a combined
    # name usually carries NO city/state/country, and a stint with no location
    # is dropped from the career map entirely -- Anthony Davis's 2012-19 New
    # Orleans stint simply wasn't on his map. Non-NBA combined names (ABA, G
    # League, international) have no authoritative split year and are
    # deliberately left alone; see split_combined_teams.
    split_n, _split_changes, split_skipped = split_combined_teams.resolve_players(players)
    summary["combined_names_split"] = split_n
    summary["combined_names_left"] = sum(split_skipped.values())
    if split_n:
        print(f"[split] resolved {split_n} combined team name(s); "
              f"left {sum(split_skipped.values())} with no authoritative split")

    # Put every career_history in canonical order (start asc, then END asc) on
    # the way out, so positional reads -- [0] for the first stop, [-1] for the
    # last -- are correct by construction for every consumer downstream rather
    # than depending on the order Wikipedia happened to list the stints in.
    # Like the split above, this runs every time: a re-fetch reintroduces
    # whatever order the source used.
    ordered_n = stint_order.order_players(players)
    summary["stints_reordered"] = ordered_n
    if ordered_n:
        print(f"[order] reordered career_history for {ordered_n} player(s)")

    write_json(CAREERS, players)
    write_json(LOCATIONS, dict(sorted(db.locations.items())))
    write_json(REVIEW, dict(sorted(db.review.items())))

    # active/retired refresh from the stored tracking status
    nba_active = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    overseas = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in players if p.get("status") == RETIRED_STATUS)
    write_json(ACTIVE, {"count": len(nba_active) + len(overseas),
                        "nba_active": nba_active, "overseas_active": overseas})
    write_json(RETIRED, {"count": len(retired), "players": retired})

    # keep the map data file in sync (drop non-map metadata for a lean file)
    map_players = []
    for p in players:
        # `player` stays the primary key the map/quiz already index on (ASCII,
        # frontend-load-bearing). display_name carries the canonical spelling
        # (e.g. diacritics) for the frontend to adopt when ready — index.html
        # ignores unknown fields, so adding it is safe today.
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("last_updated"):
            mp["last_updated"] = p["last_updated"]
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        if p.get("nationality"):
            mp["nationality"] = p["nationality"]
        if p.get("all_star") is not None:
            mp["all_star"] = p["all_star"]
        if p.get("all_star_count") is not None:
            mp["all_star_count"] = p["all_star_count"]
        map_players.append(mp)
    write_json(ROOT_MAP_FILE, map_players)

    _append_logs(summary)
    _append_transactions(summary)
    _append_spelling_review(summary)
    _append_article_review(summary)
    _append_place_review(summary)
    _notify_slack(db, summary)


def _append_transactions(summary: dict) -> None:
    """Append this run's current_team changes to the append-only ledger.

    Each ``team_moves`` entry ({player, from, to}) becomes a dated transaction
    record. The file is only ever appended to; existing records are preserved.
    """
    moves = summary.get("team_moves", [])
    if not moves:
        return
    ledger = load_json(TRANSACTIONS, {"transactions": []})
    if isinstance(ledger, list):  # tolerate a bare-list file shape
        ledger = {"transactions": ledger}
    date = summary.get("date", today())
    for mv in moves:
        ledger["transactions"].append({
            "player": mv["player"],
            "from_team": mv.get("from", ""),
            "to_team": mv.get("to", ""),
            "date": date,
        })
    write_json(TRANSACTIONS, ledger)


def _append_place_review(summary: dict) -> None:
    """Append the location lookups the sports-club guard refused."""
    rows = summary.get("wrong_place", [])
    if not rows:
        return
    doc = load_json(PLACE_REVIEW, {"refusals": []})
    doc["refusals"].extend(rows)
    write_json(PLACE_REVIEW, doc)


def _append_article_review(summary: dict) -> None:
    """Append the fetches refused because the article named somebody else.

    A refusal is a record that did not update, so it cannot be silent. Each row
    carries what was asked for, what came back, and every title tried after.
    """
    rows = summary.get("wrong_article", [])
    if not rows:
        return
    doc = load_json(ARTICLE_REVIEW, {"refusals": []})
    doc["refusals"].extend(rows)
    write_json(ARTICLE_REVIEW, doc)


def _append_spelling_review(summary: dict) -> None:
    """Append this run's suppressed / flagged club-name pairs, append-only.

    These are the pairs the move detector would not vouch for: either it
    refused to log a transfer because the two names are one club spelled two
    ways, or it logged one but the names are close enough to be worth a look.
    Each entry is a candidate row for data/teams/team_aliases.json.
    """
    pairs = summary.get("spelling_review", [])
    if not pairs:
        return
    doc = load_json(SPELLING_REVIEW, {"pairs": []})
    if isinstance(doc, list):
        doc = {"pairs": doc}
    date = summary.get("date", today())
    for p in pairs:
        doc["pairs"].append({**p, "date": date})
    write_json(SPELLING_REVIEW, doc)


# Every name that counts as "an NBA team" for Slack sentences: current
# franchises plus historical era names (a 1998 stint says "Vancouver
# Grizzlies", not "Memphis Grizzlies" -- both must register as NBA when
# walking a career history for the most recent NBA franchise).
_NBA_SLACK_NAMES = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA_SLACK_NAMES.add(_n)


def _last_nba_team(record: dict, exclude_team: str = "") -> str:
    """Most recent NBA franchise in the player's career history, or ''.

    Walks the history from the latest stint backwards. This is NOT the
    ledger's from_team: a player moving between two overseas clubs has an
    overseas from_team, but the sentence should still say which NBA team
    they are known from.

    ``exclude_team`` is the move's destination, and it matters: by the time
    the Slack sentence is built, the update has ALREADY appended the new
    stint to career_history. Without excluding it, an NBA signing resolved
    to the team the player just joined -- "Dennis Schroeder, formerly with
    the Charlotte Hornets, has signed with the Charlotte Hornets". Only the
    trailing run of destination stints is skipped, so an earlier, genuinely
    former spell with the same franchise (a player returning to a club he
    left years ago) still reads correctly.
    """
    history = list(record.get("career_history", []) or [])
    while exclude_team and history and history[-1].get("team") == exclude_team:
        history.pop()
    for stint in reversed(history):
        if stint.get("team") in _NBA_SLACK_NAMES:
            return stint["team"]
    return ""


def _slack_sentence(mv: dict, db, locations: dict) -> str:
    """One natural-language line per move, e.g.:

    "Former Golden State Warriors player Jeenathan Williams has joined
    Chiba Jets of Japan."

    - "Former X player" names the player's most recent NBA franchise
      EXCLUDING the one he just joined (see _last_nba_team) -- the record
      already contains the destination stint at this point. When the
      destination is his only NBA team, that lookup comes back empty and
      the ledger's from_team is used instead, which is the real previous
      club (e.g. Caleb Houstan: College Park Skyhawks -> Pelicans). With no
      previous team at all the sentence simply opens with the player's name.
      The team takes no article in this shape ("Former Utah Jazz player",
      never "Former the Utah Jazz player"), NBA or not.
    - An NBA destination reads "has joined the X" (no country); a
      non-NBA destination appends "of <country>" from team_locations,
      omitted entirely when the country is unknown.
    """
    player = mv.get("player", "")
    to_team = mv.get("to_team", "")
    from_team = mv.get("from_team", "")

    record = db.by_name.get(player) or db.by_name.get(db.resolve_by_name(player) or "") or {}
    former = _last_nba_team(record, to_team) or from_team
    # Belt and braces: whatever the source, the sentence must never name the
    # destination as the team the player is "formerly with" -- that reads as
    # a bug to anyone in the channel even when the underlying move is real.
    if former == to_team:
        former = ""
    if to_team in _NBA_SLACK_NAMES:
        dest = f"the {to_team}"
    else:
        country = (locations.get(to_team) or {}).get("country", "")
        dest = f"{to_team} of {country}" if country else to_team

    subject = f"Former {former} player {player}" if former else player
    return f"{subject} has joined {dest}."


def _slack_payload(new_tx: list[dict], db, date: str) -> dict:
    """Batched Slack message: header, one sentence per move, closing link."""
    locations = load_json(LOCATIONS, {})
    n = len(new_tx)
    # "detected {date}", not just the bare date: this is the day the pipeline
    # picked the move up, which can lag the actual signing by however long the
    # source took to update (and by the rotation cycle). Wikipedia career
    # stints are year-granular, so a real signing date isn't available to
    # print instead -- saying which kind of date this is, is the honest fix.
    header = f"\U0001F3C0 *{n} new team move{'s' if n != 1 else ''}* \u2014 detected {date}"
    lines = [header, ""]
    for mv in new_tx:
        lines.append(f"\u2022 {_slack_sentence(mv, db, locations)}")
    lines.append("")
    lines.append("https://hoopsmatic.com/nba-career-map")
    return {"text": "\n".join(lines)}


def _post_to_slack(webhook_url: str, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _notify_slack(db, summary: dict) -> None:
    """Post one batched Slack message for this run's newly-detected moves.

    Additive and non-blocking: reuses summary["team_moves"] (the same
    _is_real_move-gated list _append_transactions just wrote), so phantom
    club-renames never trigger an alert here either, and posts nothing when
    a run finds no moves. SLACK_MARKER tracks how many ledger entries have
    been posted so far (a plain count, since TRANSACTIONS is append-only);
    diffing against that -- rather than just posting summary["team_moves"]
    directly -- means a failed post is retried (and only it, batched with
    whatever's new) on the next run instead of being silently lost, while a
    successful post's marker advance makes a workflow re-run a no-op. The
    marker is only ever advanced after a confirmed-successful POST.
    """
    moves = summary.get("team_moves", [])
    if not moves:
        return
    ledger = load_json(TRANSACTIONS, {"transactions": []})
    if isinstance(ledger, list):
        ledger = {"transactions": ledger}
    all_tx = ledger["transactions"]

    marker = load_json(SLACK_MARKER, {})
    # A missing/never-initialized marker defaults to "only this run's moves"
    # -- never the full historical ledger -- so a lost marker file fails
    # safe against backfilling old entries rather than flooding the channel.
    posted_count = marker.get("posted_count", max(0, len(all_tx) - len(moves)))
    new_tx = all_tx[posted_count:]
    if not new_tx:
        return

    webhook = os.environ.get("SLACK_SIGNINGS_WEBHOOK", "")
    if not webhook:
        return  # secret not configured (e.g. a local/dev run) -- skip silently

    payload = _slack_payload(new_tx, db, summary.get("date", today()))
    try:
        _post_to_slack(webhook, payload)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Never log the webhook URL or its contents -- only the exception
        # type. Marker is NOT advanced, so these entries retry next run.
        print(f"[slack] notification failed: {type(exc).__name__}")
        return
    write_json(SLACK_MARKER, {"posted_count": len(all_tx)})


def _append_logs(summary: dict) -> None:
    log = load_json(UPDATE_LOG, {"runs": []})
    log["runs"].append(summary)
    write_json(UPDATE_LOG, log)

    lines = [
        f"## {summary['date']} — {summary['mode']} run",
        "",
        f"- Players updated: **{len(summary['players_updated'])}**"
        f" ({len(summary['new_players'])} new)",
        f"- New teams discovered: **{len(summary['new_teams'])}**",
        f"- Team moves detected: **{len(summary['team_moves'])}**",
        f"- Club-name pairs held for review: "
        f"**{len(summary.get('spelling_review', []))}**",
        f"- Status changes: **{len(summary.get('status_changes', []))}**"
        f" ({len(summary.get('newly_overseas', []))} → overseas,"
        f" {len(summary.get('newly_retired', []))} → retired)",
        f"- Wikipedia requests: {summary['requests']}"
        + ("  ⚠️ budget exhausted — queue truncated, continues next run"
           if summary.get("budget_exhausted")
           else (f"  ✅ queue complete ({summary.get('queue_size', 0)} queued)"
                 if summary.get("queue_completed") else "")),
    ]
    if summary["new_players"]:
        lines.append(f"- New players: {', '.join(summary['new_players'][:25])}"
                     + (" …" if len(summary["new_players"]) > 25 else ""))
    if summary["new_teams"]:
        lines.append(f"- New teams: {', '.join(summary['new_teams'][:25])}"
                     + (" …" if len(summary["new_teams"]) > 25 else ""))
    for mv in summary["team_moves"][:25]:
        lines.append(f"  - {mv['player']}: {mv['from']} → {mv['to']}")
    for sc in summary.get("status_changes", [])[:25]:
        lines.append(f"  - {sc['player']}: [{sc['from']} → {sc['to']}]")
    lines.append("")
    header = "" if CHANGELOG.exists() else "# Career Database Changelog\n\n"
    existing = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    CHANGELOG.parent.mkdir(parents=True, exist_ok=True)
    CHANGELOG.write_text(header + "\n".join(lines) + "\n" + existing,
                         encoding="utf-8")


def parse_args():
    ap = argparse.ArgumentParser(description="Update NBA career database.")
    ap.add_argument("--mode",
                    choices=["incremental", "full", "full_overseas", "single",
                             "review"],
                    default="incremental")
    ap.add_argument("--player", default=None, help="player name for --mode single")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-requests", type=int, default=650)
    return ap.parse_args()


def main():
    args = parse_args()
    summary = run(args.mode, args.player, args.delay, args.max_requests)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                      for k, v in summary.items()}, indent=2))
    # expose a one-line commit message for the workflow
    msg = (f"Auto-update: {summary['date']} - "
           f"{len(summary['players_updated'])} players updated, "
           f"{len(summary['new_teams'])} new teams")
    n_status = len(summary.get("status_changes", []))
    if n_status:
        msg += f", {n_status} status changes"
    (LOGS).mkdir(parents=True, exist_ok=True)
    (LOGS / "last_commit_message.txt").write_text(msg + "\n", encoding="utf-8")
    print(msg)


if __name__ == "__main__":
    main()
