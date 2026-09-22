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
        # letters NFKD will not take apart, which cost Žižić his surname
        ("Ante Zizic", "Ante Žižić"),
        ("Dino Radja", "Dino Rađa"),
        ("Omer Asik", "Ömer Aşık"),
        ("Petur Gudmundsson", "Pétur Guðmundsson"),
        ("Sasa Djordjevic", "Aleksandar Đorđević"),
        # a name added, dropped, or written the other way round
        ("Enes Kanter", "Enes Kanter Freedom"),
        ("Horacio Llamas Grey", "Horacio Llamas"),
        ("Hansen Yang", "Yang Hansen"),
        # one romanisation against another
        ("Sergey Monya", "Sergei Monia"),
        ("Serguei Bazarevitch", "Sergei Bazarevich"),
        ("Wayne Englestad", "Wayne Engelstad"),
        # a legal name change, confirmed by hand in KNOWN_RENAMES
        ("Metta World Peace", "Metta Sandiford-Artest"),
    ]
    refused = [
        ("Scotty Pippen Jr", "Scottie Pippen", "suffix"),
        ("Ron Harper Jr", "Ron Harper", "suffix"),
        ("Gary Payton II", "Gary Payton", "suffix"),
        ("Jabari Smith Jr.", "Jabari Smith", "suffix"),
        ("Michael Wilson", "Michael Gibson (basketball)", "surname"),
        ("Michael Wilson", "Mike Gibson (basketball)", "surname"),
        ("Ben Sheppard", "Ben Shephard (disambiguation)", "disambiguation"),
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


def test_one_typo_is_a_romanisation_and_two_is_a_different_name():
    """The tolerance that lets Monya meet Monia must not let Wilson meet Gibson.

    Wilson and Gibson are two edits apart, which is why the limit is one.
    """
    from wiki_person import _close
    assert _close("monya", "monia") and _close("bazarevitch", "bazarevich")
    assert _close("englestad", "engelstad"), "a transposition is one typo"
    assert _close("gillette", "gallette") and _close("sheppard", "shephard")
    assert not _close("wilson", "gibson")
    assert not _close("jones", "james"), "two edits is a different name"
    assert not _close("cruz", "crus"), "four letters is too short to guess"
    print("test_one_typo_is_a_romanisation_and_two_is_a_different_name PASS")


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


def test_a_sons_own_article_is_not_said_to_belong_to_his_father():
    """The guard's own false positive, caught in production.

    "KJ Martin" resolves to "Kenyon Martin Jr.", which is exactly his record's
    article -- but the name index folds the Jr. away and answered with his
    father, so the fetch was refused and his record stopped updating for three
    days. Article ownership is now judged on the exact title.
    """
    import update_careers as uc
    db = _db([
        {"player": "Kenyon Martin", "status": "retired",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Kenyon_Martin",
         "career_history": [{"years": "2000-2004", "team": "New Jersey Nets"}]},
        {"player": "KJ Martin", "status": "nba_active",
         "wikipedia_url": "https://en.wikipedia.org/wiki/Kenyon_Martin_Jr.",
         "career_history": [{"years": "2020-2023", "team": "Houston Rockets"}]},
    ])
    uc.REFUSED.clear()
    wt = ("{{Infobox basketball biography\n| name = Kenyon Martin Jr.\n"
          "| years1 = 2020\u20132023\n| team1 = [[Houston Rockets]]\n"
          "| years2 = 2025\u2013present\n| team2 = [[Ningbo Rockets]]\n}}")
    client = _client({"KJ Martin": ("Kenyon Martin Jr.", wt)})
    rec, _teams, is_new, *_ = uc.merge_player(db, "KJ Martin", client, {},
                                              set(), 2026)
    assert not uc.REFUSED, uc.REFUSED
    assert rec is not None and not is_new
    assert rec["player"] == "KJ Martin", rec["player"]
    teams = [s["team"] for s in rec["career_history"]]
    assert "Ningbo Rockets" in teams, teams
    assert len(db.by_name["Kenyon Martin"]["career_history"]) == 1, \
        "the father's record was touched"
    print("test_a_sons_own_article_is_not_said_to_belong_to_his_father PASS")


def test_a_record_is_fetched_by_the_article_it_was_built_from():
    """"Joe Smith" is forty people, and Wikipedia says so.

    Fourteen records are keyed on a name that answers with a disambiguation
    page. Asking by name refuses them every run, which freezes the record --
    and one of them, Ben Sheppard, is still playing. The article already worked
    out and stored on the record is asked for first.
    """
    import update_careers as uc
    db = _db([{"player": "Ben Sheppard", "status": "nba_active",
               "wikipedia_url": "https://en.wikipedia.org/wiki/Ben_Sheppard_(basketball)",
               "career_history": [{"years": "2023-present", "team": "Indiana Pacers"}]}])
    uc.REFUSED.clear()
    wt = ("{{Infobox basketball biography\n| name = Ben Sheppard\n"
          "| years1 = 2023\u2013present\n| team1 = [[Indiana Pacers]]\n"
          "| years2 = 2026\n| team2 = [[Indiana Mad Ants]]\n}}")
    client = _client({
        # the name still lands on a British TV presenter's disambiguation page
        "Ben Sheppard": ("Ben Shephard (disambiguation)", "{{Infobox}}\n"),
        "Ben Sheppard (basketball)": ("Ben Sheppard (basketball)", wt),
    })
    rec, _teams, is_new, *_ = uc.merge_player(db, "Ben Sheppard", client, {},
                                              set(), 2026)
    assert not uc.REFUSED, uc.REFUSED
    assert rec is not None and not is_new
    teams = [s["team"] for s in rec["career_history"]]
    assert "Indiana Mad Ants" in teams, teams
    print("test_a_record_is_fetched_by_the_article_it_was_built_from PASS")


def test_a_location_is_not_taken_from_an_article_about_something_else():
    """The place-lookup twin of the wrong-article guard.

    "Libertas" is an Irish political party as well as a dozen Italian
    basketball clubs, and the seed took the party's registered office as the
    club's home: nine stints of Italian basketball plotted in County Galway.
    An extract that says nothing about a sport is refused, and the refusal is
    recorded rather than left as a blank field.
    """
    import update_careers as uc
    import wikipedia_api

    db = _db([])
    party = ("Libertas was an Irish political party founded in 2008. "
             "Registered at Moyne Park, Abbeyknockmoy, County Galway.")
    club = ("Pallacanestro Cantù is an Italian professional basketball club "
            "based in Cantù, Lombardy.")
    answers = {"Libertas Forlì": party, "Cantù": club}
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: answers.get(t, "")
    # Both names reach their own article here, so the redirect guard has
    # nothing to say and this test stays about the sports-club rule alone.
    wikipedia_api.WikipediaClient.resolve_title = lambda self, t: t
    client = wikipedia_api.WikipediaClient(delay=0, max_requests=10)

    uc.REFUSED_PLACE.clear()
    got = db._discover_location("Libertas Forlì", client)
    assert got == {"city": "", "state": "", "country": ""}, got
    assert uc.REFUSED_PLACE and uc.REFUSED_PLACE[0]["team"] == "Libertas Forlì"
    assert "not about a sports club" in uc.REFUSED_PLACE[0]["reason"]

    uc.REFUSED_PLACE.clear()
    got = db._discover_location("Cantù", client)
    assert got["city"] == "Cantù", got
    assert not uc.REFUSED_PLACE, "a real club was refused"
    print("test_a_location_is_not_taken_from_an_article_about_something_else PASS")


def test_a_franchise_era_name_never_reaches_discovery():
    """The guard the sports-club rule cannot be: the article is a real club.

    Wikipedia answers "Chicago Packers" with the Washington Wizards, and the
    extract is about a basketball team, so the sports-club guard passes it
    happily and the 1961 Chicago team acquires a location in Washington. The
    era table is consulted first, so the question is never asked.
    """
    import update_careers as uc

    db = _db([])
    for team, want in (("Chicago Packers", ("Chicago", "Illinois", "USA")),
                       ("New Jersey Nets", ("East Rutherford", "New Jersey", "USA")),
                       ("Vancouver Grizzlies", ("Vancouver", "British Columbia", "Canada"))):
        got = db.locations.get(team) or {}
        assert (got.get("city"), got.get("state"), got.get("country")) == want, (team, got)

    # and enrich_stint takes the pinned record without asking anybody
    class Exploding:
        def get_extract(self, title):
            raise AssertionError(f"discovery ran for {title!r}")

    stint = {"team": "Chicago Packers"}
    db.enrich_stint(stint, Exploding(), {})
    assert stint["city"] == "Chicago" and stint["country"] == "USA", stint
    print("test_a_franchise_era_name_never_reaches_discovery PASS")


def test_a_place_is_not_taken_from_an_article_the_name_redirected_away_to():
    """The guard the sports-club rule cannot be, enforced at the source.

    "Chicago Packers" redirects to "Washington Wizards", whose article is
    about a basketball team and passes the sports-club guard. Only the title
    says anything is wrong.
    """
    import update_careers as uc
    import wikipedia_api

    db = _db([])
    titles = {"Chicago Packers": "Washington Wizards", "Cantù": "Cantù"}
    club = ("Pallacanestro Cantù is an Italian professional basketball club "
            "based in Cantù, Lombardy.")
    wizards = ("The Washington Wizards are an American professional "
               "basketball team based in Washington, D.C.")
    extracts = {"Chicago Packers": wizards, "Cantù": club}
    wikipedia_api.WikipediaClient.resolve_title = lambda self, t: titles.get(t)
    wikipedia_api.WikipediaClient.get_extract = lambda self, t: extracts.get(t, "")
    client = wikipedia_api.WikipediaClient(delay=0, max_requests=10)

    uc.REFUSED_PLACE.clear()
    got = db._discover_location("Chicago Packers", client)
    assert got == {"city": "", "state": "", "country": ""}, got
    assert uc.REFUSED_PLACE and "redirected" in uc.REFUSED_PLACE[0]["reason"]

    uc.REFUSED_PLACE.clear()
    got = db._discover_location("Cantù", client)
    assert got["city"] == "Cantù", got
    assert not uc.REFUSED_PLACE, "a name that reached its own article was refused"

    # A lookup that cannot answer must not refuse: a guard that fails closed
    # on a network error stops discovery altogether.
    assert uc.same_article("Anything", None)
    print("test_a_place_is_not_taken_from_an_article_the_name_redirected_away_to PASS")


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
    test_one_typo_is_a_romanisation_and_two_is_a_different_name()
    test_two_articles_that_differ_by_a_suffix_are_two_people()
    test_the_ladder_tries_the_disambiguated_title()
    test_a_son_is_never_handed_his_fathers_article()
    test_the_ladder_finds_the_son_when_his_article_exists()
    test_a_jr_is_not_merged_into_the_record_of_a_name_that_folds_to_his()
    test_a_sons_own_article_is_not_said_to_belong_to_his_father()
    test_a_record_is_fetched_by_the_article_it_was_built_from()
    test_a_location_is_not_taken_from_an_article_about_something_else()
    test_a_franchise_era_name_never_reaches_discovery()
    test_a_place_is_not_taken_from_an_article_the_name_redirected_away_to()
    test_an_ordinary_redirect_still_merges()
    print("\nALL WRONG-ARTICLE GUARD TESTS PASS")
