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

# Kept in step with scripts/og_tags.py, which owns the same constant for the
# hand-written pages. One edit there, one here, on a domain switch.
SITE_BASE_URL = "https://jsierrahoopshype.github.io/nba-career-map"
OG_IMAGE = f"{SITE_BASE_URL}/assets/og-career-map.png"
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


def render(player: dict) -> str:
    name = player.get("display_name") or player.get("player") or ""
    key = player.get("player") or name
    hist = player.get("career_history", []) or []
    title, desc = build_title(player), build_description(player)
    canon = player_url(key)
    app = f"../index.html?player={quote(key)}"

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
<meta property="og:type" content="profile">
<meta property="og:site_name" content="NBA Career Map">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(canon)}">
<meta property="og:image" content="{esc(OG_IMAGE)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="{esc(OG_IMAGE_ALT)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(OG_IMAGE)}">
<meta name="twitter:image:alt" content="{esc(OG_IMAGE_ALT)}">
<link rel="stylesheet" href="../assets/prerender.css">
</head>
<body>
<header class="ph"><a class="brand" href="../index.html">NBA Career Map</a>
<nav><a href="../teams.html">Teams</a><a href="../index.html?view=quiz">Quiz</a></nav></header>
<main>
<h1>Where has {esc(name)} played?</h1>
<p class="lede">{esc(desc)}</p>
{dl}
<p class="cta"><a class="btn" href="{esc(app)}">Open {esc(name)}'s interactive career map</a></p>
{table}
<p class="foot"><a href="../index.html">Every NBA player's career, mapped</a> ·
<a href="../teams.html">Browse by team</a></p>
</main>
</body>
</html>
"""


def write_all(players: list, out_dir: Path = PLAYER_DIR) -> dict:
    """Write one page per player. Returns {written, unchanged, removed, total}.

    Only rewrites a file whose bytes changed, so an auto-update commit carries
    the players that actually moved rather than the whole set. Pages for names
    no longer in the database are deleted, so a rename cannot strand a URL that
    outlives its data.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    expected, written, unchanged = set(), 0, 0
    for p in players:
        key = str(p.get("player") or "").strip()
        if not key:
            continue
        path = out_dir / f"{slug(key)}.html"
        expected.add(path.name)
        body = render(p).encode("utf-8")
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


if __name__ == "__main__":
    CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
    stats = write_all(json.loads(CAREERS.read_text(encoding="utf-8")))
    print(f"player pages: {stats['total']} total, {stats['written']} written, "
          f"{stats['unchanged']} unchanged, {stats['removed']} removed")
