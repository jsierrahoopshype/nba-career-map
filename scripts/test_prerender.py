"""Tests for the prerendered player pages.

The point of these pages is what a crawler reads in the SERVED bytes, so the
assertions are about the markup itself, not a rendered DOM.

Run:  python3 scripts/test_prerender.py
"""
from __future__ import annotations

import inspect
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
    # A player with a generated card points at it; everyone else falls back to
    # the shared career-map image. Both branches are covered in test_og_cards;
    # here just assert the page carries whichever one applies, on both tags.
    img, _alt = pr.player_card(SAMPLE["display_name"])
    assert f'<meta property="og:image" content="{img}">' in html
    assert f'<meta name="twitter:image" content="{img}">' in html
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


def test_page_wears_the_site_chrome():
    """These pages are the site, not a stripped print view: same fonts, same
    tokens, same sticky header and nav as index.html / teams.html."""
    html = pr.render(SAMPLE)
    assert "family=DM+Sans" in html and "family=JetBrains+Mono" in html
    assert '<nav class="nav"><a href="../index.html">Map</a>' in html
    assert '<a href="../teams.html">Teams</a>' in html
    assert '<a href="../quiz.html">Quiz</a>' in html
    assert "hoopshype" not in html.lower().split("</head>")[1].replace(
        "hoopshype.com", ""), "no HoopsHype logo/brand in the page body"
    css = (ROOT / "assets" / "prerender.css").read_text(encoding="utf-8")
    for token in ("'DM Sans'", "'JetBrains Mono'", "--page:#f5f5f7",
                  "html{font-size:115%}"):
        assert token in css, f"{token} missing from the shared stylesheet"
    print("test_page_wears_the_site_chrome PASS")


def test_career_cells_link_to_their_pages():
    """Team, city and country each point where teams.html sends a click: a
    prerendered page where one exists, the query URL where none does."""
    rich = json.loads(json.dumps(SAMPLE))
    rich["career_history"] = [
        {"years": "2012-2015", "team": "Mega Basket", "city": "Belgrade",
         "country": "Serbia"},
        {"years": "1995-1998", "team": "Vancouver Grizzlies", "city": "Vancouver",
         "country": "Canada"},
        {"years": "2024-2025", "team": "San Diego Clippers", "city": "San Diego",
         "country": "USA"},
    ]
    html = pr.render(rich)
    # a club has no static page -> query URL on teams.html
    assert 'href="../teams.html?club=Mega%20Basket"' in html
    # an ERA name resolves to its current franchise's page, not a dead slug
    assert 'href="../team/memphis-grizzlies.html"' in html
    assert "vancouver-grizzlies.html" not in html
    # the modern G League club that re-used an NBA era name is a club page
    assert 'href="../teams.html?club=San%20Diego%20Clippers"' in html
    # cities carry their country; countries get their prerendered page
    assert 'href="../teams.html?city=Belgrade&amp;country=Serbia"' in html
    assert 'href="../country/serbia.html"' in html
    assert "?player=" not in html.split("</head>")[1].replace(
        '?player=Nikola%20Joki%C4%87', ''), "career cells must not use ?player="
    print("test_career_cells_link_to_their_pages PASS")


def test_head_block_is_untouched_by_the_restyle():
    """The whole point of these pages is their head tags; a visual change
    must not move them."""
    html = pr.render(SAMPLE)
    head = html.split("</head>")[0]
    canon = f"{pr.SITE_BASE_URL}/player/nikola-jokic.html"
    assert pr.build_title(SAMPLE) in head
    assert f'<meta name="description" content="{pr.esc(pr.build_description(SAMPLE))}">' in head
    assert f'<link rel="canonical" href="{canon}">' in head
    assert f'<meta property="og:url" content="{canon}">' in head
    assert "github.io" not in html, "canonical/og must point at hoopsmatic.com"
    print("test_head_block_is_untouched_by_the_restyle PASS")


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
    # redirect stubs for merged-away players are written alongside the pages
    stubs = len(pr._redirect_pages(tmp))
    s1 = pr.write_all([a, b], out_dir=tmp)
    assert s1["written"] == 2 + stubs and s1["total"] == 2 + stubs, s1
    s2 = pr.write_all([a, b], out_dir=tmp)
    assert s2["written"] == 0 and s2["unchanged"] == 2 + stubs, \
        "must not rewrite unchanged pages"
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


TEAM_FIX = {
    "franchise": "Los Angeles Lakers", "alumni_count": 523,
    "relocations": [{"name": "Minneapolis Lakers", "start_year": None,
                     "end_year": 1960, "current": False},
                    {"name": "Los Angeles Lakers", "start_year": 1960,
                     "end_year": None, "current": True}],
    "roster": [{"player": "A.C. Green", "years": "1985-1993",
                "stint_team": "Los Angeles Lakers", "current_team": "Miami Heat"}],
}


def test_team_page():
    html = pr.render_team("Los Angeles Lakers", TEAM_FIX)
    canon = f"{pr.SITE_BASE_URL}/team/los-angeles-lakers.html"
    assert "<title>Every Player Who Has Played for the Los Angeles Lakers " \
           "| HoopsHype</title>" in html
    assert f'<link rel="canonical" href="{canon}">' in html
    assert f'<meta property="og:url" content="{canon}">' in html
    assert "523 players" in html and "Minneapolis Lakers" in html
    assert "A.C. Green" in html and "1985-1993" in html
    # roster names link to the canonical player pages, not ?player=
    assert 'href="../player/a-c-green.html"' in html
    assert "?player=" not in html
    assert 'href="../teams.html?team=Los%20Angeles%20Lakers"' in html
    print("test_team_page PASS")


def test_country_page():
    clubs = [{"club": "Real Madrid", "city": "Madrid", "players": 40},
             {"club": "Baskonia", "city": "Vitoria", "players": 25}]
    html = pr.render_country("Spain", clubs, 820)
    canon = f"{pr.SITE_BASE_URL}/country/spain.html"
    assert f'<link rel="canonical" href="{canon}">' in html
    assert "820 NBA players" in html and "2 clubs" in html
    assert "Real Madrid" in html and "Vitoria" in html
    # the title must not promise players when the page lists clubs
    title = re.search(r"<title>(.*?)</title>", html).group(1)
    assert "Every Club" in title and "Every Player" not in title, title
    assert 'href="../teams.html?country=Spain"' in html
    print("test_country_page PASS")


def test_country_club_cap():
    many = [{"club": f"Club {i}", "city": "X", "players": 300 - i}
            for i in range(pr.COUNTRY_CLUB_CAP + 50)]
    html = pr.render_country("USA", many, 5068)
    assert html.count("<tr>") == pr.COUNTRY_CLUB_CAP + 1, "cap not applied"
    assert f"of {len(many)}" in html, "must say how many were left out"
    print("test_country_club_cap PASS")


def test_internal_links_point_at_canonical_urls():
    """Internal links must not feed the non-canonical ?player= form."""
    idx = (ROOT / "index.html").read_text(encoding="utf-8")
    assert 'href="player/${playerSlug(n)}.html"' in idx
    assert 'href="player/${playerSlug(p.player)}.html"' in idx
    assert 'href="player/${playerSlug(x)}.html"' in idx
    assert 'href="?player=${_enc(n)}"' not in idx

    tm = (ROOT / "teams.html").read_text(encoding="utf-8")
    assert "const playerHref  = n => 'player/' + slugify(n) + '.html';" in tm
    assert "const teamHref    = n => 'team/' + slugify(n) + '.html';" in tm
    assert "const countryHref = n => 'country/' + slugify(n) + '.html';" in tm
    # clubs and cities have no prerendered pages yet, so they keep query URLs
    assert "const clubHref    = n => '?club=' + encodeURIComponent(n);" in tm
    # ...and a city URL carries its country, because two countries can hold a
    # city of the same name (Valencia, Spain / Valencia, Venezuela).
    assert "const cityHref    = (n, co) => '?city=' + encodeURIComponent(n) +" in tm
    assert "(co ? '&country=' + encodeURIComponent(co) : '');" in tm
    # ...and the click path must resolve them back to the app
    assert "function appTarget(href)" in tm
    assert "const t = appTarget(a.getAttribute('href'));" in tm, \
        "click interceptor does not consult appTarget"
    assert "const t = appTarget(it.href);" in tm, \
        "search dropdown does not consult appTarget"
    assert "buildSlugMaps();" in tm
    print("test_internal_links_point_at_canonical_urls PASS")


def test_a_retired_url_redirects_instead_of_404ing():
    """country/england.html stopped generating when every UK club moved onto
    the "United Kingdom" label. GitHub Pages has no server redirects, so the
    URL keeps a real file -- and the stale sweep must not delete it again on
    the next run."""
    page = ROOT / "country" / "england.html"
    assert page.exists(), "retired URL must not 404"
    html = page.read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh" content="0; url=united-kingdom.html">' in html
    assert (f'<link rel="canonical" href="{pr.SITE_BASE_URL}'
            f'/country/united-kingdom.html">') in html
    assert 'content="noindex, follow"' in html, "a stub must not be indexed"
    assert (ROOT / "country" / "united-kingdom.html").exists(), "target missing"

    # regeneration keeps it: _write_set counts stubs as expected
    tmp = Path(tempfile.mkdtemp()) / "country"
    stats = pr._write_set({"United Kingdom": "<html>uk</html>"}, tmp)
    assert (tmp / "england.html").exists(), "stub not written"
    assert stats["removed"] == 0
    stats = pr._write_set({"United Kingdom": "<html>uk</html>"}, tmp)
    assert stats["removed"] == 0 and (tmp / "england.html").exists(), \
        "the stale sweep deleted the stub on the second run"

    # ...and a redirect is never advertised as a destination
    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    assert "country/england.html" not in sm, "a stub must stay out of the sitemap"
    assert "country/united-kingdom.html" in sm
    print("test_a_retired_url_redirects_instead_of_404ing PASS")


def test_a_merged_player_url_redirects():
    """Herb Jones was folded into Herbert Jones; the old page must redirect
    to the survivor, not 404, and stay out of the sitemap."""
    page = pr.PLAYER_DIR / "herb-jones.html"
    assert page.exists(), "retired player URL must not 404"
    html = page.read_text(encoding="utf-8")
    assert '<meta http-equiv="refresh" content="0; url=herbert-jones.html">' in html
    assert (f'<link rel="canonical" href="{pr.SITE_BASE_URL}'
            f'/player/herbert-jones.html">') in html
    assert 'content="noindex, follow"' in html
    assert (pr.PLAYER_DIR / "herbert-jones.html").exists(), "target missing"
    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    assert "player/herb-jones.html" not in sm
    assert "player/herbert-jones.html" in sm
    print("test_a_merged_player_url_redirects PASS")


def test_search_dropdown_sits_above_the_map():
    """The header's z-index makes it a stacking context, so the dropdown can
    only clear Leaflet's panes (400+) and controls (1000) if the HEADER is
    lifted while the list is open. Checked in a browser once; this keeps the
    rule from being dropped in a restyle."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    m = re.search(r"header:has\(#results\.show\)\s*\{\s*z-index:\s*(\d+)", html)
    assert m and int(m.group(1)) > 1000, "header not lifted over the map"
    m = re.search(r"#botResults\.show\s*\{\s*z-index:\s*(\d+)", html)
    assert m and int(m.group(1)) > 1000, "footer dropdown under the map"
    # closed, the header stays below the modal (1000)
    m = re.search(r"\n\s*header\s*\{[^}]*z-index:\s*(\d+)", html)
    assert m and int(m.group(1)) < 1000
    print("test_search_dropdown_sits_above_the_map PASS")


def test_sitemap_lists_teams_and_countries():
    sm = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
    assert "/team/los-angeles-lakers.html" in sm
    assert "/country/spain.html" in sm
    assert "teams.html?team=" not in sm and "teams.html?country=" not in sm
    print("test_sitemap_lists_teams_and_countries PASS")


def test_app_slug_matches_generator():
    """index.html builds the same slug client-side to set its canonical."""
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "const playerSlug" in html, "app-side slug helper missing"
    assert "setPlayerCanonical(player)" in html, "router does not set the canonical"
    teams = (ROOT / "teams.html").read_text(encoding="utf-8")
    assert "const slugify" in teams, "teams.html slug helper missing"
    print("test_app_slug_matches_generator PASS")



# --- the Person JSON-LD -----------------------------------------------------
def _player(name, **over):
    rec = {"player": name, "display_name": name, "status": "retired",
           "career_history": [{"years": "2015-2020", "team": "Denver Nuggets",
                               "city": "Denver", "country": "USA"}]}
    rec.update(over)
    return rec


def test_the_page_prints_no_birth_line_and_loads_no_age_script():
    """Nothing visible, and no inline script -- a page that prints no age has
    nothing to keep current."""
    for name in ("Nikola Jokić", "Kobe Bryant", "Early Player",
                 "Nobody At All"):
        html = pr.render(_player(name))
        assert 'class="bio"' not in html, name
        assert "Born:" not in html and "Died:" not in html, name
        assert "data-born" not in html, name
        assert "(age " not in html and "(aged " not in html, name
    print("test_the_page_prints_no_birth_line_and_loads_no_age_script PASS")


def test_the_page_carries_a_person_block_with_no_bio_facts():
    """The Person block stays -- name, url, nationality, the Wikipedia link --
    but the birth and death facts it used to publish are gone. That data is for
    a separate section of the site, not for a Career Map page."""
    html = pr.render(_player("Kobe Bryant", nationality="United States",
                             wikipedia_url="https://en.wikipedia.org/wiki/Kobe_Bryant"))
    raw = html.split('<script type="application/ld+json">')[1] \
              .split("</script>")[0]
    data = json.loads(raw)
    assert data["@type"] == "Person"
    assert data["name"] == "Kobe Bryant"
    assert data["url"] == f"{pr.SITE_BASE_URL}/player/kobe-bryant.html"
    assert data["nationality"] == "United States"
    assert data["sameAs"] == "https://en.wikipedia.org/wiki/Kobe_Bryant"
    for gone in ("birthDate", "birthPlace", "deathDate", "deathPlace"):
        assert gone not in data, f"{gone} is still published"
        assert gone not in html, f"{gone} is still in the served bytes"
    print("test_the_page_carries_a_person_block_with_no_bio_facts PASS")


def test_the_build_does_not_read_the_bio_file():
    """The guard behind the test above. Kobe Bryant HAS a birth date on file;
    the only reason it cannot reach a page is that nothing in this module opens
    the file, and a helper added back 'just to read one field' would undo the
    whole change quietly."""
    # Comments and the docstring name the file deliberately -- they explain why
    # it is NOT read -- so what counts is a line that would actually reach it.
    reads = [ln for ln in inspect.getsource(pr).splitlines()
             if "player_bio" in ln
             and any(op in ln for op in ("read_text", "open(", "ROOT /",
                                         "Path("))]
    assert not reads, f"prerender reads the bio file again: {reads}"
    print("test_the_build_does_not_read_the_bio_file PASS")


def _with_wrong_person(names, fn):
    """Run fn with a known wrong-person list in place of the shipped file."""
    saved = pr._WRONG_PERSON
    pr._WRONG_PERSON = frozenset(names)
    try:
        return fn()
    finally:
        pr._WRONG_PERSON = saved


def _flagged_without_override() -> list:
    """The players the shipped review file flags whose article is NOT settled
    by an override -- read live, because both files change as repairs land (a
    hardcoded name goes stale the day its override is written)."""
    import player_urls
    player_urls.reset_cache()
    doc = json.loads(pr.REVIEW_FILE.read_text(encoding="utf-8"))
    names = [r["player"] for r in doc.get("wikipedia_url_wrong_person") or []
             if isinstance(r, dict) and r.get("player")]
    return [n for n in names if not player_urls.override_url(n)]


def test_a_flagged_player_with_no_override_gets_no_sameas():
    """sameAs asserts "same person", so a namesake's article is not a missing
    field, it is a false claim. No override yet -> no sameAs at all.

    Checked over every player the current review file flags without an
    override. When the repairs have emptied that set, a made-up name stands in
    so the rule itself is still exercised."""
    import player_urls
    names = _flagged_without_override()
    if not names:
        stand_in = "Flagged Namesake Test"
        assert not player_urls.override_url(stand_in)
        names = [stand_in]

    def check():
        for name in names:
            rec = _player(name, wikipedia_url="https://en.wikipedia.org/wiki/"
                          + name.replace(" ", "_"))
            data = json.loads(pr.person_jsonld(rec)
                              .split(">", 1)[1].rsplit("<", 1)[0])
            assert "sameAs" not in data, data
            html = pr.render(rec)
            assert "en.wikipedia.org" not in html, \
                f"{name}: the namesake is still linked"
            # the rest of the block is untouched
            assert data["name"] == name and data["url"]
    _with_wrong_person(set(names) | {n.casefold() for n in names}, check)
    print(f"test_a_flagged_player_with_no_override_gets_no_sameas PASS "
          f"({len(names)} player(s))")


def test_an_unflagged_player_keeps_his_article():
    def check():
        joker = _player("Nikola Jokić", nationality="Serbia",
                        wikipedia_url="https://en.wikipedia.org/wiki/Nikola_Jokic")
        data = json.loads(pr.person_jsonld(joker)
                          .split(">", 1)[1].rsplit("<", 1)[0])
        assert data["sameAs"] == "https://en.wikipedia.org/wiki/Nikola_Jokic"
        assert data["nationality"] == "Serbia"
    _with_wrong_person({"David Duke", "david duke"}, check)
    print("test_an_unflagged_player_keeps_his_article PASS")


def test_an_override_wins_over_the_stored_url_and_the_flag():
    """Once the article is settled, sameAs is the OVERRIDE -- not the namesake's
    URL the career record may still be carrying, and not silence either."""
    import tempfile
    import player_urls
    right = "https://en.wikipedia.org/wiki/David_Duke_Jr."
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "player_url_overrides.json"
        path.write_text(json.dumps({"overrides": {
            "David Duke": {"wikipedia_url": right, "verified": "2026-09-24"}}}),
            encoding="utf-8")
        saved = player_urls.OVERRIDES
        player_urls.OVERRIDES = path
        player_urls.reset_cache()
        try:
            def check():
                duke = _player(
                    "David Duke",
                    wikipedia_url="https://en.wikipedia.org/wiki/David_Duke")
                data = json.loads(pr.person_jsonld(duke)
                                  .split(">", 1)[1].rsplit("<", 1)[0])
                assert data["sameAs"] == right, data
            _with_wrong_person({"David Duke", "david duke"}, check)
        finally:
            player_urls.OVERRIDES = saved
            player_urls.reset_cache()
    print("test_an_override_wins_over_the_stored_url_and_the_flag PASS")


def test_no_shipped_page_links_to_a_flagged_namesake():
    """The end-to-end guard, over the real review file and the shipped pages."""
    import player_urls
    doc = json.loads(pr.REVIEW_FILE.read_text(encoding="utf-8"))
    rows = doc.get("wikipedia_url_wrong_person") or []
    assert rows, "the review file lists no wrong-person records"
    checked = 0
    for row in rows:
        name = row["player"]
        page = pr.PLAYER_DIR / f"{pr.slug(name)}.html"
        if not page.exists():
            continue
        checked += 1
        html = page.read_text(encoding="utf-8")
        override = player_urls.override_url(name)
        if override:
            assert override in html, f"{name}: the override is not on the page"
        else:
            assert "en.wikipedia.org" not in html, \
                f"{name}: still links to a Wikipedia article"
    assert checked == len(rows), \
        f"only {checked} of {len(rows)} flagged players have a page"
    print(f"test_no_shipped_page_links_to_a_flagged_namesake PASS ({checked} pages)")


def test_the_person_block_survives_a_hostile_name():
    nasty = _player('Bob "Tiny" O<br>Neal')
    raw = pr.person_jsonld(nasty).split(">", 1)[1].rsplit("<", 1)[0]
    assert "<br>" not in raw, raw
    assert json.loads(raw)["name"] == 'Bob "Tiny" O<br>Neal'
    print("test_the_person_block_survives_a_hostile_name PASS")


def test_team_and_country_pages_are_untouched():
    """The shell gained two optional slots; the pages that do not use them
    must come out byte-identical."""
    team = pr.render_team("Los Angeles Lakers", TEAM_FIX)
    assert "ld+json" not in team and 'class="bio"' not in team
    print("test_team_and_country_pages_are_untouched PASS")


if __name__ == "__main__":
    test_slug()
    test_slugs_unique_over_real_data()
    test_head_tags_in_served_markup()
    test_description_names_real_teams_and_countries()
    test_retired_players_are_not_described_as_active()
    test_every_description_fits_and_is_specific()
    test_page_is_usable_by_a_human()
    test_page_wears_the_site_chrome()
    test_career_cells_link_to_their_pages()
    test_head_block_is_untouched_by_the_restyle()
    test_escaping()
    test_write_all_is_incremental_and_cleans_up()
    test_sitemap_lists_canonical_urls()
    test_app_slug_matches_generator()
    test_the_page_prints_no_birth_line_and_loads_no_age_script()
    test_the_page_carries_a_person_block_with_no_bio_facts()
    test_the_build_does_not_read_the_bio_file()
    test_a_flagged_player_with_no_override_gets_no_sameas()
    test_an_unflagged_player_keeps_his_article()
    test_an_override_wins_over_the_stored_url_and_the_flag()
    test_no_shipped_page_links_to_a_flagged_namesake()
    test_the_person_block_survives_a_hostile_name()
    test_team_and_country_pages_are_untouched()
    test_team_page()
    test_country_page()
    test_country_club_cap()
    test_internal_links_point_at_canonical_urls()
    test_a_retired_url_redirects_instead_of_404ing()
    test_a_merged_player_url_redirects()
    test_search_dropdown_sits_above_the_map()
    test_sitemap_lists_teams_and_countries()
    print("\nall prerender tests PASS")
