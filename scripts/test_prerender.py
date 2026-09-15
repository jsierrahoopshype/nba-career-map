"""Tests for the prerendered player pages.

The point of these pages is what a crawler reads in the SERVED bytes, so the
assertions are about the markup itself, not a rendered DOM.

Run:  python3 scripts/test_prerender.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prerender as pr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = {
    "player": "Nikola Jokić",
    "display_name": "Nikola Jokić",
    "current_team": "Denver Nuggets",
    "position": "Center",
    "nationality": "Serbia",
    "career_history": [
        {"years": "2012–2015", "team": "Mega Basket", "city": "Belgrade",
         "country": "Serbia"},
        {"years": "2015–present", "team": "Denver Nuggets", "city": "Denver",
         "country": "USA"},
    ],
}


def test_slug():
    assert pr.slug("Nikola Jokić") == "nikola-jokic"
    assert pr.slug("A.J. Green") == "a-j-green"
    assert pr.slug("Luka Dončić") == "luka-doncic"
    assert pr.slug("  ") == "player"
    assert pr.slug("Calgary 88's") == "calgary-88-s"
    print("test_slug PASS")


def test_slugs_unique_over_real_data():
    """A collision would silently overwrite one player's page with another's."""
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    seen: dict[str, str] = {}
    clashes = []
    for p in db:
        s = pr.slug(p["player"])
        if s in seen:
            clashes.append((s, seen[s], p["player"]))
        seen[s] = p["player"]
    assert not clashes, f"slug collisions would overwrite pages: {clashes[:5]}"
    print(f"test_slugs_unique_over_real_data PASS ({len(seen)} players)")


def test_head_tags_in_served_markup():
    html = pr.render(SAMPLE)
    canon = f"{pr.SITE_BASE_URL}/player/nikola-jokic.html"
    assert "<title>Where Has Nikola Jokić Played? Every Team, City and Country " \
           "| HoopsHype</title>" in html
    assert f'<link rel="canonical" href="{canon}">' in html
    assert f'<meta property="og:url" content="{canon}">' in html
    assert f'<meta property="og:image" content="{pr.OG_IMAGE}">' in html
    assert f'<meta name="twitter:image" content="{pr.OG_IMAGE}">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert '<meta property="og:type" content="profile">' in html
    print("test_head_tags_in_served_markup PASS")


def test_retired_players_are_not_described_as_active():
    """current_team is the LAST club for a retired player, not a current one."""
    retired = dict(SAMPLE)
    retired["status"] = "retired"
    d = pr.build_description(retired)
    assert "last with Denver Nuggets" in d, d
    assert "now with" not in d, d
    assert d.startswith("Nikola Jokić played for"), d
    active = pr.build_description(SAMPLE)
    assert "now with Denver Nuggets" in active, active
    assert active.startswith("Nikola Jokić has played for"), active
    print("test_retired_players_are_not_described_as_active PASS")


def test_description_names_real_teams_and_countries():
    d = pr.build_description(SAMPLE)
    assert "Mega Basket" in d, d
    assert "Denver Nuggets" in d, d          # named once, as the current club
    assert d.count("Denver Nuggets") == 1, d
    assert "2 countries" in d, d
    assert len(d) <= pr.DESC_MAX, len(d)
    print("test_description_names_real_teams_and_countries PASS")


def test_every_description_fits_and_is_specific():
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    too_long = [p["player"] for p in db if len(pr.build_description(p)) > pr.DESC_MAX]
    assert not too_long, f"{len(too_long)} descriptions over {pr.DESC_MAX}: {too_long[:3]}"
    # The point of these pages is per-player copy, so every description must
    # name at least one club the player actually played for. (A one-club player
    # names it in the tail rather than after a colon, so test for the club name
    # itself, not for punctuation.)
    generic = []
    for p in db:
        clubs = pr._clubs(p.get("career_history", []))
        if clubs and not any(c in pr.build_description(p) for c in clubs):
            generic.append(p["player"])
    assert not generic, f"{len(generic)} descriptions name no real club: {generic[:3]}"
    print(f"test_every_description_fits_and_is_specific PASS ({len(db)} players)")


def test_page_is_usable_by_a_human():
    """Real content, not just a tag carrier: heading, every stint, a way in."""
    html = pr.render(SAMPLE)
    assert "<h1>Where has Nikola Jokić played?</h1>" in html
    for stint in SAMPLE["career_history"]:
        assert stint["team"] in html and stint["city"] in html and stint["years"] in html
    assert 'href="../index.html?player=Nikola%20Joki%C4%87"' in html, \
        "must link into the interactive app with a working ?player= URL"
    assert '<link rel="stylesheet" href="../assets/prerender.css">' in html
    assert (ROOT / "assets" / "prerender.css").exists(), "shared stylesheet missing"
    print("test_page_is_usable_by_a_human PASS")


def test_escaping():
    nasty = {"player": 'Bob "Tiny" O<br>Neal', "display_name": 'Bob "Tiny" O<br>Neal',
             "career_history": [{"years": "1970", "team": 'A & B <script>',
                                 "city": "X", "country": "USA"}]}
    html = pr.render(nasty)
    assert "<script>" not in html.split("</head>")[1], "unescaped markup in body"
    assert "&amp;" in html and "&lt;" in html
    assert 'content="Where Has Bob &quot;Tiny&quot;' in html
    print("test_escaping PASS")


def test_write_all_is_incremental_and_cleans_up():
    tmp = Path(tempfile.mkdtemp()) / "player"
    a = dict(SAMPLE)
    b = {"player": "Gone Soon", "career_history": []}
    s1 = pr.write_all([a, b], out_dir=tmp)
    assert s1["written"] == 2 and s1["total"] == 2, s1
    s2 = pr.write_all([a, b], out_dir=tmp)
    assert s2["written"] == 0 and s2["unchanged"] == 2, "must not rewrite unchanged pages"
    s3 = pr.write_all([a], out_dir=tmp)
    assert s3["removed"] == 1, "a page whose player left the DB must be deleted"
    assert not (tmp / "gone-soon.html").exists()
    # a changed record rewrites exactly one page
    a2 = json.loads(json.dumps(a))
    a2["current_team"] = "Somewhere Else"
    s4 = pr.write_all([a2], out_dir=tmp)
    assert s4["written"] == 1, s4
    print("test_write_all_is_incremental_and_cleans_up PASS")


def test_sitemap_lists_canonical_urls():
    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    assert "/player/nikola-jokic.html" in sm, "player pages must be in the sitemap"
    assert "index.html?player=" not in sm, \
        "the query URLs must not compete with the canonical pages in the sitemap"
    # the generated page's own canonical must be the URL the sitemap advertises
    page = (ROOT / "player" / "nikola-jokic.html").read_text(encoding="utf-8")
    m = re.search(r'<link rel="canonical" href="([^"]+)"', page)
    assert m and m.group(1) in sm, "page canonical and sitemap URL disagree"
    print("test_sitemap_lists_canonical_urls PASS")


def test_app_slug_matches_generator():
    """index.html builds the same slug client-side to set its canonical."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "const playerSlug" in html, "app-side slug helper missing"
    assert "setPlayerCanonical(player)" in html, "router does not set the canonical"
    print("test_app_slug_matches_generator PASS")


if __name__ == "__main__":
    test_slug()
    test_slugs_unique_over_real_data()
    test_head_tags_in_served_markup()
    test_description_names_real_teams_and_countries()
    test_retired_players_are_not_described_as_active()
    test_every_description_fits_and_is_specific()
    test_page_is_usable_by_a_human()
    test_escaping()
    test_write_all_is_incremental_and_cleans_up()
    test_sitemap_lists_canonical_urls()
    test_app_slug_matches_generator()
    print("\nall prerender tests PASS")
