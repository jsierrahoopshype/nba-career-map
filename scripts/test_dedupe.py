"""Dedupe tests: name normalization, geo resolution, and an end-to-end run
reproducing the Şengün case (ASCII seed + diacritic roster) plus nickname
(Bub/Carlton) and a genuine newcomer — verifying no duplication, no
dropped-from-roster churn, and map-key (READY.json) stability.

Run:  python3 scripts/test_dedupe.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from names import normkey, url_key, canonical_url
from geo import resolve_location


def test_normkey():
    # accents / suffixes / initial-spacing fold (caught pre-fetch)
    for a, b in [("Alperen Şengün", "Alperen Sengun"),
                 ("Derrick Jones Jr.", "Derrick Jones"),
                 ("A. J. Green", "AJ Green"), ("A. J. Lawson", "AJ Lawson"),
                 ("Dereck Lively II", "Dereck Lively"),
                 ("Craig Porter Jr.", "Craig Porter"),
                 ("Kristaps Porziņģis", "Kristaps Porzingis")]:
        assert normkey(a) == normkey(b), (a, b)
    # transliteration (Schröder/Schroeder) and nicknames do NOT fold — handled
    # by the canonical-URL / alias index after the first merge
    assert normkey("Dennis Schröder") != normkey("Dennis Schroeder")
    assert normkey("Bub Carrington") != normkey("Carlton Carrington")
    # distinct people never collide; "oe" in ordinary names is untouched
    assert normkey("Amari Williams") != normkey("Marvin Williams")
    assert normkey("Bob Carrington") != normkey("Bub Carrington")
    assert normkey("Joel Embiid") == "joel embiid"
    assert url_key("https://en.wikipedia.org/wiki/Derrick_Jones_(basketball)") == normkey("Derrick Jones")
    assert canonical_url("Carlton Carrington") == "https://en.wikipedia.org/wiki/Carlton_Carrington"
    print("test_normkey PASS")


def test_geo():
    assert resolve_location("Georgia", "College Park") == ("Georgia", "USA")
    assert resolve_location("Georgia", "Tbilisi") == ("", "Georgia")
    assert resolve_location("Ohio", "Cleveland") == ("Ohio", "USA")
    assert resolve_location("Lazio", "Rome") == ("Lazio", "Italy")
    assert resolve_location("Subcarpathian Voivodeship", "Tarnobrzeg") == ("Subcarpathian Voivodeship", "Poland")
    assert resolve_location("Nowhereland", "X") == ("Nowhereland", "")
    print("test_geo PASS")


def test_end_to_end():
    import update_careers as uc
    import wikipedia_api

    sb = Path(tempfile.mkdtemp())
    for a in ["CAREERS", "ACTIVE", "RETIRED", "LOCATIONS", "REVIEW",
              "UPDATE_LOG", "CHANGELOG", "ROOT_MAP_FILE"]:
        setattr(uc, a, sb / (a.lower() + ".json"))
    uc.LOGS = sb

    db = [
        {"player": "Alperen Sengun", "status": "nba_active", "current_team": "Houston Rockets",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Alperen_Sengun",
         "career_history": [{"team": "Houston Rockets", "years": "2021-present", "city": "Houston", "country": "USA"}],
         "last_updated": "2026-01-01"},
        {"player": "Carlton Carrington", "status": "nba_active", "current_team": "Washington Wizards",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Carlton_Carrington",
         "career_history": [{"team": "Washington Wizards", "years": "2024-present", "city": "Washington", "country": "USA"}],
         "last_updated": "2026-01-01"},
        {"player": "LeBron James", "status": "nba_active", "current_team": "Los Angeles Lakers",
         "wikipedia_url": "https://en.wikipedia.org/wiki/LeBron_James",
         "career_history": [{"team": "Los Angeles Lakers", "years": "2018-present", "city": "Los Angeles", "country": "USA"}],
         "last_updated": "2026-01-01"},
    ]
    uc.CAREERS.write_text(json.dumps(db))
    uc.LOCATIONS.write_text("{}")
    uc.REVIEW.write_text("{}")

    templates = {
        "Template:Houston Rockets roster": "{{NBA roster/header}}\n{{player2|num=28|first=Alperen|last=Şengün|pos=C}}",
        "Template:Washington Wizards roster": "{{NBA roster/header}}\n{{player2|num=7|first=Bub|last=Carrington|pos=PG}}",
        "Template:Los Angeles Lakers roster": "{{NBA roster/header}}\n{{player2|num=23|first=LeBron|last=James|pos=SF}}\n{{player2|num=1|first=Cooper|last=Flagg|pos=SF}}",
    }
    pages = {
        "Alperen Sengun": ("{{Infobox basketball biography|name=Alperen Şengün|years1=2021–present|team1=[[Houston Rockets]]}}", "Alperen Şengün"),
        "Carlton Carrington": ("{{Infobox basketball biography|name=Carlton Carrington|years1=2024–present|team1=[[Washington Wizards]]}}", "Carlton Carrington"),
        "Bub Carrington": ("{{Infobox basketball biography|name=Carlton Carrington|years1=2024–present|team1=[[Washington Wizards]]}}", "Carlton Carrington"),
        "LeBron James": ("{{Infobox basketball biography|name=LeBron James|years1=2018–present|team1=[[Los Angeles Lakers]]}}", "LeBron James"),
        "Cooper Flagg": ("{{Infobox basketball biography|name=Cooper Flagg|years1={{nbay|2025|start}}–present|team1=[[Dallas Mavericks]]}}", "Cooper Flagg"),
    }
    wikipedia_api.WikipediaClient.get_wikitext = lambda self, t: templates.get(t, "")
    wikipedia_api.WikipediaClient.get_wikitext_and_title = lambda self, t: pages.get(t, (None, None))
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: ""

    roster = {"Alperen Şengün", "Bub Carrington", "LeBron James", "Cooper Flagg"}
    d = uc.Database()
    nba = [n for n, r in d.by_name.items() if r["status"] == "nba_active"]
    on_roster = {d.resolve_by_name(c) for c in roster}
    on_roster.discard(None)
    dropped = [n for n in nba if n not in on_roster]
    newp = sorted(c for c in roster if d.resolve_by_name(c) is None)
    assert "Alperen Sengun" not in dropped, "churn: Sengun marked dropped"
    assert newp == ["Bub Carrington", "Cooper Flagg"], newp  # Şengün folded, not new

    s = uc.run(mode="incremental", player=None, delay=0, max_requests=80)
    byn = {p["player"]: p for p in json.loads(uc.CAREERS.read_text())}
    assert set(byn) == {"Alperen Sengun", "Carlton Carrington", "LeBron James", "Cooper Flagg"}, set(byn)
    assert s["new_players"] == ["Cooper Flagg"], s["new_players"]
    assert byn["Alperen Sengun"]["display_name"] == "Alperen Şengün"
    assert "Bub Carrington" in byn["Carlton Carrington"].get("aliases", [])
    assert byn["Cooper Flagg"]["wikipedia_url"].endswith("Cooper_Flagg")
    assert byn["Cooper Flagg"]["career_history"][0]["years"] == "2025–present"

    mp = {p["player"]: p for p in json.loads(uc.ROOT_MAP_FILE.read_text())}
    assert "Alperen Sengun" in mp and "Alperen Şengün" not in mp, "map key changed"
    assert mp["Alperen Sengun"].get("display_name") == "Alperen Şengün"
    print("test_end_to_end PASS (no dup, no churn, map-key stable)")


if __name__ == "__main__":
    test_normkey()
    test_geo()
    test_end_to_end()
    print("\nALL DEDUPE TESTS PASS")
