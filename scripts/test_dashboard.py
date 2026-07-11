"""Tests for the dashboard data layer (build_dashboard_data + transaction log).

Offline / self-contained: builds widgets from hand-made fixtures covering the
edge cases called out in the phase-1 spec (zero-country rookies, single-alumnus
teams, boomerang vs contiguous/loan patterns), and exercises the append-only
transaction ledger. Run: python3 scripts/test_dashboard.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_dashboard_data as b  # noqa: E402
import update_careers as u  # noqa: E402


def _p(player, status, current, hist):
    return {"player": player, "status": status, "current_team": current,
            "career_history": hist}


def _stint(team, years="2020", city="", state="", country=""):
    return {"team": team, "years": years, "city": city, "state": state,
            "country": country}


FIX = [
    # zero-country single-stint rookie (must not crash, counts 0 countries)
    _p("Rookie Zero", "nba_active", "Miami Heat",
       [_stint("Miami Heat", "2025–present", "Miami", "Florida", "USA")]),
    # overseas player with no resolved location at all
    _p("No Loc", "overseas_active", "Mystery FC", [_stint("Mystery FC", "2025")]),
    # two overseas players sharing a non-NBA team + an NBA franchise (via
    # different era names -> must canonicalize and match)
    _p("Sonic Guy", "overseas_active", "Real Madrid",
       [_stint("Seattle SuperSonics", "2005", "Seattle", "Washington", "USA"),
        _stint("Real Madrid", "2024", "Madrid", "", "Spain")]),
    _p("Thunder Guy", "overseas_active", "Real Madrid",
       [_stint("Oklahoma City Thunder", "2015", "Oklahoma City", "Oklahoma", "USA"),
        _stint("Real Madrid", "2024", "Madrid", "", "Spain")]),
    # single-alumnus non-NBA team -> in alltime/current index, NOT in reunions
    _p("Solo Overseas", "overseas_active", "Lonely BC",
       [_stint("Lonely BC", "2024", "Nowhere", "", "Narnia")]),
    # boomerang: A,B,A (non-contiguous) -> flagged
    _p("Boomer", "retired", "A",
       [_stint("A", "2010"), _stint("B", "2011"), _stint("A", "2012")]),
    # contiguous A,A (loan-return already collapsed) -> NOT flagged
    _p("Contig", "retired", "B",
       [_stint("A", "2010"), _stint("A", "2011"), _stint("B", "2012")]),
    # all distinct -> NOT flagged
    _p("Distinct", "retired", "C",
       [_stint("A", "2010"), _stint("B", "2011"), _stint("C", "2012")]),
]


def test_most_well_traveled_zero_country():
    rows = {r["player"]: r for r in b.w_most_well_traveled(FIX)}
    assert rows["No Loc"]["country_count"] == 0
    assert rows["Rookie Zero"]["country_count"] == 1
    print("test_most_well_traveled_zero_country PASS")


def test_boomerang():
    flagged = {r["player"]: r for r in b.w_boomerang_players(FIX)}
    assert "Boomer" in flagged, "A,B,A must be a boomerang"
    assert "Contig" not in flagged, "adjacent A,A must NOT be a boomerang"
    assert "Distinct" not in flagged
    yrs = flagged["Boomer"]["teams"][0]["years"]
    assert yrs == ["2010", "2012"], yrs
    print("test_boomerang PASS")


def test_reunions_and_single_alumnus():
    reunions = {r["team"]: r for r in b.w_team_reunions(FIX)}
    # Real Madrid has 2 current overseas players -> a reunion
    assert "Real Madrid" in reunions
    # they share an NBA franchise via SuperSonics<->Thunder canonicalization
    pairs = reunions["Real Madrid"]["shared_franchise_pairs"]
    assert pairs and pairs[0]["shared_nba_franchises"] == ["Oklahoma City Thunder"]
    # single-alumnus team is excluded from reunions...
    assert "Lonely BC" not in reunions
    # ...but present in the current-alumni index
    cur = {r["team"]: r["count"] for r in b.w_teams_by_current_nba_alumni(FIX)}
    assert cur.get("Lonely BC") == 1
    assert cur.get("Real Madrid") == 2
    print("test_reunions_and_single_alumnus PASS")


def test_alltime_index_excludes_nba():
    idx = {r["team"]: r["players"] for r in b.w_teams_by_alltime_nba_alumni(FIX)}
    assert "Miami Heat" not in idx          # current NBA team
    assert "Seattle SuperSonics" not in idx  # historical NBA era name
    assert idx.get("Real Madrid") == 2
    print("test_alltime_index_excludes_nba PASS")


def test_countries():
    live = {r["country"]: r["count"] for r in b.w_countries_live_snapshot(FIX)}
    assert live.get("Spain") == 2   # both Real Madrid players currently in Spain
    allt = {r["country"]: r["players"] for r in b.w_countries_alltime_alumni(FIX)}
    assert allt.get("USA") >= 3
    print("test_countries PASS")


def test_heatmap_coverage():
    ht = b.w_world_tour_heatmap(FIX, {"Madrid||Spain"})
    cov = ht["coverage"]
    # 4 overseas players: 2 Madrid (in coords), 1 no-city (No Loc), 1 city not
    # in coords (Solo Overseas: Nowhere||Narnia)
    assert cov["total"] == 4
    assert cov["in_coords"] == 2
    assert cov["missing_no_city"] == 1
    assert cov["missing_city_absent_from_coords"] == 1
    print("test_heatmap_coverage PASS")


def test_transactions_append_only():
    tmp = Path(tempfile.mkdtemp()) / "transactions.json"
    u.TRANSACTIONS = tmp
    u._append_transactions({"date": "2026-01-01", "team_moves": [
        {"player": "X", "from": "Real Madrid", "to": "Barcelona"}]})
    u._append_transactions({"date": "2026-01-02", "team_moves": []})  # no-op
    u._append_transactions({"date": "2026-01-03", "team_moves": [
        {"player": "Y", "from": "Utah Jazz", "to": "Partizan"}]})
    led = json.loads(tmp.read_text())
    assert len(led["transactions"]) == 2, "must append, not overwrite"
    assert led["transactions"][0]["from_team"] == "Real Madrid"
    assert led["transactions"][1]["date"] == "2026-01-03"

    b.TRANSACTIONS = tmp
    latest = b.w_latest_signings()
    assert latest[0]["player"] == "Y", "newest first"
    print("test_transactions_append_only PASS")


def test_latest_signings_missing_file():
    b.TRANSACTIONS = Path(tempfile.mkdtemp()) / "does_not_exist.json"
    assert b.w_latest_signings() == []
    print("test_latest_signings_missing_file PASS")


# --- Phase 2: team pages -----------------------------------------------------
TEAM_FIX = [
    # Minneapolis-era + LA-era players must land on the SAME Lakers roster
    _p("Old Laker", "retired", "Minneapolis Lakers",
       [_stint("Minneapolis Lakers", "1949-1954", "Minneapolis", "Minnesota", "USA")]),
    _p("New Laker", "nba_active", "Los Angeles Lakers",
       [_stint("Los Angeles Lakers", "2020–present", "Los Angeles", "California", "USA")]),
    # Seattle-era player must land on the Thunder roster (era canonicalization)
    _p("Sonic", "retired", "Seattle SuperSonics",
       [_stint("Seattle SuperSonics", "1995-2000", "Seattle", "Washington", "USA")]),
    # an overseas alum of the Lakers -> active_elsewhere on the Lakers page
    _p("Wandering Laker", "overseas_active", "Real Madrid",
       [_stint("Los Angeles Lakers", "2015", "Los Angeles", "California", "USA"),
        _stint("Real Madrid", "2024", "Madrid", "", "Spain")]),
    # a non-NBA-only career -> appears on NO team page
    _p("Euro Only", "overseas_active", "FC Barcelona",
       [_stint("FC Barcelona", "2024", "Barcelona", "", "Spain")]),
]


def test_team_pages_franchise_membership():
    teams = b.w_team_pages(TEAM_FIX)
    assert set(teams) == set(b.NBA_TEAMS), "exactly the 30 current franchises"
    lal = {r["player"] for r in teams["Los Angeles Lakers"]["roster"]}
    assert "Old Laker" in lal and "New Laker" in lal, "Minneapolis + LA on one roster"
    okc = {r["player"] for r in teams["Oklahoma City Thunder"]["roster"]}
    assert "Sonic" in okc, "Seattle-era player on Thunder roster"
    # a non-NBA-only player is on no roster
    everywhere = {r["player"] for t in teams.values() for r in t["roster"]}
    assert "Euro Only" not in everywhere
    # roster rows carry current/last team + its country (for the Active/Retired
    # team-page tables and their flag column)
    row = next(r for r in teams["Los Angeles Lakers"]["roster"] if r["player"] == "New Laker")
    assert row["current_team"] == "Los Angeles Lakers" and row["current_country"] == "USA"
    wl = next(r for r in teams["Los Angeles Lakers"]["roster"] if r["player"] == "Wandering Laker")
    assert wl["current_team"] == "Real Madrid" and wl["current_country"] == "Spain"
    print("test_team_pages_franchise_membership PASS")


def test_team_pages_active_elsewhere():
    teams = b.w_team_pages(TEAM_FIX)
    ae = {a["player"]: a for a in teams["Los Angeles Lakers"]["active_elsewhere"]}
    # overseas alum currently at Real Madrid -> listed with country
    assert ae["Wandering Laker"]["current_team"] == "Real Madrid"
    assert ae["Wandering Laker"]["country"] == "Spain"
    # a player currently ON the franchise is NOT "active elsewhere"
    assert "New Laker" not in ae, "current NBA player on this team isn't 'elsewhere'"
    # retired alum never appears in active_elsewhere
    assert "Old Laker" not in ae
    print("test_team_pages_active_elsewhere PASS")


def test_franchise_country():
    # nba_active players get their franchise's real country (part-4 fix)
    assert b.franchise_country("Toronto Raptors") == "Canada"
    assert b.franchise_country("Memphis Grizzlies") == "USA"   # not Vancouver-era Canada
    assert b.franchise_country("Los Angeles Lakers") == "USA"
    assert b.franchise_country("Seattle SuperSonics") == "USA"  # by any era name
    print("test_franchise_country PASS")


def test_nba_active_current_country():
    fix = [_p("Raptor", "nba_active", "Toronto Raptors",
              [_stint("Toronto Raptors", "2024–present", "Toronto", "Ontario", "Canada")])]
    teams = b.w_team_pages(fix)
    row = teams["Toronto Raptors"]["roster"][0]
    assert row["current_country"] == "Canada", row
    print("test_nba_active_current_country PASS")


def test_club_pages():
    fix = [
        _p("Euro Guy", "overseas_active", "Baskonia",
           [_stint("Los Angeles Lakers", "2015", "LA", "CA", "USA"),
            _stint("Baskonia", "2024", "Vitoria", "", "Spain")]),
        _p("Retired Euro", "retired", "Baskonia",
           [_stint("Baskonia", "2001-2003", "Vitoria", "", "Spain")]),
    ]
    clubs = b.w_club_pages(fix)
    assert "Los Angeles Lakers" not in clubs, "NBA teams are not clubs"
    bas = clubs["Baskonia"]
    assert bas["country"] == "Spain" and bas["count"] == 2
    players = {r["player"]: r for r in bas["roster"]}
    assert players["Euro Guy"]["years"] == "2024"
    assert players["Retired Euro"]["status"] == "retired"
    print("test_club_pages PASS")


def test_club_pages_group_by_player():
    # a player with two stints at the same club -> ONE row, joined years
    fix = [_p("Two Spell", "overseas_active", "Baskonia",
              [_stint("Baskonia", "2019", "Vitoria", "", "Spain"),
               _stint("Real Madrid", "2020", "Madrid", "", "Spain"),
               _stint("Baskonia", "2002-2004", "Vitoria", "", "Spain")])]
    clubs = b.w_club_pages(fix)
    bas = clubs["Baskonia"]
    assert bas["count"] == 1, "one row per player"
    assert bas["roster"][0]["years"] == "2002-2004, 2019", bas["roster"][0]["years"]


def test_club_location_prefers_located_stint():
    # a location-less stint must NOT blank a club placed by another stint
    fix = [
        _p("A", "overseas_active", "ClubX", [_stint("ClubX", "2020", "", "", "")]),
        _p("B", "retired", "ClubX", [_stint("ClubX", "2018", "Split", "", "Croatia")]),
    ]
    clubs = b.w_club_pages(fix)
    assert clubs["ClubX"]["country"] == "Croatia", clubs["ClubX"]
    print("test_club_pages_group_by_player + location PASS")


def test_compute_related():
    # two players shared between Lakers and a club -> they're related to each other
    fix = [
        _p("P1", "overseas_active", "ClubZ",
           [_stint("Los Angeles Lakers", "2015"), _stint("ClubZ", "2020", "X", "", "Spain")]),
        _p("P2", "overseas_active", "ClubZ",
           [_stint("Los Angeles Lakers", "2016"), _stint("ClubZ", "2021", "X", "", "Spain")]),
    ]
    rel = b.compute_related(fix)
    lakers = rel[("team", "Los Angeles Lakers")]
    assert any(r["type"] == "club" and r["name"] == "ClubZ" and r["shared"] == 2 for r in lakers), lakers
    clubz = rel[("club", "ClubZ")]
    assert any(r["type"] == "team" and r["name"] == "Los Angeles Lakers" and r["shared"] == 2 for r in clubz), clubz
    assert len(lakers) <= 8 and len(clubz) <= 8
    print("test_compute_related PASS")


def test_sitemap():
    fix = [_p("Test Player", "retired", "Boston Celtics",
              [_stint("Boston Celtics", "2000", "Boston", "MA", "USA")])]
    xml = b.build_sitemap(fix)
    assert xml.startswith("<?xml")
    assert "/teams.html?team=Atlanta%20Hawks" in xml
    assert "/index.html?player=Test%20Player" in xml
    assert xml.count("<url>") == 2 + 30 + 1  # index + teams landing + 30 teams + 1 player
    print("test_sitemap PASS")


def test_relocation_timeline():
    teams = b.w_team_pages(TEAM_FIX)
    # never-relocated franchise -> empty timeline (section skipped in UI)
    assert teams["Boston Celtics"]["relocations"] == []
    # relocated franchise -> founding era has null start, present era end null
    okc = teams["Oklahoma City Thunder"]["relocations"]
    assert [e["name"] for e in okc] == ["Seattle SuperSonics", "Oklahoma City Thunder"]
    assert okc[0]["start_year"] is None and okc[0]["end_year"] == 2008
    assert okc[1]["start_year"] == 2008 and okc[1]["end_year"] is None and okc[1]["current"]
    print("test_relocation_timeline PASS")


if __name__ == "__main__":
    test_most_well_traveled_zero_country()
    test_boomerang()
    test_reunions_and_single_alumnus()
    test_alltime_index_excludes_nba()
    test_countries()
    test_heatmap_coverage()
    test_transactions_append_only()
    test_latest_signings_missing_file()
    test_team_pages_franchise_membership()
    test_team_pages_active_elsewhere()
    test_franchise_country()
    test_nba_active_current_country()
    test_club_pages()
    test_club_pages_group_by_player()
    test_club_location_prefers_located_stint()
    test_compute_related()
    test_sitemap()
    test_relocation_timeline()
    print("\nALL DASHBOARD TESTS PASS")
