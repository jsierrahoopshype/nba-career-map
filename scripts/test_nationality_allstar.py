"""Regression tests for nationality + All-Star parsing (purely additive fields).

nationality: read directly from the infobox |nationality= parameter (a
demonym: "American", "Spanish") — NEVER derived from birth_place, since a
player can be born in one country and hold/represent another (Joel Embiid:
born Cameroon, nationality French). Absent when the field is absent.

all_star: parsed from the highlights/career_highlights/awards field,
specifically the "Nx NBA All-Star (year, year, ...)" pattern. Distinct from
All-NBA Team / All-Defensive Team / Rookie of the Year — none of those
contain the substring "All-Star", so they never conflate. Stores a year list
when it parses cleanly, True when mentioned but not cleanly parseable, or is
absent entirely when there's no mention.

The live re-fetch to backfill these on the 5,141 existing players runs
separately via GitHub Actions; this only verifies the extraction logic
against synthetic wikitext fixtures.

Run:  python3 scripts/test_nationality_allstar.py
"""
from __future__ import annotations

from team_normalizer import TeamNormalizer
from wiki_parser import parse_player


def _mk(name, **fields):
    parts = "|".join(f"{k}={v}" for k, v in fields.items())
    return f"{{{{Infobox basketball biography|name={name}|{parts}}}}}"


def test_nationality_differs_from_birth_place():
    """The exact illustrative case: nationality is read from the field and
    never inferred from birth_place, even when they point at different
    countries."""
    tn = TeamNormalizer()
    wt = _mk("Joel Embiid",
             **{"birth_place": "[[Yaoundé]], Cameroon", "nationality": "French",
                "years1": "2014-present", "team1": "[[Philadelphia 76ers]]"})
    rec = parse_player(wt, "Joel Embiid", tn)
    assert rec.get("nationality") == "French", rec.get("nationality")
    assert "Cameroon" in rec.get("birth_place", ""), rec.get("birth_place")
    print("test_nationality_differs_from_birth_place PASS")


def test_nationality_absent_when_field_missing():
    """No |nationality= field -> absent, NOT derived from birth_place."""
    tn = TeamNormalizer()
    wt = _mk("No Nat", **{"birth_place": "[[Athens]], Greece",
                          "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "No Nat", tn)
    assert "nationality" not in rec
    assert rec.get("birth_place") == "Athens, Greece"
    print("test_nationality_absent_when_field_missing PASS")


def test_all_star_clean_year_list():
    tn = TeamNormalizer()
    wt = _mk("Test Player",
             **{"highlights": "5x NBA All-Star (2015, 2017, 2019, 2021, 2023)",
                "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "Test Player", tn)
    assert rec.get("all_star") == [2015, 2017, 2019, 2021, 2023], rec.get("all_star")
    print("test_all_star_clean_year_list PASS")


def test_all_star_single_selection_no_count_prefix():
    tn = TeamNormalizer()
    wt = _mk("Once Star", **{"highlights": "NBA All-Star (2019)",
                             "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "Once Star", tn)
    assert rec.get("all_star") == [2019], rec.get("all_star")
    print("test_all_star_single_selection_no_count_prefix PASS")


def test_all_star_boolean_fallback():
    """Mentioned but no clean parenthetical year list -> True, not a list."""
    tn = TeamNormalizer()
    wt = _mk("Fuzzy Star", **{"highlights": "NBA All-Star selection",
                              "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "Fuzzy Star", tn)
    assert rec.get("all_star") is True, rec.get("all_star")
    print("test_all_star_boolean_fallback PASS")


def test_all_star_absent_when_no_mention():
    tn = TeamNormalizer()
    wt = _mk("No Star", **{"highlights": "NBA champion (2020)",
                           "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "No Star", tn)
    assert rec.get("all_star") is None
    assert "all_star" not in rec
    print("test_all_star_absent_when_no_mention PASS")


def test_all_star_not_conflated_with_other_honors():
    """All-NBA / All-Defensive / Rookie of the Year mentions, but NO All-Star
    mention, must NOT set all_star."""
    tn = TeamNormalizer()
    wt = _mk("Other Honors",
             **{"highlights": "All-NBA Third Team (2015)All-Defensive Second "
                              "Team (2016)NBA Rookie of the Year (2012)",
                "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "Other Honors", tn)
    assert rec.get("all_star") is None
    assert "all_star" not in rec
    print("test_all_star_not_conflated_with_other_honors PASS")


def test_career_highlights_field_alias():
    """Some infobox revisions use career_highlights instead of highlights."""
    tn = TeamNormalizer()
    wt = _mk("Alias Field",
             **{"career_highlights": "3x NBA All-Star (2018, 2020, 2022)",
                "years1": "2010-present", "team1": "[[Boston Celtics]]"})
    rec = parse_player(wt, "Alias Field", tn)
    assert rec.get("all_star") == [2018, 2020, 2022], rec.get("all_star")
    print("test_career_highlights_field_alias PASS")


def test_purely_additive_no_existing_field_touched():
    """Adding nationality/all_star must not alter any existing field's value
    for a record that has neither."""
    tn = TeamNormalizer()
    wt = ("{{Infobox basketball biography|name=Plain Player"
         "|birth_place=[[Chicago]], Illinois|position=Point guard"
         "|years1=2010-present|team1=[[Boston Celtics]]}}")
    rec = parse_player(wt, "Plain Player", tn)
    assert rec["position"] == "Point guard"
    assert rec["birth_place"] == "Chicago, Illinois"
    assert rec["current_team"] == "Boston Celtics"
    assert "nationality" not in rec
    assert "all_star" not in rec
    print("test_purely_additive_no_existing_field_touched PASS")


if __name__ == "__main__":
    test_nationality_differs_from_birth_place()
    test_nationality_absent_when_field_missing()
    test_all_star_clean_year_list()
    test_all_star_single_selection_no_count_prefix()
    test_all_star_boolean_fallback()
    test_all_star_absent_when_no_mention()
    test_all_star_not_conflated_with_other_honors()
    test_career_highlights_field_alias()
    test_purely_additive_no_existing_field_touched()
    print("\nALL NATIONALITY/ALL-STAR TESTS PASS")
