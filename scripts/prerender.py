"""Generate a real HTML file per player, with SEO tags baked into the markup.

The site is one 345 KB HTML file that renders everything client-side, so the
served markup for every ?player= URL is identical: same title, same
description, same OG image. Crawlers read the served HTML, not the rendered
DOM, so the per-player titles the app sets at runtime are invisible to them.
These files fix that by existing before the crawler arrives.

WHY THESE PAGES DO NOT CLONE THE APP SHELL. The obvious design -- serve the
same app with a prerendered head and let the JS take over -- costs 345 KB per
player, which is 1.8 GB across 5,179 of them. Splitting the app into external
assets first would be a large refactor of a working file. So each page is
instead a real, self-contained career summary: the head tags, the player's
full stop-by-stop table as actual HTML, and a link into the interactive map.
That reads better to a crawler than a redirect stub (it has content, not just
tags) and it is honest for a human who lands on it.

It also keeps the canonical story straight. These pages are canonical for
players and are what sitemap.xml lists; a page that instantly bounced the
visitor elsewhere could not be. The ?player= URLs keep working exactly as
before -- every existing share link and internal app link still resolves --
and index.html rewrites its canonical to the matching static page when it
boots with ?player=, so the two do not compete as duplicates.

Output is deterministic and written only when the bytes change, so a daily
pipeline run rewrites the handful of players whose data actually moved rather
than all 5,179.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

# This module is imported by build_dashboard_data.py and by the tests, both of
# which put scripts/ on the path first -- but not by everything that might, so
# the sibling imports below get their own guarantee.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_urls  # noqa: E402
from names import normkey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLAYER_DIR = ROOT / "player"
TEAM_DIR = ROOT / "team"
COUNTRY_DIR = ROOT / "country"

# A country page lists the clubs played in, not every player: the USA alone has
# 5,068 players against 647 clubs, so the club list is the bounded, readable
# one. The real player total is still stated.
COUNTRY_CLUB_CAP = 250

# scripts/site_config.py owns this now -- it used to be written out here, in
# og_tags.py and in build_dashboard_data.py, and three copies of an address is
# how one of them ends up pointing at the wrong domain.
from site_config import SITE_BASE_URL  # noqa: E402
OG_IMAGE = f"{SITE_BASE_URL}/assets/og-career-map.png"
# Per-player cards (scripts/og_cards.py) exist for the shared-heavy slice of
# players. A page uses its own card when one has been generated and the shared
# career-map image otherwise, so a missing card is a fallback, never a 404.
CARD_DIR = ROOT / "assets" / "og" / "player"
OG_IMAGE_ALT = ("NBA career paths drawn as red arcs across a map of the United "
                "States, with NBA team logos at each stop")

# Search engines truncate a description around here; the lede is built to fit.
DESC_MAX = 160


def slug(name: str) -> str:
    """'Nikola Jokić' -> 'nikola-jokic'. Stable, lowercase, ASCII-only."""
    s = "".join(c for c in unicodedata.normalize("NFKD", name or "")
                if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "player"


def player_url(name: str) -> str:
    return f"{SITE_BASE_URL}/player/{slug(name)}.html"


def team_url(name: str) -> str:
    return f"{SITE_BASE_URL}/team/{slug(name)}.html"


def country_url(name: str) -> str:
    return f"{SITE_BASE_URL}/country/{slug(name)}.html"


# --- the Wikipedia link -----------------------------------------------------
# sameAs tells a crawler "this page and that page are about the same person", so
# it is the one field on these pages that can actively assert something false.
# For 39 records the stored article is a namesake's -- David Duke the Klansman,
# Jack White the guitarist, Ace Bailey the ice hockey player -- and publishing
# it would have Google believe the page is about them. Two files decide it, both
# read lazily:
#
#   player_url_overrides.json   the curated article, verified against Wikidata.
#                               When a player has one, it IS the link, whatever
#                               the career record still says.
#   bio_needs_review.json       `wikipedia_url_wrong_person`: the records known
#                               to point at a namesake. No override yet means no
#                               article we can stand behind, so the page gets NO
#                               sameAs rather than a wrong one. Silence is a
#                               missing field; a wrong sameAs is a false claim.
#
# A player drops off that list once his record is re-read against the curated
# article, so this suppression lifts by itself as the repairs land.
REVIEW_FILE = ROOT / "data" / "players" / "bio_needs_review.json"
_WRONG_PERSON: frozenset | None = None


def _wrong_person_names() -> frozenset:
    """Names whose stored article is a namesake's, plus their normalized keys.

    A missing or unreadable review file means no suppression -- the same
    tolerance the rest of this module shows its inputs. It cannot publish a
    wrong link on its own: every URL it lets through is one the career database
    already holds.
    """
    global _WRONG_PERSON
    if _WRONG_PERSON is None:
        try:
            doc = json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
            rows = doc.get("wikipedia_url_wrong_person") or []
        except (OSError, ValueError, AttributeError):
            rows = []
        names = set()
        for row in rows:
            name = row.get("player") if isinstance(row, dict) else None
            if name:
                names.add(name)
                names.add(normkey(name))
        _WRONG_PERSON = frozenset(names)
    return _WRONG_PERSON


def wikipedia_link(player: dict) -> str:
    """The article sameAs may name, or "" when there is none to stand behind."""
    keys = [k for k in (player.get("player"), player.get("display_name")) if k]
    for key in keys:
        url = player_urls.override_url(key)
        if url:
            return url
    suspect = _wrong_person_names()
    for key in keys:
        if key in suspect or normkey(key) in suspect:
            return ""
    return (player.get("wikipedia_url") or "").strip()


# --- birth and death: deliberately absent -----------------------------------
# Nothing here reads them. data/players/player_bio.json (written by
# scripts/fetch_bio_wikidata.py) is kept and kept current, but it belongs to a
# separate section of the site, not to the Career Map: these pages publish no
# birth or death facts, visibly or in their structured data, so this build has
# no reason to open the file at all.


def person_jsonld(player: dict) -> str:
    """A schema.org Person for the player page.

    Who the page is about, where it lives, and the two facts the Career Map
    itself holds: the player's nationality and his Wikipedia article -- the
    latter only when it is his, see wikipedia_link.

    NO BIRTH OR DEATH FACTS. birthDate, birthPlace, deathDate and deathPlace
    were published here and have been removed: that data is for a separate
    section of the site, and a Career Map page is not where it belongs. The
    source file (data/players/player_bio.json) and the workflow that keeps it
    current are untouched -- this build simply does not read them.
    """
    name = player.get("display_name") or player.get("player") or ""
    key = player.get("player") or name
    data = {"@context": "https://schema.org", "@type": "Person",
            "name": name, "url": player_url(key)}
    if (player.get("nationality") or "").strip():
        data["nationality"] = player["nationality"]
    link = wikipedia_link(player)
    if link:
        data["sameAs"] = link
    # A "</script>" inside a JSON string would end the block early; escaping
    # the angle brackets is the standard fix and keeps the JSON valid.
    body = (json.dumps(data, ensure_ascii=False, indent=None)
            .replace("<", "\\u003c").replace(">", "\\u003e"))
    return f'<script type="application/ld+json">{body}</script>'


# --- internal links from a career row --------------------------------------
# The same destinations teams.html sends a click to, in the same URL forms:
# a franchise and a country have a prerendered page and get it, a club and a
# city have none and keep their query URL on teams.html. Franchise and era
# names are read from data/nba_team_index.json (the file stint_order.py reads
# for the same reason) rather than re-derived here, so there is one source of
# truth and no import cycle with build_dashboard_data, which writes it.
TEAM_INDEX = ROOT / "data" / "nba_team_index.json"
_TEAM_IDX: dict | None = None


def _team_index() -> dict:
    """{eras, franchises, collisions}, read once, on first use.

    Lazily rather than at import: build_dashboard_data imports this module at
    the top and writes nba_team_index.json part-way through its run, so a
    module-level read would see the previous run's file.
    """
    global _TEAM_IDX
    if _TEAM_IDX is None:
        try:
            _TEAM_IDX = json.loads(TEAM_INDEX.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _TEAM_IDX = {}
    return _TEAM_IDX


def _start_year(years) -> int | None:
    m = re.search(r"\d{4}", str(years or ""))
    return int(m.group()) if m else None


def franchise_of(team: str, years=None) -> str | None:
    """Current NBA franchise for a stint's team name, or None for a club.

    A collision era name (the modern San Diego Clippers re-using the Clippers'
    1978-84 name) resolves to None from its cutoff year on, so a G League
    stint never links to the NBA franchise's page.
    """
    idx = _team_index()
    cut = (idx.get("collisions") or {}).get(team)
    year = _start_year(years)
    if cut is not None and year is not None and year >= cut:
        return None
    if team in set(idx.get("franchises") or ()):
        return team
    return (idx.get("eras") or {}).get(team)


def team_href(team: str, years=None, depth: str = "..") -> str:
    fr = franchise_of(team, years)
    if fr:
        return f"{depth}/team/{slug(fr)}.html"
    return f"{depth}/teams.html?club={quote(team)}"


def city_href(city: str, country: str = "", depth: str = "..") -> str:
    # city AND country: Valencia, Spain and Valencia, Venezuela are two places
    # (see teams.html's cityHref, which builds the same URL).
    q = f"{depth}/teams.html?city={quote(city)}"
    return q + (f"&country={quote(country)}" if country else "")


def country_href(country: str, depth: str = "..") -> str:
    return f"{depth}/country/{slug(country)}.html"


def link(href: str, text: str) -> str:
    return f'<a class="link" href="{esc(href)}">{esc(text)}</a>'


def esc(text) -> str:
    return (str(text if text is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _countries(hist: list) -> list[str]:
    return list(dict.fromkeys((s.get("country") or "").strip()
                              for s in hist if (s.get("country") or "").strip()))


def _clubs(hist: list) -> list[str]:
    return list(dict.fromkeys((s.get("team") or "").strip()
                              for s in hist if (s.get("team") or "").strip()))


def build_description(player: dict) -> str:
    """A description that names the player's actual teams and countries.

    Generic copy would defeat the point of the exercise, so this always states
    the real counts and names real clubs, trimming club names off the end until
    it fits in DESC_MAX rather than cutting a sentence mid-word.
    """
    name = player.get("display_name") or player.get("player") or ""
    hist = player.get("career_history", []) or []
    clubs, countries = _clubs(hist), _countries(hist)
    current = (player.get("current_team") or "").strip()

    if not clubs:
        return (f"{name}'s NBA career path on the HoopsHype Career Map: every "
                f"team, city and country, mapped stop by stop.")[:DESC_MAX]

    where = (f"{len(clubs)} clubs" if len(clubs) != 1 else "one club")
    # "4 clubs in USA" reads wrong; a handful of country names want an article.
    _article = {"USA", "UK", "United Kingdom", "Netherlands", "Philippines",
                "Dominican Republic", "Czech Republic", "United Arab Emirates"}
    if len(countries) > 1:
        span = f" in {len(countries)} countries"
    elif countries:
        one = countries[0]
        span = f" in the {one}" if one in _article else f" in {one}"
    else:
        span = ""
    # current_team holds the LAST club for a retired player, so "now with
    # Detroit Pistons" would be wrong for someone who finished in 1977. Tense
    # and tail both follow status.
    retired = (player.get("status") or "") == "retired"
    verb = "played for" if retired else "has played for"
    head = f"{name} {verb} {where}{span}"
    if not current:
        tail = "."
    else:
        tail = f", last with {current}." if retired else f", now with {current}."
    # The current club is named in the tail, so keep it out of the list or it
    # appears twice ("... Minnesota Timberwolves, now with Minnesota
    # Timberwolves").
    listable = [c for c in clubs if c != current] if current else clubs

    # Name as many clubs as the length budget allows, trimming from the end.
    for n in range(min(4, len(listable)), 0, -1):
        named = ", ".join(listable[:n])
        more = " and more" if len(listable) > n else ""
        out = f"{head}: {named}{more}{tail}"
        if len(out) <= DESC_MAX:
            return out
    out = head + tail
    return out if len(out) <= DESC_MAX else out[:DESC_MAX - 1].rstrip(" ,") + "."


def build_title(player: dict) -> str:
    name = player.get("display_name") or player.get("player") or ""
    return f"Where Has {name} Played? Every Team, City and Country | HoopsHype"


def _shell(*, title: str, desc: str, canon: str, og_type: str, h1: str,
           lede: str, extra: str, app_href: str, cta: str, pre_cta: str = "",
           image: str | None = None, image_alt: str | None = None,
           depth: str = "..", head_extra: str = "", tail: str = "") -> str:
    """The markup every prerendered page shares: head tags, header, lede, CTA.

    One shell rather than three templates -- the whole point of these pages is
    the head block, and three copies of it would drift.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- Generated by scripts/prerender.py from the career database. Do not edit by
     hand: the next pipeline run overwrites it. -->
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{esc(canon)}">
<meta property="og:type" content="{og_type}">
<meta property="og:site_name" content="NBA Career Map">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canon)}">
<meta property="og:image" content="{esc(image or OG_IMAGE)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(image_alt or OG_IMAGE_ALT)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(image or OG_IMAGE)}">
<meta name="twitter:image:alt" content="{esc(image_alt or OG_IMAGE_ALT)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{depth}/assets/prerender.css">
{head_extra}</head>
<body>
<header class="ph"><div class="ph-inner">
<a class="brand" href="{depth}/index.html">NBA Career Map</a>
<nav class="nav"><a href="{depth}/index.html">Map</a><a href="{depth}/teams.html">Teams</a><a href="{depth}/quiz.html">Quiz</a></nav>
</div></header>
<main>
<h1>{esc(h1)}</h1>
<p class="lede">{esc(lede)}</p>
{pre_cta}<p class="cta"><a class="btn" href="{esc(app_href)}">{esc(cta)}</a></p>
{extra}
<p class="foot"><a href="{depth}/index.html">Every NBA player's career, mapped</a> ·
<a href="{depth}/teams.html">Browse by team</a></p>
</main>
{tail}</body>
</html>
"""


def player_card(name: str) -> tuple[str, str]:
    """(image_url, alt) for a player: their own card, else the shared image."""
    if (CARD_DIR / f"{slug(name)}.png").exists():
        return (f"{SITE_BASE_URL}/assets/og/player/{slug(name)}.png",
                f"{name}'s NBA career path drawn as red arcs across a world map, "
                f"with a marker at every club he has played for")
    return OG_IMAGE, OG_IMAGE_ALT


def _career_row(s: dict) -> str:
    """One career stop, with every entity in it linked to its own page.

    A crawler that can only reach a player page from the sitemap gets nothing
    from it beyond that player; linked out, each page feeds the club, city and
    country pages it names. The cells themselves are unchanged -- same text,
    same order -- so the page reads as it did.
    """
    team = (s.get("team") or "").strip()
    city = (s.get("city") or "").strip()
    country = (s.get("country") or "").strip()
    team_cell = link(team_href(team, s.get("years")), team) if team else ""
    city_cell = link(city_href(city, country), city) if city else ""
    country_cell = link(country_href(country), country) if country else ""
    return ("<tr>"
            f"<td class=\"yr\">{esc(s.get('years'))}</td>"
            f"<td class=\"wrap\">{team_cell}</td>"
            f"<td>{city_cell}</td>"
            f"<td>{country_cell}</td>"
            "</tr>")


def render(player: dict) -> str:
    name = player.get("display_name") or player.get("player") or ""
    key = player.get("player") or name
    hist = player.get("career_history", []) or []

    if hist:
        rows = "\n".join(_career_row(s) for s in hist)
        table = ('<div class="table-wrap"><table class="ct">'
                 '<thead><tr><th>Years</th><th>Team</th>'
                 '<th>City</th><th>Country</th></tr></thead>\n'
                 f"<tbody>\n{rows}\n</tbody></table></div>")
    else:
        table = '<p class="empty">No career history on record yet.</p>'

    facts = []
    for label, val in (("Position", player.get("position")),
                       ("Nationality", player.get("nationality")),
                       ("Current team", player.get("current_team"))):
        if val:
            facts.append(f"<dt>{label}</dt><dd>{esc(val)}</dd>")
    dl = f'<dl class="facts">{"".join(facts)}</dl>' if facts else ""

    img, alt = player_card(name)
    return _shell(
        title=build_title(player), desc=build_description(player),
        canon=player_url(key), og_type="profile", image=img, image_alt=alt,
        h1=f"Where has {name} played?", lede=build_description(player),
        pre_cta=(dl + "\n" if dl else ""),
        extra=table,
        app_href=f"../index.html?player={quote(key)}",
        cta=f"Open {name}'s interactive career map",
        head_extra=person_jsonld(player) + "\n")


# --- team pages -------------------------------------------------------------
def build_team_title(franchise: str) -> str:
    return (f"Every Player Who Has Played for the {franchise} | HoopsHype")


def build_team_description(franchise: str, team: dict) -> str:
    alumni = team.get("alumni_count") or len(team.get("roster", []))
    eras = [e.get("name") for e in (team.get("relocations") or []) if e.get("name")]
    era_txt = ""
    if len(eras) > 1:
        era_txt = f", back through the {eras[0]} years"
    out = (f"All {alumni} players who have suited up for the {franchise}"
           f"{era_txt}: every name, every season, and where each went next.")
    return out if len(out) <= DESC_MAX else out[:DESC_MAX - 1].rstrip(" ,") + "."


def render_team(franchise: str, team: dict) -> str:
    roster = team.get("roster") or []
    rows = "\n".join(
        "<tr>"
        f"<td class=\"wrap\"><a class=\"link\" href=\"../player/{slug(r['player'])}.html\">{esc(r['player'])}</a></td>"
        f"<td class=\"yr\">{esc(r.get('years'))}</td>"
        f"<td>{esc(r.get('stint_team'))}</td>"
        f"<td>{esc(r.get('current_team'))}</td>"
        "</tr>" for r in roster if r.get("player"))
    table = ('<div class="table-wrap"><table class="ct">'
             '<thead><tr><th>Player</th><th>Years</th>'
             '<th>Era name</th><th>Now with</th></tr></thead>\n'
             f"<tbody>\n{rows}\n</tbody></table></div>") if rows else \
        '<p class="empty">No roster on record yet.</p>'

    eras = team.get("relocations") or []
    if len(eras) > 1:
        spans = " → ".join(
            f"{esc(e.get('name'))} ({e.get('start_year') or 'founding'}"
            f"–{e.get('end_year') or 'present'})" for e in eras)
        table = f'<p class="facts">{spans}</p>\n' + table

    return _shell(
        title=build_team_title(franchise),
        desc=build_team_description(franchise, team),
        canon=team_url(franchise), og_type="website",
        h1=f"Every player who has played for the {franchise}",
        lede=build_team_description(franchise, team), extra=table,
        app_href=f"../teams.html?team={quote(franchise)}",
        cta=f"Open the {franchise} team page")


def write_all_teams(teams: dict, out_dir: Path = TEAM_DIR) -> dict:
    return _write_set({f: render_team(f, t) for f, t in teams.items()}, out_dir)


# --- country pages ----------------------------------------------------------
def build_country_title(country: str) -> str:
    # Says clubs, because clubs are what the page lists. Promising "every
    # player" and then showing a club table is the kind of mismatch that gets
    # a click and loses it again.
    return f"NBA Players in {country}: Every Club They Have Played For | HoopsHype"


def build_country_description(country: str, players: int, clubs: int) -> str:
    out = (f"{players} NBA players have played in {country}, across {clubs} "
           f"clubs. Every club, and how many NBA players each has had.")
    return out if len(out) <= DESC_MAX else out[:DESC_MAX - 1].rstrip(" ,") + "."


def render_country(country: str, clubs: list, player_count: int) -> str:
    shown = clubs[:COUNTRY_CLUB_CAP]
    rows = "\n".join(
        "<tr>"
        f"<td>{esc(c['club'])}</td><td>{esc(c.get('city'))}</td>"
        f"<td class=\"yr\">{c['players']}</td>"
        "</tr>" for c in shown)
    table = ('<div class="table-wrap"><table class="ct">'
             '<thead><tr><th>Club</th><th>City</th>'
             '<th>NBA players</th></tr></thead>\n'
             f"<tbody>\n{rows}\n</tbody></table></div>") if rows else \
        '<p class="empty">No clubs on record yet.</p>'
    if len(clubs) > len(shown):
        table += (f'\n<p class="foot">Showing the {len(shown)} clubs with the '
                  f'most NBA alumni, of {len(clubs)}. The interactive map has '
                  f'them all.</p>')

    return _shell(
        title=build_country_title(country),
        desc=build_country_description(country, player_count, len(clubs)),
        canon=country_url(country), og_type="website",
        h1=f"NBA players in {country}",
        lede=build_country_description(country, player_count, len(clubs)),
        extra=table,
        app_href=f"../teams.html?country={quote(country)}",
        cta=f"Open the {country} page on the map")


def country_rollup(players: list) -> dict:
    """{country: (player_count, [{club, city, players} ... desc])}."""
    from collections import defaultdict
    per_country_players = defaultdict(set)
    per_club = defaultdict(set)
    club_city = {}
    for p in players:
        key = p.get("player")
        for st in p.get("career_history", []) or []:
            c = (st.get("country") or "").strip()
            team = (st.get("team") or "").strip()
            if not c or not team:
                continue
            per_country_players[c].add(key)
            per_club[(c, team)].add(key)
            if st.get("city"):
                club_city.setdefault((c, team), st["city"])
    out = {}
    for c, names in per_country_players.items():
        clubs = [{"club": t, "city": club_city.get((c, t), ""),
                  "players": len(v)}
                 for (cc, t), v in per_club.items() if cc == c]
        clubs.sort(key=lambda r: (-r["players"], r["club"]))
        out[c] = (len(names), clubs)
    return out


def write_all_countries(players: list, out_dir: Path = COUNTRY_DIR) -> dict:
    roll = country_rollup(players)
    return _write_set({c: render_country(c, clubs, n)
                       for c, (n, clubs) in roll.items()}, out_dir)


# Published URLs whose subject no longer exists, and the page that replaced
# each. GitHub Pages has no server redirects, so the URL keeps a real file: a
# meta refresh for the visitor and a canonical for the crawler, both pointing
# at the survivor. Listed by hand rather than inferred -- a page that vanishes
# because its data legitimately went away should NOT get a stub -- and never
# added to sitemap.xml, which advertises destinations, not redirects.
#
#   country/england.html: every UK club now carries "United Kingdom", the
#   label geo.COUNTRY_ALIASES has always folded England into, so the country
#   "England" no longer exists in the data and its page stopped generating.
REDIRECTS: dict[str, dict[str, str]] = {
    "country": {"england": "united-kingdom"},
}


def redirect_stub(target_url: str, target_slug: str, depth: str = "..") -> str:
    """A page that exists only to send a visitor (and a crawler) onward."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<!-- Generated by scripts/prerender.py. This URL was published before its
     subject was merged away; it redirects rather than 404s. -->
<meta http-equiv="refresh" content="0; url={esc(target_slug)}">
<link rel="canonical" href="{esc(target_url)}">
<meta name="robots" content="noindex, follow">
<title>Redirecting…</title>
</head>
<body>
<p>This page has moved. <a href="{esc(target_slug)}">Continue</a>.</p>
</body>
</html>
"""


def _redirect_pages(out_dir: Path) -> dict:
    """{filename: html} for the stubs belonging to `out_dir`."""
    kind = out_dir.name
    out = {}
    for old, new in (REDIRECTS.get(kind) or {}).items():
        target_url = f"{SITE_BASE_URL}/{kind}/{new}.html"
        out[f"{old}.html"] = redirect_stub(target_url, f"{new}.html")
    return out


def _write_set(pages: dict, out_dir: Path) -> dict:
    """Write {key: html} as <slug>.html, only when the bytes change.

    Redirect stubs for retired URLs in this directory are written alongside,
    and counted as expected so the stale sweep below cannot delete them on the
    next run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    expected, written, unchanged = set(), 0, 0
    for fname, html in _redirect_pages(out_dir).items():
        path = out_dir / fname
        expected.add(fname)
        body = html.encode("utf-8")
        if path.exists() and path.read_bytes() == body:
            unchanged += 1
        else:
            path.write_bytes(body)
            written += 1
    for key, html in pages.items():
        if not str(key or "").strip():
            continue
        path = out_dir / f"{slug(key)}.html"
        expected.add(path.name)
        body = html.encode("utf-8")
        if path.exists() and path.read_bytes() == body:
            unchanged += 1
            continue
        path.write_bytes(body)
        written += 1
    removed = 0
    for stale in out_dir.glob("*.html"):
        if stale.name not in expected:
            stale.unlink()
            removed += 1
    return {"written": written, "unchanged": unchanged,
            "removed": removed, "total": len(expected)}


def write_all(players: list, out_dir: Path = PLAYER_DIR) -> dict:
    """Write one page per player. Returns {written, unchanged, removed, total}.

    Only rewrites a file whose bytes changed, so an auto-update commit carries
    the players that actually moved rather than the whole set. Pages for names
    no longer in the database are deleted, so a rename cannot strand a URL that
    outlives its data.
    """
    return _write_set({str(p.get("player") or "").strip(): render(p)
                       for p in players}, out_dir)


if __name__ == "__main__":
    # NOT the regeneration entry point. scripts/build_dashboard_data.py is:
    # it writes the seven derived data files AND calls into here and og_cards,
    # so running it is what keeps the whole set consistent.
    #
    # The trap this warning exists for: team pages are rendered from
    # data/team_pages.json, which build_dashboard_data.py writes. Run standalone
    # after the career data changed, this reads the OLD team_pages.json, renders
    # pages that match it, and reports "30 unchanged" -- a green count measured
    # against a stale input. That is how a club-merge round shipped with the
    # dashboard and every team page still carrying pre-merge club names.
    print("note: this is a partial regeneration. Run "
          "scripts/build_dashboard_data.py to rebuild everything, or "
          "--check it afterwards.\n")
    CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
    TEAM_PAGES = ROOT / "data" / "team_pages.json"
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    for label, stats in (
            ("player", write_all(players)),
            ("team", write_all_teams(json.loads(
                TEAM_PAGES.read_text(encoding="utf-8"))["teams"])),
            ("country", write_all_countries(players))):
        print(f"{label} pages: {stats['total']} total, {stats['written']} written, "
              f"{stats['unchanged']} unchanged, {stats['removed']} removed")
