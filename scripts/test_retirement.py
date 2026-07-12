"""Regression tests for explicit-retirement-language detection.

BUG: the only retirement signal was "no current stint for 2+ years", so a
player who explicitly announces retirement (Alex Abrines, 22 July 2025) stays
classified overseas_active/nba_active for up to two years. Fix: detect
retirement-announcement prose (retirement.detect_retirement) as a PRIMARY
signal, independent of recency, layered on top of the existing 2-year
fallback (not replacing it — a genuine contract gap with no announcement must
still be protected).

Run:  python3 scripts/test_retirement.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from retirement import detect_retirement
from team_normalizer import TeamNormalizer
from wiki_parser import parse_player
from player_status import classify_status
import update_careers as uc
import wikipedia_api

ABRINES_WT = (
    "{{Infobox basketball biography|name=Alex Abrines"
    "|years1=2019–2025|team1=[[FC Barcelona]]}}\n\n"
    "On 22 July 2025, Abrines announced his retirement from professional "
    "basketball."
)
SIMMONS_WT = (
    "{{Infobox basketball biography|name=Ben Simmons"
    "|years1=2016–2022|team1=[[Philadelphia 76ers]]"
    "|years2=2022–2025|team2=[[Brooklyn Nets]]"
    "|years3=2025|team3=[[LA Clippers]]}}\n\n"
    "He is a free agent heading into the 2026 offseason."
)


def test_detector_prose_patterns():
    assert detect_retirement(
        "On 22 July 2025, Abrines announced his retirement from "
        "professional basketball."
    ) == {"retirement_announced": True, "retirement_date": "2025-07-22"}
    assert detect_retirement(
        "On July 22, 2025, Abrines announced his retirement from "
        "professional basketball."
    ) == {"retirement_announced": True, "retirement_date": "2025-07-22"}
    # negation must not trigger a false positive
    assert detect_retirement(
        "Some fans speculated he had not officially retired."
    ) == {}
    # a citation title mentioning "retirement" must not trigger either
    assert detect_retirement(
        '<ref>"Retirement of veteran sparks debate", 2021.</ref> '
        "He continues to play professionally."
    ) == {}
    assert detect_retirement("He is still an active player.") == {}
    print("test_detector_prose_patterns PASS")


def test_abrines_flips_to_retired():
    """The exact bug case: a 1-year-stale stint (not yet the 2-year fallback
    threshold) with an explicit retirement announcement must retire NOW."""
    tn = TeamNormalizer()
    rec = parse_player(ABRINES_WT, "Alex Abrines", tn)
    assert rec.get("retirement_announced") is True
    assert rec.get("retirement_date") == "2025-07-22"
    status = classify_status(rec, on_nba_roster=False, current_year=2026,
                             retirement_announced=rec["retirement_announced"])
    assert status == "retired", status
    print("test_abrines_flips_to_retired PASS")


def test_fallback_still_protects_contract_gap():
    """A player 1 year stale with NO announcement must stay active — the
    2-year grace window is not shortened by this fix."""
    tn = TeamNormalizer()
    rec = parse_player(SIMMONS_WT, "Ben Simmons", tn)
    assert not rec.get("retirement_announced")
    status = classify_status(rec, on_nba_roster=False, current_year=2026,
                             retirement_announced=rec.get("retirement_announced", False))
    assert status == "nba_active", status
    print("test_fallback_still_protects_contract_gap PASS")


def _mk_db(sb: Path, players):
    uc.CAREERS = sb / "c.json"; uc.CAREERS.write_text(json.dumps(players))
    uc.LOCATIONS = sb / "l.json"; uc.LOCATIONS.write_text("{}")
    uc.REVIEW = sb / "r.json"; uc.REVIEW.write_text("{}")
    return uc.Database()


def test_merge_player_sticky_and_comeback():
    """End-to-end through merge_player (the real pipeline path):
      1. a fetch with the announcement retires the player immediately;
      2. a LATER fetch whose wording no longer matches must not un-retire
         them (sticky — a transient regex miss should never resurrect
         someone already confirmed retired);
      3. a fetch showing a genuine new stint dated AFTER the retirement year
         (a real comeback) clears the flag and reclassifies normally.
    """
    sb = Path(tempfile.mkdtemp())
    db = _mk_db(sb, [])
    client = wikipedia_api.WikipediaClient(delay=0, max_requests=100)

    wikipedia_api.WikipediaClient.get_wikitext_and_title = \
        lambda self, t: (ABRINES_WT, "Alex Abrines")
    rec, *_ = uc.merge_player(db, "Alex Abrines", client, {}, set(), 2026)
    assert rec["status"] == "retired"
    assert rec["retirement_announced"] is True

    wt_noflag = ("{{Infobox basketball biography|name=Alex Abrines"
                "|years1=2019–2025|team1=[[FC Barcelona]]}}")
    wikipedia_api.WikipediaClient.get_wikitext_and_title = \
        lambda self, t: (wt_noflag, "Alex Abrines")
    rec2, *_ = uc.merge_player(db, "Alex Abrines", client, {}, set(), 2026)
    assert rec2["status"] == "retired", rec2["status"]
    assert rec2.get("retirement_announced") is True

    wt_comeback = ("{{Infobox basketball biography|name=Alex Abrines"
                  "|years1=2019–2025|team1=[[FC Barcelona]]"
                  "|years2=2026–present|team2=[[Valencia Basket]]}}")
    wikipedia_api.WikipediaClient.get_wikitext_and_title = \
        lambda self, t: (wt_comeback, "Alex Abrines")
    rec3, *_ = uc.merge_player(db, "Alex Abrines", client, {}, set(), 2026)
    assert not rec3.get("retirement_announced", False)
    assert rec3["status"] == "overseas_active", rec3["status"]
    assert rec3["current_team"] == "Valencia Basket"
    print("test_merge_player_sticky_and_comeback PASS")


if __name__ == "__main__":
    test_detector_prose_patterns()
    test_abrines_flips_to_retired()
    test_fallback_still_protects_contract_gap()
    test_merge_player_sticky_and_comeback()
    print("\nALL RETIREMENT TESTS PASS")
