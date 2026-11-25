"""Tests for the Wikidata birth/death fetcher, against saved fixtures.

No network: every response comes from tests/fixtures (see the README there).

Run:  python3 scripts/test_fetch_bio_wikidata.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_bio_wikidata as fb  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _args(tmp: Path, **over) -> argparse.Namespace:
    base = dict(limit=None, refresh_days=fb.REFRESH_DAYS,
                refresh_limit=fb.REFRESH_LIMIT, full=False, reapply=False,
                delay=0.0, fixtures=FIXTURES, skip_nba=False, dry_run=False,
                npd_bio=None, no_nba_com=True,
                nba_com_cache=tmp / "nba_com_birthdates.json",
                careers=FIXTURES / "careers.json",
                out=tmp / "player_bio.json", review=tmp / "bio_needs_review.json")
    base.update(over)
    return argparse.Namespace(**base)


def _run(tmp: Path, **over):
    args = _args(tmp, **over)
    return args, fb.run(args, fb.FixtureTransport(FIXTURES))


# --- date precision ---------------------------------------------------------
def test_date_precision_is_kept():
    assert fb.format_date("1984-12-30T00:00:00Z", "11") == "1984-12-30"
    assert fb.format_date("1994-06-01T00:00:00Z", "10") == "1994-06"
    assert fb.format_date("1922-01-01T00:00:00Z", "9") == "1922"
    assert fb.format_date("1920-01-01T00:00:00Z", "8") is None   # decade
    assert fb.format_date("", "11") is None
    assert fb.format_date("-0044-03-15T00:00:00Z", "11") is None  # BCE
    print("test_date_precision_is_kept PASS")


def test_a_year_only_date_never_becomes_january_first():
    """Early Player has no nba-player-data row, so Wikidata's year-only
    date stands -- and stays a year."""
    with tempfile.TemporaryDirectory() as d:
        _args_, res = _run(Path(d))
        rec = res["bio"]["Early Player"]
        assert rec["birth_date"] == "1922"
        assert rec["death_date"] == "1994-06"
        assert rec["source"] == "wikidata"
    print("test_a_year_only_date_never_becomes_january_first PASS")


# --- resolution -------------------------------------------------------------
def test_titles_resolve_through_redirects_and_skip_missing_articles():
    got = fb.resolve_qids(fb.FixtureTransport(FIXTURES),
                          ["LeBron James", "Redirected Player", "Unknown Player"])
    assert got["LeBron James"] == "Q36159", got
    assert got["Redirected Player"] == "Q999002", got
    assert "Unknown Player" not in got, got
    print("test_titles_resolve_through_redirects_and_skip_missing_articles PASS")


def test_a_bare_item_id_is_not_a_place_name():
    """The label service echoes the item ID when an item has no English
    label; that is a missing place, not a place called 'Q7654321'."""
    with tempfile.TemporaryDirectory() as d:
        _args_, res = _run(Path(d))
        assert res["bio"]["Redirected Player"]["birth_place"] == ""
    print("test_a_bare_item_id_is_not_a_place_name PASS")


# --- the identity gate ------------------------------------------------------
def test_the_gate_reads_occupation_and_sport():
    assert fb.passes_gate({"basketball": True})
    assert not fb.passes_gate({"basketball": False})
    assert not fb.passes_gate(None)
    print("test_the_gate_reads_occupation_and_sport PASS")


def test_a_namesake_loses_his_dates_and_the_right_item_is_found():
    """Namesake Player's article resolves to an ice hockey player who died in
    1992. Nothing of his may survive, and the basketball player of that name
    born in 2006 takes his place."""
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        rec = res["bio"]["Namesake Player"]
        assert rec["birth_date"] == "2006-08-13", rec
        assert rec["death_date"] is None, rec          # the hockey player died
        assert rec["birth_place"] == "Chattanooga", rec
        assert rec["death_place"] == "", rec
        assert "Bracebridge" not in json.dumps(rec), rec
        assert rec["wikidata_id"] == "Q999005", rec    # the replacement
        assert rec["rejected_wikidata_id"] == "Q999004", rec

        review = json.loads(args.review.read_text(encoding="utf-8"))
        row = [r for r in review["wrong_entity"]
               if r["player"] == "Namesake Player"][0]
        assert row["replacement_found"] is True
        assert row["resolved_to"]["description"].startswith("Canadian ice hockey")
        assert row["replacement"]["wikidata_id"] == "Q999005"
    print("test_a_namesake_loses_his_dates_and_the_right_item_is_found PASS")


def test_a_namesake_with_no_replacement_keeps_nothing_but_the_nba_date():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        rec = res["bio"]["Lost Player"]
        assert rec["birth_date"] == "1949-01-21", rec   # nba-player-data
        assert rec["source"] == "nba-player-data", rec
        assert rec["wikidata_id"] == "", rec
        assert rec["rejected_wikidata_id"] == "Q999006", rec
        assert rec["birth_place"] == "" and rec["death_date"] is None, rec

        review = json.loads(args.review.read_text(encoding="utf-8"))
        row = [r for r in review["wrong_entity"]
               if r["player"] == "Lost Player"][0]
        assert row["replacement_found"] is False
        assert row["replacement"] is None
    print("test_a_namesake_with_no_replacement_keeps_nothing_but_the_nba_date PASS")


def test_an_ambiguous_search_is_refused():
    """Two basketball players of the same name born the same year is a coin
    toss, not an answer."""
    twins = [{"qid": "Q1", "birth_date": "1998-06-29"},
             {"qid": "Q2", "birth_date": "1998-01-03"}]
    assert fb.pick_replacement(twins, "1998-06-29") is None
    assert fb.pick_replacement(twins[:1], "1998-06-29")["qid"] == "Q1"
    # ... and a candidate born in the wrong decade is not the man either.
    assert fb.pick_replacement([{"qid": "Q3", "birth_date": "1968-02-02"}],
                               "2006-08-13") is None
    # With no NBA birth date there is nothing to disambiguate on.
    assert fb.pick_replacement(twins[:1], "") is None
    print("test_an_ambiguous_search_is_refused PASS")


def test_the_wikipedia_url_of_a_namesake_is_reported():
    """Their club history was scraped from that same article, so it is
    suspect too -- but the career data is not touched here."""
    with tempfile.TemporaryDirectory() as d:
        args, _res = _run(Path(d))
        review = json.loads(args.review.read_text(encoding="utf-8"))
        flagged = {r["player"]: r for r in review["wikipedia_url_wrong_person"]}
        assert set(flagged) == {"Namesake Player", "Lost Player"}, flagged
        assert flagged["Lost Player"]["wikipedia_url"].endswith("Lost_Player")
        assert review["counts"]["wikipedia_url_wrong_person"] == 2
    print("test_the_wikipedia_url_of_a_namesake_is_reported PASS")


class _FlakySearch(fb.FixtureTransport):
    """Everything answers except the replacement search."""

    def sparql(self, query):
        if "rdfs:label" in query:
            raise fb.BioFetchError("query timed out")
        return super().sparql(query)


def test_a_failed_search_costs_the_replacements_not_the_run():
    """The gate has already done the job that matters -- the namesake's dates
    are out. A label search that times out must not empty the file."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args = _args(tmp)
        res = fb.run(args, _FlakySearch(FIXTURES))
        rec = res["bio"]["Namesake Player"]
        assert rec["wikidata_id"] == "", rec        # no replacement invented
        assert rec["rejected_wikidata_id"] == "Q999004", rec
        assert rec["birth_date"] == "2006-08-13", rec
        assert rec["birth_place"] == "", rec
        assert res["review"]["counts"]["wrong_entity"] == 2
        assert args.out.exists()
    print("test_a_failed_search_costs_the_replacements_not_the_run PASS")


# --- source precedence ------------------------------------------------------
def test_the_nba_date_decides_the_birth_date():
    with tempfile.TemporaryDirectory() as d:
        _args_, res = _run(Path(d))
        rec = res["bio"]["Redirected Player"]
        assert rec["birth_date"] == "1990-07-04"       # NBA, not 1991-07-04
        assert rec["wikidata_birth_date"] == "1991-07-04"
        assert rec["source"] == "nba-player-data"
    print("test_the_nba_date_decides_the_birth_date PASS")


def test_a_full_date_is_never_traded_for_a_coarser_one():
    """A Wikidata day inside a year-only NBA date is the better answer; a
    Wikidata year-only date never replaces a full NBA date."""
    item = {"qid": "Q1", "birth_date": "1950-03-04", "death_date": None,
            "birth_place": "Here", "death_place": ""}
    rec = fb.compose(item, "1950", checked="2026-11-25")
    assert rec["birth_date"] == "1950-03-04" and rec["source"] == "wikidata"
    rec = fb.compose(dict(item, birth_date="1950"), "1950-03-04",
                     checked="2026-11-25", nba_source="nba.com")
    assert rec["birth_date"] == "1950-03-04" and rec["source"] == "nba.com"
    assert "wikidata_birth_date" not in rec
    print("test_a_full_date_is_never_traded_for_a_coarser_one PASS")


def test_nba_com_fills_what_bio_json_lacks_and_a_refusal_is_not_fatal():
    import nba_birthdates as nb
    players = [{"player": "Has Row"}, {"player": "No Row"},
               {"player": "Blocked Guy"}, {"player": "No Id"}]
    rows = [{"PLAYER": "Has Row", "BIRTHDAY": "1/2/2000"}]
    ids = {"no row": "11", "blocked guy": "22"}

    def fetch(pid):
        if pid == "22":
            raise OSError("timed out")
        return "2001-05-06"
    dates, cache = nb.build(players, rows, ids=ids, fetch=fetch,
                            today="2026-11-25")
    assert dates.date("Has Row") == "2000-01-02"
    assert dates.source("Has Row") == "nba-player-data"
    assert dates.date("No Row") == "2001-05-06"
    assert dates.source("No Row") == "nba.com"
    assert dates.date("Blocked Guy") == "" and "Blocked Guy" in dates.not_fetched
    assert dates.date("No Id") == ""
    assert cache["No Row"] == {"person_id": "11", "birth_date": "2001-05-06",
                               "checked": "2026-11-25"}
    # The saved answer is used next time without asking again.
    again, _ = nb.build(players, rows, cache=cache, ids={}, fetch=None)
    assert again.source("No Row") == "nba.com"
    print("test_nba_com_fills_what_bio_json_lacks_and_a_refusal_is_not_fatal PASS")


def test_bio_json_names_match_strictly():
    import nba_birthdates as nb
    rows = [{"PLAYER": "Bobby Jones", "BIRTHDAY": "12/18/1951"},
            {"PLAYER": "Bobby Jones (1984)", "BIRTHDAY": "1/9/1984"},
            {"PLAYER": "A.J. Green", "BIRTHDAY": "9/27/1999"},
            {"PLAYER": "Bad Date", "BIRTHDAY": "13/45/1999"}]
    players = [{"player": "Bobby Jones"}, {"player": "Bobby Jones (1984)"},
               {"player": "AJ Green"}, {"player": "Bad Date"},
               {"player": "Bobby Jones Jr."}]
    got = nb.match_npd(players, rows)
    assert got == {"Bobby Jones": "1951-12-18",
                   "Bobby Jones (1984)": "1984-01-09",
                   "AJ Green": "1999-09-27"}, got
    # The same name with a birth date his career rules out is a namesake's row.
    rejected = []
    got = nb.match_npd([{"player": "Old Guard",
                         "career_history": [{"years": "1972-1975"}]}],
                       [{"PLAYER": "Old Guard", "BIRTHDAY": "5/4/2000"}], rejected)
    assert got == {} and rejected[0]["nba_birth_date"] == "2000-05-04", rejected
    # ... unless his own Wikidata item agrees on the year (a youth club at the
    # top of his career, not a namesake).
    nba = nb.NbaBirthDates({}, rejected=rejected)
    assert fb.nba_date_for(nba, "Old Guard", {"birth_date": "2000-05-04"}) == \
        ("2000-05-04", "nba-player-data")
    assert fb.nba_date_for(nba, "Old Guard", {"birth_date": "1950-04-17"}) == ("", "")
    assert fb.nba_date_for(nba, "Old Guard", None) == ("", "")
    # His article's title names a birth year: a row years off it is not his.
    rejected = []
    got = nb.match_npd([{"player": "Titled Guy", "wikipedia_url":
                         "https://en.wikipedia.org/wiki/Titled_Guy_(basketball,_born_1942)"}],
                       [{"PLAYER": "Titled Guy", "BIRTHDAY": "2/23/1950"}], rejected)
    assert got == {} and "title says born 1942" in rejected[0]["why"], rejected
    print("test_bio_json_names_match_strictly PASS")


def test_a_wrong_year_costs_the_item_its_places():
    """Same occupation, but a birth year years out: the places on that item
    are somebody else's."""
    far = {"qid": "Q9", "birth_date": "1963-04-24", "death_date": "1990-01-01",
           "birth_place": "Split", "death_place": "Zagreb"}
    rec = fb.compose(far, "1967-04-24", checked="2026-09-24")
    assert rec["birth_date"] == "1967-04-24"
    assert rec["birth_place"] == "" and rec["death_place"] == ""
    assert rec["death_date"] is None
    near = dict(far, birth_date="1967-11-02")
    rec = fb.compose(near, "1967-04-24", checked="2026-09-24")
    assert rec["birth_place"] == "Split"
    print("test_a_wrong_year_costs_the_item_its_places PASS")


def test_a_birth_year_within_a_year_is_the_same_man():
    assert fb.birth_years_agree("1962-09-19", "1962-07-19")
    assert fb.birth_years_agree("1920-10-17", "1921-10-17")
    assert not fb.birth_years_agree("1903-07-03", "2006-08-13")
    assert fb.birth_years_agree(None, "2006-08-13")   # nothing to compare
    assert fb.birth_years_agree("1903-07-03", "")
    print("test_a_birth_year_within_a_year_is_the_same_man PASS")


# --- the full run -----------------------------------------------------------
def test_a_full_run_writes_the_expected_records():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        bio = json.loads(args.out.read_text(encoding="utf-8"))
        assert set(bio) == {"LeBron James", "Kobe Bryant", "Wilt Chamberlain",
                            "Early Player", "Redirected Player",
                            "Unknown Player", "Namesake Player",
                            "Lost Player"}, sorted(bio)
        kobe = bio["Kobe Bryant"]
        assert kobe["birth_date"] == "1978-08-23"
        assert kobe["death_date"] == "2020-01-26"
        assert kobe["birth_place"] == "Philadelphia"
        assert kobe["death_place"] == "Calabasas"
        assert kobe["wikidata_id"] == "Q41421"
        assert kobe["source"] == "nba-player-data"
        assert kobe["checked"]
        assert bio["LeBron James"]["death_date"] is None
        cov = res["coverage"]
        assert cov == {"players": 8, "with_birth_date": 8,
                       "with_death_date": 3, "with_birth_place": 5,
                       "with_death_place": 2, "year_only_birth_date": 1,
                       "from_nba_player_data": 7, "from_nba_com": 0,
                       "rejected_wikidata_item": 2}, cov
    print("test_a_full_run_writes_the_expected_records PASS")


def test_a_player_with_no_wikidata_item_is_recorded_not_invented():
    """No item means no places -- but nba-player-data still has his
    birth date, and he still gets a record, or every run from here pays to
    look him up again."""
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        rec = res["bio"]["Unknown Player"]
        assert rec["wikidata_id"] == ""
        assert rec["birth_date"] == "1995-03-03"
        assert rec["source"] == "nba-player-data"
        review = json.loads(args.review.read_text(encoding="utf-8"))
        assert [r["player"] for r in review["still_missing"]] == [], review
    print("test_a_player_with_no_wikidata_item_is_recorded_not_invented PASS")


def test_a_player_neither_source_knows_lands_in_still_missing():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "player_bio.json").write_text(json.dumps({
            "Dateless Player": {"birth_date": None, "death_date": None,
                                "birth_place": "", "death_place": "",
                                "wikidata_id": "Q999003", "source": "wikidata",
                                "checked": fb._today()}}), encoding="utf-8")
        args, _res = _run(tmp)
        review = json.loads(args.review.read_text(encoding="utf-8"))
        names = [r["player"] for r in review["still_missing"]]
        assert names == ["Dateless Player"], review["still_missing"]
        assert review["counts"]["still_missing"] == 1
    print("test_a_player_neither_source_knows_lands_in_still_missing PASS")


# --- cross-check ------------------------------------------------------------
def test_the_review_reports_a_real_disagreement_and_what_was_used():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        review = json.loads(args.review.read_text(encoding="utf-8"))
        names = [r["player"] for r in review["date_disagreement"]]
        assert names == ["Redirected Player"], review["date_disagreement"]
        row = review["date_disagreement"][0]
        assert row["wikidata"] == "1991-07-04"
        assert row["nba_birth_date"] == "1990-07-04"
        assert row["nba_source"] == "nba-player-data"
        assert row["used"] == "1990-07-04"
        assert row["used_source"] == "nba-player-data"
        assert row["days_apart"] == 365
        assert row["places_kept"] is True
        assert review["counts"]["date_disagreement"] == 1
        assert res["review"]["cross_check_source"].startswith("nba-player-data")
    print("test_the_review_reports_a_real_disagreement_and_what_was_used PASS")


def test_an_empty_birthday_is_not_a_date():
    """bio.json leaves an unknown BIRTHDAY empty; that is no date at all,
    not one to compare a real date against."""
    import nba_birthdates as nb
    rows = json.loads((FIXTURES / "npd_bio.json").read_text(encoding="utf-8"))
    got = nb.match_npd([{"player": "NA Player"}, {"player": "Kobe Bryant"}], rows)
    assert "NA Player" not in got, got
    assert got["Kobe Bryant"] == "1978-08-23"
    assert nb.parse_birthday("") == "" and nb.parse_birthday("2/30/1990") == ""
    print("test_an_empty_birthday_is_not_a_date PASS")


def test_a_year_only_date_agrees_with_a_full_date_in_that_year():
    assert fb.same_date("1922", "1922-01-14")
    assert fb.same_date("1978-08", "1978-08-23")
    assert not fb.same_date("1922", "1923-01-14")
    assert not fb.same_date("1978-08-23", "1978-08-24")
    assert fb.same_date("", "1978-08-24")      # nothing to disagree with
    print("test_a_year_only_date_agrees_with_a_full_date_in_that_year PASS")


# --- offline re-apply -------------------------------------------------------
def test_reapply_re_runs_the_rules_without_the_network():
    """The strongest signal a stored record still carries is its birth year.
    Years off the NBA date means the item was a namesake, so its places go
    and the NBA date stands."""
    import nba_birthdates as nb
    nba = nb.NbaBirthDates({"Ace Bailey": ("2006-08-13", nb.SRC_NPD),
                            "Alfredrick Hughes": ("1962-07-19", nb.SRC_NPD),
                            "Disputed Player": ("1918-10-17", nb.SRC_NPD)})
    bio = {
        "Ace Bailey": {"birth_date": "1903-07-03", "death_date": "1992-04-07",
                       "birth_place": "Bracebridge", "death_place": "Toronto",
                       "wikidata_id": "Q323039", "source": "wikidata",
                       "checked": "2026-09-01"},
        "Alfredrick Hughes": {"birth_date": "1962-09-19", "death_date": None,
                              "birth_place": "Chicago", "death_place": "",
                              "wikidata_id": "Q3611535", "source": "wikidata",
                              "checked": "2026-09-01"},
        # Two years apart on a player born in the 1920s: a disputed date,
        # not a namesake, so the item ID survives.
        "Disputed Player": {"birth_date": "1920-10-17", "death_date": None,
                            "birth_place": "Boston", "death_place": "",
                            "wikidata_id": "Q3624026", "source": "wikidata",
                            "checked": "2026-09-01"},
        "No Nba Player": {"birth_date": "1955", "death_date": None,
                           "birth_place": "Riga", "death_place": "",
                           "wikidata_id": "Q123", "source": "wikidata",
                           "checked": "2026-09-01"},
    }
    out, details = fb.reapply(bio, nba)

    ace = out["Ace Bailey"]
    assert ace["birth_date"] == "2006-08-13"
    assert ace["death_date"] is None and ace["birth_place"] == ""
    assert ace["wikidata_id"] == "" and ace["rejected_wikidata_id"] == "Q323039"
    assert "Ace Bailey" in details

    # A few months apart is the same man: he keeps his item and his home town.
    al = out["Alfredrick Hughes"]
    assert al["birth_date"] == "1962-07-19"
    assert al["wikidata_birth_date"] == "1962-09-19"
    assert al["birth_place"] == "Chicago"
    assert al["wikidata_id"] == "Q3611535"

    dp = out["Disputed Player"]
    assert dp["wikidata_id"] == "Q3624026", dp
    assert "rejected_wikidata_id" not in dp, dp
    assert dp["birth_date"] == "1918-10-17"           # NBA still decides
    assert dp["wikidata_birth_date"] == "1920-10-17"
    assert dp["birth_place"] == "", dp                # ... but not his town
    assert "Disputed Player" not in details

    # Nothing to compare against leaves the record as it was.
    nn = out["No Nba Player"]
    assert nn["birth_date"] == "1955" and nn["birth_place"] == "Riga"
    assert nn["source"] == "wikidata"

    # The death sweep is not reset: the stamps the records came with survive.
    assert {r["checked"] for r in out.values()} == {"2026-09-01"}
    print("test_reapply_re_runs_the_rules_without_the_network PASS")


# --- incremental ------------------------------------------------------------
def test_a_second_run_fetches_nobody():
    """FixtureTransport raises when a run asks for a response that was never
    saved, so this passing IS the assertion that no second fetch happened."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _run(tmp)
        before = json.loads((tmp / "player_bio.json").read_text(encoding="utf-8"))
        _args_, res = _run(tmp)          # the fixtures allow exactly one pass
        after = res["bio"]
        assert {k: v["birth_date"] for k, v in after.items()} == \
               {k: v["birth_date"] for k, v in before.items()}
    print("test_a_second_run_fetches_nobody PASS")


def test_a_second_run_keeps_the_wrong_entity_list_whole():
    """An incremental run re-reads nobody, so the players an earlier run
    rejected must carry over from the review file rather than vanish."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _run(tmp)
        args, _res = _run(tmp)
        review = json.loads(args.review.read_text(encoding="utf-8"))
        rows = {r["player"]: r for r in review["wrong_entity"]}
        assert set(rows) == {"Namesake Player", "Lost Player"}, rows
        got = rows["Namesake Player"]["resolved_to"]
        assert got["description"].startswith("Canadian ice hockey"), got
        assert review["counts"]["wikipedia_url_wrong_person"] == 2
    print("test_a_second_run_keeps_the_wrong_entity_list_whole PASS")


def test_a_stale_living_record_comes_back_for_a_death_check():
    players = json.loads((FIXTURES / "careers.json").read_text(encoding="utf-8"))
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    bio = {"LeBron James": {"birth_date": "1984-12-30", "death_date": None,
                            "checked": old, "wikidata_id": "Q36159"},
           "Kobe Bryant": {"birth_date": "1978-08-23",
                           "death_date": "2020-01-26", "checked": old,
                           "wikidata_id": "Q41421"}}
    new, due = fb.select_targets(players, bio, full=False, limit=None,
                                 refresh_days=7, refresh_limit=100)
    assert [p["player"] for p in due] == ["LeBron James"], due
    # Everyone not on file yet is new; the player already known to be dead is
    # not re-read.
    assert "Kobe Bryant" not in [p["player"] for p in new]
    fresh = dict(bio)
    fresh["LeBron James"] = {**bio["LeBron James"], "checked": fb._today()}
    _new2, due2 = fb.select_targets(players, fresh, full=False, limit=None,
                                    refresh_days=7, refresh_limit=100)
    assert due2 == [], due2
    print("test_a_stale_living_record_comes_back_for_a_death_check PASS")


def test_the_refresh_limit_spreads_the_sweep():
    players = json.loads((FIXTURES / "careers.json").read_text(encoding="utf-8"))
    old = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    older = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    bio = {"LeBron James": {"death_date": None, "checked": old},
           "Wilt Chamberlain": {"death_date": None, "checked": older}}
    _new, due = fb.select_targets(players, bio, full=False, limit=None,
                                  refresh_days=7, refresh_limit=1)
    assert [p["player"] for p in due] == ["Wilt Chamberlain"], due
    print("test_the_refresh_limit_spreads_the_sweep PASS")


def test_full_rewrites_a_record_from_scratch():
    """--full is what to run after the rules change: a record carrying a
    namesake's places must come back clean, not merged."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "player_bio.json").write_text(json.dumps({
            "Namesake Player": {"birth_date": "1903-07-03",
                                "death_date": "1992-04-07",
                                "birth_place": "Bracebridge",
                                "death_place": "Toronto",
                                "wikidata_id": "", "source": "wikidata",
                                "checked": "2020-01-01"}}), encoding="utf-8")
        _args_, res = _run(tmp, full=True)
        rec = res["bio"]["Namesake Player"]
        assert rec["birth_date"] == "2006-08-13", rec
        assert rec["death_date"] is None, rec
        assert rec["birth_place"] == "Chattanooga", rec
        assert rec["death_place"] == "", rec
    print("test_full_rewrites_a_record_from_scratch PASS")


# --- failure is loud --------------------------------------------------------
class DeadTransport:
    def get_json(self, url, params):
        raise fb.BioFetchError("connection refused")

    def sparql(self, query):
        raise fb.BioFetchError("connection refused")

    def get_text(self, url):
        raise fb.BioFetchError("connection refused")


def test_an_unreachable_api_writes_nothing_and_raises():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        args = _args(tmp)
        try:
            fb.run(args, DeadTransport())
        except fb.BioFetchError as exc:
            assert "connection refused" in str(exc)
        else:
            raise AssertionError("an unreachable API must not be swallowed")
        assert not args.out.exists(), "nothing may be written on a failed run"
        assert not args.review.exists()
    print("test_an_unreachable_api_writes_nothing_and_raises PASS")


def test_a_missing_fixture_is_an_error_not_an_empty_answer():
    with tempfile.TemporaryDirectory() as d:
        try:
            fb.run(_args(Path(d), careers=FIXTURES / "careers.json"),
                   fb.FixtureTransport(Path(d)))
        except fb.BioFetchError:
            pass
        else:
            raise AssertionError("a missing fixture must fail the run")
    print("test_a_missing_fixture_is_an_error_not_an_empty_answer PASS")


def test_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d), dry_run=True)
        assert res["coverage"]["with_birth_date"] == 8
        assert not args.out.exists()
    print("test_dry_run_writes_nothing PASS")


if __name__ == "__main__":
    test_date_precision_is_kept()
    test_a_year_only_date_never_becomes_january_first()
    test_titles_resolve_through_redirects_and_skip_missing_articles()
    test_a_bare_item_id_is_not_a_place_name()
    test_the_gate_reads_occupation_and_sport()
    test_a_namesake_loses_his_dates_and_the_right_item_is_found()
    test_a_namesake_with_no_replacement_keeps_nothing_but_the_nba_date()
    test_an_ambiguous_search_is_refused()
    test_the_wikipedia_url_of_a_namesake_is_reported()
    test_a_failed_search_costs_the_replacements_not_the_run()
    test_the_nba_date_decides_the_birth_date()
    test_a_full_date_is_never_traded_for_a_coarser_one()
    test_nba_com_fills_what_bio_json_lacks_and_a_refusal_is_not_fatal()
    test_bio_json_names_match_strictly()
    test_a_wrong_year_costs_the_item_its_places()
    test_a_birth_year_within_a_year_is_the_same_man()
    test_a_full_run_writes_the_expected_records()
    test_a_player_with_no_wikidata_item_is_recorded_not_invented()
    test_a_player_neither_source_knows_lands_in_still_missing()
    test_the_review_reports_a_real_disagreement_and_what_was_used()
    test_an_empty_birthday_is_not_a_date()
    test_a_year_only_date_agrees_with_a_full_date_in_that_year()
    test_reapply_re_runs_the_rules_without_the_network()
    test_a_second_run_fetches_nobody()
    test_a_second_run_keeps_the_wrong_entity_list_whole()
    test_a_stale_living_record_comes_back_for_a_death_check()
    test_the_refresh_limit_spreads_the_sweep()
    test_full_rewrites_a_record_from_scratch()
    test_an_unreachable_api_writes_nothing_and_raises()
    test_a_missing_fixture_is_an_error_not_an_empty_answer()
    test_dry_run_writes_nothing()
    print("\nall bio fetcher tests PASS")
