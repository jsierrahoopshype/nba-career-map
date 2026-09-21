"""Tests for the article audit and the alias dedupe.

Run:  python3 scripts/test_wiki_audit.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_wiki_titles as aud  # noqa: E402
import dedupe_alias_records as ded  # noqa: E402
import update_careers as uc  # noqa: E402

FATHER = [{"years": "1987-1998", "team": "Chicago Bulls", "city": "Chicago",
           "country": "USA"},
          {"years": "2008", "team": "Torpan Pojat", "city": "Helsinki",
           "country": "Finland"}]
SON_WT = ("{{Infobox basketball biography\n"
          "| name = Scotty Pippen Jr.\n"
          "| birth_date = {{birth date and age|2000|11|10}}\n"
          "| years1 = 2022\u20132024\n"
          "| team1 = [[Los Angeles Lakers]]\n}}")

RECORDS = [
    {"player": "Scottie Pippen", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Scottie_Pippen",
     "career_history": [dict(s) for s in FATHER]},
    {"player": "Scotty Pippen Jr", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Scotty_Pippen",
     "career_history": [dict(s) for s in FATHER]},
    # the other shape: his name reaches nothing but his father's article, so
    # both records answer to it and they look like a duplicate pair
    {"player": "Ron Harper", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Ron_Harper",
     "career_history": [{"years": "1986-1989", "team": "Cleveland Cavaliers",
                         "city": "Cleveland", "country": "USA"}]},
    {"player": "Ron Harper Jr", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Ron_Harper",
     "career_history": [{"years": "1986-1989", "team": "Cleveland Cavaliers",
                         "city": "Cleveland", "country": "USA"}]},
    {"player": "Pat Ewing", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Pat_Ewing",
     "all_star_count": 11,
     "career_history": [{"years": "1985-2000", "team": "New York Knicks",
                         "city": "New York", "country": "USA"}]},
    {"player": "Patrick Ewing", "status": "retired",
     "wikipedia_url": "https://en.wikipedia.org/wiki/Patrick_Ewing",
     "career_history": [{"years": "1985-2000", "team": "New York Knicks",
                         "city": "New York", "country": "USA"},
                        {"years": "2001-2002", "team": "Orlando Magic",
                         "city": "Orlando", "country": "USA"}]},
]

# what Wikipedia answers: title asked -> title reached
WIKI = {
    "Scottie Pippen": "Scottie Pippen",
    # his name reaches his own article today; the career in his record does
    # not come from it, which is the whole point of checking both ends
    "Scotty Pippen Jr": "Scotty Pippen Jr.",
    "Scotty Pippen Jr.": "Scotty Pippen Jr.",  # the article that fixes it
    "Scotty Pippen": "Scottie Pippen",
    "Ron Harper": "Ron Harper",
    "Ron Harper Jr": "Ron Harper",
    "Pat Ewing": "Patrick Ewing",
    "Patrick Ewing": "Patrick Ewing",
}


class FakeClient:
    requests_made = 0

    def resolve_titles(self, titles, batch=50):
        return {t: WIKI.get(t) for t in dict.fromkeys(titles)}

    def get_wikitext_and_title(self, title):
        got = WIKI.get(title)
        if not got:
            return None, None
        return (SON_WT if got == "Scotty Pippen Jr." else "{{Infobox}}\n"), got

    def get_extract(self, title):
        return ""


def _db(records):
    sb = Path(tempfile.mkdtemp())
    for a in ["CAREERS", "ACTIVE", "RETIRED", "LOCATIONS", "REVIEW",
              "UPDATE_LOG", "CHANGELOG", "ROOT_MAP_FILE", "SPELLING_REVIEW",
              "ARTICLE_REVIEW", "TRANSACTIONS"]:
        if hasattr(uc, a):
            setattr(uc, a, sb / (a.lower() + ".json"))
    uc.LOGS = sb
    uc.CAREERS.write_text(json.dumps(records), encoding="utf-8")
    uc.LOCATIONS.write_text("{}"); uc.REVIEW.write_text("{}")
    db = uc.Database()
    return db, sb


def test_the_sweep_names_the_record_built_from_the_wrong_article():
    db, _sb = _db([dict(r) for r in RECORDS])
    players = [db.by_name[n] for n in db.order]
    report = aud.sweep(FakeClient(), players)

    # Pippen Jr's name resolves fine, so only the stored article condemns him;
    # Harper Jr's name lands on his father too, so both ends condemn him
    assert {r["player"] for r in report["wrong_person"]} == {"Ron Harper Jr"}
    bad = {r["player"] for r in report["bad_source"]}
    assert bad == {"Scotty Pippen Jr", "Ron Harper Jr"}, bad
    assert all("suffix" in r["reason"] for r in report["bad_source"])
    # the father is not accused of anything
    assert "Scottie Pippen" not in bad
    # and the two Ewings are reported as one article held by two records
    dups = {tuple(d["players"]) for d in report["duplicates"]}
    assert ("Pat Ewing", "Patrick Ewing") in dups, report["duplicates"]
    print("test_the_sweep_names_the_record_built_from_the_wrong_article PASS")


def test_the_fix_replaces_the_invented_career_with_the_real_one():
    db, _sb = _db([dict(r) for r in RECORDS])
    result = aud.fix(FakeClient(), db, ["Scotty Pippen Jr"], 2026)
    assert not result["unresolved"], result["unresolved"]
    rec = db.by_name["Scotty Pippen Jr"]
    teams = [s["team"] for s in rec["career_history"]]
    assert teams == ["Los Angeles Lakers"], teams
    assert rec["wikipedia_url"].endswith("Scotty_Pippen_Jr."), rec["wikipedia_url"]
    # replacement, not a merge: none of his father's stops survive
    assert "Chicago Bulls" not in teams
    # and the father keeps his own career
    assert [s["team"] for s in db.by_name["Scottie Pippen"]["career_history"]] \
        == ["Chicago Bulls", "Torpan Pojat"]
    print("test_the_fix_replaces_the_invented_career_with_the_real_one PASS")


def test_a_record_is_not_overwritten_by_an_article_that_disagrees_with_it():
    """Fourteen records point at a disambiguation page, with fine careers.

    Repointing those is worth doing, but "Joe Smith (basketball)" is not
    necessarily our Joe Smith. When the article shares no stint with what is
    stored, the record is left exactly as it was and reported instead.
    """
    records = [{"player": "Joe Smith", "status": "retired",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Joseph_Smith",
                "career_history": [{"years": "1995-1998", "team": "Golden State Warriors",
                                    "city": "Oakland", "country": "USA"}]}]
    db, _sb = _db(records)
    other = ("{{Infobox basketball biography\n| name = Joe Smith\n"
             "| years1 = 1951\u20131952\n| team1 = [[Baltimore Bullets]]\n}}")
    WIKI["Joe Smith (basketball)"] = "Joe Smith (basketball)"
    try:
        client = FakeClient()
        client.get_wikitext_and_title = lambda t: (
            (other, "Joe Smith (basketball)") if t == "Joe Smith (basketball)"
            else (None, None))
        result = aud.fix(client, db, ["Joe Smith"], 2026, replace=set())
        assert not result["fixed"], result["fixed"]
        assert result["conflicts"], result
        assert "shares no stint" in result["conflicts"][0]["conflict"]
        teams = [s["team"] for s in db.by_name["Joe Smith"]["career_history"]]
        assert teams == ["Golden State Warriors"], teams
    finally:
        WIKI.pop("Joe Smith (basketball)", None)
    print("test_a_record_is_not_overwritten_by_an_article_that_disagrees_with_it PASS")


def test_an_empty_parse_never_replaces_a_career():
    """What went wrong on the first apply run.

    Michael Wilson's thirteen stints were Mike Gibson's, so the record was
    condemned and replaced -- with an article that parses to nothing. A wrong
    career is bad; no career is not better. The record is left alone and
    reported instead.
    """
    records = [{"player": "Michael Wilson", "status": "retired",
                "wikipedia_url": "https://en.wikipedia.org/wiki/Michael_Gibson_(basketball)",
                "career_history": [{"years": "1997", "team": "Memphis Tigers",
                                    "city": "Memphis", "country": "USA"}]}]
    db, _sb = _db(records)
    client = FakeClient()
    client.get_wikitext_and_title = lambda t: (
        ("== Career ==\nNo infobox here.\n", "Michael Wilson")
        if t == "Michael Wilson" else (None, None))
    result = aud.fix(client, db, ["Michael Wilson"], 2026)
    assert not result["fixed"], result["fixed"]
    assert result["conflicts"][0]["conflict"] == "the article parsed to nothing"
    assert len(db.by_name["Michael Wilson"]["career_history"]) == 1
    print("test_an_empty_parse_never_replaces_a_career PASS")


def test_dedupe_unions_the_stints_and_keeps_the_canonical_key():
    db, _sb = _db([dict(r) for r in RECORDS])
    report = aud.sweep(FakeClient(), [db.by_name[n] for n in db.order])
    ready, blocked = ded.groups([db.by_name[n] for n in db.order], report)
    names = {g["article"]: g["players"] for g in ready}
    assert names == {"Patrick Ewing": ["Pat Ewing", "Patrick Ewing"]}, names

    row = ded._merge(db, "Patrick Ewing", ["Pat Ewing", "Patrick Ewing"])
    assert row["kept"] == "Patrick Ewing", row
    assert "Pat Ewing" not in db.by_name
    kept = db.by_name["Patrick Ewing"]
    teams = sorted(s["team"] for s in kept["career_history"])
    assert teams == ["New York Knicks", "Orlando Magic"], teams
    assert "Pat Ewing" in kept["aliases"]
    # the display name is a name, not an article's disambiguator
    assert "(" not in (kept.get("display_name") or ""), kept.get("display_name")
    # the all-star count only one row carried is not lost in the merge
    assert kept["all_star_count"] == 11, kept.get("all_star_count")
    print("test_dedupe_unions_the_stints_and_keeps_the_canonical_key PASS")


def test_dedupe_refuses_to_bury_a_wrong_article_record():
    """Ron Harper and his son share an article only because the son's is wrong.

    Merging them would make the duplicate go away and the fabrication
    permanent, so the group is skipped until the fix has run.
    """
    db, _sb = _db([dict(r) for r in RECORDS])
    report = aud.sweep(FakeClient(), [db.by_name[n] for n in db.order])
    _ready, blocked = ded.groups([db.by_name[n] for n in db.order], report)
    skipped = {tuple(b["players"]) for b in blocked}
    assert ("Ron Harper", "Ron Harper Jr") in skipped, blocked
    assert "Ron Harper" in db.by_name and "Ron Harper Jr" in db.by_name
    print("test_dedupe_refuses_to_bury_a_wrong_article_record PASS")


def test_one_article_does_not_make_two_players_one():
    """Cliff Robinson and Clifford Robinson resolve to the same title.

    They are two different players, and so are the two Freddie Lewises, whose
    rows hold the same career only because one was copied from the other. A
    shared article is a question, not an answer; the careers answer it.
    """
    records = [
        {"player": "Cliff Robinson", "status": "retired",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Clifford_Robinson",
         "career_history": [{"years": "1979-1981", "team": "New Jersey Nets"}]},
        {"player": "Clifford Robinson", "status": "retired",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Clifford_Robinson",
         "career_history": [{"years": "1989-1997", "team": "Portland Trail Blazers"}]},
        {"player": "Freddie Lewis (1921)", "status": "retired",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Freddie_Lewis",
         "career_history": [{"years": "1967-1974", "team": "Indiana Pacers"}]},
        {"player": "Freddie Lewis (1943)", "status": "retired",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Freddie_Lewis",
         "career_history": [{"years": "1967-1974", "team": "Indiana Pacers"}]},
    ]
    db, _sb = _db(records)
    players = [db.by_name[n] for n in db.order]
    ready, blocked = ded.groups(players, {"resolved": {
        "Cliff Robinson": "Clifford Robinson",
        "Clifford Robinson": "Clifford Robinson",
        "Freddie Lewis (1921)": "Freddie Lewis",
        "Freddie Lewis (1943)": "Freddie Lewis"}})
    assert not ready, ready
    whys = {b["why"] for b in blocked}
    assert "careers do not overlap" in whys, whys
    assert any("is not" in w for w in whys), whys
    print("test_one_article_does_not_make_two_players_one PASS")


def test_the_surviving_key_stays_ascii():
    """The primary key addresses a page and indexes the map; diacritics go in
    display_name, which is the convention the rest of the database follows."""
    assert ded._survivor(["Jakob Poeltl", "Jakob Pöltl"], "Jakob Pöltl") == \
        "Jakob Poeltl"
    assert ded._survivor(["Pat Ewing", "Patrick Ewing"], "Patrick Ewing") == \
        "Patrick Ewing"
    print("test_the_surviving_key_stays_ascii PASS")


if __name__ == "__main__":
    test_the_sweep_names_the_record_built_from_the_wrong_article()
    test_the_fix_replaces_the_invented_career_with_the_real_one()
    test_a_record_is_not_overwritten_by_an_article_that_disagrees_with_it()
    test_an_empty_parse_never_replaces_a_career()
    test_dedupe_unions_the_stints_and_keeps_the_canonical_key()
    test_dedupe_refuses_to_bury_a_wrong_article_record()
    test_one_article_does_not_make_two_players_one()
    test_the_surviving_key_stays_ascii()
    print("\nALL ARTICLE AUDIT TESTS PASS")
