"""Fifth location backfill pass: the head of the location-less distribution.

Reuses backfill_locations.py's _fill / _missing_location_teams helpers verbatim,
same idempotent mechanism as rounds 2-4.

WHAT THIS ROUND FOUND. The stints these teams own were not un-located because
nobody had ever looked at them -- 116 entries in team_locations.json carry a
city and an EMPTY country, and a stint needs both to plot (index.html's
getCoords returns null without a city, and falls back to a country centroid
without an exact match, so a missing country means no position at all). Those
partial entries were invisible to earlier rounds because _located() and
_missing_location_teams() both test "city AND country" / "no city AND no
country" -- a city-only stint reads as located to one and as not-missing to the
other, so it fell through the gap between them. 302 stints sit in that gap.

"New Orleans Hornets" -- a legitimate NBA era name, reported separately -- is
the same bug: {'city': 'New Orleans', 'state': '', 'country': ''}.

CONFIDENCE. Every entry below is a club whose home city is unambiguous
real-world knowledge. Four recorded cities were misparses and are corrected
here rather than trusted: "Bahcesehir Koleji" was recorded in "Harvard",
"Mega Vizura" in "DNA repair", "Boca Juniors" in the La Boca neighbourhood
rather than Buenos Aires, and "Kazma" in "Kuwait" the country. Anything whose
club identity is genuinely ambiguous ("Al Arabi", "La Palma", "Mega", "Hebei
Xianglan", "TNT Tropang 5G", "Manchester British-Americans") is NOT guessed --
it goes to teams_needing_review.json instead, per the standing rule.

Run:  python3 scripts/backfill_locations_round5.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import (  # noqa: E402
    CAREERS, READY, LOCATIONS, REVIEW, _fill, _missing_location_teams,
)
import json  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "location_review_proposals.txt"

# team -> (city, state, country). Home city is unambiguous for every one.
LOC_FIX: dict[str, tuple[str, str, str]] = {
    # --- North America ---
    "Anchorage Northern Knights": ("Anchorage", "Alaska", "USA"),
    "Elmira Colonels": ("Elmira", "New York", "USA"),
    "New Haven Elms": ("New Haven", "Connecticut", "USA"),
    "Memphis Rockers": ("Memphis", "Tennessee", "USA"),
    "New Orleans Hornets": ("New Orleans", "Louisiana", "USA"),
    "Diablos Rojos del Mexico": ("Mexico City", "", "Mexico"),
    "Diablos Rojos del México": ("Mexico City", "", "Mexico"),
    # --- Latin America / Caribbean ---
    "Boca Juniors": ("Buenos Aires", "", "Argentina"),          # was: the La Boca barrio
    "Pinheiros": ("São Paulo", "", "Brazil"),
    "Cañeros del Este": ("La Romana", "", "Dominican Republic"),
    "Titanes del Licey": ("Santo Domingo", "", "Dominican Republic"),
    "Español de Talca": ("Talca", "", "Chile"),
    # --- Europe ---
    "Bahçeşehir Koleji": ("Istanbul", "", "Turkey"),            # was: "Harvard"
    "Mega Vizura": ("Belgrade", "", "Serbia"),                  # was: "DNA repair"
    "Élan Béarnais": ("Pau", "", "France"),
    "Zrinjski Mostar": ("Mostar", "", "Bosnia and Herzegovina"),
    "Basket Kwidzyn": ("Kwidzyn", "", "Poland"),
    "Álftanes": ("Álftanes", "", "Iceland"),
    # --- Middle East / Africa ---
    "Maccabi Kiryat Motzkin": ("Kiryat Motzkin", "", "Israel"),
    "Maccabi Ra'anana": ("Ra'anana", "", "Israel"),
    "Al Ittihad Alexandria": ("Alexandria", "", "Egypt"),
    "Al Ahly": ("Cairo", "", "Egypt"),
    "Al-Difaa Al-Jawi": ("Baghdad", "", "Iraq"),
    "Ohud Medina": ("Medina", "", "Saudi Arabia"),
    "Kazma": ("Kuwait City", "", "Kuwait"),                     # was: "Kuwait" the country
    "Club Africain": ("Tunis", "", "Tunisia"),
    "Ezzahra Sports": ("Ezzahra", "", "Tunisia"),
    # --- Asia ---
    "Shimane Susanoo Magic": ("Matsue", "Shimane", "Japan"),
    "Sendai 89ers": ("Sendai", "Miyagi", "Japan"),
    "Japan Energy Griffins": ("Tokyo", "", "Japan"),
    "Kaohsiung Steelers": ("Kaohsiung", "", "Taiwan"),
    "Kaohsiung Aquas": ("Kaohsiung", "", "Taiwan"),
    "Hsinchu Toplus Lioneers": ("Hsinchu", "", "Taiwan"),
    "Hsinchu JKO Lioneers": ("Hsinchu", "", "Taiwan"),
}

# Ambiguous by name alone -- flagged for a human rather than guessed. The
# reason is recorded so the next pass doesn't have to re-derive it.
REVIEW_ADD: dict[str, str] = {
    "Al Arabi": "several unrelated clubs of this name (Kuwait, Qatar, Saudi Arabia) — which one is unclear from the stint alone",
    "La Palma": "ambiguous: the Canary Island, CB La Palma (Spain), and Palma clubs all fit the bare name",
    "Mega": "probably KK Mega (Belgrade) but the bare name is also a common sponsor prefix — not confirmable from the stint",
    "Hebei Xianglan": "CBA-affiliated club in Hebei, China; the home city within the province isn't established",
    "TNT Tropang 5G": "PBA (Philippines) club with no single home city — the league plays a rotating venue schedule",
    "Manchester British-Americans": "historical club; Manchester, New Hampshire and Manchester, England are both plausible readings",
    "Fukushima Firebonds": "B.League club in Fukushima prefecture; commonly associated with Koriyama, but the home city isn't confirmed here",
}


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    import backfill_locations as bl
    bl.LOC_FIX = LOC_FIX

    n1 = _fill(careers)
    n2 = _fill(ready)
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    locations = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    loc_updated = 0
    for team, (city, state, country) in LOC_FIX.items():
        cur = locations.get(team, {})
        entry = {"team": team, "city": city, "state": state, "country": country,
                 "league": cur.get("league", "")}
        if cur != entry:
            locations[team] = entry
            loc_updated += 1
    LOCATIONS.write_text(json.dumps(dict(sorted(locations.items())),
                                    ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    removed = 0
    for team in LOC_FIX:
        if team in review:
            del review[team]
            removed += 1
    added = 0
    for team, reason in REVIEW_ADD.items():
        cur = locations.get(team, {})
        entry = {"team": team, "reason": reason,
                 "city": cur.get("city", ""), "country": cur.get("country", "")}
        if review.get(team) != entry:
            review[team] = entry
            added += 1
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    still_missing = _missing_location_teams(careers)
    unlocated = sum(1 for p in careers for s in p.get("career_history", [])
                    if not (s.get("city") and s.get("country")))

    log_entry = f"""
================================================================================
ROUND 5 -- head of the location-less distribution ({len(LOC_FIX)} teams applied,
{len(REVIEW_ADD)} sent to review)
================================================================================
  Cause: 116 team_locations.json entries carry a city and an EMPTY country.
  A stint needs both to plot, and earlier rounds could not see these because
  _located() tests "city AND country" while _missing_location_teams() tests
  "no city AND no country" -- a city-only stint falls between the two.

  Applied ({len(LOC_FIX)}): home city unambiguous. Four recorded cities were
  misparses and were corrected rather than trusted -- Bahçeşehir Koleji
  ("Harvard"), Mega Vizura ("DNA repair"), Boca Juniors (the La Boca barrio),
  Kazma ("Kuwait", the country).

  Sent to review ({len(REVIEW_ADD)}), NOT guessed:
""" + "".join(f"    {t!r}: {r}\n" for t, r in REVIEW_ADD.items())
    # Idempotent like the rest of the pass: a re-run must not append a second copy.
    marker = "ROUND 5 -- head of the location-less distribution"
    if marker not in LOG.read_text(encoding="utf-8"):
        with LOG.open("a", encoding="utf-8") as f:
            f.write(log_entry)

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"teams removed from review (now fixed): {removed}")
    print(f"teams added to review (ambiguous, not guessed): {added}")
    print(f"teams with no city AND no country: {len(still_missing)}")
    print(f"stints still un-plottable (missing city or country): {unlocated}")


if __name__ == "__main__":
    main()
