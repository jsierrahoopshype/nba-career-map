"""Regression tests for status / current-team contradictions (Sep 2026).

Three records contradicted themselves on the live site:
  * Devin Booker   status "retired" with a 2015-present Phoenix Suns stint.
    An undated retirement sentence matched on his page, the flag is sticky,
    and it outranked everything, including a current NBA roster spot.
  * Jalen Duren / James Harden   nba_active with no open stint: their records
    were last refreshed 26 Aug, because the incremental queue put all 706
    overseas players ahead of every on-roster NBA player and the 650-request
    budget ran out first, every day.

Fixes covered here:
  1. rosters.roster_team_index: an under-contract roster spot, matched on an
     accent-folded, suffix-keeping key; FA rows and ambiguous names excluded.
  2. classify_status(roster_team=...) outranks the retirement prose.
  3. merge_player: roster spot clears the sticky flag; a stint that STARTED
     after the retirement year is a comeback even while the prose remains;
     with no open stint, the latest stint on the roster team is reopened
     ("2022–2026" -> "2022–present") and becomes current_team.
  4. build_queue: overseas and stale NBA share one staleness order.

Run:  python3 scripts/test_status_contradictions.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import rosters
import update_careers as uc
import wikipedia_api
from player_status import classify_status


def _mk_db(sb: Path, players):
    uc.CAREERS = sb / "c.json"; uc.CAREERS.write_text(json.dumps(players))
    uc.LOCATIONS = sb / "l.json"; uc.LOCATIONS.write_text("{}")
    uc.REVIEW = sb / "r.json"; uc.REVIEW.write_text("{}")
    return uc.Database()


def _serve(wt: str, title: str) -> None:
    wikipedia_api.WikipediaClient.get_wikitext_and_title = \
        lambda self, t: (wt, title)


def _client():
    return wikipedia_api.WikipediaClient(delay=0, max_requests=100)


BOOKER_WT = (
    "{{Infobox basketball biography|name=Devin Booker"
    "|years1=2015–present|team1=[[Phoenix Suns]]}}\n\n"
    "A teammate later announced his retirement from professional basketball."
)
DUREN_WT = ("{{Infobox basketball biography|name=Jalen Duren"
            "|years1=2022–2026|team1=[[Detroit Pistons]]}}")
RUBIO_WT = (
    "{{Infobox basketball biography|name=Ricky Rubio"
    "|years1=2021–2023|team1=[[Cleveland Cavaliers]]"
    "|years2=2025–present|team2=[[Joventut Badalona]]}}\n\n"
    "On August 5, 2023, Rubio announced his retirement from the NBA."
)


def test_roster_index():
    entries = {
        "Orlando Magic": [{"name": "Nikola Vučević", "note": ""}],
        "Detroit Pistons": [{"name": "Jalen Duren", "note": "FA"}],
        "Utah Jazz": [{"name": "Vince Williams Jr.", "note": ""}],
        "Atlanta Hawks": [{"name": "Twin Name", "note": ""}],
        "Boston Celtics": [{"name": "Twin Name", "note": ""}],
    }
    idx = rosters.roster_team_index(entries)
    k = rosters._roster_key
    assert idx.get(k("Nikola Vucevic")) == "Orlando Magic"         # accents fold
    assert idx.get(k("Vince Williams Jr")) == "Utah Jazz"          # punctuation folds
    assert k("Vince Williams") not in idx                          # suffix kept
    assert k("Jalen Duren") not in idx                             # FA row is no evidence
    assert k("Twin Name") not in idx                               # ambiguous -> no answer
    print("test_roster_index PASS")


def test_classify_roster_outranks_retirement():
    rec = {"career_history": [{"team": "Phoenix Suns", "years": "2015–present"}],
           "current_team": "Phoenix Suns"}
    assert classify_status(rec, False, 2026, retirement_announced=True) == "retired"
    assert classify_status(rec, False, 2026, retirement_announced=True,
                           roster_team="Phoenix Suns") == "nba_active"
    # a non-NBA "roster team" is never evidence
    assert classify_status(rec, False, 2026, retirement_announced=True,
                           roster_team="Real Madrid") == "retired"
    print("test_classify_roster_outranks_retirement PASS")


def test_booker_unretired_by_roster():
    sb = Path(tempfile.mkdtemp())
    db = _mk_db(sb, [{"player": "Devin Booker", "status": "retired",
                      "retirement_announced": True,
                      "career_history": [{"team": "Phoenix Suns",
                                          "years": "2015–present"}],
                      "current_team": "Phoenix Suns",
                      "wikipedia_url": "https://en.wikipedia.org/wiki/Devin_Booker"}])
    _serve(BOOKER_WT, "Devin Booker")
    idx = {rosters._roster_key("Devin Booker"): "Phoenix Suns"}
    rec, *_ = uc.merge_player(db, "Devin Booker", _client(), {}, {"Devin Booker"},
                              2026, idx)
    assert rec["status"] == "nba_active", rec["status"]
    assert rec["current_team"] == "Phoenix Suns"
    assert not rec.get("retirement_announced")

    # Without the roster evidence the old behaviour is unchanged (sticky).
    db2 = _mk_db(Path(tempfile.mkdtemp()), [])
    rec2, *_ = uc.merge_player(db2, "Devin Booker", _client(), {}, set(), 2026)
    assert rec2["status"] == "retired"
    print("test_booker_unretired_by_roster PASS")


def test_closed_stint_on_roster_team_is_current():
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve(DUREN_WT, "Jalen Duren")
    idx = {rosters._roster_key("Jalen Duren"): "Detroit Pistons"}
    rec, *_ = uc.merge_player(db, "Jalen Duren", _client(), {}, set(), 2026, idx)
    assert rec["current_team"] == "Detroit Pistons"
    assert rec["status"] == "nba_active"
    assert rec["career_history"][-1]["years"] == "2022–present"

    # Same-year tie (Harden: Clippers 2023–2026, Cavaliers 2026): the roster
    # team's stint is reopened, not the other one.
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve("{{Infobox basketball biography|name=James Harden"
           "|years1=2023–2026|team1=[[Los Angeles Clippers|LA Clippers]]"
           "|years2=2026|team2=[[Cleveland Cavaliers]]}}", "James Harden")
    idx = {rosters._roster_key("James Harden"): "Cleveland Cavaliers"}
    rec, *_ = uc.merge_player(db, "James Harden", _client(), {}, set(), 2026, idx)
    assert rec["current_team"] == "Cleveland Cavaliers"
    assert [s["years"] for s in rec["career_history"]] == ["2023–2026", "2026–present"]

    # Not on a roster: nothing is reopened (a free agent stays as the page has him).
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve(DUREN_WT, "Jalen Duren")
    rec, *_ = uc.merge_player(db, "Jalen Duren", _client(), {}, set(), 2026, {})
    assert rec["career_history"][-1]["years"] == "2022–2026"

    # A roster team the player has no latest stint with is never invented...
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    idx = {rosters._roster_key("Jalen Duren"): "Boston Celtics"}
    rec, *_ = uc.merge_player(db, "Jalen Duren", _client(), {}, set(), 2026, idx)
    assert rec["current_team"] == "Detroit Pistons"
    assert rec["career_history"][-1]["years"] == "2022–2026"

    # ...and an open stint beats a lagging template (trade not yet on it).
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve("{{Infobox basketball biography|name=Jalen Duren"
           "|years1=2022–2026|team1=[[Detroit Pistons]]"
           "|years2=2026–present|team2=[[Boston Celtics]]}}", "Jalen Duren")
    idx = {rosters._roster_key("Jalen Duren"): "Detroit Pistons"}
    rec, *_ = uc.merge_player(db, "Jalen Duren", _client(), {}, set(), 2026, idx)
    assert rec["current_team"] == "Boston Celtics", rec["current_team"]
    print("test_closed_stint_on_roster_team_is_current PASS")


def test_old_namesake_never_matches_roster():
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve("{{Infobox basketball biography|name=John Old"
           "|years1=1975–1980|team1=[[Phoenix Suns]]}}", "John Old")
    idx = {rosters._roster_key("John Old"): "Phoenix Suns"}
    rec, *_ = uc.merge_player(db, "John Old", _client(), {}, set(), 2026, idx)
    assert rec["status"] == "retired", rec["status"]
    print("test_old_namesake_never_matches_roster PASS")


def test_comeback_while_prose_remains():
    db = _mk_db(Path(tempfile.mkdtemp()), [])
    _serve(RUBIO_WT, "Ricky Rubio")
    rec, *_ = uc.merge_player(db, "Ricky Rubio", _client(), {}, set(), 2026)
    assert rec["status"] == "overseas_active", rec["status"]
    assert not rec.get("retirement_announced")
    print("test_comeback_while_prose_remains PASS")


def test_queue_shares_one_staleness_order():
    db = _mk_db(Path(tempfile.mkdtemp()), [
        {"player": "Overseas New", "status": "overseas_active", "last_updated": "2026-09-27"},
        {"player": "Overseas Old", "status": "overseas_active", "last_updated": "2026-09-01"},
        {"player": "Nba Stale", "status": "nba_active", "last_updated": "2026-08-26"},
        {"player": "Nba Dropped", "status": "nba_active", "last_updated": "2026-09-27"},
    ])
    q = uc.build_queue(db, "incremental", None, {"Nba Stale", "Rookie Guy"})
    assert q == ["Rookie Guy", "Nba Dropped", "Nba Stale", "Overseas Old",
                 "Overseas New"], q
    assert uc.build_queue(db, "single", "A B; C D ;", set()) == ["A B", "C D"]
    assert uc.build_queue(db, "single", "Devin Booker", set()) == ["Devin Booker"]
    print("test_queue_shares_one_staleness_order PASS")


if __name__ == "__main__":
    test_roster_index()
    test_classify_roster_outranks_retirement()
    test_booker_unretired_by_roster()
    test_closed_stint_on_roster_team_is_current()
    test_old_namesake_never_matches_roster()
    test_comeback_while_prose_remains()
    test_queue_shares_one_staleness_order()
    print("\nALL STATUS-CONTRADICTION TESTS PASS")
