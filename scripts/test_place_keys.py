"""Two cities called Valencia, and a search box that ignores diacritics.

BUG 1: place pages keyed a city by NAME alone, so Valencia, Spain and
       Valencia, Venezuela were one page -- one set of clubs, one pin, one
       count. Eleven city names in this dataset are used in more than one
       country, so the fix is the key, not a special case for Valencia.
BUG 2: search compared raw lowercased strings, so "Besiktas" found nothing
       and "Zalgiris" found nothing. Both sides now fold to an accent-free
       key.

These assert the SHAPE of the front-end code (there is no JS runtime here)
plus the data-level facts the fix rests on. The rendered behaviour was
checked in a browser against a local server; see the PR.

Run:  python3 scripts/test_place_keys.py
"""
from __future__ import annotations

import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = (ROOT / "index.html").read_text(encoding="utf-8")
TEAMS = (ROOT / "teams.html").read_text(encoding="utf-8")
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"

# Every city name this dataset uses in more than one country. The list is the
# point: it is what a name-only key silently merged.
#
# Four names left this list when the clubs behind them were corrected, and
# they are the reason the audit exists: Anyang, Skopje and St. John's were
# each ONE city with a club wrongly labelled "USA", and Newcastle upon Tyne
# was one city under two spellings of the same country. None of them was ever
# two places. See scripts/audit_club_countries.py, which finds that shape, and
# scripts/fix_club_countries.py, which fixed these four.
KNOWN_COLLISIONS = {
    "León", "Lima", "San Carlos", "Santiago", "Tripoli", "Valencia",
    "Worcester",
}


def city_country_pairs() -> dict:
    pairs = collections.defaultdict(set)
    db = json.loads(CAREERS.read_text(encoding="utf-8"))
    for p in db:
        for s in p.get("career_history", []):
            city = (s.get("city") or "").strip()
            country = (s.get("country") or "").strip()
            if city and country:
                pairs[city].add(country)
    return pairs


def test_the_collision_list_is_still_the_whole_list():
    """A new same-named city must show up here rather than quietly merging."""
    pairs = city_country_pairs()
    found = {c for c, countries in pairs.items() if len(countries) > 1}
    assert found == KNOWN_COLLISIONS, (
        f"city collisions changed: +{sorted(found - KNOWN_COLLISIONS)} "
        f"-{sorted(KNOWN_COLLISIONS - found)}")
    assert pairs["Valencia"] == {"Spain", "Venezuela"}, pairs["Valencia"]
    print(f"test_the_collision_list_is_still_the_whole_list PASS "
          f"({len(found)} collisions over {len(pairs)} cities)")


def test_city_links_carry_their_country():
    """Every city href builder on the site passes the country through."""
    assert "const cityHref    = (n, co) => '?city=' + encodeURIComponent(n) +" in TEAMS
    assert "const cityUrl = (c, co) => 'teams.html?city=' + encodeURIComponent(c) +" in INDEX
    # ...and nothing still builds a bare city URL by hand
    for name, src in (("index.html", INDEX), ("teams.html", TEAMS)):
        bare = re.findall(r"\?city=\$\{_?enc(?:odeURIComponent)?\(([^)]*)\)\}(?!\$)", src)
        assert not bare, f"{name} still builds a country-less city URL: {bare}"
    # the club page and the country page's city column pass it too
    assert "cityHref(info.city, info.country)" in TEAMS
    assert "cityHref(r.city, r.country||value)" in TEAMS
    assert "cityUrl(s.city, s.country)" in INDEX
    assert "cityUrl(c.city, c.country)" in INDEX, "map pin click drops the country"
    print("test_city_links_carry_their_country PASS")


def test_the_city_route_is_read_before_the_country_route():
    """?city=Valencia&country=Spain must render the city, not Spain."""
    i_city = TEAMS.index("if (cityParam){ renderPlace('city', cityParam")
    i_country = TEAMS.index("if (countryParam){ renderPlace('country', countryParam)")
    assert i_city < i_country, "country route still shadows a city+country URL"
    assert "renderPlace(kind, value, country)" in TEAMS
    print("test_the_city_route_is_read_before_the_country_route PASS")


def test_static_pages_link_cities_with_their_country():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prerender as pr
    assert pr.city_href("Valencia", "Spain") == "../teams.html?city=Valencia&country=Spain"
    assert pr.city_href("Valencia", "Venezuela") == "../teams.html?city=Valencia&country=Venezuela"
    assert pr.city_href("Valencia", "") == "../teams.html?city=Valencia"
    page = (ROOT / "player" / "justin-dentmon.html").read_text(encoding="utf-8")
    assert "?city=" in page and "&amp;country=" in page, \
        "generated player pages must carry the country on city links"
    print("test_static_pages_link_cities_with_their_country PASS")


# --- accent-insensitive search ---------------------------------------------
# The letters NFD cannot decompose, because the stroke or bar is part of the
# glyph. Every one of them must be in the explicit map or the fold misses it.
NON_DECOMPOSING = "ıİłŁđĐøØæÆœŒßþÞðÐ"


def test_both_search_boxes_fold_the_query_and_the_index():
    for name, src in (("index.html", INDEX), ("teams.html", TEAMS)):
        assert "const fold = s =>" in src, f"{name} has no fold()"
        assert ".normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')" in src, name
        for ch in NON_DECOMPOSING:
            assert f"'{ch}':" in src, f"{name}: {ch} missing from FOLD_MAP"
        # both sides of the comparison, query and indexed name
        assert "SEARCH_ITEMS.filter(it => fold(it.match).includes(q))" in src, name
        assert "const as=fold(a.match).startsWith(q), bs=fold(b.match).startsWith(q);" in src, name
        assert "const q = fold(input.value).trim();" in src, name
    # teams.html's per-table filter boxes fold too
    assert "const q = fold(state.q).trim();" in TEAMS
    assert not re.search(r"filter:\(r,q\)=>[^\n]*\.toLowerCase\(\)\.includes\(q\)", TEAMS), \
        "a table filter still compares raw lowercase"
    print("test_both_search_boxes_fold_the_query_and_the_index PASS")


def test_merged_away_club_names_stay_searchable():
    """A merge must not make a name un-findable: "Élan Béarnais" is now
    Pau-Orthez, and typing the old name has to still get you there."""
    assert "for (const [alias, canon] of Object.entries(CLUB_ALIAS_TO_CANON)){" in TEAMS
    assert "for(const [alias, canon] of Object.entries(CLUB_ALIASES)){" in INDEX
    assert "fetchData('data/teams/team_aliases.json')" in INDEX
    aliases = json.loads((ROOT / "data" / "teams" / "team_aliases.json")
                         .read_text(encoding="utf-8"))["aliases"]
    for old, now in (("Élan Béarnais", "Pau-Orthez"),
                     ("Scavolini Pesaro", "Victoria Libertas Pesaro"),
                     ("Herbalife Gran Canaria", "Gran Canaria"),
                     ("Real Madrid B", "Real Madrid")):
        assert aliases.get(old) == now, f"{old!r} -> {aliases.get(old)!r}, expected {now!r}"
    print("test_merged_away_club_names_stay_searchable PASS")


def test_no_alias_points_at_another_alias():
    """A two-hop alias never resolves: TeamNormalizer follows exactly one."""
    aliases = json.loads((ROOT / "data" / "teams" / "team_aliases.json")
                         .read_text(encoding="utf-8"))["aliases"]
    chains = {k: v for k, v in aliases.items() if v in aliases and aliases[v] != v}
    assert not chains, f"aliases pointing at aliases: {list(chains.items())[:5]}"
    print(f"test_no_alias_points_at_another_alias PASS ({len(aliases)} aliases)")


if __name__ == "__main__":
    test_the_collision_list_is_still_the_whole_list()
    test_city_links_carry_their_country()
    test_the_city_route_is_read_before_the_country_route()
    test_static_pages_link_cities_with_their_country()
    test_both_search_boxes_fold_the_query_and_the_index()
    test_merged_away_club_names_stay_searchable()
    test_no_alias_points_at_another_alias()
    print("\nall place-key / search tests PASS")
