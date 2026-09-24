"""Tests for the curated phantom-stint removals.

Run:  python3 scripts/test_stint_corrections.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stint_corrections as sc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _rules(tmp: Path, rows: list) -> Path:
    path = tmp / "corrections.json"
    path.write_text(json.dumps({"removals": rows}), encoding="utf-8")
    return path


def test_a_curated_stint_is_dropped_and_the_rest_survive():
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Andrei Kirilenko",
                                 "team": "Partizan", "start_year": 2001}])
        players = [{"player": "Andrei Kirilenko", "current_team": "CSKA Moscow",
                    "career_history": [
                        {"team": "CSKA Moscow", "years": "1998-2001"},
                        {"team": "Partizan", "years": "2001"},
                        {"team": "Utah Jazz", "years": "2001-2011"},
                    ]}]
        assert sc.apply_removals(players, path) == 1
        teams = [s["team"] for s in players[0]["career_history"]]
        assert teams == ["CSKA Moscow", "Utah Jazz"], teams
        assert players[0]["current_team"] == "CSKA Moscow"
    print("test_a_curated_stint_is_dropped_and_the_rest_survive PASS")


def test_the_start_year_keeps_a_real_spell_at_the_same_club():
    """A removal is one stint, not one club: a player who genuinely played
    there later must keep that stop."""
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Test Player", "team": "Partizan",
                                 "start_year": 2001}])
        players = [{"player": "Test Player", "career_history": [
            {"team": "Partizan", "years": "2001"},
            {"team": "Partizan", "years": "2009-2011"},
        ]}]
        assert sc.apply_removals(players, path) == 1
        assert [s["years"] for s in players[0]["career_history"]] == ["2009-2011"]
    print("test_the_start_year_keeps_a_real_spell_at_the_same_club PASS")


def test_a_reshaped_span_still_matches():
    """Wikipedia closing '2001' into '2001-2002' must not un-match the rule."""
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Test Player", "team": "Partizan",
                                 "start_year": 2001}])
        players = [{"player": "Test Player",
                    "career_history": [{"team": "Partizan", "years": "2001–2002"}]}]
        assert sc.apply_removals(players, path) == 1
        assert players[0]["career_history"] == []
    print("test_a_reshaped_span_still_matches PASS")


def test_no_start_year_removes_every_stint_at_the_club():
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Test Player", "team": "Partizan"}])
        players = [{"player": "Test Player", "career_history": [
            {"team": "Partizan", "years": "2001"},
            {"team": "Partizan", "years": "2009-2011"},
            {"team": "Utah Jazz", "years": "2002"},
        ]}]
        assert sc.apply_removals(players, path) == 2
        assert [s["team"] for s in players[0]["career_history"]] == ["Utah Jazz"]
    print("test_no_start_year_removes_every_stint_at_the_club PASS")


def test_current_team_falls_back_when_its_stint_goes():
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Test Player", "team": "Partizan"}])
        players = [{"player": "Test Player", "current_team": "Partizan",
                    "career_history": [
                        {"team": "Utah Jazz", "years": "2001-2011"},
                        {"team": "Partizan", "years": "2012"},
                    ]}]
        assert sc.apply_removals(players, path) == 1
        assert players[0]["current_team"] == "Utah Jazz"
    print("test_current_team_falls_back_when_its_stint_goes PASS")


def test_the_display_name_matches_too():
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Nikola Jokić", "team": "Partizan"}])
        players = [{"player": "Nikola Jokic", "display_name": "Nikola Jokić",
                    "career_history": [{"team": "Partizan", "years": "2011"}]}]
        assert sc.apply_removals(players, path) == 1
    print("test_the_display_name_matches_too PASS")


def test_an_untouched_player_is_left_exactly_as_it_was():
    with tempfile.TemporaryDirectory() as d:
        path = _rules(Path(d), [{"player": "Andrei Kirilenko",
                                 "team": "Partizan", "start_year": 2001}])
        before = {"player": "A.C. Green", "current_team": "Miami Heat",
                  "career_history": [{"team": "Miami Heat", "years": "2000-2001"}]}
        players = [json.loads(json.dumps(before))]
        assert sc.apply_removals(players, path) == 0
        assert players[0] == before
    print("test_an_untouched_player_is_left_exactly_as_it_was PASS")


def test_the_shipped_rules_load():
    rows = sc.load_removals()
    assert rows, "data/teams/stint_corrections.json has no usable removals"
    for r in rows:
        assert r.get("reason"), f"{r['player']}/{r['team']} carries no reason"
    print(f"test_the_shipped_rules_load PASS ({len(rows)} removal(s))")


def test_the_shipped_database_is_already_clean():
    """The stored database must already reflect every curated removal --
    otherwise the pages served today still carry the phantom stint."""
    players = json.loads((ROOT / "data" / "players" /
                          "nba_players_careers.json").read_text(encoding="utf-8"))
    assert sc.apply_removals(players) == 0, \
        "run python3 scripts/stint_corrections.py --apply"
    ready = json.loads((ROOT / "nba_players_careers_READY.json")
                       .read_text(encoding="utf-8"))
    assert sc.apply_removals(ready) == 0, \
        "nba_players_careers_READY.json still carries a curated phantom stint"
    print("test_the_shipped_database_is_already_clean PASS")


if __name__ == "__main__":
    test_a_curated_stint_is_dropped_and_the_rest_survive()
    test_the_start_year_keeps_a_real_spell_at_the_same_club()
    test_a_reshaped_span_still_matches()
    test_no_start_year_removes_every_stint_at_the_club()
    test_current_team_falls_back_when_its_stint_goes()
    test_the_display_name_matches_too()
    test_an_untouched_player_is_left_exactly_as_it_was()
    test_the_shipped_rules_load()
    test_the_shipped_database_is_already_clean()
