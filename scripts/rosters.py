"""Fetch current NBA rosters from Wikipedia (API only, no HTML scraping).

Each franchise has a roster template ``Template:<Team> roster`` that Wikipedia
transcludes into the team article. We fetch that template's wikitext and
extract the player wikilinks. This avoids scraping rendered HTML and stays
within the Wikipedia API.

If a roster template can't be read the team is skipped (logged) so a single
missing page never aborts a run.
"""
from __future__ import annotations

import re

from wikipedia_api import WikipediaClient, RequestBudgetExceeded

# Canonical current franchise names -> the Wikipedia roster-template title.
NBA_TEAMS = {
    "Atlanta Hawks": "Atlanta Hawks roster",
    "Boston Celtics": "Boston Celtics roster",
    "Brooklyn Nets": "Brooklyn Nets roster",
    "Charlotte Hornets": "Charlotte Hornets roster",
    "Chicago Bulls": "Chicago Bulls roster",
    "Cleveland Cavaliers": "Cleveland Cavaliers roster",
    "Dallas Mavericks": "Dallas Mavericks roster",
    "Denver Nuggets": "Denver Nuggets roster",
    "Detroit Pistons": "Detroit Pistons roster",
    "Golden State Warriors": "Golden State Warriors roster",
    "Houston Rockets": "Houston Rockets roster",
    "Indiana Pacers": "Indiana Pacers roster",
    "LA Clippers": "Los Angeles Clippers roster",
    "Los Angeles Lakers": "Los Angeles Lakers roster",
    "Memphis Grizzlies": "Memphis Grizzlies roster",
    "Miami Heat": "Miami Heat roster",
    "Milwaukee Bucks": "Milwaukee Bucks roster",
    "Minnesota Timberwolves": "Minnesota Timberwolves roster",
    "New Orleans Pelicans": "New Orleans Pelicans roster",
    "New York Knicks": "New York Knicks roster",
    "Oklahoma City Thunder": "Oklahoma City Thunder roster",
    "Orlando Magic": "Orlando Magic roster",
    "Philadelphia 76ers": "Philadelphia 76ers roster",
    "Phoenix Suns": "Phoenix Suns roster",
    "Portland Trail Blazers": "Portland Trail Blazers roster",
    "Sacramento Kings": "Sacramento Kings roster",
    "San Antonio Spurs": "San Antonio Spurs roster",
    "Toronto Raptors": "Toronto Raptors roster",
    "Utah Jazz": "Utah Jazz roster",
    "Washington Wizards": "Washington Wizards roster",
}

# Tokens that appear as wikilinks inside roster templates but are not players.
_STOP = re.compile(
    r"^(?:point guard|shooting guard|small forward|power forward|center|guard|"
    r"forward|head coach|assistant coach|two-way|injured|list of|"
    r"national basketball association|nba|g league|"
    r".* (?:season|roster|draft))\b",
    re.IGNORECASE,
)


def extract_players_from_roster(wikitext: str) -> list[str]:
    names = []
    seen = set()
    for m in re.finditer(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", wikitext):
        target = m.group(1).strip()
        if not target or _STOP.match(target):
            continue
        # players are person articles: require at least two capitalized words
        if not re.match(r"[A-ZÀ-Ý].*\s+\S", target):
            continue
        if target not in seen:
            seen.add(target)
            names.append(target)
    return names


def fetch_all_rosters(client: WikipediaClient) -> dict[str, list[str]]:
    """Return {canonical_team: [player names]} for every NBA team reachable."""
    rosters: dict[str, list[str]] = {}
    for team, template in NBA_TEAMS.items():
        try:
            wt = client.get_wikitext(f"Template:{template}")
        except RequestBudgetExceeded:
            print(f"[rosters] budget exhausted before {team}")
            break
        except Exception as exc:  # noqa: BLE001 - never abort on one team
            print(f"[rosters] {team}: fetch failed ({exc})")
            continue
        if not wt:
            print(f"[rosters] {team}: roster template not found")
            continue
        players = extract_players_from_roster(wt)
        rosters[team] = players
        print(f"[rosters] {team}: {len(players)} players")
    return rosters
