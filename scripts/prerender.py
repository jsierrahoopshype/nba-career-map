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
import unicodedata
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
PLAYER_DIR = ROOT / "player"
TEAM_DIR = ROOT / "team"
COUNTRY_DIR = ROOT / "country"

# A country page lists the clubs played in, not every player: the USA alone has
# 5,068 players against 647 clubs, so the club list is the bounded, readable
# one. The real player total is still stated.
COUNTRY_CLUB_CAP = 250

# Kept in step with scripts/og_tags.py, which owns the same constant for the
# hand-written pages. One edit there, one here, on a domain switch.
SITE_BASE_URL = "https://hoopsmatic.com/career-maps"
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
           depth: str = "..") -> str:
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
<link rel="stylesheet" href="{depth}/assets/prerender.css">
</head>
<body>
<header class="ph"><a class="brand" href="{depth}/index.html">NBA Career Map</a>
<nav><a href="{depth}/teams.html">Teams</a><a href="{depth}/index.html?view=quiz">Quiz</a></nav></header>
<main>
<h1>{esc(h1)}</h1>
<p class="lede">{esc(lede)}</p>
{pre_cta}<p class="cta"><a class="btn" href="{esc(app_href)}">{esc(cta)}</a></p>
{extra}
<p class="foot"><a href="{depth}/index.html">Every NBA player's career, mapped</a> ·
<a href="{depth}/teams.html">Browse by team</a></p>
</main>
</body>
</html>
"""


def player_card(name: str) -> tuple[str, str]:
    """(image_url, alt) for a player: their own card, else the shared image."""
    if (CARD_DIR / f"{slug(name)}.png").exists():
        return (f"{SITE_BASE_URL}/assets/og/player/{slug(name)}.png",
                f"{name}'s NBA career path drawn as red arcs across a world map, "
                f"with a marker at every club he has played for")
    return OG_IMAGE, OG_IMAGE_ALT


def render(player: dict) -> str:
    name = player.get("display_name") or player.get("player") or ""
    key = player.get("player") or name
    hist = player.get("career_history", []) or []

    if hist:
        rows = "\n".join(
            "<tr>"
            f"<td class=\"yr\">{esc(s.get('years'))}</td>"
            f"<td>{esc(s.get('team'))}</td>"
            f"<td>{esc(s.get('city'))}</td>"
            f"<td>{esc(s.get('country'))}</td>"
            "</tr>" for s in hist)
        table = ('<table class="ct"><thead><tr><th>Years</th><th>Team</th>'
                 '<th>City</th><th>Country</th></tr></thead>\n'
                 f"<tbody>\n{rows}\n</tbody></table>")
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
        pre_cta=dl + "\n" if dl else "", extra=table,
        app_href=f"../index.html?player={quote(key)}",
        cta=f"Open {name}'s interactive career map")


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
        f"<td><a href=\"../player/{slug(r['player'])}.html\">{esc(r['player'])}</a></td>"
        f"<td class=\"yr\">{esc(r.get('years'))}</td>"
        f"<td>{esc(r.get('stint_team'))}</td>"
        f"<td>{esc(r.get('current_team'))}</td>"
        "</tr>" for r in roster if r.get("player"))
    table = ('<table class="ct"><thead><tr><th>Player</th><th>Years</th>'
             '<th>Era name</th><th>Now with</th></tr></thead>\n'
             f"<tbody>\n{rows}\n</tbody></table>") if rows else \
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
    table = ('<table class="ct"><thead><tr><th>Club</th><th>City</th>'
             '<th>NBA players</th></tr></thead>\n'
             f"<tbody>\n{rows}\n</tbody></table>") if rows else \
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


def _write_set(pages: dict, out_dir: Path) -> dict:
    """Write {key: html} as <slug>.html, only when the bytes change."""
    out_dir.mkdir(parents=True, exist_ok=True)
    expected, written, unchanged = set(), 0, 0
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
