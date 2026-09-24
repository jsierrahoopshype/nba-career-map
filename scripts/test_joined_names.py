"""One infobox field, two club names: the shapes that used to make a club.

THE BUG. A teamN field can name two clubs -- a rename the player's spell ran
through. Two shapes reached storage as a single club name nobody could look
up, because no alias, no normalizer and no reader can resolve them:

  [[Anyang KGC]][[Anyang Jung Kwan Jang Red Boosters]]
      -> "Anyang KGCAnyang Jung Kwan Jang Red Boosters"
  [[Anyang SBS Stars]]·[[Anyang KT&G Kites|KT&G Kites]]
      -> "Anyang SBS Stars·KT&G Kites"

Both are now split, and the first (always complete) name is what the stint
records. Slash-joined names are deliberately left alone: split_combined_teams
resolves those by year majority, and splitting them here would take that
decision away from it.

Run:  python3 scripts/test_joined_names.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_normalizer import TeamNormalizer  # noqa: E402
from wiki_parser import (_clean_text, _parse_career_history,  # noqa: E402
                         parse_player, split_joined_name)

ROOT = Path(__file__).resolve().parent.parent


def test_adjacent_wikilinks_get_a_separator():
    assert _clean_text("[[Anyang KGC]][[Anyang Jung Kwan Jang Red Boosters]]") == \
        "Anyang KGC · Anyang Jung Kwan Jang Red Boosters"
    # whitespace between them is the same case
    assert _clean_text("[[A]] [[B]]") == "A · B"
    # a single link is untouched
    assert _clean_text("[[Los Angeles Lakers]]") == "Los Angeles Lakers"
    # and so is a name that merely contains brackets-adjacent text
    assert _clean_text("[[Anyang KGC|KGC]]") == "KGC"
    print("test_adjacent_wikilinks_get_a_separator PASS")


def test_split_joined_name():
    assert split_joined_name("Anyang SBS Stars·KT&G Kites") == \
        ["Anyang SBS Stars", "KT&G Kites"]
    assert split_joined_name("A · B · C") == ["A", "B", "C"]
    assert split_joined_name("Real Madrid") == ["Real Madrid"]
    assert split_joined_name("") == []
    # every interpunct-family separator, not just the one editors use most
    for sep in "·‧∙•・":
        assert split_joined_name(f"A{sep}B") == ["A", "B"], sep
    print("test_split_joined_name PASS")


def test_slash_names_are_left_for_split_combined_teams():
    """split_combined_teams.py resolves these by year majority; splitting them
    here would take that decision away from it."""
    assert split_joined_name("New Orleans / Utah Jazz") == ["New Orleans / Utah Jazz"]
    assert split_joined_name("Taugrés/TAU Cerámica") == ["Taugrés/TAU Cerámica"]
    print("test_slash_names_are_left_for_split_combined_teams PASS")


def _stints(team_field: str) -> list[dict]:
    hist, _raw = _parse_career_history(
        {"years1": "2015–2018", "team1": team_field}, TeamNormalizer())
    return hist


def test_a_joined_field_stores_the_first_club_not_the_blob():
    hist = _stints("[[Anyang SBS Stars]]·[[Anyang KT&G Kites|KT&G Kites]]")
    assert len(hist) == 1, hist
    # the alias table folds the first name onto the surviving club
    assert hist[0]["team"] == "Anyang KGC", hist[0]
    # ...and the whole field survives in team_raw, so nothing is lost
    assert hist[0]["team_raw"] == "Anyang SBS Stars · KT&G Kites", hist[0]
    assert hist[0]["years"] == "2015–2018"

    hist = _stints("[[Anyang KGC]][[Anyang Jung Kwan Jang Red Boosters]]")
    assert hist[0]["team"] == "Anyang KGC", hist[0]
    print("test_a_joined_field_stores_the_first_club_not_the_blob PASS")


def test_an_ordinary_field_is_unchanged():
    hist = _stints("[[Denver Nuggets]]")
    assert hist[0]["team"] == "Denver Nuggets"
    assert hist[0]["team_raw"] == "Denver Nuggets", "team_raw must not gain a separator"
    hist = _stints("→ [[Baskonia]] (loan)")
    assert hist[0]["team"] == "Baskonia" and hist[0].get("loan") is True
    print("test_an_ordinary_field_is_unchanged PASS")


def test_end_to_end_through_parse_player():
    wt = ("{{Infobox basketball biography|name=Test Player"
          "|years1=2013–2015|team1=[[Anyang KGC]][[Anyang Jung Kwan Jang Red Boosters]]"
          "|years2=2015–present|team2=[[Denver Nuggets]]}}")
    rec = parse_player(wt, "Test Player", TeamNormalizer())
    teams = [s["team"] for s in rec["career_history"]]
    assert teams == ["Anyang KGC", "Denver Nuggets"], teams
    assert rec["current_team"] == "Denver Nuggets"
    print("test_end_to_end_through_parse_player PASS")


def test_no_joined_blob_survives_in_the_shipped_data():
    """Whatever the parser does next run, nothing of this shape may be on file
    now -- the stored ones were merged away."""
    from wiki_parser import JOINED_SEPARATORS
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    bad = sorted({s["team"] for p in db for s in p.get("career_history", [])
                  if any(c in (s.get("team") or "") for c in JOINED_SEPARATORS)})
    assert not bad, f"joined club names still stored: {bad[:5]}"
    # the concatenated shape has no separator to look for, so check the two
    # names that were on file are gone and resolve through the alias table
    aliases = json.loads((ROOT / "data" / "teams" / "team_aliases.json")
                         .read_text(encoding="utf-8"))["aliases"]
    for blob in ("Anyang KGCAnyang Jung Kwan Jang Red Boosters",
                 "Anyang SBS Stars·KT&G Kites"):
        assert aliases.get(blob) == "Anyang KGC", blob
        assert not any((s.get("team") or "") == blob
                       for p in db for s in p.get("career_history", [])), blob
    print("test_no_joined_blob_survives_in_the_shipped_data PASS")


if __name__ == "__main__":
    test_adjacent_wikilinks_get_a_separator()
    test_split_joined_name()
    test_slash_names_are_left_for_split_combined_teams()
    test_a_joined_field_stores_the_first_club_not_the_blob()
    test_an_ordinary_field_is_unchanged()
    test_end_to_end_through_parse_player()
    test_no_joined_blob_survives_in_the_shipped_data()
    print("\nall joined-name tests PASS")
