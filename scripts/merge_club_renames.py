"""Merge club names that are the same club described at more length.

"Al Riyadi" and "Al Riyadi Club Beirut" are one club. A Wikipedia editor
expanded the article's name to its formal form, every stint scraped afterwards
carried the longer string, and the diff between two scrapes read as a transfer.
The spelling guard cannot catch it -- the letters are not the same, the name
GAINED words -- so this is the containment case, handled on tokens.

WHAT COUNTS AS THE SAME CLUB. One name's token set wholly inside the other's,
and every extra token either a descriptor (Club, BC, KK, SC, CD, CB, Basket,
Basketball, an article) or a place already attested for that country. Those
sets live in team_normalizer alongside the rule itself, so the move guard in
update_careers.py and this merger cannot disagree about what a rename is.

WHAT DOES NOT, and this is the half that matters:

  Reserve sides.   "Real Madrid" / "Real Madrid Castilla" and "Barcelona" /
                   "Barcelona B" are a first team and its farm team, and
                   players move between them for real. Any extra token in
                   RESERVE_TOKENS vetoes the match before anything else is
                   considered. Castilla is refused on a second ground too: it
                   is not a descriptor and not a Spanish city in this dataset,
                   so it reads as a distinguishing word.

  Shared names.    "Al Ahly" sits inside Al Ahly Cairo, Al Ahly Benghazi AND
                   Al Ahly Ly; "Al-Ahli" inside eight names across four
                   countries. These are different clubs that share a name, and
                   which one a bare stint meant is not knowable from here. A
                   name with more than one superset is merged ONLY when those
                   supersets are themselves all one club; otherwise it goes to
                   review.

  Sponsor names.   "Kosner Baskonia" and "HDI Sigorta Afyon Belediye" carry a
                   sponsor, which is neither a descriptor nor a place, so they
                   are reported rather than merged. Detecting sponsors needs a
                   list of sponsors; guessing would merge real clubs.

  Two countries.   Al Nasr Dubai and Al-Nasr Benghazi.

Canonical is picked by usage, exactly as in the spelling rounds, and the writes
go through merge_spelling_variants.apply_merges so both mergers touch the same
files the same way.

Idempotent. Run:  python3 scripts/merge_club_renames.py            # report
                  python3 scripts/merge_club_renames.py --apply    # write
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS  # noqa: E402
from era_correct_teams import ERA_TABLE  # noqa: E402
from update_careers import _is_slash_joined  # noqa: E402
from merge_spelling_variants import (_diacritic_count, _usage,  # noqa: E402
                                     apply_merges)
from rosters import NBA_TEAMS  # noqa: E402
from team_normalizer import (GENERIC_TOKENS, KNOWN_DISTINCT,  # noqa: E402
                             RESERVE_TOKENS, rename_containment, spelling_key,
                             spelling_tokens, strip_diacritics)

# Pairs the automatic rule reports rather than merges, confirmed by hand.
# Each is a sponsor prefix on an otherwise identical name.
FORCE_MERGE = {
    frozenset({"Baskonia", "Kosner Baskonia"}):
        "Kosner was a shirt sponsor; one stint against 130",
    # The rule refuses this one because the club's location rows say Manara,
    # a Beirut district, so "Beirut" is not among its own place tokens. It is
    # the club the whole containment rule was written for.
    frozenset({"Al Riyadi", "Al Riyadi Beirut"}):
        "same Lebanese club; Manara is a district of Beirut",
}

# --- sponsor names ---------------------------------------------------------
#
# A club name that is another club's name plus a COMMERCIAL token. Nothing
# about the string says whether "Kosner Baskonia" is a sponsor or a different
# club, so these are named one at a time rather than detected. Built from
# scripts/sponsor_report.py, which lists every containment pair whose extra
# token is neither a descriptor nor a place and whose two sides sit in the same
# city -- then read by hand, because same-city is necessary and nowhere near
# sufficient. Beijing has four different clubs, Larissa two, Manama two, and
# every one of them passes the same-city test.
#
# Sponsors change often. An entry that goes stale merges two real clubs, so
# each one says which token it is claiming and why.
SPONSOR_PAIRS = {
    # --- the two that reached the ledger as phantoms ---
    frozenset({"Baskonia", "Kosner Baskonia"}): "Kosner, shirt sponsor",
    frozenset({"Valencia", "Valencia Hoja del Lunes"}):
        "Hoja del Lunes, newspaper sponsor of the 1970s side",

    # --- Spain ---
    frozenset({"FC Barcelona", "FC Barcelona Lassa"}): "Lassa Tyres",
    frozenset({"FC Barcelona", "FC Barcelona Regal"}): "Regal",
    frozenset({"Granada", "Puleva Granada"}): "Puleva, dairy",
    frozenset({"Alicante", "Lucentum Alicante"}): "CB Lucentum Alicante",
    frozenset({"Alicante", "Etosa Alicante"}): "Etosa",
    frozenset({"Alicante", "HLA Alicante"}): "HLA",
    frozenset({"Palencia", "Zunder Palencia"}): "Zunder, energy",
    frozenset({"Palencia", "Faymasa Palencia"}): "Faymasa",
    frozenset({"Ourense", "Caixa Ourense"}): "Caixa, bank",
    frozenset({"Ourense", "Coren Ourense"}): "Coren, food",
    frozenset({"Ourense", "Xacobeo 99 Ourense"}): "Xacobeo 99",
    frozenset({"Lleida", "Caprabo Lleida"}): "Caprabo, supermarkets",
    frozenset({"Lleida", "Plus Pujol Lleida"}): "Plus Pujol",
    frozenset({"Gijón", "Cabitel Gijón"}): "Cabitel",
    frozenset({"Cantabria", "Alerta Cantabria"}): "Alerta, newspaper",
    frozenset({"Inca", "Drac Inca"}): "Drac",
    frozenset({"Collado Villalba", "BBV Collado Villalba"}): "BBV, bank",
    frozenset({"Obradoiro", "Obradoiro CAB"}): "club abbreviation",
    frozenset({"Cáceres", "Cáceres Ciudad del Baloncesto"}): "club's full name",

    # --- Italy: long sponsor histories on one club each ---
    frozenset({"Verona", "Scaligera Verona"}): "Scaligera, the club itself",
    frozenset({"Verona", "Scaligera Basket Verona"}): "Scaligera",
    frozenset({"Verona", "Glaxo Verona"}): "Glaxo, pharma",
    frozenset({"Verona", "Tezenis Verona"}): "Tezenis, clothing",
    frozenset({"Verona", "Müller Verona"}): "Müller, dairy",
    frozenset({"Verona", "Citrosil Verona"}): "Citrosil",
    frozenset({"Verona", "Mash J. Verona"}): "Mash Jeans",
    frozenset({"Montegranaro", "Sutor Montegranaro"}): "Sutor, the club itself",
    frozenset({"Montegranaro", "Premiata Montegranaro"}): "Premiata, footwear",
    frozenset({"Montegranaro", "Fabi Shoes Montegranaro"}): "Fabi Shoes",
    frozenset({"Montegranaro", "Sigma Coatings Montegranaro"}): "Sigma Coatings",
    frozenset({"Montegranaro", "Supernova Montegranaro"}): "Supernova",
    frozenset({"Fabriano", "Indesit Fabriano"}): "Indesit, appliances",
    frozenset({"Fabriano", "Faber Fabriano"}): "Faber, appliances",
    frozenset({"Fabriano", "Alno Fabriano"}): "Alno",
    frozenset({"Fabriano", "Carifac Fabriano"}): "Carifac, bank",
    frozenset({"Fabriano", "Turboair Fabriano"}): "Turboair",
    frozenset({"Fabriano", "Teamsystem Fabriano"}): "TeamSystem, software",
    frozenset({"Fabriano", "Zara Imballaggi Fabriano"}): "Zara Imballaggi",
    frozenset({"Forlì", "Olitalia Forlì"}): "Olitalia, oil",
    frozenset({"Forlì", "Filanto Forlì"}): "Filanto",
    frozenset({"Forlì", "Telemarket Forlì"}): "Telemarket",
    frozenset({"Forlì", "Montana Forlì"}): "Montana, food",
    frozenset({"Forlì", "Latini Forlì"}): "Latini",
    frozenset({"Forlì", "Jollycolombani Forlì"}): "Jolly Colombani",
    frozenset({"Scafati", "Givova Scafati"}): "Givova, sportswear",
    frozenset({"Scafati", "Legea Scafati"}): "Legea, sportswear",
    frozenset({"Scafati", "Eurorida Scafati"}): "Eurorida",
    frozenset({"Scafati", "Rida Scafati"}): "Rida",
    frozenset({"Scafati", "Longobardi Scafati"}): "Longobardi",
    frozenset({"Teramo", "Bancatercas Teramo"}): "Banca Tercas",
    frozenset({"Teramo", "Navigo.it Teramo"}): "Navigo.it",
    frozenset({"Teramo", "Siviglia Wear Teramo"}): "Siviglia Wear",
    frozenset({"Casale Monferrato", "Novipiù Casale Monferrato"}): "Novipiù",
    frozenset({"Casale Monferrato", "Fastweb Casale Monferrato"}): "Fastweb",
    frozenset({"Pavia", "Edimes Pavia"}): "Edimes",
    frozenset({"Pavia", "Annabella Pavia"}): "Annabella",
    frozenset({"Pavia", "Fernet Branca Pavia"}): "Fernet-Branca",
    frozenset({"Ferrara", "Carife Ferrara"}): "Carife, bank",
    frozenset({"Ferrara", "Cercom Ferrara"}): "Cercom",
    frozenset({"Ferrara", "Kleb Basket Ferrara"}): "Kleb, adhesives",
    frozenset({"Imola", "Andrea Costa Imola"}): "Andrea Costa, the club itself",
    frozenset({"Imola", "Fillattice Imola"}): "Fillattice",
    frozenset({"Imola", "Lineltex Imola"}): "Lineltex",
    frozenset({"Imola", "Casetti Imola"}): "Casetti",
    frozenset({"Dinamo Sassari", "Dinamo Banco di Sardegna Sassari"}):
        "Banco di Sardegna, bank",
    frozenset({"Auxilium Torino", "Auxilium CUS Torino"}): "CUS, university club",
    frozenset({"Reyer Venezia", "Reyer Venezia Mestre"}): "Mestre, the district",
    frozenset({"Roseto", "Roseto Sharks"}): "the club's nickname",
    frozenset({"Pistoia Basket", "Pistoia Basket 2000"}): "founding year",

    # --- rest of Europe ---
    frozenset({"Beşiktaş", "Beşiktaş Icrypex"}): "Icrypex, exchange",
    frozenset({"Bahçeşehir", "Bahçeşehir Koleji"}): "Koleji, the school behind it",
    frozenset({"Manisa Basket", "Glint Manisa Basket"}): "Glint",
    frozenset({"Budućnost", "Budućnost VOLI"}): "Voli, supermarkets",
    frozenset({"FMP", "FMP Železnik"}): "Železnik, the district",
    frozenset({"Nymburk", "ČEZ Nymburk"}): "ČEZ, energy",
    frozenset({"BK Pardubice", "BK JIP Pardubice"}): "JIP, paper",
    frozenset({"Starogard Gdański", "Polpharma Starogard Gdański"}): "Polpharma",
    frozenset({"Prokom Trefl", "Prokom Trefl Sopot"}): "Sopot, the town",
    frozenset({"Skyliners Frankfurt", "Frankfurt Opel Skyliners"}): "Opel",
    frozenset({"Telekom Bonn", "Telekom Baskets Bonn"}): "Baskets, the club name",
    frozenset({"Heidelberg", "MLP Academics Heidelberg"}): "MLP, finance",
    frozenset({"Heidelberg", "USC Heidelberg"}): "USC, university club",
    frozenset({"Bayer Leverkusen", "TSV Bayer 04 Leverkusen"}): "full club name",
    frozenset({"Bayer Leverkusen", "Bayer 04 Leverkusen"}): "full club name",
    frozenset({"Bayer Leverkusen", "Bayer Giants Leverkusen"}): "the nickname",
    frozenset({"Antwerp Giants", "Antwerp Diamond Giants"}): "Diamond",
    frozenset({"Leuven", "Leuven Bears"}): "the club's nickname",
    frozenset({"Leuven", "Spotter Leuven"}): "Spotter",
    frozenset({"Racing Mechelen", "Racing Maes Pils Mechelen"}): "Maes Pils, beer",
    frozenset({"Charleroi", "Spirou Charleroi"}): "Spirou, the club itself",
    frozenset({"Liège", "Belgacom Liège"}): "Belgacom, telecoms",
    frozenset({"Leiden", "Elmex Leiden"}): "Elmex",
    frozenset({"Leiden", "Parker Leiden"}): "Parker",
    frozenset({"AEK Larnaca", "Petrolina AEK Larnaca"}): "Petrolina, fuel",
    frozenset({"Roanne", "Chorale Roanne"}): "Chorale, the club itself",
    frozenset({"Antibes", "Olympique Antibes"}): "the club's full name",
    frozenset({"Antibes", "Antibes Sharks"}): "the club's nickname",
    frozenset({"Le Mans", "Le Mans Sarthe"}): "Sarthe, the department",
    frozenset({"Le Havre", "STB Le Havre"}): "STB, the club abbreviation",
    frozenset({"Boulazac", "Boulazac Basket Dordogne"}): "Dordogne, the department",
    frozenset({"BCM Gravelines", "BCM Gravelines-Dunkerque"}): "the paired town",
    frozenset({"Poitiers", "Poitiers 86"}): "86, the department number",
    frozenset({"Chalon", "Chalon-sur-Saône"}): "the town's full name",
    frozenset({"Fos Provence", "Fos Ouest Provence"}): "the district",
    frozenset({"İTÜ", "Sigortam.net İTÜ BB"}): "Sigortam.net, insurance",

    # --- Asia and the Pacific ---
    frozenset({"Seoul Thunders", "Seoul Samsung Thunders"}): "Samsung",
    frozenset({"Ulsan Mobis Phoebus", "Ulsan Hyundai Mobis Phoebus"}): "Hyundai",
    frozenset({"Goyang Orions", "Goyang Orion Orions"}): "Orion, confectionery",
    frozenset({"Shanghai Sharks", "Shanghai Xiyang Sharks"}): "Xiyang",
    frozenset({"Xinjiang", "Xinjiang Flying Tigers"}): "the club's nickname",
    frozenset({"Qingdao", "Qingdao Eagles"}): "the club's nickname",
    frozenset({"Qingdao", "Qingdao DoubleStar"}): "DoubleStar, tyres",
    frozenset({"Qingdao", "Qingdao DoubleStar Eagles"}): "DoubleStar",
    frozenset({"Shanxi Brave Dragons", "Shanxi Zhongyu Brave Dragons"}): "Zhongyu",
    frozenset({"Shaanxi Kylins", "Shaanxi Dongsheng Kylins"}): "Dongsheng",
    frozenset({"Shaanxi Kylins", "Shaanxi Gaitianli Kylins"}): "Gaitianli",
    frozenset({"Zhejiang Cyclones", "Zhejiang Wanma Cyclones"}): "Wanma",
    frozenset({"Liaoning Hunters", "Liaoning Panpan Hunters"}): "Panpan",
    frozenset({"Guangdong", "Guangdong Southern Tigers"}): "the club's nickname",
    frozenset({"Nagoya Diamond", "Nagoya Diamond Dolphins"}): "the nickname",
    frozenset({"Formosa Dreamers", "Formosa Taishin Dreamers"}): "Taishin, bank",
    frozenset({"Taipei Mars", "Taipei Taishin Mars"}): "Taishin, bank",
    frozenset({"Hsinchu Lioneers", "Hsinchu Toplus Lioneers"}): "Toplus",
    frozenset({"Hsinchu Lioneers", "Hsinchu JKO Lioneers"}): "JKO",
    frozenset({"Kaohsiung Steelers", "Kaohsiung 17LIVE Steelers"}): "17LIVE",
    frozenset({"Taichung Suns", "Taichung Wagor Suns"}): "Wagor",
    frozenset({"Phoenix Fuel Masters", "Phoenix Super LPG Fuel Masters"}):
        "Super LPG",
    frozenset({"Phoenix Fuel Masters", "Phoenix Pulse Fuel Masters"}): "Pulse",
    frozenset({"Manila Beer", "Manila Beer Brewmasters"}): "the nickname",

    # --- the Americas ---
    frozenset({"Akron Wingfoots", "Akron Goodyear Wingfoots"}): "Goodyear",
    frozenset({"Anderson Packers", "Anderson Duffey Packers"}): "Duffey Packers",
    frozenset({"Wilmington Bombers", "Wilmington Blue Bombers"}): "the nickname",
    frozenset({"Cangrejeros", "Cangrejeros de Santurce"}): "Santurce, the district",
    frozenset({"Aguada", "Santeros de Aguada"}): "the club's full name",
    frozenset({"Trotamundos", "Trotamundos de Carabobo"}): "Carabobo, the state",
    frozenset({"Barranquilla", "Titanes de Barranquilla"}): "the club's full name",
    frozenset({"Quimsa", "Asociacion Quimsa Santiago del Estero"}): "full name",
    frozenset({"San Lorenzo", "San Lorenzo de Almagro"}): "Almagro, the district",
    frozenset({"Halcones Xalapa", "Halcones UV Xalapa"}):
        "UV, Universidad Veracruzana",
    frozenset({"Lobos Grises", "Lobos Grises UAD"}): "UAD, the university",
    frozenset({"Pioneros de Quintana", "Pioneros de Quintana Roo"}): "the state",
    frozenset({"Indios de San Francisco", "Indios de San Francisco de Macorís"}):
        "the town's full name",
    frozenset({"Minas", "Minas Tênis Clube"}): "the club's full name",
    frozenset({"Franca", "Franca Basquetebol Clube"}): "the club's full name",
    frozenset({"Temuco", "Unión Deportiva Española Temuco"}): "the club's full name",
}

# Read but NOT merged, recorded so the next person does not have to work them
# out again. Every one passes the same-city test and every one is two clubs.
NOT_SPONSORS = {
    frozenset({"Beijing", "Beijing Ducks"}):
        "Beijing has four different clubs -- Ducks, Royal Fighters, Olympians, "
        "Fly Dragons -- and a bare 'Beijing' stint cannot be assigned to one",
    frozenset({"Larisa", "Olympia Larissa"}):
        "Olympia Larissa and Gymnastikos Larissa are separate clubs",
    frozenset({"Maccabi Tel Aviv", "Maccabi Darom Tel Aviv"}):
        "Maccabi Darom is its own club, not a sponsored Maccabi Tel Aviv",
    frozenset({"Dubai", "Al Nasr Dubai"}):
        "Al Nasr, Al Ahli and Al Naser all play in Dubai",
    frozenset({"Manama", "Al-Ahli Manama"}):
        "Al-Ahli and Al-Ittihad both play in Manama",
    frozenset({"Khimik", "Khimik Engels"}):
        "Khimik Engels is in Russia, Khimik in Pivdenne, Ukraine",
    frozenset({"Atletico Madrid", "Atlético Madrid Villalba"}):
        "Villalba is a separate club, not a sponsored Atletico",
    frozenset({"Sporting", "Sporting CP"}):
        "bare 'Sporting' sits inside nine clubs across six countries",
    frozenset({"Liège", "Standard Liège"}):
        "Standard Liège is not RBC Liège",
    frozenset({"Dynamo Moscow", "Dynamo Moscow Region"}):
        "Dynamo Moscow Region is a separate club",
    frozenset({"Žalgiris", "Žalgiris -Arvydas Sabonis School"}):
        "the Sabonis school is an academy, not the first team",
    frozenset({"Pittsburgh Pirates", "East Pittsburgh Pirates"}):
        "two stints each and no evidence either way",
    frozenset({"Soles de Santo Domingo", "Soles de Santo Domingo Este"}):
        "Santo Domingo Este is its own municipality",
}


# Usage decides the canonical name, as in the spelling rounds -- except where
# usage would enshrine a sponsor that has since lapsed. A club scraped more
# often while a sponsor's name was on the shirt is not thereby named after that
# sponsor. Naming the club here wins its group outright.
#
# Deliberately NOT here: Seoul Samsung Thunders, Ulsan Hyundai Mobis Phoebus
# and ČEZ Nymburk. Those companies own or have long backed the clubs and the
# names are how the clubs are actually known, so usage gets them right.
CANONICAL_OVERRIDE = {
    "Cantabria",          # not Alerta Cantabria: Alerta is a newspaper
    "Liège",              # not Belgacom Liège
    "Racing Mechelen",    # not Racing Maes Pils Mechelen
    "İTÜ",                # not Sigortam.net İTÜ BB
    "Taipei Mars",        # not Taipei Taishin Mars
    "Zhejiang Cyclones",  # not Zhejiang Wanma Cyclones
}

# Both lists feed the same machinery: a pair named here is merged whether or
# not the automatic rule would have found it.
FORCE_MERGE.update(SPONSOR_PAIRS)

_NBA_NAMES = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA_NAMES.add(_n)


def _country(places: dict, name: str) -> str:
    return "".join(spelling_tokens((places.get(name) or ("", ""))[-1]))


def index(dbs: list) -> tuple[list, dict, dict]:
    """Club names, where each plays, and the cities attested per country."""
    counts: dict = defaultdict(int)
    seen: dict = defaultdict(lambda: defaultdict(int))
    cities: dict = defaultdict(set)
    for db in dbs:
        for p in db:
            for s in p.get("career_history") or []:
                team = (s.get("team") or "").strip()
                if not team:
                    continue
                counts[team] += 1
                city = (s.get("city") or "").strip()
                country = (s.get("country") or "").strip()
                if city or country:
                    seen[team][(city, country)] += 1
                if city and country:
                    cities["".join(spelling_tokens(country))].update(
                        spelling_tokens(city))
    places = {t: max(v.items(), key=lambda kv: kv[1])[0]
              for t, v in seen.items()}
    return sorted(counts), places, cities


def edges(names: list, places: dict, cities: dict) -> tuple[dict, dict, list]:
    """(accepted subset -> [supersets], every subset -> [supersets], held)."""
    ok: dict = defaultdict(list)
    every: dict = defaultdict(list)
    held: list = []
    toks = {n: set(spelling_tokens(n)) for n in names}
    for a in names:
        for b in names:
            if a == b or not toks[a] or not toks[b] or not toks[a] < toks[b]:
                continue
            # Recorded before the country and descriptor checks: the ambiguity
            # guard has to see EVERY club a bare name sits inside, not just the
            # ones that survived them. Counting only the survivors is what let
            # "Al Ahly" merge into Al Ahly Cairo while Al Ahly Benghazi and Al
            # Ahly Ly were quietly filtered out first.
            #
            # A reserve side is the exception. "Valencia B" does not make
            # "Valencia" ambiguous -- a bare stint at Valencia means the first
            # team, and the B side is already refused on its own account. Left
            # in the tally it blocked the first team from merging with its own
            # longer names.
            if not (toks[b] - toks[a]) & RESERVE_TOKENS:
                every[a].append(b)
            pair = frozenset({strip_diacritics(a).casefold().strip(),
                              strip_diacritics(b).casefold().strip()})
            if pair in KNOWN_DISTINCT:
                held.append(((a, b), "pinned as distinct clubs"))
                continue
            # A slash usually marks a relocated or merged franchise
            # ("Pittsburgh / Minnesota Pipers", "Baltimore/Rockford
            # Lightning"), and folding one side in throws away the other half
            # of its history. A few are harmless sponsor slashes
            # ("Kalev/Cramo"), but telling them apart is exactly the judgement
            # call that belongs to a human, so all of them are reported.
            if "/" in a or "/" in b:
                held.append(((a, b), "relocation / merged-franchise name"))
                continue
            same, why = rename_containment(
                a, b, place_a=places.get(a, ()), place_b=places.get(b, ()))
            if same is True:
                ok[a].append(b)
            elif same is None or why:
                held.append(((a, b), why))
    return ok, every, held


def plan(dbs: list) -> tuple[list, list]:
    """(merges, held_back). Each merge is (canonical, [variants...])."""
    names, places, cities = index(dbs)
    ok, every, held = edges(names, places, cities)
    toks = {n: set(spelling_tokens(n)) for n in names}

    def one_club(a: str, b: str) -> bool:
        """Could these two names be the same club?

        Either one contains the other, or the only things separating them are
        descriptors -- "Valencia BC" and "Valencia Basket" are one club twice.
        "Al Ahly Cairo" and "Al Ahly Benghazi" differ by two place names and
        are two clubs; "Las Vegas Silvers" and "Albuquerque Silvers" by two
        city names and are the same franchise in two towns, which is not
        something to fold into one name either.
        """
        if toks[a] < toks[b] or toks[b] < toks[a]:
            return True
        return not (toks[a] ^ toks[b]) - GENERIC_TOKENS

    # A name inside SEVERAL others is only safe when those others are one club
    # between themselves -- Al Riyadi Beirut and Al Riyadi Club Beirut are,
    # Al Ahly Cairo and Al Ahly Benghazi are not.
    def restatement(sub: str, sup: str) -> bool:
        """sup is sub said at more length, not a candidate for a second club.

        "Scafati Basket" and "Valencia BC" add nothing but a descriptor, so
        they are the bare name restated. Counting them as rival clubs is what
        stopped a club merging with its own name.
        """
        return not (toks[sup] - toks[sub]) - GENERIC_TOKENS

    def confirmed(x: str, y: str) -> bool:
        return frozenset({x, y}) in FORCE_MERGE

    def one_club_between(sub: str, sups: list) -> bool:
        """Are the rival names for `sub` all the same club as each other?

        Judged BETWEEN them, never through `sub`. Every superset contains the
        bare name by construction, so allowing it as a hub answers yes for
        anything and the guard stops guarding: that is how "Al Ahly" merged
        into Al Ahly Cairo while Al Ahly Benghazi and Al Ahly Ly sat next to it.

        A human assertion is the exception. Each of Scafati's five jersey
        sponsors was confirmed against "Scafati" by hand, which links them to
        each other; no two of them resemble each other at all, and a pairwise
        resemblance test threw the group away.
        """
        rivals = [x for x in sups if not restatement(sub, x)]
        if len(rivals) < 2:
            return True
        seen, stack = {rivals[0]}, [rivals[0]]
        while stack:
            a = stack.pop()
            for b in rivals:
                if b in seen:
                    continue
                if (confirmed(a, b) or one_club(a, b)
                        or (confirmed(a, sub) and confirmed(b, sub))):
                    seen.add(b)
                    stack.append(b)
        return len(seen) == len(rivals)

    accepted: list = []
    for sub, sups in ok.items():
        allsups = every.get(sub, [])
        if len(allsups) > 1 and not one_club_between(sub, allsups):
            held.append(((sub, " / ".join(sorted(allsups))),
                         f"name shared by {len(allsups)} clubs"))
            continue
        accepted.extend((sub, s) for s in sups)

    forced = {tuple(sorted(p)) for p in FORCE_MERGE}
    for a, b in ({tuple(sorted(p)) for p in forced} - {tuple(sorted(e))
                                                       for e in accepted}):
        if a in names and b in names:
            accepted.append((a, b))

    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in accepted:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups: dict = defaultdict(set)
    for n in list(parent):
        groups[find(n)].add(n)

    merges = []
    for members in groups.values():
        members = sorted(members)
        if any(m in _NBA_NAMES for m in members):
            held.append((tuple(members), "NBA franchise / era name"))
            continue
        # Union-find is transitive; containment is not. Las Vegas Silvers and
        # Albuquerque Silvers both sit inside "Las Vegas/Albuquerque Silvers"
        # and neither sits inside the other, so the chain dragged two clubs in
        # two cities into one group. A group has to be a chain: every pair
        # ordered by containment, or it is not one club getting longer.
        def same_as(x: str, y: str) -> bool:
            return frozenset({x, y}) in FORCE_MERGE or one_club(x, y)

        def connected(group: list) -> bool:
            """Is every name in the group linked to the rest by a verified edge?

            Union-find already assumes transitivity. This guard exists to catch
            it running through an UNVERIFIED hub: "Las Vegas Silvers" and
            "Albuquerque Silvers" both sit inside "Las Vegas/Albuquerque
            Silvers" and became one club in two cities that way.

            Transitivity through a hub a HUMAN confirmed is a different matter,
            and testing every PAIR instead of every edge threw those away. Seven
            of Verona's names are jersey sponsors -- Glaxo, Tezenis, Müller,
            Citrosil -- and no two resemble each other at all, so the pairwise
            test rejected the whole group and 77 of the 147 reviewed sponsor
            pairs silently did nothing. Each was confirmed against "Verona"
            individually, and that is what makes them one club.
            """
            seen, stack = {group[0]}, [group[0]]
            while stack:
                x = stack.pop()
                for y in group:
                    if y not in seen and same_as(x, y):
                        seen.add(y)
                        stack.append(y)
            return len(seen) == len(group)

        pairs = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]]
        if not connected(members):
            held.append((tuple(members), "not a containment chain"))
            continue
        # KNOWN_DISTINCT pins pairs, and a third name must not be allowed to
        # reunite them: "Al Nassr" and "Al-Nasr" are pinned apart but both sit
        # inside "Al-Nasr SC".
        pinned = [(a, b) for a, b in pairs
                  if frozenset({strip_diacritics(a).casefold().strip(),
                                strip_diacritics(b).casefold().strip()})
                  in KNOWN_DISTINCT]
        if pinned:
            held.append((tuple(members),
                         f"pinned as distinct clubs ({pinned[0][0]} / "
                         f"{pinned[0][1]})"))
            continue
        pick = [m for m in members if m in CANONICAL_OVERRIDE]
        canonical = pick[0] if pick else sorted(
            members,
            key=lambda n: (-_usage(dbs, n)[0], -_usage(dbs, n)[1],
                           -_diacritic_count(n), n))[0]
        merges.append((canonical, [m for m in members if m != canonical]))
    return sorted(merges), held


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the merges")
    args = ap.parse_args()

    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    dbs = [careers, ready]

    merges, held = plan(dbs)
    print(f"=== merges: {len(merges)} ===")
    for canonical, variants in merges:
        st, al = _usage(dbs, canonical)
        detail = ", ".join(f"{v!r} ({_usage(dbs, v)[0]})" for v in variants)
        print(f"  {canonical!r} ({st} stints, {al} alumni)  <-  {detail}")

    buckets: dict = defaultdict(list)
    for members, why in held:
        buckets[why].append(members)
    print(f"\n=== held back: {len(held)} ===")
    for why, items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {why}: {len(items)}")
        for m in sorted(items)[:6]:
            print(f"      {m}")
        if len(items) > 6:
            print(f"      ... and {len(items) - 6} more")

    if not args.apply:
        print("\n(report only — pass --apply to write)")
        return 0
    apply_merges(merges, careers, ready, locations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
