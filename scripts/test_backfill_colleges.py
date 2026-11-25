"""Tests for the college backfill (the player's own Wikipedia infobox).

No network: the article texts are canned.

Run:  python3 scripts/test_backfill_colleges.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill_colleges as bc  # noqa: E402
from wikipedia_api import RequestBudgetExceeded  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WIKI = "https://en.wikipedia.org/wiki/"

IVERSON = """{{Infobox basketball biography
| name = Allen Iverson
| birth_date = {{birth date and age|1975|6|7}}
| college = [[Georgetown Hoyas men's basketball|Georgetown]] (1994–1996)
}}
'''Allen Ezail Iverson''' is an American former basketball player."""
BRYANT = """{{Infobox basketball biography
| birth_date = {{birth date|1978|8|23}}
| high_school = [[Lower Merion High School|Lower Merion]]
}}"""
NAMESAKE = """{{Infobox basketball biography
| birth_date = {{birth date|1931|2|2}}
| college = [[Somewhere State]]
}}"""
NOT_A_PLAYER = """{{Infobox musical artist
| name = Plain Singer
}}"""

PLAYERS = [
    {"player": "Allen Iverson", "wikipedia_url": WIKI + "Allen_Iverson"},
    {"player": "Kobe Bryant", "wikipedia_url": WIKI + "Kobe_Bryant"},
    {"player": "Old Namesake", "wikipedia_url": WIKI + "Old_Namesake"},
    {"player": "Plain Singer", "wikipedia_url": WIKI + "Plain_Singer"},
    {"player": "Flagged Guy", "wikipedia_url": WIKI + "Flagged_Guy"},
    {"player": "Has One", "college": "Duke", "wikipedia_url": WIKI + "Has_One"},
]
BIO = {"Allen Iverson": {"birth_date": "1975-06-07", "source": "nba-player-data"},
       "Kobe Bryant": {"birth_date": "1978-08-23", "source": "nba-player-data"},
       "Old Namesake": {"birth_date": "1990-01-01", "source": "nba.com"}}
TEXTS = {"Allen Iverson": IVERSON, "Kobe Bryant": BRYANT,
         "Old Namesake": NAMESAKE, "Plain Singer": NOT_A_PLAYER}


def _fetch(titles):
    return {t: TEXTS[t] for t in titles if t in TEXTS}


def test_college_comes_from_the_infobox():
    res = bc.backfill(PLAYERS, BIO, {"Flagged Guy"}, _fetch)
    got = {f["player"]: f["college"] for f in res["filled"]}
    assert got == {"Allen Iverson": "Georgetown (1994–1996)"}, got
    assert [r["player"] for r in res["no_college_in_infobox"]] == ["Kobe Bryant"]
    why = {r["player"]: r["why"] for r in res["skipped"]}
    assert why["Old Namesake"].startswith("infobox born 1931"), why
    assert why["Plain Singer"] == "no basketball infobox", why
    assert why["Flagged Guy"] == "no article known to be his", why
    assert "Has One" not in why and "Has One" not in got
    print("test_college_comes_from_the_infobox PASS")


def test_a_spent_budget_leaves_the_rest_for_next_time():
    calls = []

    def fetch(titles):
        calls.append(titles)
        raise RequestBudgetExceeded("spent")
    res = bc.backfill(PLAYERS, BIO, set(), fetch)
    assert res["filled"] == [] and len(calls) == 1
    assert "Allen Iverson" in res["not_read_yet"]

    def refused(titles):
        raise OSError("HTTP Error 429: Too Many Requests")
    res = bc.backfill(PLAYERS, BIO, set(), refused)
    assert res["filled"] == [] and "Allen Iverson" in res["not_read_yet"]
    print("test_a_spent_budget_leaves_the_rest_for_next_time PASS")


def test_shipped_data_has_iverson_college():
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    iverson = next(p for p in db if p["player"] == "Allen Iverson")
    assert "Georgetown" in (iverson.get("college") or ""), iverson.get("college")
    print("test_shipped_data_has_iverson_college PASS")


if __name__ == "__main__":
    test_college_comes_from_the_infobox()
    test_a_spent_budget_leaves_the_rest_for_next_time()
    test_shipped_data_has_iverson_college()
    print("\nall college-backfill tests PASS")
