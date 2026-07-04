"""Roster-parsing tests against the real {{player2}} template format.

Fixtures mirror the current Wikipedia structure: {{NBA roster/header}}, a
sequence of {{player2 | first=.. | last=.. | ...}} rows (names split across
params, no wikilinks), a coach block, and trailing Category:/season links —
exactly the shapes that broke the old wikilink-scraping extractor.

Run:  python3 scripts/test_roster_parse.py
"""
from __future__ import annotations

from rosters import extract_players_from_roster, extract_roster_entries

# ~15 players. Includes Jr. suffixes, a free agent (FA), a draft pick (DP),
# a two-way (TW) and an injured player, plus a coach block + category/season
# links that must NOT be captured.
MEMPHIS = """
{{NBA roster/header}}
{{player2 | num = 12 | first = Ja | last = Morant | pos = PG | ft=6 | in=2 | lbs=174 | college=Murray State | DOB=1999-08-10 }}
{{player2 | num = 13 | first = Jaren | last = Jackson Jr. | pos = FC | ft=6 | in=10 | lbs=242 | college=Michigan State | DOB=1999-09-15 }}
{{player2 | num = 7  | first = Santi | last = Aldama | pos = FC | ft=7 | in=0 | lbs=215 | college=Loyola (MD) | DOB=2001-01-10 | inj=yes }}
{{player2 | num = 36 | first = Marcus | last = Smart | pos = G | ft=6 | in=3 | lbs=220 | college=Oklahoma State | DOB=1994-03-06 | note=FA }}
{{player2 | num = 14 | first = Zach | last = Edey | pos = C | ft=7 | in=4 | lbs=299 | college=Purdue | DOB=2002-05-14 }}
{{player2 | num = 45 | first = GG | last = Jackson | pos = F | ft=6 | in=9 | lbs=214 | college=South Carolina | DOB=2004-12-17 }}
{{player2 | num = 5  | first = Vince | last = Williams Jr. | pos = GF | ft=6 | in=5 | lbs=205 | college=VCU | DOB=2000-08-30 }}
{{player2 | num = 10 | first = Luke | last = Kennard | pos = SG | ft=6 | in=5 | lbs=206 | college=Duke | DOB=1996-06-24 | note=FA }}
{{player2 | num = 1  | first = Scotty | last = Pippen Jr. | pos = PG | ft=6 | in=1 | lbs=185 | college=Vanderbilt | DOB=2000-11-10 }}
{{player2 | num = 30 | first = Jay | last = Huff | pos = C | ft=7 | in=1 | lbs=240 | college=Virginia | DOB=1998-03-27 }}
{{player2 | num = 15 | first = Brandon | last = Clarke | pos = FC | ft=6 | in=8 | lbs=215 | college=Gonzaga | DOB=1996-09-19 | inj=yes }}
{{player2 | num = 46 | first = John | last = Konchar | pos = GF | ft=6 | in=5 | lbs=210 | college=Purdue Fort Wayne | DOB=1996-03-22 }}
{{player2 | num = 21 | first = Cam | last = Spencer | pos = SG | ft=6 | in=4 | lbs=190 | college=UConn | DOB=2000-08-17 | note=TW }}
{{player2 | num = 0  | first = Jaylen | last = Wells | pos = SF | ft=6 | in=8 | lbs=205 | college=Washington State | DOB=2003-06-24 }}
{{player2 | num = 8  | first = Cedric | last = Coward | pos = SF | ft=6 | in=6 | lbs=213 | college=Washington State | DOB=2003-08-04 | note=DP }}
{{NBA roster/footer | S = [[2025–26 Memphis Grizzlies season]] }}
{{NBA coaches | Tuomas Iisalo | assistants = Noah LaRoche; Tuomas Iisalo }}
[[Category:Memphis Grizzlies templates]]
[[Category:National Basketball Association current rosters]]
"""

# ~16 players; another Jr. suffix and a coach line.
CELTICS = """
{{NBA roster/header}}
{{player2 | num = 0  | first = Jayson | last = Tatum | pos = SF | college=Duke | inj=yes }}
{{player2 | num = 7  | first = Jaylen | last = Brown | pos = SG | college=California }}
{{player2 | num = 4  | first = Jrue | last = Holiday | pos = PG | college=UCLA }}
{{player2 | num = 12 | first = Derrick | last = White | pos = SG | college=Colorado }}
{{player2 | num = 42 | first = Al | last = Horford | pos = C | college=Florida | note=FA }}
{{player2 | num = 8  | first = Kristaps | last = Porziņģis | pos = C | college= }}
{{player2 | num = 40 | first = Luke | last = Kornet | pos = C | college=Vanderbilt | note=FA }}
{{player2 | num = 11 | first = Payton | last = Pritchard | pos = PG | college=Oregon }}
{{player2 | num = 26 | first = Xavier | last = Tillman Sr. | pos = FC | college=Michigan State }}
{{player2 | num = 88 | first = Neemias | last = Queta | pos = C | college=Utah State }}
{{player2 | num = 20 | first = Jordan | last = Walsh | pos = SF | college=Arkansas }}
{{player2 | num = 9  | first = Sam | last = Hauser | pos = SF | college=Virginia }}
{{player2 | num = 30 | first = Baylor | last = Scheierman | pos = SG | college=Creighton }}
{{player2 | num = 55 | first = JD | last = Davison | pos = PG | college=Alabama | note=TW }}
{{player2 | num = 13 | first = Drew | last = Peterson | pos = SF | college=USC | note=TW }}
{{player2 | num = 44 | first = Anton | last = Watson | pos = FC | college=Gonzaga | note=DP }}
{{NBA roster/footer | S = [[2025–26 Boston Celtics season]] }}
{{NBA coaches | Joe Mazzulla }}
[[Category:Boston Celtics templates]]
"""


def _check(team, wikitext, lo=13, hi=18):
    names = extract_players_from_roster(wikitext)
    entries = extract_roster_entries(wikitext)
    assert names == [e["name"] for e in entries], "names must match entries order"
    n = len(names)
    print(f"{team}: {n} players")
    assert lo <= n <= hi, f"{team}: expected {lo}-{hi} players, got {n}: {names}"
    # no coaches / categories / season links leaked
    for bad in ("Tuomas Iisalo", "Noah LaRoche", "Joe Mazzulla"):
        assert bad not in names, f"coach leaked: {bad}"
    assert not any(x.startswith("Category:") or "season" in x.lower()
                   or "High School" in x for x in names), f"non-player leaked in {names}"
    return names, entries


def main() -> None:
    mnames, mentries = _check("Memphis", MEMPHIS)
    cnames, centries = _check("Celtics", CELTICS)

    by = {e["name"]: e for e in mentries}
    # suffix handling
    assert "Jaren Jackson Jr." in mnames, mnames
    assert "Vince Williams Jr." in mnames
    assert "Scotty Pippen Jr." in mnames
    # exact example row from the spec
    santi = by["Santi Aldama"]
    assert santi["num"] == "7" and santi["pos"] == "FC" and santi["injured"] is True, santi
    # metadata: notes captured, FA included
    assert by["Marcus Smart"]["note"] == "FA", "FA player must be present with note"
    assert by["Cam Spencer"]["note"] == "TW"
    assert by["Cedric Coward"]["note"] == "DP"
    assert by["Ja Morant"]["num"] == "12" and by["Ja Morant"]["pos"] == "PG"
    fa = [e["name"] for e in mentries if e["note"] == "FA"]
    print(f"Memphis FA included: {fa}")
    # Celtics diacritics + suffix
    assert "Kristaps Porziņģis" in cnames, cnames
    assert "Xavier Tillman Sr." in cnames

    print("\nALL ROSTER-PARSE TESTS PASS")
    print("sample Memphis entries:")
    for e in mentries[:3]:
        print("  ", e)


if __name__ == "__main__":
    main()
