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

# Template names (normalized: lowercased, underscores->spaces) whose `name=`
# parameter holds a *player*. Coach rows use a different template and are
# deliberately excluded so head/assistant coaches never enter the roster set.
_PLAYER_TEMPLATES = {"nba roster/player", "roster player"}

# Namespaced / non-person link targets that must never be treated as a player.
_NON_PERSON_PREFIX = re.compile(
    r"^(?:category|file|image|template|wikipedia|help|portal|list of)\s*:",
    re.IGNORECASE,
)


def _iter_template_bodies(wikitext: str, names: set[str]):
    """Yield the inner body (text after the template name) of every
    ``{{name|...}}`` invocation whose name matches one of ``names``.

    Brace-aware so nested templates/links inside a row do not break scanning.
    """
    i, n = 0, len(wikitext)
    while i < n - 1:
        if wikitext[i:i + 2] == "{{":
            depth, j = 1, i + 2
            while j < n - 1 and depth:
                pair = wikitext[j:j + 2]
                if pair == "{{":
                    depth += 1; j += 2
                elif pair == "}}":
                    depth -= 1; j += 2
                else:
                    j += 1
            inner = wikitext[i + 2:j - 2]
            head, sep, rest = inner.partition("|")
            norm = re.sub(r"[_\s]+", " ", head).strip().lower()
            if sep and norm in names:
                yield rest
            i = j
        else:
            i += 1


def _split_top_level(body: str, sep: str) -> list[str]:
    """Split on ``sep`` only at brace/bracket depth 0."""
    parts, buf = [], []
    td = ld = 0
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two == "{{":
            td += 1; buf.append(two); i += 2; continue
        if two == "}}":
            td = max(0, td - 1); buf.append(two); i += 2; continue
        if two == "[[":
            ld += 1; buf.append(two); i += 2; continue
        if two == "]]":
            ld = max(0, ld - 1); buf.append(two); i += 2; continue
        ch = body[i]
        if ch == sep and td == 0 and ld == 0:
            parts.append("".join(buf)); buf = []; i += 1; continue
        buf.append(ch); i += 1
    parts.append("".join(buf))
    return parts


def _params(body: str) -> dict[str, str]:
    out = {}
    for chunk in _split_top_level(body, "|"):
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            out[k.strip().lower()] = v.strip()
    return out


def _player_name_from_value(val: str) -> str:
    """Extract a player's article name from a `name=` value.

    Prefers the wikilink *target* ([[Target|Display]] -> Target) so we key on
    the canonical article title; falls back to cleaned plain text.
    """
    if not val:
        return ""
    m = re.search(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]", val)
    target = m.group(1).strip() if m else val
    target = re.sub(r"<[^>]+>", "", target)
    target = re.sub(r"\{\{[^}]*\}\}", "", target)
    return re.sub(r"\s+", " ", target).strip()


def _is_person_name(name: str) -> bool:
    if not name or _NON_PERSON_PREFIX.match(name):
        return False
    if "high school" in name.lower():
        return False
    # a person article title has at least two whitespace-separated tokens
    return bool(re.match(r"[^\W\d].*\s+\S", name))


def extract_players_from_roster(wikitext: str) -> list[str]:
    """Return the player names from a roster template.

    Parses the structured ``{{NBA roster/player|...|name=[[Player]]|...}}`` rows
    (and the older ``{{Roster player|...}}`` form) rather than scraping every
    wikilink, so coach rows, ``[[Category:...]]`` tags, and high-school links are
    never captured.
    """
    names, seen = [], set()
    for body in _iter_template_bodies(wikitext, _PLAYER_TEMPLATES):
        name = _player_name_from_value(_params(body).get("name", ""))
        if _is_person_name(name) and name not in seen:
            seen.add(name)
            names.append(name)
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
        warn = "  ⚠️ no player rows parsed — template format may have changed" \
            if not players else ""
        print(f"[rosters] {team}: {len(players)} players{warn}")
    return rosters
