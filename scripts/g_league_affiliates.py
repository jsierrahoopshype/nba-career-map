"""G League (and NBA D-League) clubs mapped to their NBA parent franchise.

WHY THIS EXISTS. When a player is assigned to his NBA club's own G League
affiliate, both stints start in the same year and both are ranges, so the
end-year tie-break in stint_order puts whichever ended first at the front --
which reads as though he played for the affiliate before the NBA club that
assigned him there. Aaron Wiggins came out as Oklahoma City Blue (2021-2023)
before Oklahoma City Thunder (2021-2026). The parent belongs first.

The relationship cannot be inferred from the names: "Oklahoma City Blue" and
"Oklahoma City Thunder" happen to share a city, but "Rio Grande Valley Vipers"
and "Houston Rockets" share nothing, while "Memphis Hustle" and "Memphis
Grizzlies" share a city with each other AND with clubs that are not affiliates.
So the pairs are curated here and consumed as data.

SCOPE, deliberately conservative. This lists clubs whose NBA parent is
unambiguous -- the one-to-one G League era (2017-present) and the earlier
D-League single-affiliate arrangements that are well established. Clubs from
the early D-League years that several NBA teams shared, and clubs from other
leagues entirely (CBA, EPBL, CBA-era barnstorming sides), are deliberately
ABSENT: without a single parent there is no basis to reorder, so those stints
keep the order they already have. Absent means "leave alone", never "guess".

A few clubs are the same franchise under a later name (Maine Red Claws ->
Maine Celtics, Delaware 87ers -> Delaware Blue Coats, Los Angeles D-Fenders ->
South Bay Lakers). Each name is listed separately, because the stored stint
carries whichever name was current at the time.

Parents are named by their CURRENT franchise name; stint_order resolves an era
name (e.g. "New Jersey Nets") to the current franchise before looking here.
"""
from __future__ import annotations

# G League / D-League club -> current NBA franchise that is its parent.
AFFILIATE_PARENT: dict[str, str] = {
    # --- Atlantic ---
    "Maine Red Claws": "Boston Celtics",
    "Maine Celtics": "Boston Celtics",
    "Long Island Nets": "Brooklyn Nets",
    "Springfield Armor": "Brooklyn Nets",
    "Westchester Knicks": "New York Knicks",
    "Delaware 87ers": "Philadelphia 76ers",
    "Delaware Blue Coats": "Philadelphia 76ers",
    "Raptors 905": "Toronto Raptors",
    # --- Central ---
    "Windy City Bulls": "Chicago Bulls",
    "Canton Charge": "Cleveland Cavaliers",
    "Cleveland Charge": "Cleveland Cavaliers",
    "Grand Rapids Drive": "Detroit Pistons",
    "Motor City Cruise": "Detroit Pistons",
    "Fort Wayne Mad Ants": "Indiana Pacers",
    "Indiana Mad Ants": "Indiana Pacers",
    "Noblesville Boom": "Indiana Pacers",
    "Wisconsin Herd": "Milwaukee Bucks",
    # --- Southeast ---
    "College Park Skyhawks": "Atlanta Hawks",
    "Greensboro Swarm": "Charlotte Hornets",
    "Sioux Falls Skyforce": "Miami Heat",
    "Lakeland Magic": "Orlando Magic",
    "Osceola Magic": "Orlando Magic",
    "Capital City Go-Go": "Washington Wizards",
    # --- Southwest ---
    "Texas Legends": "Dallas Mavericks",
    "Rio Grande Valley Vipers": "Houston Rockets",
    "Memphis Hustle": "Memphis Grizzlies",
    "Iowa Energy": "Memphis Grizzlies",
    "Birmingham Squadron": "New Orleans Pelicans",
    "Austin Toros": "San Antonio Spurs",
    "Austin Spurs": "San Antonio Spurs",
    # --- Northwest ---
    "Grand Rapids Gold": "Denver Nuggets",
    "Iowa Wolves": "Minnesota Timberwolves",
    "Oklahoma City Blue": "Oklahoma City Thunder",
    "Tulsa 66ers": "Oklahoma City Thunder",
    "Rip City Remix": "Portland Trail Blazers",
    "Idaho Stampede": "Utah Jazz",
    "Utah Flash": "Utah Jazz",
    "Salt Lake City Stars": "Utah Jazz",
    # --- Pacific ---
    "Golden State Warriors G League": "Golden State Warriors",
    "Santa Cruz Warriors": "Golden State Warriors",
    "Agua Caliente Clippers": "LA Clippers",
    "Ontario Clippers": "LA Clippers",
    "Los Angeles D-Fenders": "Los Angeles Lakers",
    "South Bay Lakers": "Los Angeles Lakers",
    "Northern Arizona Suns": "Phoenix Suns",
    "Valley Suns": "Phoenix Suns",
    "Reno Bighorns": "Sacramento Kings",
    "Stockton Kings": "Sacramento Kings",
}

# Deliberately NOT listed, and why -- so the next pass doesn't re-derive it.
# Early D-League clubs shared by several NBA teams at once (Bakersfield Jam,
# Erie BayHawks, Anaheim Arsenal, Fort Worth Flyers, Albuquerque Thunderbirds,
# Colorado 14ers, Roanoke Dazzle, Florida Flame), and clubs from leagues with
# no NBA affiliation at all (Rockford Lightning, Wichita Falls Texans, Hamden
# Bics, Atlantic City Hi-Rollers, Sunbury Mercuries, Wilkes-Barre Barons,
# Hazleton Pros, Wisconsin Flyers, Oklahoma City Cavalry, Harrisburg Patriots,
# Grand Rapids Tackers, Shanghai Sharks).
