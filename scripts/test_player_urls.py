"""Tests for the curated Wikipedia articles and the resolver behind them.

The thing being protected: a player whose name reaches a namesake's article
(David Duke the Klansman, Jack White the guitarist) gets one curated article,
and NOTHING may walk it back -- not the daily career run, not the bio fetcher,
not an unverified guess sitting in the same file.

Run:  python3 scripts/test_player_urls.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_urls  # noqa: E402
import resolve_player_urls as rpu  # noqa: E402

WIKI = "https://en.wikipedia.org/wiki/"


class _Tmp:
    """A temporary overrides file, installed as the module-level default."""

    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "player_url_overrides.json"
        self.path.write_text(json.dumps(self.doc), encoding="utf-8")
        self.saved = player_urls.OVERRIDES
        player_urls.OVERRIDES = self.path
        player_urls.reset_cache()
        return self.path

    def __exit__(self, *exc):
        player_urls.OVERRIDES = self.saved
        player_urls.reset_cache()
        self.dir.cleanup()


# --- the loader -------------------------------------------------------------
def test_only_a_verified_entry_goes_live():
    """The gate the whole design rests on: a guess in the file is not a fix."""
    doc = {
        "overrides": {
            "Jack White": {"wikipedia_url": WIKI + "Jack_White_(basketball)",
                           "verified": "2026-09-24"},
            "Bill Jones": {"wikipedia_url": WIKI + "Bill_Jones_(basketball)"},
            "Blank Man": {"wikipedia_url": "", "verified": True},
        },
        "candidates": {
            "Ace Bailey": {"wikipedia_url": WIKI + "Ace_Bailey_(basketball)"},
        },
    }
    with _Tmp(doc):
        assert player_urls.override_url("Jack White") == \
            WIKI + "Jack_White_(basketball)"
        assert player_urls.override_title("Jack White") == "Jack White (basketball)"
        # unverified, empty, and a candidate are all invisible to the pipeline
        assert player_urls.override_url("Bill Jones") == ""
        assert player_urls.override_url("Blank Man") == ""
        assert player_urls.override_url("Ace Bailey") == ""
        assert player_urls.overridden_players() == ["Jack White"]
        assert list(player_urls.candidates()) == ["Ace Bailey"]
    print("test_only_a_verified_entry_goes_live PASS")


def test_a_hand_written_url_counts_as_verified():
    """A human typing an article into the file IS the verification, and the
    bare {player: url} shape has to work with no wrapper at all."""
    with _Tmp({"Mike Lynn": WIKI + "Mike_Lynn_(basketball)",
               "note": "not a player"}):
        assert player_urls.override_url("Mike Lynn") == \
            WIKI + "Mike_Lynn_(basketball)"
        assert player_urls.overridden_players() == ["Mike Lynn"]
    print("test_a_hand_written_url_counts_as_verified PASS")


def test_lookup_folds_the_spelling():
    with _Tmp({"overrides": {"Mamadou N'diaye": {
            "wikipedia_url": WIKI + "Mamadou_N'Diaye_(basketball)",
            "verified": True}}}):
        for spelling in ("Mamadou N'diaye", "Mamadou N'Diaye",
                         "Mamadou  N'Diaye"):
            assert player_urls.override_url(spelling), spelling
        assert player_urls.override_url("Somebody Else") == ""
    print("test_lookup_folds_the_spelling PASS")


def test_a_missing_or_broken_file_is_no_overrides_not_a_crash():
    with _Tmp({}) as path:
        path.write_text("{not json", encoding="utf-8")
        player_urls.reset_cache()
        assert player_urls.load() == {}
        assert player_urls.candidates() == {}
        path.unlink()
        player_urls.reset_cache()
        assert player_urls.override_url("Anyone") == ""
    print("test_a_missing_or_broken_file_is_no_overrides_not_a_crash PASS")


# --- the career pipeline ----------------------------------------------------
class FakeClient:
    """Answers with the articles it was given, and records what it was asked."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def get_wikitext_and_title(self, title):
        self.asked.append(title)
        page = self.pages.get(title)
        return (page, title) if page else (None, None)


DUKE_ARTICLE = """'''David Duke Jr.''' is a basketball player.
{{Infobox basketball biography
| career_start = 2021
}}
== Career ==
"""


def _db(tmp: Path, record: dict):
    """A Database over a one-record careers file."""
    import update_careers as uc
    uc.CAREERS = careers = tmp / "careers.json"
    uc.LOCATIONS = tmp / "locations.json"
    uc.REVIEW = tmp / "review.json"
    careers.write_text(json.dumps([record]), encoding="utf-8")
    # Seeded so enrich_stint never reaches for the network to place a team.
    (tmp / "locations.json").write_text(json.dumps({
        "Brooklyn Nets": {"team": "Brooklyn Nets", "city": "Brooklyn",
                          "state": "NY", "country": "USA"},
        "Long Island Nets": {"team": "Long Island Nets", "city": "Uniondale",
                             "state": "NY", "country": "USA"},
    }), encoding="utf-8")
    (tmp / "review.json").write_text("{}", encoding="utf-8")
    return uc, uc.Database()


def test_the_pipeline_asks_for_the_override_and_nothing_else():
    """right_article must not consult same_person() for an overridden player:
    'David Duke' and 'David Duke' are the same name, which is exactly why the
    scraper could not tell the Klansman from the guard."""
    import importlib
    import update_careers as uc
    importlib.reload(uc)          # undo any path patching from another test
    with tempfile.TemporaryDirectory() as d, \
            _Tmp({"overrides": {"David Duke": {
                "wikipedia_url": WIKI + "David_Duke_Jr.",
                "verified": True}}}):
        tmp = Path(d)
        uc, db = _db(tmp, {"player": "David Duke",
                           "wikipedia_url": WIKI + "David_Duke",
                           "career_history": [], "status": "retired"})
        client = FakeClient({"David Duke Jr.": DUKE_ARTICLE})
        wt, title, refusal = uc.right_article(db, "David Duke", client)
        assert refusal is None
        assert title == "David Duke Jr."
        assert client.asked == ["David Duke Jr."], client.asked
    print("test_the_pipeline_asks_for_the_override_and_nothing_else PASS")


def test_an_unfetchable_override_is_a_refusal_not_a_fall_back_to_the_name():
    """Falling through to the name is how the namesake got in; a broken
    override has to be visible instead."""
    import importlib
    import update_careers as uc
    importlib.reload(uc)
    with tempfile.TemporaryDirectory() as d, \
            _Tmp({"overrides": {"David Duke": {
                "wikipedia_url": WIKI + "David_Duke_Jnr", "verified": True}}}):
        tmp = Path(d)
        uc, db = _db(tmp, {"player": "David Duke",
                           "wikipedia_url": WIKI + "David_Duke",
                           "career_history": [], "status": "retired"})
        client = FakeClient({"David Duke": "the Klansman's article"})
        wt, title, refusal = uc.right_article(db, "David Duke", client)
        assert wt is None and refusal and refusal["kind"] == "override-missing"
        assert "David Duke" not in client.asked, client.asked
    print("test_an_unfetchable_override_is_a_refusal_not_a_fall_back_to_the_name PASS")


def test_the_queue_mode_picks_exactly_the_overridden_records():
    import importlib
    import update_careers as uc
    importlib.reload(uc)
    with tempfile.TemporaryDirectory() as d, \
            _Tmp({"overrides": {
                "David Duke": {"wikipedia_url": WIKI + "David_Duke_Jr.",
                               "verified": True},
                "Not In The Database": {"wikipedia_url": WIKI + "Nobody",
                                        "verified": True}}}):
        tmp = Path(d)
        uc, db = _db(tmp, {"player": "David Duke",
                           "wikipedia_url": WIKI + "David_Duke",
                           "career_history": [], "status": "retired"})
        assert uc.build_queue(db, "override", None, set()) == ["David Duke"]
    print("test_the_queue_mode_picks_exactly_the_overridden_records PASS")


NETS_ARTICLE = """{{Infobox basketball biography
| name = David Duke Jr.
| career_start = 2021
}}
'''David Duke Jr.''' is an American basketball player.
{{Basketball career
}}
"""


def test_the_override_replaces_the_wrong_career_and_survives_the_write():
    """Two things at once. The stored career came off the wrong article, so
    _richer() must NOT protect it -- and the URL written back must be the
    override, or tomorrow's run asks Wikipedia the same wrong question again."""
    import importlib
    import update_careers as uc
    importlib.reload(uc)
    with tempfile.TemporaryDirectory() as d, \
            _Tmp({"overrides": {"Mike Lynn": {
                "wikipedia_url": WIKI + "Mike_Lynn_(basketball)",
                "verified": True}}}):
        tmp = Path(d)
        # the NFL career the Vikings general manager's article gave us
        uc, db = _db(tmp, {
            "player": "Mike Lynn", "wikipedia_url": WIKI + "Mike_Lynn",
            "status": "retired", "current_team": "Minnesota Vikings",
            "career_history": [
                {"years": "1974", "team": "Minnesota Vikings"},
                {"years": "1975-1990", "team": "Minnesota Vikings"},
                {"years": "1990-1992", "team": "Minnesota Vikings"}]})
        real = """{{Infobox basketball biography
| name = Mike Lynn
}}
'''Mike Lynn''' is a former American basketball player.
"""
        client = FakeClient({"Mike Lynn (basketball)": real})
        parsed = {"career_history": [{"years": "1968-1969",
                                      "team": "Brooklyn Nets"}],
                  "current_team": "Brooklyn Nets", "status": "success"}
        saved_parse = uc.parse_player
        uc.parse_player = lambda wt, name, norm: dict(parsed)
        try:
            rec, _teams, is_new, _ps, _pc = uc.merge_player(
                db, "Mike Lynn", client, {}, set(), 2026)
        finally:
            uc.parse_player = saved_parse
        assert not is_new, "the repair must land in the existing record"
        assert rec["wikipedia_url"] == WIKI + "Mike_Lynn_(basketball)"
        teams = [s["team"] for s in rec["career_history"]]
        assert teams == ["Brooklyn Nets"], teams
        assert list(db.by_name) == ["Mike Lynn"], list(db.by_name)
    print("test_the_override_replaces_the_wrong_career_and_survives_the_write PASS")


def test_an_empty_parse_still_does_not_clobber_a_career():
    """The one protection the override does not remove: an article that parses
    to nothing is not an improvement on a career, even a wrong one."""
    import importlib
    import update_careers as uc
    importlib.reload(uc)
    with tempfile.TemporaryDirectory() as d, \
            _Tmp({"overrides": {"Mike Lynn": {
                "wikipedia_url": WIKI + "Mike_Lynn_(basketball)",
                "verified": True}}}):
        tmp = Path(d)
        uc, db = _db(tmp, {
            "player": "Mike Lynn", "wikipedia_url": WIKI + "Mike_Lynn",
            "status": "retired", "current_team": "Brooklyn Nets",
            "career_history": [{"years": "1968-1969", "team": "Brooklyn Nets"}]})
        client = FakeClient({"Mike Lynn (basketball)": "a stub"})
        saved_parse = uc.parse_player
        uc.parse_player = lambda wt, name, norm: {"career_history": [],
                                                  "current_team": "",
                                                  "status": "no_career_data"}
        try:
            rec, *_ = uc.merge_player(db, "Mike Lynn", client, {}, set(), 2026)
        finally:
            uc.parse_player = saved_parse
        assert [s["team"] for s in rec["career_history"]] == ["Brooklyn Nets"]
        # the URL is still corrected, even when the career is left alone
        assert rec["wikipedia_url"] == WIKI + "Mike_Lynn_(basketball)"
    print("test_an_empty_parse_still_does_not_clobber_a_career PASS")


# --- the bio fetcher --------------------------------------------------------
def test_the_bio_fetcher_reads_the_override_not_the_stored_url():
    import fetch_bio_wikidata as fb
    with _Tmp({"overrides": {"Ace Bailey": {
            "wikipedia_url": WIKI + "Ace_Bailey_(basketball)",
            "verified": True}}}):
        player = {"player": "Ace Bailey", "wikipedia_url": WIKI + "Ace_Bailey"}
        assert fb._title_of(player) == "Ace Bailey (basketball)"
        other = {"player": "Nikola Jokić",
                 "wikipedia_url": WIKI + "Nikola_Joki%C4%87"}
        assert fb._title_of(other) == "Nikola Jokić"
    print("test_the_bio_fetcher_reads_the_override_not_the_stored_url PASS")


def test_the_review_file_stops_naming_a_repaired_player():
    import fetch_bio_wikidata as fb
    careers = [{"player": "Ace Bailey", "wikipedia_url": WIKI + "Ace_Bailey"}]
    bio = {"Ace Bailey": {"birth_date": "2006-08-13", "checked": "2026-09-24",
                          "rejected_wikidata_id": "Q323039",
                          "wikidata_id": ""}}
    with _Tmp({"overrides": {"Ace Bailey": {
            "wikipedia_url": WIKI + "Ace_Bailey_(basketball)",
            "verified": True}}}):
        review = fb.build_review(bio, careers, {}, {})
        urls = [r["wikipedia_url"]
                for r in review["wikipedia_url_wrong_person"]]
        assert urls == [WIKI + "Ace_Bailey_(basketball)"], urls
    print("test_the_review_file_stops_naming_a_repaired_player PASS")


# --- the resolver -----------------------------------------------------------
def _item(qid, *, p106=True, birth="1999-01-01", article="", label=""):
    return {"qid": qid, "label": label or qid, "description": "",
            "p106": p106, "p641": False, "birth_date": birth,
            "article": article or (WIKI + "Some_Article")}


def test_the_candidate_ladder_covers_the_conventions():
    row = {"player": "Ron Holland", "wikipedia_title": "Ron Holland"}
    titles = rpu.candidate_titles(row, {"display_name": "Ron Holland"},
                                  "2005-07-07", prefilled="Ron Holland II")
    assert titles[0] == "Ron Holland II", titles
    assert "Ron Holland (basketball)" in titles
    assert "Ron Holland Jr." in titles
    assert "Ron Holland (basketball, born 2005)" in titles
    assert "Ron Holland" not in titles, "the known-wrong article must be dropped"
    print("test_the_candidate_ladder_covers_the_conventions PASS")


def test_the_acceptance_test_is_p106_plus_the_birth_year():
    assert rpu.accepts(_item("Q1", birth="1999-04-02"), "1998-12-31")
    assert not rpu.accepts(_item("Q1", birth="1999-04-02"), "1997-04-02")
    assert not rpu.accepts(_item("Q1", p106=False), "1999-01-01")
    assert not rpu.accepts(_item("Q1", birth=None), "1999-01-01")
    assert not rpu.accepts(_item("Q1"), "")       # nothing to check against
    assert not rpu.accepts(None, "1999-01-01")
    print("test_the_acceptance_test_is_p106_plus_the_birth_year PASS")


class FakeTransport:
    """Canned Wikipedia/Wikidata answers for the resolver."""

    def __init__(self, bref, search, titles, items):
        self.bref, self.search = bref, search
        self.titles, self.items = titles, items
        self.requests = 0

    def get_text(self, url):
        rows = ["player,birth_date"]
        rows += [f"{name},{date}" for name, date in self.bref.items()]
        return "\n".join(rows) + "\n"

    def get_json(self, url, params):
        self.requests += 1
        if params.get("list") == "search":
            name = params["srsearch"].rsplit(" basketball", 1)[0]
            return {"query": {"search": [{"title": t}
                                         for t in self.search.get(name, [])]}}
        asked = params["titles"].split("|")
        return {"query": {"pages": [
            {"title": t, "pageprops": {"wikibase_item": self.titles[t]}}
            for t in asked if t in self.titles]}}

    def sparql(self, query):
        wanted = [q for q in self.items if f"wd:{q}" in query]
        return {"results": {"bindings": [{
            "item": {"value": "http://www.wikidata.org/entity/" + q},
            "itemLabel": {"value": self.items[q]["label"]},
            "itemDescription": {"value": self.items[q]["description"]},
            "p106": {"value": "true" if self.items[q]["p106"] else "false"},
            "p641": {"value": "false"},
            "birth": {"value": (self.items[q]["birth_date"] or "") + "T00:00:00Z"
                      if self.items[q]["birth_date"] else ""},
            "bprec": {"value": "11"},
            "article": {"value": self.items[q]["article"]},
        } for q in wanted]}}


def _run(rows, players, transport, prefills=None):
    return rpu.resolve(transport, rows, players, prefills or {})


def test_exactly_one_accepted_article_becomes_an_override():
    rows = [{"player": "Jack White", "wikipedia_title": "Jack White",
             "wikipedia_url": WIKI + "Jack_White",
             "rejected_wikidata_id": "Q272031"}]
    players = [{"player": "Jack White", "wikipedia_url": WIKI + "Jack_White"}]
    t = FakeTransport(
        bref={"Jack White": "1997-11-24"},
        search={"Jack White": ["Jack White", "Jack White (basketball)"]},
        titles={"Jack White (basketball)": "Q55123",
                "Jack White": "Q272031"},
        items={"Q55123": _item("Q55123", birth="1997-11-24",
                               article=WIKI + "Jack_White_(basketball)",
                               label="Jack White"),
               "Q272031": _item("Q272031", p106=False, birth="1975-07-09",
                                article=WIKI + "Jack_White", label="Jack White")})
    report = _run(rows, players, t)
    assert report["counts"] == {"flagged": 1, "verified": 1, "needs_a_human": 0}
    rec = report["verified"]["Jack White"]
    assert rec["wikipedia_url"] == WIKI + "Jack_White_(basketball)"
    assert rec["wikidata_id"] == "Q55123" and rec["verified"]
    print("test_exactly_one_accepted_article_becomes_an_override PASS")


def test_two_candidates_born_the_same_year_are_listed_not_guessed():
    rows = [{"player": "Mike Green", "wikipedia_title": "Mike Green",
             "wikipedia_url": WIKI + "Mike_Green",
             "rejected_wikidata_id": "Q999"}]
    players = [{"player": "Mike Green", "wikipedia_url": WIKI + "Mike_Green"}]
    t = FakeTransport(
        bref={"Mike Green": "1985-01-01"},
        search={"Mike Green": ["Mike Green (basketball, born 1985)",
                              "Mike Green (basketball)"]},
        titles={"Mike Green (basketball, born 1985)": "Q1",
                "Mike Green (basketball)": "Q2"},
        items={"Q1": _item("Q1", birth="1985-02-02"),
               "Q2": _item("Q2", birth="1985-06-06")})
    report = _run(rows, players, t)
    assert report["counts"]["verified"] == 0
    why = report["needs_a_human"][0]["why"]
    assert "2 different basketball players" in why, why
    print("test_two_candidates_born_the_same_year_are_listed_not_guessed PASS")


def test_nothing_acceptable_is_listed_with_the_reason_per_candidate():
    rows = [{"player": "Ray Ellefson", "wikipedia_title": "Ray Ellefson",
             "wikipedia_url": WIKI + "Ray_Ellefson",
             "rejected_wikidata_id": "Q7297443"}]
    players = [{"player": "Ray Ellefson", "wikipedia_url": WIKI + "Ray_Ellefson"}]
    t = FakeTransport(
        bref={"Ray Ellefson": "1925-04-14"},
        search={"Ray Ellefson": ["Ray Ellefson"]},
        titles={"Ray Ellefson (basketball)": "Q1"},
        items={"Q1": _item("Q1", p106=False, birth="1925-04-14")})
    report = _run(rows, players, t)
    assert report["counts"] == {"flagged": 1, "verified": 0, "needs_a_human": 1}
    cands = report["needs_a_human"][0]["candidates"]
    reasons = [c["why_not"] for c in cands if c["title"].endswith("(basketball)")]
    assert reasons and "not a basketball player" in reasons[0], cands
    print("test_nothing_acceptable_is_listed_with_the_reason_per_candidate PASS")


def test_an_article_another_record_already_owns_is_refused():
    """Pointing two records at one article makes a duplicate, not a repair."""
    rows = [{"player": "Michael Porter", "wikipedia_title": "Michael Porter",
             "wikipedia_url": WIKI + "Michael_Porter",
             "rejected_wikidata_id": "Q272146"}]
    players = [{"player": "Michael Porter", "wikipedia_url": WIKI + "Michael_Porter"},
               {"player": "Michael Porter Jr.",
                "wikipedia_url": WIKI + "Michael_Porter_Jr."}]
    t = FakeTransport(
        bref={"Michael Porter": "1998-06-29"},
        search={"Michael Porter": ["Michael Porter Jr."]},
        titles={"Michael Porter Jr.": "Q1"},
        items={"Q1": _item("Q1", birth="1998-06-29",
                           article=WIKI + "Michael_Porter_Jr.")})
    report = _run(rows, players, t)
    assert report["counts"]["verified"] == 0
    assert "already Michael Porter Jr.'s record" in \
        report["needs_a_human"][0]["why"]
    print("test_an_article_another_record_already_owns_is_refused PASS")


def test_apply_promotes_the_candidate_it_verified():
    doc = {"overrides": {}, "candidates": {
        "Jack White": {"wikipedia_url": WIKI + "Jack_White_(basketball)"},
        "Ace Bailey": {"wikipedia_url": WIKI + "Ace_Bailey_(basketball)"}}}
    with _Tmp(doc) as path:
        report = {"verified": {"Jack White": {
            "wikipedia_url": WIKI + "Jack_White_(basketball)",
            "verified": "2026-09-24"}}}
        out = rpu.apply_overrides(report, path)
        assert list(out["overrides"]) == ["Jack White"]
        assert list(out["candidates"]) == ["Ace Bailey"]
        assert player_urls.override_url("Jack White")
        assert player_urls.override_url("Ace Bailey") == ""
    print("test_apply_promotes_the_candidate_it_verified PASS")


def test_the_audit_says_what_each_record_shows():
    rows = [{"player": "Michael Phelps", "wikipedia_title": "Michael Phelps",
             "wikipedia_url": WIKI + "Michael_Phelps",
             "rejected_wikidata_id": "Q39562"},
            {"player": "Mike Lynn", "wikipedia_title": "Mike Lynn",
             "wikipedia_url": WIKI + "Mike_Lynn", "rejected_wikidata_id": "Q1"},
            {"player": "Reggie Jackson", "wikipedia_title": "Reggie Jackson",
             "wikipedia_url": WIKI + "Reggie_Jackson",
             "rejected_wikidata_id": "Q2"}]
    players = [
        {"player": "Michael Phelps", "career_history": []},
        {"player": "Mike Lynn", "career_history": [
            {"years": "1975-1990", "team": "Minnesota Vikings"}]},
        {"player": "Reggie Jackson", "career_history": [
            {"years": "2011-2015", "team": "Oklahoma City Thunder"}]}]
    doc = rpu.audit_careers(rows, players)
    kinds = {r["player"]: r["kind"] for r in doc["audit"]}
    assert kinds == {"Michael Phelps": "empty", "Mike Lynn": "namesake_career",
                     "Reggie Jackson": "frozen"}, kinds
    print("test_the_audit_says_what_each_record_shows PASS")


if __name__ == "__main__":
    test_only_a_verified_entry_goes_live()
    test_a_hand_written_url_counts_as_verified()
    test_lookup_folds_the_spelling()
    test_a_missing_or_broken_file_is_no_overrides_not_a_crash()
    test_the_pipeline_asks_for_the_override_and_nothing_else()
    test_an_unfetchable_override_is_a_refusal_not_a_fall_back_to_the_name()
    test_the_queue_mode_picks_exactly_the_overridden_records()
    test_the_override_replaces_the_wrong_career_and_survives_the_write()
    test_an_empty_parse_still_does_not_clobber_a_career()
    test_the_bio_fetcher_reads_the_override_not_the_stored_url()
    test_the_review_file_stops_naming_a_repaired_player()
    test_the_candidate_ladder_covers_the_conventions()
    test_the_acceptance_test_is_p106_plus_the_birth_year()
    test_exactly_one_accepted_article_becomes_an_override()
    test_two_candidates_born_the_same_year_are_listed_not_guessed()
    test_nothing_acceptable_is_listed_with_the_reason_per_candidate()
    test_an_article_another_record_already_owns_is_refused()
    test_apply_promotes_the_candidate_it_verified()
    test_the_audit_says_what_each_record_shows()
    print("\nall player-URL override tests PASS")
