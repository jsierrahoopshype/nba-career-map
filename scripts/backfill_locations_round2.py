"""Second location backfill pass, generated from a tiered review of
teams_needing_review.json (idempotent). Reuses backfill_locations.py's
_fill / _missing_location_teams helpers verbatim.

Each entry below was classified HIGH confidence by one of two rules:
  - the team name contains a city name that is unambiguous elsewhere in this
    dataset (every other located stint using that exact city name agrees on
    one country), and the name isn't slash-joined and isn't preceded by an
    article/preposition that could mean a dropped compound-name word; or
  - this exact team-name string already has a located stint elsewhere in the
    data, reused directly.

A same-city-name match was NOT enough on its own where real-world knowledge
contradicts it — a manual review pass over the full candidate set caught and
excluded several statistically-unambiguous-in-this-dataset but factually
wrong matches (e.g. "Club de Regatas Lima" is a Peruvian club, not Lima,
Ohio; "Phoenix Fuel Masters" is a Philippine PBA team, not Phoenix, AZ;
"Newcastle Eagles" is English, not Australian; "Universidad de Los Lagos" is
Chilean, not Lagos, Nigeria) — see logs/location_review_proposals.txt for
the full tiered list and reasoning, including the ~105 additional HIGH-tier
candidates held at this round's ~150-change checkpoint.

Run:  python3 scripts/backfill_locations_round2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill_locations import (  # noqa: E402
    CAREERS, READY, LOCATIONS, REVIEW, _fill, _missing_location_teams,
)
import json  # noqa: E402

LOC_FIX: dict[str, tuple[str, str, str]] = {
    '7bet-Lietkabelis Panevėžys': ('Panevėžys', '', 'Lithuania'),
    'AEK Larnaca': ('Larnaca', '', 'Cyprus'),
    'AO Dafni': ('Dafni', '', 'Greece'),
    'APOEL Nicosia': ('Nicosia', '', 'Cyprus'),
    'APU Udine': ('Udine', '', 'Italy'),
    'Aguada': ('Aguada', '', 'Puerto Rico'),
    'Airmatic Valve of Cleveland': ('Cleveland', 'Ohio', 'USA'),
    'Aironi Novara': ('Novara', '', 'Italy'),
    'Akita Isuzu Motors': ('Akita', '', 'Japan'),
    'Al Jalaa Aleppo': ('Aleppo', '', 'Syria'),
    'Al Muharraq': ('Muharraq', '', 'Bahrain'),
    'Al Naser Dubai': ('Dubai', '', 'UAE'),
    'Al Nasr Dubai': ('Dubai', '', 'UAE'),
    'Al Rayyan Club': ('Al Rayyan', '', 'Qatar'),
    'Al Sadd Doha': ('Doha', '', 'Qatar'),
    'Al-Ahli Manama': ('Manama', '', 'Bahrain'),
    'Al-Ittihad Manama': ('Manama', '', 'Bahrain'),
    'Al-Manama': ('Manama', '', 'Bahrain'),
    'Al-Riyadh SC': ('Riyadh', '', 'Saudi Arabia'),
    'Alicante': ('Alicante', 'Valencian Community', 'Spain'),
    'Andino La Rioja': ('La Rioja', '', 'Argentina'),
    'Antalya': ('Antalya', '', 'Turkey'),
    'Antalya Kepez Belediyesi': ('Antalya', '', 'Turkey'),
    'Antibes': ('Antibes', '', 'France'),
    'Antranik Beirut': ('Beirut', '', 'Lebanon'),
    'Apoel Nicosia': ('Nicosia', '', 'Cyprus'),
    'Apollon Limassol': ('Limassol', '', 'Cyprus'),
    'Auckland Tuatara': ('Auckland', '', 'New Zealand'),
    'Axarquía': ('Vélez-Málaga', '', 'Spain'),
    'Ayandez Sazan Tehran': ('Tehran', '', 'Iran'),
    'Azad University Tehran': ('Tehran', '', 'Iran'),
    'BBV Collado Villalba': ('Collado Villalba', '', 'Spain'),
    'BC Slovan Bratislava': ('Bratislava', '', 'Slovakia'),
    'Baltimore Claws': ('Baltimore', 'Maryland', 'USA'),
    'Baltimore Colts': ('Baltimore', 'Maryland', 'USA'),
    'Baltimore Metros': ('Baltimore', 'Maryland', 'USA'),
    'Barranquilla': ('Barranquilla', '', 'Colombia'),
    'Basket Groot Leuven': ('Leuven', '', 'Belgium'),
    'Battle Creek Braves': ('Battle Creek', 'Michigan', 'USA'),
    'Battle Creek Warriors': ('Battle Creek', 'Michigan', 'USA'),
    'Bayer 04 Leverkusen': ('Leverkusen', '', 'Germany'),
    'Belgrano San Nicolás': ('San Nicolás', '', 'Argentina'),
    'Birra Messina Trapani': ('Trapani', '', 'Italy'),
    'Blue Stars Beirut': ('Beirut', '', 'Lebanon'),
    'Boston Frenzy': ('Boston', 'Massachusetts', 'USA'),
    'Boulazac': ('Boulazac', '', 'France'),
    'Boulazac Dordogne': ('Boulazac', '', 'France'),
    'Brescialat Gorizia': ('Gorizia', '', 'Italy'),
    'Bridgeport Newfield Steelers': ('Bridgeport', 'Connecticut', 'USA'),
    'Brocēni Rīga': ('Rīga', '', 'Latvia'),
    'Byblos': ('Byblos', '', 'Lebanon'),
    'C. Montana Forlì': ('Forlì', '', 'Italy'),
    'CBP Huesca': ('Huesca', '', 'Spain'),
    'COC-Ribeirão Preto': ('Ribeirão Preto', '', 'Brazil'),
    'Cabitel Gijón': ('Gijón', '', 'Spain'),
    'Caixa Ourense': ('Ourense', '', 'Spain'),
    'Cajacanarias La Laguna': ('La Laguna', '', 'Spain'),
    'Calgary Drillers': ('Calgary', 'Alberta', 'Canada'),
    "Capo d'Orlando": ("Capo d'Orlando", '', 'Italy'),
    'Casale Monferrato': ('Casale Monferrato', '', 'Italy'),
    'Caspian Qazvin': ('Qazvin', '', 'Iran'),
    'Cercom Ferrara': ('Ferrara', '', 'Italy'),
    'Chabeb-Zahle': ('Zahle', '', 'Lebanon'),
    'Changsha Bank Guangdong': ('Changsha', '', 'China'),
    'Changsha Wantian Yongsheng': ('Changsha', '', 'China'),
    'Charleroi': ('Charleroi', '', 'Belgium'),
    'Chemnitz 99': ('Chemnitz', '', 'Germany'),
    'Cherry Hill Demons': ('Cherry Hill', 'New Jersey', 'USA'),
    'Châlons-en-Champagne': ('Châlons-en-Champagne', '', 'France'),
    'Cidneo Brescia': ('Brescia', '', 'Italy'),
    'Citrosil Verona': ('Verona', '', 'Italy'),
    'Cleveland Brass': ('Cleveland', 'Ohio', 'USA'),
    'Cleveland Chase Brassmen': ('Cleveland', 'Ohio', 'USA'),
    'Club Bàsquet Llíria': ('Llíria', '', 'Spain'),
    'Collado Villalba': ('Collado Villalba', '', 'Spain'),
    'Coren Ourense': ('Ourense', '', 'Spain'),
    'Cáceres C.B.': ('Cáceres', '', 'Spain'),
    'Denver Truckers': ('Denver', 'Colorado', 'USA'),
    'Drac Inca': ('Inca', '', 'Spain'),
    'Draghi Novara': ('Novara', '', 'Italy'),
    'Dubai': ('Dubai', '', 'UAE'),
    'ESPE Châlons-en-Champagne': ('Châlons-en-Champagne', '', 'France'),
    'East Pittsburgh Pirates': ('Pittsburgh', 'Pennsylvania', 'USA'),
    'Elecon Desio': ('Desio', '', 'Italy'),
    'Eskişehir': ('Eskişehir', '', 'Turkey'),
    'Espé Basket Châlons-en-Champagne': ('Châlons-en-Champagne', '', 'France'),
    'FL Forlì': ('Forlì', '', 'Italy'),
    'Fabriano': ('Fabriano', '', 'Italy'),
    'Fargo–Moorhead Fever': ('Fargo', 'North Dakota', 'USA'),
    'Fastlink Amman': ('Amman', '', 'Jordan'),
    'Ferrara': ('Ferrara', '', 'Italy'),
    'Ferrys Llíria': ('Llíria', '', 'Spain'),
    'Filanto Desio': ('Desio', '', 'Italy'),
    'Filanto Forlì': ('Forlì', '', 'Italy'),
    'Forlì': ('Forlì', '', 'Italy'),
    'Fortress Körmend': ('Körmend', '', 'Hungary'),
    'Fos-sur-mer Basket': ('Fos-sur-mer', '', 'France'),
    'Fribourg': ('Fribourg', '', 'Switzerland'),
    'Fürstenfeld Panthers': ('Fürstenfeld', '', 'Austria'),
    'GSA Udine': ('Udine', '', 'Italy'),
    'Galicia Ferrol': ('Ferrol', '', 'Spain'),
    'GeVi Napoli': ('Napoli', '', 'Italy'),
    'Girona Gavis': ('Girona', '', 'Spain'),
    'Glint Manisa Basket': ('Manisa', '', 'Turkey'),
    'Great Falls Sky': ('Great Falls', 'Montana', 'USA'),
    'Grupo AGB Huesca': ('Huesca', '', 'Spain'),
    'Guadalajara Black Knights': ('Guadalajara', '', 'Mexico'),
    'Hangzhou Jingwei': ('Hangzhou', '', 'China'),
    'Hartford Downtowners': ('Hartford', 'Connecticut', 'USA'),
    'Heidelberg': ('Heidelberg', '', 'Germany'),
    'Hong Kong Eastern Long Lions': ('Hong Kong', '', 'China'),
    'Houston Flyers': ('Houston', 'Texas', 'USA'),
    'Huesca La Magia': ('Huesca', '', 'Spain'),
    'Hyundai Desio': ('Desio', '', 'Italy'),
    'Ignis Novara': ('Novara', '', 'Italy'),
    'Illescas': ('Illescas', '', 'Spain'),
    'Imola': ('Imola', '', 'Italy'),
    'Inca': ('Inca', '', 'Spain'),
    'JA Vichy-Clermont': ('Vichy', '', 'France'),
    'Jackson Jammers': ('Jackson', 'Mississippi', 'USA'),
    'Jacksonville Jets': ('Jacksonville', 'Florida', 'USA'),
    'Jahesh Tarabar Qom': ('Qom', '', 'Iran'),
    'Juvi Cremona': ('Cremona', '', 'Italy'),
    'Kahraba Beirut': ('Beirut', '', 'Lebanon'),
    'Kansas City Blues': ('Kansas City', 'Missouri', 'USA'),
    'Kansas City Steers': ('Kansas City', 'Missouri', 'USA'),
    'Kepez Bld Antalya': ('Antalya', '', 'Turkey'),
    'Kinzo Amstelveen': ('Amstelveen', '', 'Netherlands'),
    'Komfort Stargard Szczec': ('Stargard', '', 'Poland'),
    'Konya Kombassan': ('Konya', '', 'Turkey'),
    'Kotwica Kołobrzeg': ('Kołobrzeg', '', 'Poland'),
    'Las Vegas Silver Streaks': ('Las Vegas', 'Nevada', 'USA'),
    'Latini Forlì': ('Forlì', '', 'Italy'),
    'Lavoropiù Fortitudo Bologna': ('Bologna', '', 'Italy'),
    'Le Havre': ('Le Havre', '', 'France'),
    'Lebole Mestre': ('Mestre', '', 'Italy'),
    'Leiden': ('Leiden', '', 'Netherlands'),
    'Leuven': ('Leuven', '', 'Belgium'),
    'Liège': ('Liège', '', 'Belgium'),
    'Lleida': ('Lleida', '', 'Spain'),
    'Llíria': ('Llíria', '', 'Spain'),
    'Los Angeles Jaguars': ('Los Angeles', 'California', 'USA'),
    'Magia Huesca': ('Huesca', '', 'Spain'),
    'Magic M7 Borås': ('Borås', '', 'Sweden'),
    'Manama Club': ('Manama', '', 'Bahrain'),
    'Manisa': ('Manisa', '', 'Turkey'),
    'Manisa BB': ('Manisa', '', 'Turkey'),
    'Manner Novara': ('Novara', '', 'Italy'),
    'Melilla': ('Melilla', '', 'Spain'),
    'Memphis Fire': ('Memphis', 'Tennessee', 'USA'),
}


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    # _fill reads the module-level LOC_FIX in backfill_locations, so patch it
    # to this round's map before calling the shared helper.
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
    still_missing = _missing_location_teams(careers)
    REVIEW.write_text(json.dumps(dict(sorted(review.items())),
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints located: {n1}")
    print(f"READY.json  stints located: {n2}")
    print(f"team_locations.json entries updated: {loc_updated}")
    print(f"teams removed from review (now fixed): {removed}")
    print(f"teams still location-less total: {len(still_missing)}")


if __name__ == "__main__":
    main()
