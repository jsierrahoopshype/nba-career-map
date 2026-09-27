"""Put back the display names the override re-scrapes overwrote.

update_careers.merge_player used to set `display_name` to whatever article the
run read. After the fix-player-urls re-scrapes pointed ~200 records at their
curated articles, those records started to read "Anthony Edwards
(basketball)", "Mike James (basketball, born 1990)", "Harry Giles III" and
"Cat Barber" on their pages, in search, on team/club/place pages and in the
quiz. The pipeline no longer does that (names.display_name_for); this repairs
what it already wrote.

Every entry below is  key: (the bad display name, the right one), worked out
from the display name each record carried BEFORE the re-scrapes (commit
cbf6418c) and names.display_name_for:
  * a parenthetical disambiguator is removed
  * a suffix the key does not carry (Jr., II, III, IV, Sr.) is dropped
  * a different name (nickname, full name, article title) goes back to the key
  * a spelling of the SAME name is kept when the record had it before or the
    article gives it -- accents, initials, capitalisation: "Alex García",
    "A. J. Green", "Mamadou N'Diaye"

Records keyed with a year ("Mike James (1990)") show the name without it, as
they did before; the 46 year-keyed records with no display name are untouched.

A record is only changed while it still shows the bad name, so this is
idempotent and cannot undo a later, deliberate edit. The primary key -- and so
every page slug and URL -- is never touched.

Run:  python3 scripts/repair_display_names.py
Then: python3 scripts/build_dashboard_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"

FIXES: dict[str, tuple[str, str]] = {
    'Aaron Henry': ('Aaron Henry (basketball)', 'Aaron Henry'),
    'Ace Bailey': ('Ace Bailey (basketball)', 'Ace Bailey'),
    'AJ Green': ('A. J. Green (basketball)', 'A. J. Green'),
    'AJ Griffin': ('AJ Griffin (basketball)', 'AJ Griffin'),
    'AJ Johnson': ('AJ Johnson (basketball)', 'A. J. Johnson'),
    'Alan Williams': ('Alan Williams (basketball)', 'Alan Williams'),
    'Alex Garcia': ('Alex Garcia (basketball)', 'Alex García'),
    'Andre Jackson': ('Andre Jackson Jr.', 'Andre Jackson'),
    'Andrew Harrison': ('Andrew Harrison (basketball)', 'Andrew Harrison'),
    'Andrew Nicholson': ('Andrew Nicholson (basketball)', 'Andrew Nicholson'),
    'Andrew White': ('Andrew White (basketball)', 'Andrew White'),
    'Anthony Barber': ('Cat Barber', 'Anthony Barber'),
    'Anthony Bennett': ('Anthony Bennett (basketball)', 'Anthony Bennett'),
    'Anthony Black': ('Anthony Black (basketball)', 'Anthony Black'),
    'Anthony Brown': ('Anthony Brown (basketball)', 'Anthony Brown'),
    'Anthony Edwards': ('Anthony Edwards (basketball)', 'Anthony Edwards'),
    'Anthony Gill': ('Anthony Gill (basketball)', 'Anthony Gill'),
    'Anthony Lamb': ('Anthony Lamb (basketball)', 'Anthony Lamb'),
    'Archie Goodwin': ('Archie Goodwin (basketball)', 'Archie Goodwin'),
    'Ben Moore': ('Ben Moore (basketball)', 'Ben Moore'),
    'Bill Hosket': ('Bill Hosket Jr.', 'Bill Hosket'),
    'Bill Jones': ('Bill Jones (basketball, born 1966)', 'Bill Jones'),
    'Billy Garrett': ('Billy Garrett Jr.', 'Billy Garrett'),
    'BJ Johnson': ('B. J. Johnson (basketball)', 'B. J. Johnson'),
    'Blake Wesley': ('Blake Wesley (basketball)', 'Blake Wesley'),
    'Bogdan Bogdanovic': ('Bogdan Bogdanović (basketball)', 'Bogdan Bogdanović'),
    'Brandon Armstrong': ('Brandon Armstrong (basketball)', 'Brandon Armstrong'),
    'Brandon Goodwin': ('Brandon Goodwin (basketball)', 'Brandon Goodwin'),
    'Brandon Knight': ('Brandon Knight (basketball)', 'Brandon Knight'),
    'Brandon Miller': ('Brandon Miller (basketball, born 2002)', 'Brandon Miller'),
    'Brandon Williams': ('Brandon Williams (basketball, born 1999)', 'Brandon Williams'),
    'Brian Oliver': ('Brian Oliver (basketball, born 1968)', 'Brian Oliver'),
    'Bruce Brown': ('Bruce Brown (basketball)', 'Bruce Brown'),
    'Caleb Martin': ('Caleb Martin (basketball)', 'Caleb Martin'),
    'Carter Bryant': ('Carter Bryant (basketball)', 'Carter Bryant'),
    'Charles Thomas': ('Charles Thomas (basketball, born 1969)', 'Charles Thomas'),
    'Charlie Black': ('Charlie T. Black', 'Charlie Black'),
    'Charlie Brown': ('Charlie Brown Jr. (basketball)', 'Charlie Brown'),
    'Chris Boucher': ('Chris Boucher (basketball)', 'Chris Boucher'),
    'Chris Clemons': ('Chris Clemons (basketball)', 'Chris Clemons'),
    'Chris Crawford': ('Chris Crawford (basketball, born 1975)', 'Chris Crawford'),
    'Chris Duarte': ('Chris Duarte (basketball)', 'Chris Duarte'),
    'Chris McNealy': ('Chris McNealy (basketball, born 1961)', 'Chris McNealy'),
    'Chris Singleton': ('Chris Singleton (basketball, born 1989)', 'Chris Singleton'),
    'Chris Youngblood': ('Chris Youngblood (basketball)', 'Chris Youngblood'),
    'CJ Williams': ('C. J. Williams (basketball)', 'C. J. Williams'),
    'Cody Martin': ('Cody Martin (basketball)', 'Cody Martin'),
    'Connie Norman': ('Coniel Norman', 'Connie Norman'),
    'Corey Williams': ('Corey Williams (basketball, born 1970)', 'Corey Williams'),
    'Curtis Jones': ('Curtis Jones (basketball)', 'Curtis Jones'),
    'Damian Jones': ('Damian Jones (basketball)', 'Damian Jones'),
    'Daniel Hamilton': ('Daniel Hamilton (basketball)', 'Daniel Hamilton'),
    'David Duke': ('David Duke Jr.', 'David Duke'),
    'David Johnson': ('David Johnson (basketball)', 'David Johnson'),
    'Dennis Smith': ('Dennis Smith Jr.', 'Dennis Smith'),
    'Deonte Burton': ('Deonte Burton (basketball, born 1994)', 'Deonte Burton'),
    'Derrick Jones': ('Derrick Jones Jr.', 'Derrick Jones'),
    'Derrick Williams': ('Derrick Williams (basketball)', 'Derrick Williams'),
    'Donovan Williams': ('Donovan Williams (basketball)', 'Donovan Williams'),
    'Drew Peterson': ('Drew Peterson (basketball)', 'Drew Peterson'),
    'Ed Davis': ('Ed Davis (basketball)', 'Ed Davis'),
    'Emanuel Miller': ('Emanuel Miller (basketball)', 'Emanuel Miller'),
    'Eric Anderson': ('Eric Anderson (basketball, born 1970)', 'Eric Anderson'),
    'Eric Williams': ('Eric Williams (basketball, born 1972)', 'Eric Williams'),
    'Frank Jackson': ('Frank Jackson (basketball)', 'Frank Jackson'),
    'Frank Johnson': ('Frank Johnson (basketball, born 1958)', 'Frank Johnson'),
    'Frank Mason': ('Frank Mason III', 'Frank Mason'),
    'Gary Clark': ('Gary Clark (basketball)', 'Gary Clark'),
    'George King': ('George King (basketball, born 1994)', 'George King'),
    'Grant Nelson': ('Grant Nelson (basketball)', 'Grant Nelson'),
    'Grant Williams': ('Grant Williams (basketball)', 'Grant Williams'),
    'Greg Brown': ('Greg Brown III', 'Greg Brown'),
    'Guillermo Diaz': ('Guillermo Diaz (basketball)', 'Guillermo Diaz'),
    'Harry Giles': ('Harry Giles III', 'Harry Giles'),
    'Henry Walker': ('Henry Walker (basketball)', 'Henry Walker'),
    'Herbert Jones': ('Herbert Jones (basketball)', 'Herbert Jones'),
    'Hugo González': ('Hugo González (basketball)', 'Hugo González'),
    'Ian Clark': ('Ian Clark (basketball)', 'Ian Clark'),
    'Isaac Jones': ('Isaac Jones (basketball)', 'Isaac Jones'),
    'Isaiah Jackson': ('Isaiah Jackson (basketball)', 'Isaiah Jackson'),
    'Isaiah Thomas': ('Isaiah Thomas (basketball)', 'Isaiah Thomas'),
    'Jack White': ('Jack White (basketball)', 'Jack White'),
    'James Anderson': ('James Anderson (basketball)', 'James Anderson'),
    'James Ennis': ('James Ennis III', 'James Ennis'),
    'James Johnson': ('James Johnson (basketball, born 1987)', 'James Johnson'),
    'James Robinson': ('James Robinson (basketball, born 1970)', 'James Robinson'),
    'James Webb': ('James Webb III', 'James Webb'),
    'James Young': ('James Young (basketball)', 'James Young'),
    'Jay Miller': ('Jay Miller (basketball)', 'Jay Miller'),
    'Jaylin Williams': ('Jaylin Williams (basketball, born 2002)', 'Jaylin Williams'),
    'Jeff Green': ('Jeff Green (basketball)', 'Jeff Green'),
    'Jesse Edwards': ('Jesse Edwards (basketball)', 'Jesse Edwards'),
    'Jim Garvin': ('James Garvin (basketball)', 'Jim Garvin'),
    'Joe Harris': ('Joe Harris (basketball)', 'Joe Harris'),
    'John Brown': ('John Brown (basketball, born 1951)', 'John Brown'),
    'John Butler': ('John Butler Jr.', 'John Butler'),
    'John Collins': ('John Collins (basketball)', 'John Collins'),
    'John Holland': ('John Holland (basketball)', 'John Holland'),
    'John Jenkins': ('John Jenkins (basketball)', 'John Jenkins'),
    'Johnny Simmons': ('John Simmons (baseball)', 'Johnny Simmons'),
    'Jonathan Gibson': ('Jonathan Gibson (basketball)', 'Jonathan Gibson'),
    'Jordan Adams': ('Jordan Adams (basketball, born 1994)', 'Jordan Adams'),
    'Jordan Hall': ('Jordan Hall (basketball)', 'Jordan Hall'),
    'Jordan Hamilton': ('Jordan Hamilton (basketball)', 'Jordan Hamilton'),
    'Jordan Miller': ('Jordan Miller (basketball)', 'Jordan Miller'),
    'Jorge Gutierrez': ('Jorge Gutiérrez (basketball)', 'Jorge Gutierrez'),
    'Jose Alvarado': ('Jose Alvarado (basketball)', 'José Alvarado'),
    'Joseph Young': ('Joe Young (basketball)', 'Joseph Young'),
    'Josh Gray': ('Josh Gray (basketball)', 'Josh Gray'),
    'Josh Green': ('Josh Green (basketball)', 'Josh Green'),
    'Julian Phillips': ('Julian Phillips (basketball)', 'Julian Phillips'),
    'Justin Anderson': ('Justin Anderson (basketball)', 'Justin Anderson'),
    'Justin Edwards': ('Justin Edwards (basketball, born 2003)', 'Justin Edwards'),
    'Justin Harper': ('Justin Harper (basketball)', 'Justin Harper'),
    'Justin Jackson': ('Justin Jackson (basketball, born 1995)', 'Justin Jackson'),
    'Justin James': ('Justin James (basketball)', 'Justin James'),
    'Kendall Brown': ('Kendall Brown (basketball)', 'Kendall Brown'),
    'Kenny McIntosh': ('Kennedy McIntosh', 'Kenny McIntosh'),
    'Kenny Williams': ('Kenny Williams (basketball, born 1969)', 'Kenny Williams'),
    'Keon Johnson': ('Keon Johnson (basketball, born 2002)', 'Keon Johnson'),
    'Kevin Jones': ('Kevin Jones (basketball)', 'Kevin Jones'),
    'Kevin Knox': ('Kevin Knox II', 'Kevin Knox'),
    'Kevin Murphy': ('Kevin Murphy (basketball)', 'Kevin Murphy'),
    'Kyle Anderson': ('Kyle Anderson (basketball)', 'Kyle Anderson'),
    'Larry Sanders': ('Larry Sanders (basketball)', 'Larry Sanders'),
    'Leonard Miller': ('Leonard Miller (basketball)', 'Leonard Miller'),
    'Louis King': ('Louis King (basketball)', 'Louis King'),
    'Malachi Smith': ('Malachi Smith (basketball)', 'Malachi Smith'),
    'Malcolm Hill': ('Malcolm Hill (basketball)', 'Malcolm Hill'),
    'Malcolm Miller': ('Malcolm Miller (basketball)', 'Malcolm Miller'),
    'Malcolm Thomas': ('Malcolm Thomas (basketball, born 1988)', 'Malcolm Thomas'),
    "Mamadou N'diaye": ("Mamadou N'Diaye (basketball, born 1975)", "Mamadou N'Diaye"),
    'Marcus Thornton': ('Marcus Thornton (basketball, born 1987)', 'Marcus Thornton'),
    'Marcus Vinicius': ('Marquinhos Vieira', 'Marcus Vinicius'),
    'Mark Williams': ('Mark Williams (basketball)', 'Mark Williams'),
    'Marko Simonovic': ('Marko Simonović (basketball, born 1999)', 'Marko Simonović'),
    'Mason Jones': ('Mason Jones (basketball)', 'Mason Jones'),
    'Matt Ryan': ('Matt Ryan (basketball)', 'Matt Ryan'),
    'Matt Thomas': ('Matt Thomas (basketball)', 'Matt Thomas'),
    'Michael Foster': ('Michael Foster Jr.', 'Michael Foster'),
    'Michael Phelps': ('Mike Phelps', 'Michael Phelps'),
    'Michael Porter': ('Michael Porter Jr.', 'Michael Porter'),
    'Mike Conley': ('Mike Conley (basketball)', 'Mike Conley'),
    'Mike Green': ('Mike Green (basketball, born 1951)', 'Mike Green'),
    'Mike James (1990)': ('Mike James (basketball, born 1990)', 'Mike James'),
    'Mike Lynn': ('Mike Lynn (basketball)', 'Mike Lynn'),
    'Mike Morrison': ('Mike Morrison (basketball, born 1967)', 'Mike Morrison'),
    'Mike Scott': ('Mike Scott (basketball, born 1988)', 'Mike Scott'),
    'Mike Smith': ('Mike Smith (basketball, born 1976)', 'Mike Smith'),
    'Moritz Wagner': ('Moritz Wagner (basketball)', 'Moritz Wagner'),
    'Moses Brown': ('Moses Brown (basketball)', 'Moses Brown'),
    'Moussa Cissé': ('Moussa Cissé (basketball)', 'Moussa Cissé'),
    'Nate Williams': ('Nate Williams (basketball, born 1999)', 'Nate Williams'),
    'Nick Johnson': ('Nick Johnson (basketball)', 'Nick Johnson'),
    'Nick Smith': ('Nick Smith Jr.', 'Nick Smith'),
    'Patrick Williams': ('Patrick Williams (basketball)', 'Patrick Williams'),
    'Paul Reed': ('Paul Reed (basketball)', 'Paul Reed'),
    'Paul Watson': ('Paul Watson (basketball)', 'Paul Watson'),
    'Pepe Sanchez': ('Pepe Sánchez (basketball)', 'Pepe Sánchez'),
    'Phillip Wheeler': ('Phillip Wheeler (basketball)', 'Phillip Wheeler'),
    'Ray McCallum': ('Ray McCallum Jr.', 'Ray McCallum'),
    'Reggie Jackson': ('Reggie Jackson (basketball, born 1990)', 'Reggie Jackson'),
    'Reggie Perry': ('Reggie Perry (basketball)', 'Reggie Perry'),
    'Rob Edwards': ('Rob Edwards (basketball)', 'Rob Edwards'),
    'Rob Rose': ('Robert Rose (basketball)', 'Rob Rose'),
    'Robert Franks': ('Robert Franks (basketball)', 'Robert Franks'),
    'Robert Williams': ('Robert Williams III', 'Robert Williams'),
    'Robert Woodard': ('Robert Woodard II', 'Robert Woodard'),
    'Ron Anderson': ('Ron Anderson (basketball, born 1958)', 'Ron Anderson'),
    'Ron Holland': ('Ron Holland (basketball)', 'Ron Holland'),
    'Rudy Fernandez': ('Rudy Fernández (basketball)', 'Rudy Fernandez'),
    'Russ Smith': ('Russ Smith (basketball)', 'Russ Smith'),
    'Ryan Dunn': ('Ryan Dunn (basketball)', 'Ryan Dunn'),
    'Ryan Kelly': ('Ryan Kelly (basketball)', 'Ryan Kelly'),
    'Sam Vincent': ('Sam Vincent (basketball)', 'Sam Vincent'),
    'Sean McDermott': ('Sean McDermott (basketball)', 'Sean McDermott'),
    'Spencer Jones': ('Spencer Jones (basketball)', 'Spencer Jones'),
    'Stanley Johnson': ('Stanley Johnson (basketball)', 'Stanley Johnson'),
    'Sterling Brown': ('Sterling Brown (basketball)', 'Sterling Brown'),
    'Steve Hamilton': ('Steve Hamilton (sportsman, born 1934)', 'Steve Hamilton'),
    'Terry Taylor': ('Terry Taylor (basketball)', 'Terry Taylor'),
    'Thomas Robinson': ('Thomas Robinson (basketball)', 'Thomas Robinson'),
    'Thomas Welsh': ('Thomas Welsh (basketball)', 'Thomas Welsh'),
    'Tony Bradley': ('Tony Bradley (basketball)', 'Tony Bradley'),
    'Tony Farmer': ('Tony Farmer (basketball, born 1970)', 'Tony Farmer'),
    'Tony Mitchell (1989)': ('Tony Mitchell (basketball, born 1989)', 'Tony Mitchell'),
    'Travis Williams': ('Travis Williams (basketball player)', 'Travis Williams'),
    "Tre' Johnson": ('Tre Johnson (basketball)', "Tre' Johnson"),
    'Troy Brown': ('Troy Brown Jr.', 'Troy Brown'),
    'Tyler Davis': ('Tyler Davis (basketball)', 'Tyler Davis'),
    'Tyler Ennis': ('Tyler Ennis (basketball)', 'Tyler Ennis'),
    'Tyler Hall': ('Tyler Hall (basketball)', 'Tyler Hall'),
    'Tyler Kolek': ('Tyler Kolek (basketball)', 'Tyler Kolek'),
    'Tyler Smith': ('Tyler Smith (basketball, born 2004)', 'Tyler Smith'),
    'Vernon Carey': ('Vernon Carey Jr.', 'Vernon Carey'),
    'Vincent Edwards': ('Vincent Edwards (basketball)', 'Vincent Edwards'),
    'Walt Williams': ('Walt Williams (basketball)', 'Walt Williams'),
    'Wendell Moore': ('Wendell Moore Jr.', 'Wendell Moore'),
    'Wil Jones': ('Wil Jones (basketball, born 1947)', 'Wil Jones'),
    'William Howard': ('William Howard (basketball)', 'William Howard'),
}


def repair(path: Path, *, ready: bool) -> list[tuple[str, str, str]]:
    """Apply FIXES to one file. Returns [(key, before, after)] changed.

    The map file (READY) only carries `display_name` when it differs from the
    key, so there a name restored to the key drops the field instead.
    """
    records = json.loads(path.read_text(encoding="utf-8"))
    changed = []
    for rec in records:
        key = rec.get("player")
        if key not in FIXES:
            continue
        bad, good = FIXES[key]
        if rec.get("display_name") != bad:
            continue
        if ready and good == key:
            rec.pop("display_name", None)
        else:
            rec["display_name"] = good
        changed.append((key, bad, good))
    if changed:
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return changed


def main() -> None:
    done = repair(CAREERS, ready=False)
    ready = repair(READY, ready=True) if READY.exists() else []
    print(f"data/players/nba_players_careers.json: {len(done)} display name(s) "
          f"restored")
    print(f"nba_players_careers_READY.json: {len(ready)} display name(s) "
          f"restored")
    for key, bad, good in done:
        print(f"  {key}: {bad!r} -> {good!r}")

    left = [(r["player"], r["display_name"]) for r in
            json.loads(CAREERS.read_text(encoding="utf-8"))
            if "(" in (r.get("display_name") or "")]
    assert not left, f"display names still carrying a disambiguator: {left}"
    print("sanity checks PASS")


if __name__ == "__main__":
    main()
