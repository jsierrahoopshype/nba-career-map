"""Bulk-locate the clubs that have NEITHER a city NOR a country.

SCOPE IS STRICT. Only clubs whose stints carry neither field are considered.
Anything already holding a city, a country, or both is read (as evidence) but
never written. A club that fails to resolve is left exactly as it was.

A NOTE ON THE GEOCODER. The plan was Nominatim. This environment's egress
policy refuses it -- the proxy answers 403 to CONNECT for
nominatim.openstreetmap.org, and equally for photon, opencage, maps.co and
download.geonames.org -- so no live geocoding service is reachable from here
at all. Instead of a service, this uses a LOCAL GeoNames extract (the
`geonamescache` package from PyPI: 34,006 populated places with country code,
population and alternate names). That answers the same question -- "is this
token a real populated place, and where?" -- with three advantages worth
noting: no rate limit, no usage-policy exposure, and the confidence rules
below can inspect population and name-collision counts directly rather than
trusting a single opaque ranked result. It is inherently reproducible, and
results are still written to a cache so a re-run does no work.

THE PIPELINE

  Step 1, zero-risk, from data already in this repo:
    (a) the club's name, normalised, is identical to a club that already has a
        location ("Al Riffa" / "Al-Riffa");
    (b) the club's name contains a city name already in team_locations, and
        that city name resolves to exactly one (city, country) there.

  Step 2, the gazetteer: strip team-name words ("Knights", "BC", "Basket",
    "Club" and ~200 more) and look up what remains as a place.

  Tiers: only HIGH is applied. MEDIUM and LOW go to the review file with the
    candidate and the reason, and change nothing.

  Plottability: a HIGH result is only applied if the map can actually place it
    -- an exact COORDS hit, or a country present in index.html's FALLBACK
    table. A fill that would render nowhere is not a fill, so it is downgraded
    to review and reported. (Countries the FALLBACK table is missing are listed
    at the end for a follow-up commit, as in round 7.)

Idempotent. Run:  python3 scripts/bulk_locate.py [--apply]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
INDEX_HTML = ROOT / "index.html"
CACHE = ROOT / "data" / "teams" / "bulk_locate_cache.json"
REVIEW_OUT = ROOT / "logs" / "bulk_locate_review.json"


# --------------------------------------------------------------- normalising
def fold(s) -> str:
    """Lowercase, strip diacritics and punctuation. Used for every comparison
    so "Besançon" and "Besancon" are the same token."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower())


def toks(s) -> list[str]:
    return [t for t in fold(s).split() if t]


# Words that belong to a team's NAME, not to a place. Stripped before the
# gazetteer sees the name, because "Anchorage Northern Knights" is not a place
# but "Anchorage" is. Deliberately long: a leftover nickname is the main way a
# lookup goes wrong.
TEAM_WORDS = set("""
bc kk ku sk sc fc cb cd ca ac as us cs ub bk bbc abc cbc pbc
club clube deportivo deportiva sports sport sporting athletic atletico atletica
basket basketball basquete baloncesto bala pallacanestro koszykowka krepsinio
team teams squad academy academia university universidad universitario college
institute institut sociedad associacao association asociacion federation
kings king queens knights lions lion tigers tiger bears bear wolves wolf
eagles eagle hawks hawk falcons falcon panthers panther jaguars leopards
bulls bull rams ram stags bucks stallions mustangs broncos colts
warriors warrior raiders raider pirates buccaneers vikings titans giants
sharks dolphins whales seahawks gulls pelicans penguins ravens owls
stars star comets meteors rockets jets flyers pilots aviators
thunder lightning storm cyclone hurricane tornado twisters heat flame flames
blaze fire sparks suns sun moon eclipse
dragons dragon phoenix griffins gladiators spartans trojans centurions
legends legend generals admirals marines sailors mariners
miners diggers drillers oilers packers brewers bakers weavers
express jets bullets pistons wheels riders rangers scouts pioneers
force power energy dynamo dinamo spirit pride pace
red blue green black white gold golden silver crimson scarlet royal
north south east west northern southern eastern western central
new old first second junior senior youth
i ii iii b c d
bs bb ii
""".split())

# Names that ARE places but read as team words far more often than as the
# club's home. A false positive here silently sends a club to the wrong
# continent, so they are never used as a place token on their own.
RISKY_PLACE_WORDS = set("""
real racing union victoria olympia olympic olimpia olympiakos independiente
nacional national international central america africa asia europa europe
sport sporting athletic estudiantes libertad progreso juventud unidos
santa san sao saint st mount monte casa villa vista buena
""".split())


# Every HIGH result was read by hand before this round was applied. These are
# the ones that survived the rules but are wrong or unsafe, with the reason --
# they are forced to review rather than quietly applied. The rules that caught
# the rest were tightened first (canonical-name-only, edge-of-name, population,
# single-country); this list is what a human found that structure could not.
AUDIT_EXCLUDE: dict[str, str] = {
    "AC Golfe-Juan-Vallauris":
        "resolved to Golfe, Angola -- the club is Golfe-Juan in Vallauris, France; "
        "'Golfe' is a coincidental match",
    "Rosalia de Castro":
        "resolved to Castro, Chile -- the club is Spanish (named for the Galician "
        "poet Rosalia de Castro), 'Castro' here is a surname not a city",
    "Panna Firenze":
        "resolved to Panna, India -- the place word is Firenze (Florence, Italy); "
        "'Panna' is the sponsor",
    "Vasco da Gama":
        "resolved to Vasco da Gama, India -- the basketball club is Brazilian "
        "(Rio de Janeiro); the Goan town is a coincidence",
    "Vasco da gama":
        "same as 'Vasco da Gama': the club is Brazilian, not Goan",
    "Universidad de Los Lagos":
        "resolved to Lagos, Nigeria -- the university is Chilean (Los Lagos Region)",
    "Ipiranga Santa Catarina":
        "resolved to Santa Catarina, Mexico -- Santa Catarina is a Brazilian state "
        "and the club is Brazilian",
    "San Martin de Marcos Juarez":
        "resolved to Juarez, Mexico -- Marcos Juarez is in Cordoba, Argentina",
    "Coviran Cervezas Alhambra":
        "resolved to Alhambra, USA -- Cervezas Alhambra is a Spanish sponsor; the "
        "club is in Granada, Spain",
    "South West Metro Pirates":
        "resolved to Metro, Indonesia -- the club is Australian (Queensland); "
        "'Metro' is a league word",
    "Hamilton Pat Pavers":
        "resolved to Hamilton, New Zealand -- a North American barnstorming club; "
        "Hamilton Ohio and Hamilton Ontario are both likelier and undecided",
    "Toyota Motors Pacers":
        "resolved to Toyota, Japan -- the Pacers were a Philippine PBA club and "
        "Toyota is the sponsor, not the city",
    "Toyota Pacers":
        "same as 'Toyota Motors Pacers': a PBA club with a sponsor name",
    "Al Arabi":
        "resolved to Arabi, Ethiopia -- Al Arabi is a Kuwaiti/Qatari club name; "
        "round 5 already sent it to review as unresolvable from the name alone",
    "Al Arabi SC": "same as 'Al Arabi'",
    "Al-Arabi": "same as 'Al Arabi'",
    "Al-Arabi SC": "same as 'Al Arabi'",
    "Al Hala":
        "resolved to Hala, Pakistan -- Al Hala is a Bahraini club (Muharraq)",
    "Al Ahli":
        "the bare name is shared by unrelated clubs in Saudi Arabia, Egypt, the "
        "UAE, Bahrain and Libya; matching the hyphenated Jeddah entry does not "
        "make it that club",
    "Al Hilal":
        "the bare name is shared by clubs in Saudi Arabia, Sudan and Libya",
    "Paysandu BB":
        "resolved to Paysandu, Uruguay -- Paysandu Sport Club (Belem, Brazil) is "
        "an equally good reading of the bare name",
}
_AUDIT_EXCLUDE = {fold(k).strip(): v for k, v in AUDIT_EXCLUDE.items()}


# --------------------------------------------------------------- map lookup
def _map_tables() -> tuple[set, set]:
    """COORDS keys and FALLBACK countries, read straight out of index.html so
    plottability is judged against what the page will really do."""
    src = INDEX_HTML.read_text(encoding="utf-8")
    coords = set(json.loads(re.search(r"const COORDS = (\{.*?\});", src, re.S).group(1)))
    fb = set(json.loads(re.search(r"const FALLBACK = (\{.*?\});", src, re.S).group(1)))
    return coords, fb


def plottable(city: str, state: str, country: str, coords: set, fb: set) -> bool:
    if not city or not country:
        return False
    return (f"{city}|{state}|{country}" in coords
            or f"{city}||{country}" in coords
            or country in fb)


# --------------------------------------------------------------- the targets
def targets(db: list) -> dict[str, int]:
    """Club -> stint count, for stints carrying NEITHER field."""
    out: dict[str, int] = collections.Counter()
    for p in db:
        for s in p.get("career_history") or []:
            if not s.get("city") and not s.get("country"):
                out[s.get("team", "")] += 1
    out.pop("", None)
    return dict(out)


# --------------------------------------------------------------- step 1
def step1_index(locations: dict, exclude: set):
    """Evidence drawn only from clubs located BEFORE this round.

    `exclude` is every club this round touches. Without it the pass is not
    idempotent: once a HIGH result is written, the next run sees it as evidence
    and resolves further clubs from it, so the answer depends on how many times
    the script has been run and this round's own guesses become the basis for
    more guesses. Evidence must be independently established.
    """
    by_name: dict[str, set] = collections.defaultdict(set)
    by_city: dict[str, set] = collections.defaultdict(set)
    for team, v in locations.items():
        if team in exclude:
            continue
        if not (v.get("city") and v.get("country")):
            continue
        trip = (v["city"], v.get("state", ""), v["country"])
        by_name[fold(team).strip()].add(trip)
        by_city[fold(v["city"]).strip()].add(trip)
    return by_name, by_city


def step1(club: str, by_name: dict, by_city: dict, locations: dict):
    """Returns (tier, triple, reason, how)."""
    # Step 0, and the only rule that involves no inference at all: the club's
    # OWN team_locations entry is already complete and its stints simply were
    # never enriched from it. Six clubs are in this state, all of them combined
    # names the enrichment pass skipped.
    own = locations.get(club) or {}
    if own.get("city") and own.get("country"):
        return ("HIGH", (own["city"], own.get("state", ""), own["country"]),
                "the club's own team_locations entry is already complete; its "
                "stints were never enriched from it", "own-entry")
    f = fold(club).strip()
    if f in by_name:
        same = by_name[f]
        if len(same) == 1:
            return ("HIGH", next(iter(same)),
                    "name is identical to an already-located club", "name-match")
        # "Al Ahli" folds onto Jeddah, Dubai, Manama, Tripoli AND Benghazi. A
        # name that is shared by several real clubs identifies none of them.
        return ("MEDIUM", sorted(same)[0],
                f"the name is shared by {len(same)} already-located clubs in different "
                "places (" + "; ".join(f"{a}, {c}" for a, _, c in sorted(same)[:4]) + ")",
                "name-match")

    # A combined name ("Anaheim Amigos/Los Angeles Stars") is two clubs; a
    # single location cannot be right for both, so it is never auto-applied.
    combined = "/" in club

    t = toks(club)
    found: set = set()
    edge_span = False
    for n in range(min(4, len(t)), 0, -1):
        for i in range(len(t) - n + 1):
            span = " ".join(t[i:i + n])
            if n == 1 and (span in RISKY_PLACE_WORDS or len(span) < 4):
                continue
            if span in by_city:
                found |= by_city[span]
                if i == 0 or i + n == len(t):
                    edge_span = True
        if found:
            break
    if not found:
        return None, None, "", ""
    if len(found) > 1:
        countries = {c for _, _, c in found}
        return ("MEDIUM", sorted(found)[0],
                f"city name in the club name matches {len(found)} located places "
                f"across {len(countries)} countr{'y' if len(countries)==1 else 'ies'}: "
                + "; ".join(f"{a}, {c}" for a, _, c in sorted(found)[:4]),
                "city-in-name")
    trip = next(iter(found))
    if not edge_span:
        return ("MEDIUM", trip,
                f"the city match ({trip[0]}, {trip[2]}) sits in the MIDDLE of the club "
                "name, where a coincidental common noun is as likely as a place",
                "city-in-name")
    if combined:
        return ("MEDIUM", trip,
                "combined club name (contains '/'), so one location cannot be "
                f"right for both halves; the city match was {trip[0]}, {trip[2]}",
                "city-in-name")
    return ("HIGH", trip,
            f"club name contains {trip[0]!r}, which is one unambiguous place in "
            "the locations table", "city-in-name")


# --------------------------------------------------------------- step 2
def gazetteer():
    import geonamescache
    gc = geonamescache.GeonamesCache()
    countries = {c["iso"]: c["name"] for c in gc.get_countries().values()}
    by_name: dict[str, list] = collections.defaultdict(list)
    for c in gc.get_cities().values():
        rec = {"name": c["name"], "cc": c["countrycode"], "pop": c.get("population") or 0,
               "canonical": True}
        by_name[fold(c["name"]).strip()].append(rec)
        # Alternate names catch endonyms ("Muenchen" for Munich) but are noisy,
        # so they are only indexed for reasonably large places.
        if rec["pop"] >= 50000:
            for alt in c.get("alternatenames") or []:
                a = fold(alt).strip()
                if a and a.isascii() and len(a) >= 4:
                    by_name[a].append({**rec, "canonical": False})
    return by_name, countries


# Country names as the map spells them, where GeoNames differs.
COUNTRY_ALIAS = {
    "United States": "USA",
    "Russian Federation": "Russia",
    "Korea, Republic of": "South Korea",
    "Korea, Democratic People's Republic of": "North Korea",
    "Iran, Islamic Republic of": "Iran",
    "Taiwan, Province of China": "Taiwan",
    "Venezuela, Bolivarian Republic of": "Venezuela",
    "Bolivia, Plurinational State of": "Bolivia",
    "Tanzania, United Republic of": "Tanzania",
    "Syrian Arab Republic": "Syria",
    "Viet Nam": "Vietnam",
    "Czechia": "Czech Republic",
    "Macedonia": "North Macedonia",
    "Bosnia and Herzegovina": "Bosnia and Herzegovina",
    "Cote d'Ivoire": "Ivory Coast",
    "Congo, the Democratic Republic of the": "DR Congo",
    "Moldova, Republic of": "Moldova",
    "Palestine, State of": "Palestine",
    "United Kingdom": "United Kingdom",
    "Hong Kong": "Hong Kong",
    "Macao": "China",
    # GeoNames spells several of these differently from the map's own tables.
    "The Netherlands": "Netherlands",
    "Congo, The Democratic Republic of the": "DR Congo",
    "Democratic Republic of the Congo": "DR Congo",
    "Republic of Korea": "South Korea",
}


def step2(club: str, gaz: dict, countries: dict):
    """Strip team words, look the remainder up as a place."""
    t = [w for w in toks(club) if w not in TEAM_WORDS]
    if not t:
        return ("LOW", None,
                "every token in the name is a team word, nothing left to look up", "")

    # Longest span first: "san sebastian" beats "san".
    combined = "/" in club
    for n in range(min(3, len(t)), 0, -1):
        for i in range(len(t) - n + 1):
            span = " ".join(t[i:i + n])
            if n == 1 and (span in RISKY_PLACE_WORDS or len(span) < 4):
                continue
            hits = gaz.get(span)
            if not hits:
                continue
            edge = (i == 0 or i + n == len(t))
            best = {}
            for h in hits:                       # keep the biggest per country
                if h["pop"] > best.get(h["cc"], {"pop": -1})["pop"]:
                    best[h["cc"]] = h
            cands = sorted(best.values(), key=lambda h: -h["pop"])
            top = cands[0]
            cname = COUNTRY_ALIAS.get(countries.get(top["cc"], ""), countries.get(top["cc"], ""))
            trip = (top["name"], "", cname)
            others = ", ".join(
                f"{COUNTRY_ALIAS.get(countries.get(c['cc'],''), countries.get(c['cc'],''))}"
                for c in cands[1:4])

            # HIGH is deliberately narrow. Every one of these conditions was
            # added because relaxing it produced a wrong answer in the audit:
            #   canonical  -- "Ginebra" is an ALTERNATE name for Geneva, and
            #                 sent the Philippine club Barangay Ginebra to
            #                 Switzerland;
            #   edge       -- a place word in the MIDDLE of a name is usually a
            #                 sponsor or a common noun ("Covi ran Cervezas
            #                 Alhambra", "Sichuan Panda");
            #   population -- small same-named places are mostly coincidence
            #                 ("Lobos Cantabria" -> Lobos, Argentina);
            #   one country-- anything else is a guess between countries;
            #   not combined -- a "/" name is two clubs and one location cannot
            #                 be right for both.
            why_not = []
            if not top.get("canonical"):
                why_not.append(f"{span!r} is only an ALTERNATE name for {top['name']}")
            if not edge:
                why_not.append(f"{span!r} sits mid-name, where sponsors and common nouns live")
            if top["pop"] < 50000:
                why_not.append(f"{top['name']} is small (pop {top['pop']:,})")
            if len(cands) > 1:
                why_not.append(f"{span!r} is also a place in {others}")
            if combined:
                why_not.append("combined club name (contains '/'), two clubs in one string")

            if not why_not:
                return ("HIGH", trip,
                        f"{span!r} is the {'first' if i == 0 else 'last'} name token and is a "
                        f"populated place (pop {top['pop']:,}) in exactly one country, {cname}",
                        span)
            return ("MEDIUM", trip,
                    f"best candidate {top['name']}, {cname} -- not applied because: "
                    + "; ".join(why_not), span)
    return ("LOW", None,
            "no token in the name (after stripping team words) is a populated place "
            f"in the gazetteer; tried: {' '.join(t)}", "")


# --------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the HIGH-tier results (default: dry run)")
    args = ap.parse_args()

    ready = json.loads(READY.read_text(encoding="utf-8"))
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    coords, fb = _map_tables()

    tgt = targets(ready)
    # Anything this round has EVER claimed stays out of the evidence set, so a
    # re-run reproduces the same answer rather than compounding on itself.
    prior = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    exclude = set(tgt) | set(prior)
    by_name, by_city = step1_index(locations, exclude)
    gaz, countries = gazetteer()

    results: dict[str, dict] = {}
    for club in sorted(tgt):
        tier, trip, why, how = step1(club, by_name, by_city, locations)
        stage = "step1"
        if tier is None:
            tier, trip, why, how = step2(club, gaz, countries)
            stage = "step2"
        audit = _AUDIT_EXCLUDE.get(fold(club).strip())
        if audit and tier == "HIGH":
            tier, why = "MEDIUM", "excluded by hand audit: " + audit
        rec = {"club": club, "stints": tgt[club], "tier": tier, "stage": stage,
               "reason": why, "matched": how}
        if trip:
            rec["city"], rec["state"], rec["country"] = trip
        # Plottability gate: a location the map cannot place is not a fill.
        if tier == "HIGH" and trip and not plottable(trip[0], trip[1], trip[2], coords, fb):
            rec["tier"] = "MEDIUM"
            rec["reason"] = (f"resolved to {trip[0]}, {trip[2]} but the map has no way to "
                             f"place {trip[2]!r} (absent from COORDS and FALLBACK); "
                             "extend FALLBACK before applying. Original reason: " + why)
            rec["needs_fallback"] = trip[2]
        results[club] = rec

    # MERGED, never replaced. Once a club has been resolved its record stays,
    # which is what keeps `exclude` complete on later runs -- a cache that
    # shrank to the current target set would forget the clubs it had just
    # located and let them back in as evidence.
    merged = {**prior, **results}
    CACHE.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")

    tiers = collections.Counter(r["tier"] for r in results.values())
    stages = collections.Counter((r["stage"], r["tier"]) for r in results.values())
    high = {k: v for k, v in results.items() if v["tier"] == "HIGH"}
    need_fb = collections.Counter(r["needs_fallback"] for r in results.values()
                                  if r.get("needs_fallback"))

    print(f"targets (neither city nor country): {len(tgt)} clubs, {sum(tgt.values())} stints")
    print(f"  step 1 HIGH : {stages[('step1','HIGH')]}")
    print(f"  step 1 MEDIUM: {stages[('step1','MEDIUM')]}")
    print(f"  step 2 HIGH : {stages[('step2','HIGH')]}")
    print(f"  step 2 MEDIUM: {stages[('step2','MEDIUM')]}")
    print(f"  step 2 LOW  : {stages[('step2','LOW')]}")
    print(f"  tiers: {dict(tiers)}")
    if need_fb:
        print(f"  HIGH downgraded for want of a FALLBACK centroid: {sum(need_fb.values())}")
        print(f"    countries: {dict(need_fb)}")

    review = {k: v for k, v in results.items() if v["tier"] != "HIGH"}
    REVIEW_OUT.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
    print(f"  review file: {REVIEW_OUT.relative_to(ROOT)} ({len(review)} clubs)")

    if not args.apply:
        print("\ndry run -- nothing written. Re-run with --apply to write the HIGH tier.")
        return

    applied = 0
    for club, r in high.items():
        cur = locations.get(club, {})
        entry = {"team": club, "city": r["city"], "state": r["state"],
                 "country": r["country"], "league": cur.get("league", "")}
        if cur != entry:
            locations[club] = entry
            applied += 1
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    located = 0
    for path, db in ((CAREERS, careers), (READY, ready)):
        for p in db:
            for s in p.get("career_history") or []:
                if s.get("city") or s.get("country"):
                    continue                      # never overwrite
                r = high.get(s.get("team", ""))
                if not r:
                    continue
                s["city"], s["state"], s["country"] = r["city"], r["state"], r["country"]
                located += 1
        path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    left = targets(json.loads(READY.read_text(encoding="utf-8")))
    print(f"\napplied to team_locations: {applied}")
    print(f"stints located: {located}")
    print(f"remaining with neither field: {len(left)} clubs, {sum(left.values())} stints")


if __name__ == "__main__":
    main()
