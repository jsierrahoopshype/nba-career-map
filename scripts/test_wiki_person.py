"""Tests for the wrong-article guard.

The bug it exists for: "Scotty Pippen Jr" redirects to his father, so the son's
record held a career that ended before he was born. The guard has to stop that
without stopping the redirects the scraper depends on -- diacritics, nicknames,
transliteration -- which is the whole difficulty.

Run:  python3 scripts/test_wiki_person.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_person import same_person, candidate_titles  # noqa: E402


def test_a_redirect_may_respell_a_name_but_not_change_the_person():
    """Every redirect the scraper legitimately follows, and the ones it must not."""
    allowed = [
        ("Alperen Sengun", "Alperen Şengün"),        # diacritics
        ("Jakob Poeltl", "Jakob Pöltl"),             # transliteration, both ways
        ("Dennis Schroder", "Dennis Schröder"),
        ("Bub Carrington", "Carlton Carrington"),    # nickname
        ("Pat Ewing", "Patrick Ewing"),
        ("Johnny Davis", "Johnny Davis (NBA)"),      # disambiguator added
        ("Charles Jones (1957)", "Charles Jones (basketball, born 1957)"),
        ("Craig Porter", "Craig Porter Jr."),        # our key catching up
        ("Trayce Jackson", "Trayce Jackson-Davis"),  # compound surname
    ]
    refused = [
        ("Scotty Pippen Jr", "Scottie Pippen", "suffix"),
        ("Ron Harper Jr", "Ron Harper", "suffix"),
        ("Gary Payton II", "Gary Payton", "suffix"),
        ("Jabari Smith Jr.", "Jabari Smith", "suffix"),
        ("Michael Wilson", "Michael Gibson (basketball)", "surname"),
        ("Charles Jones (1957)", "Charles Jones (basketball, born 1962)", "born"),
        ("Freddie Lewis (1921)", "Freddie Lewis (basketball, born 1943)", "born"),
    ]
    for a, b in allowed:
        ok, why = same_person(a, b)
        assert ok, f"{a} -> {b} wrongly refused ({why})"
    for a, b, signal in refused:
        ok, why = same_person(a, b)
        assert not ok, f"{a} -> {b} wrongly allowed"
        assert signal in why, f"{a} -> {b}: expected {signal}, got {why}"
    print(f"test_a_redirect_may_respell_a_name_but_not_change_the_person PASS "
          f"({len(allowed)} allowed, {len(refused)} refused)")


def test_two_articles_that_differ_by_a_suffix_are_two_people():
    """Comparing a request to its answer is not comparing two articles.

    "Craig Porter" -> "Craig Porter Jr." is a key catching up with its own
    article. "Craig Porter Jr." as an existing record's article, against a
    fresh "Craig Porter", is a father and a son.
    """
    assert same_person("Craig Porter", "Craig Porter Jr.")[0]
    assert not same_person("Craig Porter", "Craig Porter Jr.",
                           strict_suffix=True)[0]
    assert same_person("Alperen Sengun", "Alperen Şengün", strict_suffix=True)[0]
    print("test_two_articles_that_differ_by_a_suffix_are_two_people PASS")


def test_the_ladder_tries_the_disambiguated_title():
    """The period is the whole difference between a father and his son."""
    assert candidate_titles("Scotty Pippen Jr")[0] == "Scotty Pippen Jr."
    assert candidate_titles("Ron Harper Jr")[0] == "Ron Harper Jr."
    assert "Michael Wilson (basketball, born 1972)" in candidate_titles(
        "Michael Wilson", 1972)
    assert "Michael Wilson (basketball)" in candidate_titles("Michael Wilson")
    # a title that is already disambiguated is not re-disambiguated into junk
    assert all("((" not in t for t in candidate_titles("Charles Jones (1957)"))
    print("test_the_ladder_tries_the_disambiguated_title PASS")


# --- end to end, through merge_player ---------------------------------------

def _db(records):
    import update_careers as uc
    sb = Path(tempfile.mkdtemp())
    uc.CAREERS = sb / "c.json"
    uc.CAREERS.write_text(json.dumps(records), encoding="utf-8")
    uc.LOCATIONS = sb / "l.json"; uc.LOCATIONS.write_text("{}")
    uc.REVIEW = sb / "r.json"; uc.REVIEW.write_text("{}")
    return uc.Database()


def _client(pages: dict):
    """A Wikipedia that answers only the titles it actually has.

    `pages` maps a requested title to (canonical_title, wikitext); anything
    absent comes back missing, exactly as the API reports it.
    """
    import wikipedia_api
    wikipedia_api.WikipediaClient.get_wikitext_and_title = (
        lambda self, t: pages.get(t, (None, None))[::-1]
        if t in pages else (None, None))
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: ""
    return wikipedia_api.WikipediaClient(delay=0, max_requests=20)


SON = "{{Infobox basketball biography}}\n'''Scotty Pippen Jr.''' is a player.\n"
FATHER = "{{Infobox basketball biography}}\n'''Scottie Pippen''' is a player.\n"


def test_a_son_is_never_handed_his_fathers_article():
    """The live bug, end to end.

    With only the father's article reachable, the son's record must be left
    alone and the refusal recorded -- not filled with a career that ended
    thirteen years before he was born.
    """
    import update_careers as uc
    db = _db([{"player": "Scottie Pippen", "status": "retired",
               "wikipedia_url": "https://en.wikipedia.org/wiki/Scottie_Pippen",
               "career_history": [{"years": "1987-1998", "team": "Chicago Bulls"}]}])
    uc.REFUSED.clear()
    client = _client({"Scotty Pippen Jr": ("Scottie Pippen", FATHER)})
    rec, _teams, is_new, *_ = uc.merge_player(db, "Scotty Pippen Jr", client,
                                              {}, set(), 2026)
    assert rec is None, "the son was written from his father's article"
    assert not is_new
    assert "Scotty Pippen Jr" not in db.by_name
    assert len(db.by_name["Scottie Pippen"]["career_history"]) == 1
    assert uc.REFUSED and uc.REFUSED[0]["player"] == "Scotty Pippen Jr"
    assert "suffix" in uc.REFUSED[0]["reason"], uc.REFUSED[0]
    print("test_a_son_is_never_handed_his_fathers_article PASS")


def test_the_ladder_finds_the_son_when_his_article_exists():
    """Refusing is the floor, not the goal: with the period, he resolves."""
    import update_careers as uc
    db = _db([{"player": "Scottie Pippen", "status": "retired",
               "wikipedia_url": "https://en.wikipedia.org/wiki/Scottie_Pippen",
               "career_history": [{"years": "1987-1998", "team": "Chicago Bulls"}]}])
    uc.REFUSED.clear()
    client = _client({"Scotty Pippen Jr": ("Scottie Pippen", FATHER),
                      "Scotty Pippen Jr.": ("Scotty Pippen Jr.", SON)})
    rec, _teams, is_new, *_ = uc.merge_player(db, "Scotty Pippen Jr", client,
                                              {}, set(), 2026)
    assert rec is not None and is_new, "the son's own article was not used"
    assert rec["wikipedia_url"].endswith("Scotty_Pippen_Jr."), rec["wikipedia_url"]
    assert not uc.REFUSED
    assert len(db.by_name["Scottie Pippen"]["career_history"]) == 1, \
        "the father's record was touched"
    print("test_the_ladder_finds_the_son_when_his_article_exists PASS")


def test_a_jr_is_not_merged_into_the_record_of_a_name_that_folds_to_his():
    """The second vector: the name index folds Jr away.

    "Jabari Smith Jr." resolves to his own article, but the name index answers
    with his father's record, and the merge would have written the son's career
    into it. The article, not the folded name, decides.
    """
    import update_careers as uc
    father = {"player": "Jabari Smith", "status": "retired",
              "wikipedia_url": "https://en.wikipedia.org/wiki/Jabari_Smith",
              "career_history": [{"years": "2007-2008", "team": "Sioux Falls Skyforce"}]}
    db = _db([father])
    uc.REFUSED.clear()
    son_wt = "{{Infobox basketball biography}}\n'''Jabari Smith Jr.''' plays.\n"
    client = _client({"Jabari Smith Jr.": ("Jabari Smith Jr.", son_wt)})
    rec, _teams, is_new, *_ = uc.merge_player(db, "Jabari Smith Jr.", client,
                                              {}, set(), 2026)
    assert rec is not None and is_new, "the son did not get his own record"
    assert rec["player"] == "Jabari Smith Jr.", rec["player"]
    assert db.by_name["Jabari Smith"]["career_history"][0]["team"] == \
        "Sioux Falls Skyforce", "the father's career was overwritten"
    print("test_a_jr_is_not_merged_into_the_record_of_a_name_that_folds_to_his PASS")


def test_an_ordinary_redirect_still_merges():
    """The guard must not cost the pipeline its reason for following redirects."""
    import update_careers as uc
    db = _db([{"player": "Alperen Sengun", "status": "nba_active",
               "wikipedia_url": "https://en.wikipedia.org/wiki/Alperen_Sengun",
               "career_history": [{"years": "2021-present", "team": "Houston Rockets"}]}])
    uc.REFUSED.clear()
    wt = "{{Infobox basketball biography}}\n'''Alperen Şengün''' plays.\n"
    client = _client({"Alperen Şengün": ("Alperen Şengün", wt)})
    rec, _teams, is_new, *_ = uc.merge_player(db, "Alperen Şengün", client,
                                              {}, set(), 2026)
    assert rec is not None, "a diacritic redirect was refused"
    assert not is_new, "a diacritic redirect created a duplicate record"
    assert rec["player"] == "Alperen Sengun", rec["player"]
    assert not uc.REFUSED
    print("test_an_ordinary_redirect_still_merges PASS")


if __name__ == "__main__":
    test_a_redirect_may_respell_a_name_but_not_change_the_person()
    test_two_articles_that_differ_by_a_suffix_are_two_people()
    test_the_ladder_tries_the_disambiguated_title()
    test_a_son_is_never_handed_his_fathers_article()
    test_the_ladder_finds_the_son_when_his_article_exists()
    test_a_jr_is_not_merged_into_the_record_of_a_name_that_folds_to_his()
    test_an_ordinary_redirect_still_merges()
    print("\nALL WRONG-ARTICLE GUARD TESTS PASS")
