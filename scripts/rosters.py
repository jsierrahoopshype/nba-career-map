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

# Current NBA roster templates list each player in a {{player2}} row whose name
# is split across `first`/`last` params with NO wikilink, e.g.:
#   {{player2 | num=7 | first=Santi | last=Aldama | pos=FC | note=FA | inj=yes }}
# Older/other templates used a single `name=[[Player]]` wikilink; those are kept
# as fallbacks. Coach rows, {{NBA roster/header}}, Category: links and high
# schools are never {{player2}} rows, so they are excluded by construction.
_PLAYER2_TEMPLATE = {"player2"}
_LEGACY_PLAYER_TEMPLATES = {"nba roster/player", "roster player"}

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


def _clean_field(val: str) -> str:
    """Strip any stray markup from a template field and normalize whitespace."""
    if not val:
        return ""
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", val)
    v = re.sub(r"<[^>]+>", "", v)
    v = re.sub(r"\{\{[^}]*\}\}", "", v)
    return re.sub(r"\s+", " ", v).strip()


def _combine_name(params: dict) -> str:
    """Join first + last into a full name. A suffix carried on the last-name
    field (e.g. last='Clayton Jr.') is preserved, yielding 'Walter Clayton Jr.'.
    """
    first = _clean_field(params.get("first", ""))
    last = _clean_field(params.get("last", ""))
    return re.sub(r"\s+", " ", f"{first} {last}").strip()


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


def extract_roster_entries(wikitext: str) -> list[dict]:
    """Parse a roster template into per-player entries with metadata.

    Each entry is ``{"name", "num", "pos", "note", "injured"}``:
      * name    — full name (first + last, suffix preserved)
      * num     — jersey number (may be "")
      * pos     — position code (e.g. "PG", "FC")
      * note    — roster note, upper-cased: "FA" (free agent / expiring),
                  "DP" (draft pick), "TW" (two-way), etc. ("" if none)
      * injured — True when the row carries inj=yes

    Primary format is the current ``{{player2|first=..|last=..|...}}`` row; if a
    template yields none we fall back to the older ``{{NBA roster/player}}`` /
    ``{{Roster player}}`` forms (name held in a ``name=[[..]]`` wikilink).

    Free-agent (note=FA) players are INCLUDED: they still appear on the roster
    template, and their real status is decided later from their own Wikipedia
    page by the classification guard (a FA with no current NBA stint will not
    end up nba_active). Excluding them here would instead risk a real player
    being wrongly treated as "dropped from the roster".
    """
    entries, seen = [], set()

    for body in _iter_template_bodies(wikitext, _PLAYER2_TEMPLATE):
        p = _params(body)
        name = _combine_name(p)
        if not _is_person_name(name) or name in seen:
            continue
        seen.add(name)
        entries.append({
            "name": name,
            "num": _clean_field(p.get("num", "")),
            "pos": _clean_field(p.get("pos", "")),
            "note": _clean_field(p.get("note", "")).upper(),
            "injured": _clean_field(p.get("inj", "")).lower() in ("y", "yes", "true", "1"),
        })
    if entries:
        return entries

    # fallback: older wikilink-based player rows
    for body in _iter_template_bodies(wikitext, _LEGACY_PLAYER_TEMPLATES):
        p = _params(body)
        name = _player_name_from_value(p.get("name", ""))
        if not _is_person_name(name) or name in seen:
            continue
        seen.add(name)
        entries.append({
            "name": name,
            "num": _clean_field(p.get("num", "")),
            "pos": _clean_field(p.get("pos", "")),
            "note": "",
            "injured": False,
        })
    return entries


def extract_players_from_roster(wikitext: str) -> list[str]:
    """Return just the player names from a roster template (primary pipeline
    output). Metadata is available via :func:`extract_roster_entries`."""
    return [e["name"] for e in extract_roster_entries(wikitext)]


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
