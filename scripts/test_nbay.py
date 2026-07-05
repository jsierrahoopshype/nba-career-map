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


if __name__ == "__main__":
    test_nbay_expansion()
    test_kuminga_full_parse()
    test_retired_persist_populated()
    print("\nALL NBAY / RETIRED TESTS PASS")
