"""Tests for {{Nbay}} season-template expansion (BUG 1) and that retired_players
is populated (BUG 2 regression guard).

{{nbay|YYYY|start}} = YYYY, {{nbay|YYYY|end}} = YYYY+1, {{nbay|YYYY}} = "YYYY–YY".
Surrounding text such as "–present" must be preserved.

Run:  python3 scripts/test_nbay.py
"""
from __future__ import annotations

from wiki_parser import _clean_text, parse_player
from team_normalizer import TeamNormalizer


def test_nbay_expansion():
    # exact raw career strings from Jonathan Kuminga (preserved in commit 5d6f101)
    cases = {
        "{{nbay|2021|start}}–{{nbay|2025|end}}": "2021–2026",  # end -> YYYY+1
        "{{nbay|2025|end}}–present": "2026–present",           # present preserved
        # plain form expands to a season label
        "{{nbay|2020}}": "2020–21",
        "{{nbay|1999}}": "1999–00",                            # century rollover
        # start form + open-ended
        "{{nbay|2021|start}}–present": "2021–present",
        # case-insensitive template name
        "{{Nbay|2018|start}}–{{Nbay|2020|end}}": "2018–2021",
    }
    for raw, expected in cases.items():
        got = _clean_text(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"
    print("test_nbay_expansion PASS")


def test_kuminga_full_parse():
    wt = """{{Infobox basketball biography
| name = Jonathan Kuminga
| years1 = {{nbay|2021|start}}–{{nbay|2025|end}}
| team1 = [[Golden State Warriors]]
| years2 = {{nbay|2025|end}}–present
| team2 = [[Atlanta Hawks]]
}}"""
    rec = parse_player(wt, "Jonathan Kuminga", TeamNormalizer())
    ch = rec["career_history"]
    assert ch[0]["years"] == "2021–2026", ch[0]["years"]
    assert ch[1]["years"] == "2026–present", ch[1]["years"]
    assert rec["current_team"] == "Atlanta Hawks", rec["current_team"]
    print("test_kuminga_full_parse PASS")


def test_retired_persist_populated():
    """Regression guard for BUG 2: _persist must write retired players, not []."""
    import json
    import tempfile
    from pathlib import Path
    import update_careers as uc

    db = uc.Database.__new__(uc.Database)
    db.by_name = {
        "Ret One": {"player": "Ret One", "status": "retired", "career_history": []},
        "Ret Two": {"player": "Ret Two", "status": "retired", "career_history": []},
        "Active": {"player": "Active", "status": "nba_active", "career_history": []},
    }
    db.order = list(db.by_name)
    db.locations, db.review = {}, {}

    sb = Path(tempfile.mkdtemp())
    for a in ["CAREERS", "ACTIVE", "RETIRED", "LOCATIONS", "REVIEW",
              "UPDATE_LOG", "CHANGELOG", "ROOT_MAP_FILE"]:
        setattr(uc, a, sb / (a.lower() + ".json"))
    uc.LOGS = sb
    uc._persist(db, {"date": "x", "mode": "single", "players_updated": [],
                     "new_players": [], "new_teams": [], "team_moves": [],
                     "status_changes": [], "newly_overseas": [], "newly_retired": [],
                     "requests": 0, "budget_exhausted": False})
    ret = json.loads((sb / "retired.json").read_text())
    act = json.loads((sb / "active.json").read_text())
    assert ret["count"] == 2 and sorted(ret["players"]) == ["Ret One", "Ret Two"], ret
    assert act["count"] == 1, act
    print("test_retired_persist_populated PASS")


def _run_merge(existing, wikitext, canonical_title, name="Jonathan Kuminga"):
    """Drive merge_player end-to-end with mocked network; return stored record."""
    import json
    import tempfile
    from pathlib import Path
    import update_careers as uc
    import wikipedia_api

    sb = Path(tempfile.mkdtemp())
    uc.CAREERS = sb / "c.json"
    uc.CAREERS.write_text(json.dumps(existing))
    uc.LOCATIONS = sb / "l.json"; uc.LOCATIONS.write_text("{}")
    uc.REVIEW = sb / "r.json"; uc.REVIEW.write_text("{}")
    db = uc.Database()
    wikipedia_api.WikipediaClient.get_wikitext_and_title = lambda self, t: (wikitext, canonical_title)
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: ""
    client = wikipedia_api.WikipediaClient(delay=0, max_requests=10)
    return uc.merge_player(db, name, client, {}, set(), 2026), db


def test_merge_preserves_present_end_to_end():
    """The stored years (through merge_player, not just the parser) must keep
    "–present" — including when the separator / "present" are templated."""
    existing = [{"player": "Jonathan Kuminga", "status": "nba_active",
                 "current_team": "Atlanta Hawks",
                 "career_history": [{"team": "Atlanta Hawks", "years": "2025",
                                     "city": "", "country": ""}]}]
    for years2 in ("{{nbay|2025|end}}–present",
                   "{{nbay|2025|end}}{{ndash}}{{small|present}}"):
        wt = ("{{Infobox basketball biography\n"
              "| name = Jonathan Kuminga\n"
              "| years1 = {{nbay|2021|start}}–{{nbay|2025|end}}\n"
              "| team1 = [[Golden State Warriors]]\n"
              f"| years2 = {years2}\n"
              "| team2 = [[Atlanta Hawks]]\n}}")
        (rec, _, _, _, _), _ = _run_merge(existing, wt, "Jonathan Kuminga")
        yrs = {s["team"]: s["years"] for s in rec["career_history"]}
        assert yrs["Golden State Warriors"] == "2021–2026", yrs
        assert yrs["Atlanta Hawks"] == "2026–present", (years2, yrs)
    print("test_merge_preserves_present_end_to_end PASS")


def test_empty_name_guard():
    """A page resolving to an empty name must not create a record, and _persist
    must never write an empty-name row."""
    import json
    import tempfile
    from pathlib import Path
    import update_careers as uc

    # merge_player refuses to create an empty-name record
    result_tuple, db = _run_merge([], "{{Infobox basketball biography}}", "", name="")
    assert result_tuple[0] is None, result_tuple

    # _persist drops any empty-name record already in the DB
    db.by_name = {"": {"player": "", "status": "retired", "career_history": []},
                  "Real Guy": {"player": "Real Guy", "status": "retired", "career_history": []}}
    db.order = ["", "Real Guy"]
    db.locations, db.review = {}, {}
    sb = Path(tempfile.mkdtemp())
    for a in ["CAREERS", "ACTIVE", "RETIRED", "LOCATIONS", "REVIEW",
              "UPDATE_LOG", "CHANGELOG", "ROOT_MAP_FILE"]:
        setattr(uc, a, sb / (a.lower() + ".json"))
    uc.LOGS = sb
    uc._persist(db, {"date": "x", "mode": "single", "players_updated": [],
                     "new_players": [], "new_teams": [], "team_moves": [],
                     "status_changes": [], "newly_overseas": [], "newly_retired": [],
                     "requests": 0, "budget_exhausted": False})
    careers = json.loads((sb / "careers.json").read_text())
    ret = json.loads((sb / "retired.json").read_text())
    assert all(str(p["player"]).strip() for p in careers), "empty-name record persisted"
    assert "" not in ret["players"] and ret["count"] == 1, ret
    print("test_empty_name_guard PASS")


if __name__ == "__main__":
    test_nbay_expansion()
    test_kuminga_full_parse()
    test_retired_persist_populated()
    test_merge_preserves_present_end_to_end()
    test_empty_name_guard()
    print("\nALL NBAY / RETIRED / MERGE / GUARD TESTS PASS")
