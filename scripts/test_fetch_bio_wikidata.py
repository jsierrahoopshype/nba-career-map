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
                refresh_limit=fb.REFRESH_LIMIT, full=False, delay=0.0,
                fixtures=FIXTURES, skip_bref=False, dry_run=False,
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
    with tempfile.TemporaryDirectory() as d:
        _args_, res = _run(Path(d))
        assert res["bio"]["Early Player"]["birth_date"] == "1922"
        assert res["bio"]["Early Player"]["death_date"] == "1994-06"
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


# --- the full run -----------------------------------------------------------
def test_a_full_run_writes_the_expected_records():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        bio = json.loads(args.out.read_text(encoding="utf-8"))
        assert set(bio) == {"LeBron James", "Kobe Bryant", "Wilt Chamberlain",
                            "Early Player", "Redirected Player",
                            "Unknown Player"}, sorted(bio)
        kobe = bio["Kobe Bryant"]
        assert kobe["birth_date"] == "1978-08-23"
        assert kobe["death_date"] == "2020-01-26"
        assert kobe["birth_place"] == "Philadelphia"
        assert kobe["death_place"] == "Calabasas"
        assert kobe["wikidata_id"] == "Q41421"
        assert kobe["source"] == "wikidata"
        assert kobe["checked"]
        assert bio["LeBron James"]["death_date"] is None
        cov = res["coverage"]
        assert cov == {"players": 6, "with_birth_date": 5, "with_death_date": 3,
                       "with_birth_place": 4, "with_death_place": 2,
                       "year_only_birth_date": 1}, cov
    print("test_a_full_run_writes_the_expected_records PASS")


def test_a_player_with_no_wikidata_item_is_recorded_not_invented():
    """No item means no facts -- but the player still gets a record, or every
    run from here pays to look him up again and he never reaches the review
    file."""
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        rec = res["bio"]["Unknown Player"]
        assert rec["birth_date"] is None and rec["wikidata_id"] == ""
        assert rec["source"] == "unresolved"
        review = json.loads(args.review.read_text(encoding="utf-8"))
        missing = {r["player"]: r for r in review["missing_birth_date"]}
        assert "Unknown Player" in missing, review["missing_birth_date"]
        assert missing["Unknown Player"]["basketball_reference"] == "1995-03-03"
    print("test_a_player_with_no_wikidata_item_is_recorded_not_invented PASS")


# --- cross-check ------------------------------------------------------------
def test_the_cross_check_reports_a_real_disagreement():
    with tempfile.TemporaryDirectory() as d:
        args, res = _run(Path(d))
        review = json.loads(args.review.read_text(encoding="utf-8"))
        names = [r["player"] for r in review["mismatches"]]
        assert names == ["Redirected Player"], review["mismatches"]
        row = review["mismatches"][0]
        assert row["wikidata"] == "1991-07-04"
        assert row["basketball_reference"] == "1990-07-04"
        assert review["counts"]["mismatches"] == 1
        assert res["review"]["cross_check_source"].startswith("https://raw.")
    print("test_the_cross_check_reports_a_real_disagreement PASS")


def test_an_unknown_bbref_birth_date_is_not_a_date():
    """The CSV writes an unknown date as 'NA'; comparing a real date against
    that string would report every one of them as a disagreement."""
    class T:
        def get_text(self, url):
            return (FIXTURES / "bref.csv").read_text(encoding="utf-8")
    bref = fb.load_bref(T())
    assert "na player" not in bref, bref
    assert bref[fb.normkey("Kobe Bryant")] == "1978-08-23"
    print("test_an_unknown_bbref_birth_date_is_not_a_date PASS")


def test_a_year_only_date_agrees_with_a_full_date_in_that_year():
    assert fb.same_date("1922", "1922-01-14")
    assert fb.same_date("1978-08", "1978-08-23")
    assert not fb.same_date("1922", "1923-01-14")
    assert not fb.same_date("1978-08-23", "1978-08-24")
    assert fb.same_date("", "1978-08-24")      # nothing to disagree with
    print("test_a_year_only_date_agrees_with_a_full_date_in_that_year PASS")


def test_a_player_with_no_wikidata_birth_date_lands_in_the_review_file():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        # A record that resolved to an item but carries no birth date.
        (tmp / "player_bio.json").write_text(json.dumps({
            "Dateless Player": {"birth_date": None, "death_date": None,
                                "birth_place": "", "death_place": "",
                                "wikidata_id": "Q999003", "source": "wikidata",
                                "checked": fb._today()}}), encoding="utf-8")
        args, _res = _run(tmp)
        review = json.loads(args.review.read_text(encoding="utf-8"))
        names = [r["player"] for r in review["missing_birth_date"]]
        assert "Dateless Player" in names, review["missing_birth_date"]
    print("test_a_player_with_no_wikidata_birth_date_lands_in_the_review_file PASS")


# --- incremental ------------------------------------------------------------
def test_a_second_run_fetches_nobody():
    """FixtureTransport raises when a run asks for a response that was never
    saved, so this passing IS the assertion that no second fetch happened."""
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _run(tmp)
        before = json.loads((tmp / "player_bio.json").read_text(encoding="utf-8"))
        _args_, res = _run(tmp)          # the fixtures allow exactly one of each
        after = res["bio"]
        assert {k: v["birth_date"] for k, v in after.items()} == \
               {k: v["birth_date"] for k, v in before.items()}
    print("test_a_second_run_fetches_nobody PASS")


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


# --- nothing is overwritten silently ---------------------------------------
def test_a_changed_birth_date_is_reported_not_applied():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "player_bio.json").write_text(json.dumps({
            "Kobe Bryant": {"birth_date": "1978-08-22", "death_date": None,
                            "birth_place": "Philadelphia", "death_place": "",
                            "wikidata_id": "Q41421", "source": "wikidata",
                            "checked": "2020-01-01"}}), encoding="utf-8")
        args, res = _run(tmp)
        assert res["bio"]["Kobe Bryant"]["birth_date"] == "1978-08-22"
        # ... and the death, which is what the re-check exists for, IS taken.
        assert res["bio"]["Kobe Bryant"]["death_date"] == "2020-01-26"
        review = json.loads(args.review.read_text(encoding="utf-8"))
        held = review["changed_upstream"]
        assert [r["field"] for r in held] == ["birth_date"], held
        assert held[0]["stored"] == "1978-08-22"
        assert held[0]["wikidata"] == "1978-08-23"
    print("test_a_changed_birth_date_is_reported_not_applied PASS")


def test_full_lets_wikidata_win():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "player_bio.json").write_text(json.dumps({
            "Kobe Bryant": {"birth_date": "1978-08-22", "death_date": None,
                            "birth_place": "", "death_place": "",
                            "wikidata_id": "Q41421", "source": "wikidata",
                            "checked": "2020-01-01"}}), encoding="utf-8")
        _args_, res = _run(tmp, full=True)
        assert res["bio"]["Kobe Bryant"]["birth_date"] == "1978-08-23"
    print("test_full_lets_wikidata_win PASS")


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
        assert res["coverage"]["with_birth_date"] == 5
        assert not args.out.exists()
    print("test_dry_run_writes_nothing PASS")


if __name__ == "__main__":
    test_date_precision_is_kept()
    test_a_year_only_date_never_becomes_january_first()
    test_titles_resolve_through_redirects_and_skip_missing_articles()
    test_a_bare_item_id_is_not_a_place_name()
    test_a_full_run_writes_the_expected_records()
    test_a_player_with_no_wikidata_item_is_recorded_not_invented()
    test_the_cross_check_reports_a_real_disagreement()
    test_an_unknown_bbref_birth_date_is_not_a_date()
    test_a_year_only_date_agrees_with_a_full_date_in_that_year()
    test_a_player_with_no_wikidata_birth_date_lands_in_the_review_file()
    test_a_second_run_fetches_nobody()
    test_a_stale_living_record_comes_back_for_a_death_check()
    test_the_refresh_limit_spreads_the_sweep()
    test_a_changed_birth_date_is_reported_not_applied()
    test_full_lets_wikidata_win()
    test_an_unreachable_api_writes_nothing_and_raises()
    test_a_missing_fixture_is_an_error_not_an_empty_answer()
    test_dry_run_writes_nothing()
