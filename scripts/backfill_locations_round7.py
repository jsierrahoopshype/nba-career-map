"""Round 7: the city-but-no-country entries, the last of the round-5 class.

WHAT THIS CLASS IS. team_locations.json entries carrying a city and an EMPTY
country. Round 5 found 116 of them and cleared the head of the distribution;
85 remained. They damage two things at once:

  * the map, because getCoords needs BOTH a city and a country -- an exact
    "City|State|Country" hit, else a country centroid with per-city jitter, and
    with no country there is nothing to fall back to;
  * the Slack signing sentence, whose "of <country>" suffix is dropped
    entirely when the country is blank. That is how "Gifu Swoops of Japan"
    came out as "Gifu Swoops" in round 6.

WHAT IS FILLED. A country is filled only where it follows unambiguously from
the recorded city plus the club's identity. Several recorded CITIES were
themselves misparses -- a country in the city field ("Netherlands", "Turkey",
"Paraguay", "Montenegro"), a region rather than a city ("Fukushima
Prefecture", "Hiroshima Prefecture", "Hsinchu County"), a street address
("6000 Santa Monica BoulevardLos Angeles"), a stray English word ("English"),
or a sentence fragment ("Hapoel Galil Elyon to form a new team") -- and those
are corrected here rather than trusted, the same way round 5 handled
"Harvard" and "DNA repair".

WHAT IS NOT. Three clubs whose COUNTRY genuinely cannot be settled go to
teams_needing_review.json with the reason recorded, and are left blank rather
than guessed. Two more are filled at country level but flagged, because the
recorded city disagrees with the club's own name.

Run:  python3 scripts/backfill_locations_round7.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import CAREERS, READY, LOCATIONS, REVIEW  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "location_review_proposals.txt"

# team -> (city, state, country). City is repeated even when unchanged so the
# intended value is explicit; a "# was:" note marks every correction.
FIX: dict[str, tuple[str, str, str]] = {
    # --- Japan ---
    "Fukushima Firebonds": ("Fukushima", "", "Japan"),        # was: "Fukushima Prefecture"
    "Hiroshima Dragonflies": ("Hiroshima", "", "Japan"),      # was: "Hiroshima Prefecture"
    "Altiri Chiba": ("Chiba", "Chiba Prefecture", "Japan"),
    "Matsushita Denki": ("Kadoma", "", "Japan"),
    # --- Taiwan ---
    "Kaohsiung 17LIVE Steelers": ("Kaohsiung", "", "Taiwan"),
    "Hsinchu Lioneers": ("Hsinchu", "", "Taiwan"),            # was: "Hsinchu County"
    "Yankey Ark": ("Hsinchu", "", "Taiwan"),
    # --- China ---
    "Zhuhai Wolf Warriors": ("Macau", "", "China"),
    "Macau Black Bears": ("Macau", "", "China"),
    "Qianwei Aoshen": ("Beijing", "", "China"),
    # --- rest of Asia ---
    "Dewa United Banten": ("Serang", "Banten", "Indonesia"),  # was: "Serang Regency"
    "Erdenet Miners": ("Erdenet", "", "Mongolia"),
    "Hi-Tech Bangkok City": ("Bangkok", "", "Thailand"),
    # --- Gulf / Middle East ---
    "Al Wahda": ("Abu Dhabi", "", "United Arab Emirates"),
    "Al-Riffa": ("Riffa", "", "Bahrain"),
    "Al Riffa": ("Riffa", "", "Bahrain"),
    "Al-Riffa SC": ("Riffa", "", "Bahrain"),
    "Al Ahli Bahrein": ("Manama", "", "Bahrain"),
    "Al-Shamal SC": ("Madinat ash Shamal", "", "Qatar"),
    "Al-Seeb Club": ("Al-Seeb", "", "Oman"),
    "Sagesse SC (basketball)": ("Beirut", "", "Lebanon"),
    # --- Israel ---
    "Elitzur Kiryat Ata": ("Kiryat Ata", "", "Israel"),
    "Ironi Kiryat Ata B.C.": ("Kiryat Ata", "", "Israel"),
    "Hapoel Eliat": ("Eilat", "", "Israel"),
    "Hapoel Migdal Ha'emek": ("Migdal HaEmek", "", "Israel"),
    "Hapoel Tsfat": ("Tirat Carmel", "", "Israel"),           # city flagged below
    "Maccabi Raanana": ("Ra'anana", "", "Israel"),
    # was: "Hapoel Galil Elyon to form a new team" -- a sentence fragment
    "Hapoel Afula/Gilboa": ("Afula", "", "Israel"),
    # --- Africa ---
    "ASC Ville de Dakar": ("Dakar", "", "Senegal"),
    "City Oilers": ("Kampala", "", "Uganda"),                 # was city "Lugogo", a Kampala suburb
    "Cobra Sport": ("Juba", "", "South Sudan"),
    "RSSB Tigers": ("Kigali", "", "Rwanda"),
    "Maghreb de Fes": ("Fez", "", "Morocco"),
    # --- Balkans ---
    "HKK Široki": ("Široki Brijeg", "", "Bosnia and Herzegovina"),
    "Borac Banja Luka": ("Banja Luka", "", "Bosnia and Herzegovina"),
    "KK Igokea": ("Laktaši", "", "Bosnia and Herzegovina"),
    "Sloboda Tuzla": ("Tuzla", "", "Bosnia and Herzegovina"),
    "Čelik Zenica": ("Zenica", "", "Bosnia and Herzegovina"),
    "HKK Zrinjski Mostar": ("Mostar", "", "Bosnia and Herzegovina"),
    "Lavovi 063": ("Belgrade", "", "Serbia"),
    "Lovćen": ("Cetinje", "", "Montenegro"),                  # was: "Montenegro", the country
    # --- rest of Europe ---
    "Filathlitikos": ("Zografou", "", "Greece"),
    "Pagrati": ("Athens", "", "Greece"),
    "Pezoporikos Larnaca": ("Larnaca", "", "Cyprus"),
    "Tekelspor": ("Istanbul", "", "Turkey"),
    "Körfez Basket": ("Kocaeli", "", "Turkey"),
    "Tindastóll": ("Sauðárkrókur", "", "Iceland"),
    "Universidad Complutense": ("Madrid", "", "Spain"),
    "CPN Pueblo Nuevo": ("Calamonte", "", "Spain"),
    "Valencia B": ("Valencia", "", "Spain"),
    "Élan Béarnais Pau-Lacq-Orthez": ("Pau", "", "France"),
    "Élan Béarnais Pau-Orthez": ("Pau", "", "France"),
    "Rotterdam-Zuid": ("Rotterdam", "", "Netherlands"),       # was: "Netherlands", the country
    "Mens Sana 1871 Basket": ("Siena", "", "Italy"),
    "Viganello": ("Lugano", "", "Switzerland"),               # Viganello is a Lugano quarter
    "Klosterneuburg Dukes": ("Klosterneuburg", "", "Austria"),
    "Rasta Vechta II": ("Vechta", "Lower Saxony", "Germany"),
    "Rīgas Laiks": ("Riga", "", "Latvia"),
    "TTÜ-A. Le Coq": ("Tallinn", "", "Estonia"),
    "Tartu Ülikool": ("Tartu", "", "Estonia"),
    "Turun NMKY": ("Turku", "", "Finland"),
    "Slovan Bratislava": ("Bratislava", "", "Slovakia"),
    "Sparta Prague": ("Prague", "", "Czech Republic"),
    "Newcastle Eagles": ("Newcastle upon Tyne", "", "England"),
    "Sunderland Maestros": ("Newcastle upon Tyne", "", "England"),   # city flagged below
    "Sunderland Saints": ("Newcastle upon Tyne", "", "England"),     # city flagged below
    # --- Americas ---
    "Hollywood Fame": ("Los Angeles", "California", "USA"),   # was a street address
    "Chautauqua Hurricane": ("Erie", "Pennsylvania", "USA"),  # city flagged below
    "Snohomish County Explosion": ("Monroe", "Washington", "USA"),
    "Worcester Counts": ("Worcester", "Massachusetts", "USA"),
    "Saskatoon Mamba": ("Saskatoon", "Saskatchewan", "Canada"),
    "Zonkeys de Tijuana": ("Tijuana", "Baja California", "Mexico"),
    "Universitarios": ("Culiacán", "Sinaloa", "Mexico"),
    "Avancinos de Villalba": ("Coamo", "", "Puerto Rico"),
    "Club de Regatas Lima": ("Lima", "", "Peru"),
    "Club Atlético Peñarol": ("Montevideo", "", "Uruguay"),
    "Hebraica Macabi": ("Montevideo", "", "Uruguay"),
    "Comunicaciones": ("Guatemala City", "", "Guatemala"),
    "Olimpia Asuncion": ("Asunción", "", "Paraguay"),         # was: "Paraguay", the country
    "Toros De Aragua": ("Maracay", "Aragua", "Venezuela"),    # was city "Aragua", the state
    "São Carlos": ("São Carlos", "São Paulo", "Brazil"),      # was: "English"
}

# Country cannot be settled -- left blank, recorded for a human.
REVIEW_ADD: dict[str, str] = {
    "Manchester British-Americans":
        "historical club; Manchester, New Hampshire and Manchester, England are "
        "both plausible readings and nothing in the record decides it",
    "BC Gand":
        "the name is the French form of Ghent (Belgium) but the recorded city is "
        "Andorra la Vella; the two cannot both be right, so neither is applied",
    "Jersey Reds":
        "recorded city Saint Peter is a parish of Jersey, a Crown Dependency "
        "rather than a country the map knows; the club's league is unconfirmed",
}

# Country filled, but the recorded CITY disagrees with the club's own name.
REVIEW_CITY: dict[str, str] = {
    "Alpella":
        "country is Turkey, but the recorded city was the word 'Turkey'; the "
        "club's home city is unconfirmed and has been left as recorded",
    "Hapoel Tsfat":
        "country is Israel; the name points at Safed (Tsfat) while the record "
        "says Tirat Carmel — the recorded city was kept, unverified",
    "Sunderland Maestros":
        "country is England; the name says Sunderland while the record says "
        "Newcastle upon Tyne — the recorded city was kept, unverified",
    "Sunderland Saints":
        "country is England; the name says Sunderland while the record says "
        "Newcastle upon Tyne — the recorded city was kept, unverified",
    "Chautauqua Hurricane":
        "country is USA; Chautauqua is in New York while the record says Erie "
        "(Pennsylvania) — the recorded city was kept, unverified",
}

# Alpella keeps whatever city it had; only its country is settled.
COUNTRY_ONLY: dict[str, str] = {"Alpella": "Turkey"}


def main() -> None:
    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))

    filled = 0
    for team, (city, state, country) in FIX.items():
        cur = locations.get(team, {})
        entry = {"team": team, "city": city, "state": state, "country": country,
                 "league": cur.get("league", "")}
        if cur != entry:
            locations[team] = entry
            filled += 1
    for team, country in COUNTRY_ONLY.items():
        cur = locations.get(team)
        if cur and cur.get("country") != country:
            cur["country"] = country
            filled += 1

    reviewed = 0
    for team, reason in {**REVIEW_ADD, **REVIEW_CITY}.items():
        cur = locations.get(team, {})
        entry = {"team": team, "reason": reason,
                 "city": cur.get("city", ""), "country": cur.get("country", "")}
        if review.get(team) != entry:
            review[team] = entry
            reviewed += 1
    # Anything settled here no longer needs a human, unless it is flagged above.
    cleared = 0
    for team in FIX:
        if team in review and team not in REVIEW_CITY and team not in REVIEW_ADD:
            del review[team]
            cleared += 1

    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Push the settled values onto the stints themselves.
    located = 0
    for path in (CAREERS, READY):
        db = json.loads(path.read_text(encoding="utf-8"))
        for p in db:
            for s in p.get("career_history", []):
                loc = locations.get(s.get("team", ""))
                if not loc or not (loc.get("city") and loc.get("country")):
                    continue
                if s.get("city") == loc["city"] and s.get("country") == loc["country"]:
                    continue
                if s.get("city") and s.get("country"):
                    continue
                s["city"] = loc["city"]
                s["state"] = loc.get("state", "")
                s["country"] = loc["country"]
                located += 1
        path.write_text(json.dumps(db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    remaining = [k for k, v in locations.items() if v.get("city") and not v.get("country")]

    marker = "ROUND 7 -- city-but-no-country entries"
    entry = f"""
================================================================================
{marker} ({len(FIX)} filled, {len(REVIEW_ADD)} left blank for review)
================================================================================
  A blank country drops the map position entirely (getCoords has nothing to
  fall back to) and silently drops the Slack sentence's "of <country>" suffix.

  Corrected misparses in the CITY field along the way: a country in the city
  slot (Netherlands, Turkey, Paraguay, Montenegro), a region rather than a city
  (Fukushima Prefecture, Hiroshima Prefecture, Hsinchu County, Aragua), a
  street address (Hollywood Fame), a stray word ("English", Sao Carlos) and a
  sentence fragment (Hapoel Afula/Gilboa).

  NOT guessed -- country genuinely unsettled:
""" + "".join(f"    {t!r}: {r}\n" for t, r in REVIEW_ADD.items()) + """
  Country filled, city flagged as disagreeing with the club's name:
""" + "".join(f"    {t!r}\n" for t in REVIEW_CITY)
    if marker not in LOG.read_text(encoding="utf-8"):
        with LOG.open("a", encoding="utf-8") as f:
            f.write(entry)

    print(f"team_locations entries filled: {filled}")
    print(f"sent to / kept in review: {reviewed}")
    print(f"cleared from review (now settled): {cleared}")
    print(f"stints given a location: {located}")
    print(f"city-but-no-country entries remaining: {len(remaining)}  {remaining}")


if __name__ == "__main__":
    main()
