"""Regression tests for the live-QA bugs on the drain (commit 3bb83ef).

BUG 1: current_team must be the stint with the latest end-year (present =
       latest), not array order — so an NBA stint outlasting a concurrent
       G League/affiliate stint wins, and the player classifies nba_active.
BUG 2: merge_player must always return a 5-tuple, so names that don't resolve
       (Wikipedia "(YYYY)" disambiguators whose exact title has no article, or
       "Jr"/"Sr" without periods) skip cleanly instead of crashing the caller's
       5-value unpack. "(YYYY)" and "Jr/Sr" are valid, permanent name shapes.

Run:  python3 scripts/test_qa_bugs.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from wiki_parser import _select_current_team, parse_player
from team_normalizer import TeamNormalizer
from player_status import classify_status
import update_careers as uc
import wikipedia_api


def test_current_team_latest_end_year():
    tn = TeamNormalizer()
    # exact AJ Green case: NBA stint more recent than the G League affiliate
    hist = [{"team": "Milwaukee Bucks", "years": "2022-2026"},
            {"team": "Wisconsin Herd", "years": "2022-2024"}]
    assert _select_current_team(hist) == "Milwaukee Bucks", _select_current_team(hist)
    # tie on "present" prefers the NBA franchise over the affiliate
    assert _select_current_team([{"team": "Wisconsin Herd", "years": "2022-present"},
                                 {"team": "Milwaukee Bucks", "years": "2022-present"}]) == "Milwaukee Bucks"
    # a genuinely later non-NBA stint still wins (present is latest)
    assert _select_current_team([{"team": "Milwaukee Bucks", "years": "2024-2025"},
                                 {"team": "Wisconsin Herd", "years": "2024-present"}]) == "Wisconsin Herd"

    # end-to-end: parse + classify AJ Green -> nba_active
    wt = ("{{Infobox basketball biography|name=AJ Green"
          "|years1=2022–2026|team1=[[Milwaukee Bucks]]"
          "|years2=2022–2024|team2=[[Wisconsin Herd]]}}")
    rec = parse_player(wt, "AJ Green", tn)
    assert rec["current_team"] == "Milwaukee Bucks", rec["current_team"]
    assert classify_status(rec, on_nba_roster=False, current_year=2026) == "nba_active"
    print("test_current_team_latest_end_year PASS")


# The 8 names that crashed live, plus a synthetic disambiguator not in that list
# and a "Jr"/"Sr"-without-period name, to catch the general pattern.
DISAMBIGUATED = [
    "AK Okereke", "BJ Johnson", "Juan Hernangomez", "Mike James (1990)",
    "Tony Mitchell (1992)", "Tony Mitchell (1989)", "Vince Williams Jr",
    "Walter Lemon Jr", "Chris Johnson (1995)", "Marcus Williams (1985)",
]


def _mk_db(sb: Path, players):
    uc.CAREERS = sb / "c.json"; uc.CAREERS.write_text(json.dumps(players))
    uc.LOCATIONS = sb / "l.json"; uc.LOCATIONS.write_text("{}")
    uc.REVIEW = sb / "r.json"; uc.REVIEW.write_text("{}")
    return uc.Database()


def test_disambiguated_names_never_crash():
    """merge_player returns a 5-tuple (never a 3-tuple) for unresolvable AND
    resolvable disambiguated names, so the caller's `a,b,c,d,e = merge_player()`
    unpack never raises."""
    sb = Path(tempfile.mkdtemp())
    db = _mk_db(sb, [])
    client = wikipedia_api.WikipediaClient(delay=0, max_requests=100)

    # case A: page not found for the exact "(YYYY)" title -> clean skip
    wikipedia_api.WikipediaClient.get_wikitext_and_title = lambda self, t: (None, None)
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: ""
    for name in DISAMBIGUATED:
        result = uc.merge_player(db, name, client, {}, set(), 2026)
        assert len(result) == 5, (name, result)          # arity is the bug
        rec, nt, is_new, ps, pc = result                 # must unpack cleanly
        assert rec is None

    # case B: a "(1995)" page that DOES resolve -> processed into a real record
    wt = ("{{Infobox basketball biography|name=Chris Johnson"
          "|years1={{nbay|2024|start}}–present|team1=[[Boston Celtics]]}}")
    wikipedia_api.WikipediaClient.get_wikitext_and_title = lambda self, t: (wt, "Chris Johnson (1995)")
    result = uc.merge_player(db, "Chris Johnson (1995)", client, {}, set(), 2026)
    assert len(result) == 5, result
    rec, nt, is_new, ps, pc = result
    assert rec is not None and rec["player"] == "Chris Johnson (1995)", rec
    assert is_new is True and rec["status"] == "nba_active"
    # the "(1995)" disambiguator is preserved, never stripped
    assert "(1995)" in rec["player"]
    print("test_disambiguated_names_never_crash PASS")


if __name__ == "__main__":
    test_current_team_latest_end_year()
    test_disambiguated_names_never_crash()
    print("\nALL QA-BUG TESTS PASS")
