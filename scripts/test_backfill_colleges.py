"""Tests for the Basketball-Reference college backfill.

Run:  python3 scripts/test_backfill_colleges.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backfill_colleges as bc  # noqa: E402
from names import normkey  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _index(rows):
    by_key, by_birth = {}, {}
    for r in rows:
        by_key.setdefault(normkey(r["player"]), []).append(r)
        by_birth.setdefault(r["birth_date"], []).append(r)
    return by_key, by_birth


ROWS = [
    {"player": "Allen Iverson", "player_id": "iversal01", "pos": "G",
     "birth_date": "1975-06-07", "colleges": "Georgetown"},
    {"player": "Kobe Bryant", "player_id": "bryanko01", "pos": "G-F",
     "birth_date": "1978-08-23", "colleges": "NA"},
    {"player": "Charles Smith", "player_id": "smithch01", "pos": "F",
     "birth_date": "1965-07-16", "colleges": "Pitt"},
    {"player": "Charles Smith", "player_id": "smithch04", "pos": "G",
     "birth_date": "1975-08-22", "colleges": "New Mexico"},
    {"player": "Hedo Türkoğlu", "player_id": "turkohe01", "pos": "F",
     "birth_date": "1979-03-19", "colleges": "NA"},
    {"player": "J.R. Smith", "player_id": "smithjr01", "pos": "G",
     "birth_date": "1985-09-09", "colleges": "North Carolina A&T"},
]


def test_format():
    assert bc.format_colleges("Georgetown") == "Georgetown"
    assert bc.format_colleges("NA") == ""
    assert bc.format_colleges("Weber State, Utah") == "* Weber State * Utah"
    assert bc.format_colleges("University of California, Irvine") == \
        "University of California, Irvine"
    print("test_format PASS")


def test_matching():
    k, b = _index(ROWS)
    r, _ = bc.match({"player": "Allen Iverson"}, "1975-06-07", k, b)
    assert r["colleges"] == "Georgetown"
    r, _ = bc.match({"player": "Kobe Bryant"}, "1978-08-23", k, b)
    assert bc.format_colleges(r["colleges"]) == "", "high school stays empty"
    r, _ = bc.match({"player": "Charles Smith (1975)"}, "", k, b)
    assert r["player_id"] == "smithch04"
    r, why = bc.match({"player": "Charles Smith"}, "", k, b)
    assert r is None, "two same-name rows and no birth date: no guess"
    r, _ = bc.match({"player": "Hidayet Turkoglu"}, "1979-03-19", k, b)
    assert r["player_id"] == "turkohe01"
    r, _ = bc.match({"player": "JR Smith"}, "1985-09-09", k, b)
    assert r["player_id"] == "smithjr01"
    print("test_matching PASS")


def test_shipped_data_has_iverson_college():
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    iverson = next(p for p in db if p["player"] == "Allen Iverson")
    assert iverson.get("college") == "Georgetown", iverson.get("college")
    print("test_shipped_data_has_iverson_college PASS")


if __name__ == "__main__":
    test_format()
    test_matching()
    test_shipped_data_has_iverson_college()
    print("\nall college-backfill tests PASS")
