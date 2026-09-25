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
    """A verified override is the repair: the player leaves the wrong-person
    list as soon as it is written, not only once his bio is re-read."""
    import fetch_bio_wikidata as fb
    careers = [{"player": "Ace Bailey", "wikipedia_url": WIKI + "Ace_Bailey"}]
    bio = {"Ace Bailey": {"birth_date": "2006-08-13", "checked": "2026-09-24",
                          "rejected_wikidata_id": "Q323039",
                          "wikidata_id": ""}}
    with _Tmp({"overrides": {"Ace Bailey": {
            "wikipedia_url": WIKI + "Ace_Bailey_(basketball)",
            "verified": True}}}):
        review = fb.build_review(bio, careers, {}, {})
        assert review["wikipedia_url_wrong_person"] == [], review
        assert review["counts"]["wikipedia_url_wrong_person"] == 0
        # still a wrong-entity row until the bio record is re-read
        assert [r["player"] for r in review["wrong_entity"]] == ["Ace Bailey"]
    with _Tmp({"overrides": {}}):
        review = fb.build_review(bio, careers, {}, {})
        assert [r["player"] for r in review["wikipedia_url_wrong_person"]] \
            == ["Ace Bailey"]
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

    def __init__(self, bref, search, titles, items, *, disambig=(),
                 redirects=None):
        self.bref, self.search = bref, search
        self.titles, self.items = titles, items
        self.disambig, self.redirects = set(disambig), redirects or {}
        self.requests = 0
        self.searched = []

    def get_text(self, url):
        rows = ["player,birth_date"]
        rows += [f"{name},{date}" for name, date in self.bref.items()]
        return "\n".join(rows) + "\n"

    def get_json(self, url, params):
        self.requests += 1
        if params.get("list") == "search":
            name = params["srsearch"].rsplit(" basketball", 1)[0]
            self.searched.append(name)
            return {"query": {"search": [{"title": t}
                                         for t in self.search.get(name, [])]}}
        asked = params["titles"].split("|")
        hops = [{"from": t, "to": self.redirects[t]}
                for t in asked if t in self.redirects]
        landed = [self.redirects.get(t, t) for t in asked]
        pages = []
        for t in dict.fromkeys(landed):
            if t in self.disambig:
                pages.append({"title": t, "pageprops": {"disambiguation": ""}})
            elif t in self.titles:
                props = ({"wikibase_item": self.titles[t]}
                         if self.titles[t] else {})
                pages.append({"title": t, "pageprops": props})
            else:
                pages.append({"title": t, "missing": True})
        return {"query": {"redirects": hops, "pages": pages}}

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


def _run(rows, players, transport, prefills=None, human=None):
    return rpu.resolve(transport, rows, players, prefills or {}, human=human)


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
    assert report["counts"] == {"flagged": 1, "verified": 1, "needs_a_human": 0,
                                "human_verified": 0, "human_skipped": 0}
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
    assert report["counts"] == {"flagged": 1, "verified": 0, "needs_a_human": 1,
                                "human_verified": 0, "human_skipped": 0}
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


# --- human-verified overrides -----------------------------------------------
def _flag(name, title, rejected="Q9"):
    return {"player": name, "wikipedia_title": title,
            "wikipedia_url": WIKI + title.replace(" ", "_"),
            "rejected_wikidata_id": rejected}


def test_a_human_verified_article_skips_the_gate_and_the_search():
    """Ray Ellefson's item has no P106 and Jay Miller's has a bad birth year:
    the Wikidata test would refuse both, and the human already answered it."""
    rows = [_flag("Ray Ellefson", "Ray Ellefson", "Q7297443"),
            _flag("Jay Miller", "Jay Miller (basketball)", "Q518561")]
    players = [{"player": "Ray Ellefson", "wikipedia_url": WIKI + "Ray_Ellefson"},
               {"player": "Jay Miller",
                "wikipedia_url": WIKI + "Jay_Miller_(basketball)"}]
    human = {"Ray Ellefson": {"wikipedia_url": WIKI + "Ray_Ellefson",
                              "verified_by": "jorge"},
             "Jay Miller": {"wikipedia_url": WIKI + "Jay_Miller_(basketball)",
                            "verified_by": "jorge"}}
    t = FakeTransport(
        bref={"Ray Ellefson": "1922-11-18", "Jay Miller": "1943-07-19"},
        search={},
        titles={"Ray Ellefson": "Q7297443",
                "Jay Miller (basketball)": "Q518561"},
        items={"Q7297443": _item("Q7297443", p106=False, birth="1922-11-18"),
               "Q518561": _item("Q518561", birth="1950-01-01")})
    report = _run(rows, players, t, human=human)
    c = report["counts"]
    assert (c["human_verified"], c["human_skipped"], c["verified"],
            c["needs_a_human"], c["flagged"]) == (2, 0, 0, 0, 2), c
    assert t.searched == [], "a human-verified player is never searched"
    ray = report["human_verified"]["Ray Ellefson"]
    assert ray["wikipedia_url"] == WIKI + "Ray_Ellefson"
    assert ray["wikidata_id"] == "Q7297443" and ray["human_verified"]
    assert ray["verified_by"] == "jorge" and ray["verified"]
    assert any("P106" in w for w in ray["warnings"]), ray["warnings"]
    jay = report["human_verified"]["Jay Miller"]
    # the birth year disagrees by seven years: a warning, not a block
    assert any("born 1950" in w and "1943" in w for w in jay["warnings"]), \
        jay["warnings"]
    print("test_a_human_verified_article_skips_the_gate_and_the_search PASS")


def test_a_missing_or_disambiguation_article_is_skipped_not_written():
    rows = [_flag("Brian Oliver", "Brian Oliver"),
            _flag("Eric Williams", "Eric Williams"),
            _flag("Terry Taylor", "Terry Taylor")]
    players = [{"player": r["player"], "wikipedia_url": r["wikipedia_url"]}
               for r in rows]
    human = {
        "Brian Oliver": {"wikipedia_url":
                         WIKI + "Brian_Oliver_(basketball,_born_1968)",
                         "verified_by": "jorge"},
        "Eric Williams": {"wikipedia_url":
                          WIKI + "Eric_Williams_(basketball,_born_1972)",
                          "verified_by": "jorge"},
        "Terry Taylor": {"wikipedia_url": WIKI + "Terry_Taylor_(basketball)",
                         "verified_by": "jorge"}}
    t = FakeTransport(
        bref={}, search={},
        titles={"Terry Taylor (basketball)": "Q3"},
        items={"Q3": _item("Q3")},
        disambig={"Eric Williams (basketball)"},
        redirects={"Eric Williams (basketball, born 1972)":
                   "Eric Williams (basketball)"})
    report = _run(rows, players, t, human=human)
    assert list(report["human_verified"]) == ["Terry Taylor"]
    why = {r["player"]: r["why"] for r in report["human_skipped"]}
    assert "does not exist" in why["Brian Oliver"], why
    assert "redirects to the disambiguation page" in why["Eric Williams"], why
    with _Tmp({"overrides": {}, "human_verified": human}) as path:
        out = rpu.apply_overrides(report, path)
        assert list(out["overrides"]) == ["Terry Taylor"]
        # the skipped ones stay staged, so the URL can be fixed and re-run
        assert sorted(out["human_verified"]) == ["Brian Oliver", "Eric Williams"]
        assert player_urls.override_url("Brian Oliver") == ""
        assert player_urls.is_human_verified("Terry Taylor")
    print("test_a_missing_or_disambiguation_article_is_skipped_not_written PASS")


def test_a_redirect_to_a_real_article_is_written_as_its_target():
    rows = [_flag("Charlie Black", "Charlie Black")]
    players = [{"player": "Charlie Black",
                "wikipedia_url": WIKI + "Charlie_Black"}]
    human = {"Charlie Black": {"wikipedia_url": WIKI + "Charlie_Black_(x)",
                               "verified_by": "jorge"}}
    t = FakeTransport(bref={"Charlie Black": "1921-06-15"}, search={},
                      titles={"Charlie T. Black": "Q5"},
                      items={"Q5": _item("Q5", birth="1921-06-15")},
                      redirects={"Charlie Black (x)": "Charlie T. Black"})
    rec = _run(rows, players, t, human=human)["human_verified"]["Charlie Black"]
    assert rec["wikipedia_url"] == WIKI + "Charlie_T._Black", rec
    assert rec["requested_url"] == WIKI + "Charlie_Black_(x)"
    print("test_a_redirect_to_a_real_article_is_written_as_its_target PASS")


def test_human_and_machine_players_resolve_side_by_side():
    """The search still runs for everyone the human did not decide."""
    rows = [_flag("Jack White", "Jack White", "Q272031"),
            _flag("Ron Holland", "Ron Holland")]
    players = [{"player": "Jack White", "wikipedia_url": WIKI + "Jack_White"},
               {"player": "Ron Holland", "wikipedia_url": WIKI + "Ron_Holland"}]
    human = {"Ron Holland": {"wikipedia_url": WIKI + "Ron_Holland_II",
                             "verified_by": "jorge"}}
    t = FakeTransport(
        bref={"Jack White": "1997-08-05", "Ron Holland": "2005-07-07"},
        search={"Jack White": ["Jack White (basketball)"]},
        titles={"Jack White (basketball)": "Q55", "Ron Holland II": "Q66"},
        items={"Q55": _item("Q55", birth="1997-08-05",
                            article=WIKI + "Jack_White_(basketball)"),
               "Q66": _item("Q66", birth="2005-07-07")})
    report = _run(rows, players, t, human=human)
    assert list(report["verified"]) == ["Jack White"]
    assert list(report["human_verified"]) == ["Ron Holland"]
    assert report["human_verified"]["Ron Holland"]["warnings"] == []
    assert t.searched == ["Jack White"], t.searched
    print("test_human_and_machine_players_resolve_side_by_side PASS")


def test_an_unsigned_human_entry_is_ignored():
    with _Tmp({"overrides": {}, "human_verified": {
            "Ryan Dunn": {"wikipedia_url": WIKI + "Ryan_Dunn_(basketball)"},
            "Vernon Carey": {"wikipedia_url": WIKI + "Vernon_Carey_Jr.",
                             "verified_by": "jorge"}}}):
        assert list(player_urls.human_verified()) == ["Vernon Carey"]
        # staged is not live
        assert player_urls.override_url("Vernon Carey") == ""
        assert player_urls.overridden_players() == []
    print("test_an_unsigned_human_entry_is_ignored PASS")


def test_settle_drops_the_overridden_players_from_the_review():
    with tempfile.TemporaryDirectory() as d, _Tmp({"overrides": {
            "Jack White": {"wikipedia_url": WIKI + "Jack_White_(basketball)",
                           "verified": "2026-09-24"},
            "Ray Ellefson": {"wikipedia_url": WIKI + "Ray_Ellefson",
                             "verified": "2026-09-24", "verified_by": "jorge",
                             "human_verified": True}}}):
        path = Path(d) / "review.json"
        path.write_text(json.dumps({
            "counts": {"wikipedia_url_wrong_person": 3},
            "wikipedia_url_wrong_person": [
                {"player": "Jack White"}, {"player": "Ray Ellefson"},
                {"player": "Mike Green"}]}), encoding="utf-8")
        dropped, left = rpu.settle_review(path)
        assert sorted(dropped) == ["Jack White", "Ray Ellefson"] and left == 1
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert [r["player"] for r in doc["wikipedia_url_wrong_person"]] == \
            ["Mike Green"]
        assert doc["counts"]["wikipedia_url_wrong_person"] == 1
    print("test_settle_drops_the_overridden_players_from_the_review PASS")


def test_the_bio_fetcher_trusts_a_human_verified_item_without_p106():
    import fetch_bio_wikidata as fb

    class T:
        requests = 0

        def get_json(self, url, params):
            return {"query": {"pages": [{"title": "Ray Ellefson",
                                         "pageprops": {"wikibase_item":
                                                       "Q7297443"}}]}}

        def sparql(self, query):
            assert "wd:Q7297443" in query, "no replacement search expected"
            return {"results": {"bindings": [{
                "item": {"value": "http://www.wikidata.org/entity/Q7297443"},
                "bball": {"value": "false"},
                "birth": {"value": "1920-11-18T00:00:00Z"},
                "bprec": {"value": "11"},
                "bplaceLabel": {"value": "Fergus Falls"}}]}}

    player = {"player": "Ray Ellefson", "wikipedia_url": WIKI + "Ray_Ellefson"}
    # a stale item ID on file must not short-cut the human's article
    bio = {"Ray Ellefson": {"wikidata_id": "Q1", "checked": "2026-01-01"}}
    bref = {fb.normkey("Ray Ellefson"): "1922-11-18"}
    with _Tmp({"overrides": {"Ray Ellefson": {
            "wikipedia_url": WIKI + "Ray_Ellefson", "verified": "2026-09-24",
            "verified_by": "jorge", "human_verified": True}}}):
        items, details = fb.gather_items(T(), [player], bio, bref)
        assert items["Ray Ellefson"]["qid"] == "Q7297443"
        assert details == {}, details
        rec = fb.compose(items["Ray Ellefson"], "1922-11-18",
                         checked="2026-09-24", trusted=True)
        # two years apart: reported, but the item keeps its places
        assert rec["birth_date"] == "1922-11-18"
        assert rec["birth_place"] == "Fergus Falls"
        assert rec["wikidata_birth_date"] == "1920-11-18"
        assert "rejected_wikidata_id" not in rec
        review = fb.build_review({"Ray Ellefson": rec}, [player], bref, {})
        row = review["date_disagreement"][0]
        assert row["years_apart"] == 2 and row["places_kept"] is True
        # --reapply does not throw the item away either
        far = dict(rec, wikidata_birth_date="1900-01-01")
        out, det = fb.reapply({"Ray Ellefson": far}, bref)
        assert out["Ray Ellefson"]["wikidata_id"] == "Q7297443" and not det
    # without the human flag, the same item is still refused
    with _Tmp({"overrides": {"Ray Ellefson": {
            "wikipedia_url": WIKI + "Ray_Ellefson", "verified": "2026-09-24"}}}):
        class T2(T):
            def sparql(self, query):
                if "rdfs:label" in query:
                    return {"results": {"bindings": []}}
                return T.sparql(self, query)
        items, details = fb.gather_items(T2(), [player], {}, bref)
        assert items["Ray Ellefson"] is None and "Ray Ellefson" in details
    print("test_the_bio_fetcher_trusts_a_human_verified_item_without_p106 PASS")


# --- tie-breaks -------------------------------------------------------------
def _tie(name, bref, search, items, *, career=None, rejected="Q999",
         display=""):
    """Resolve one flagged player against canned candidates.

    `items` is {title: (qid, birth, p106)}; titles sharing a qid are redirect
    aliases of one item.
    """
    rows = [_flag(name, name, rejected)]
    rec = {"player": name, "wikipedia_url": WIKI + name.replace(" ", "_")}
    if career is not None:
        rec["career_history"] = [{"years": y, "team": "T"} for y in career]
    if display:
        rec["display_name"] = display
    t = FakeTransport(
        bref={name: bref} if bref else {},
        search={name: list(search)},
        titles={title: q for title, (q, _, _) in items.items()},
        items={q: _item(q, birth=b, p106=p,
                        article=WIKI + title.replace(" ", "_"))
               for title, (q, b, p) in items.items()})
    return _run(rows, [rec], t)


def test_rule1_name_key_strips_what_it_should():
    k = rpu.name_key
    assert k("Bill Hosket Jr.") == k("Bill Hosket")
    assert k("Robert Williams III") == k("Robert Williams")
    assert k("Ron Holland II") == k("Ron Holland")
    assert k("Brandon Williams (basketball, born 1999)") == k("Brandon Williams")
    assert k("Mike James (1990)") == k("Mike James (basketball, born 1990)")
    assert k("Nikola Jokić") == k("Nikola Jokic")
    assert k("Tre' Johnson") == k("Tre Johnson (basketball)")
    assert k("A. J. Green (basketball)") == k("AJ Green")
    for a, b in [("Aaron Harrison", "Andrew Harrison"),
                 ("Jesse Edwards", "Anthony Edwards"),
                 ("Don May", "Bill Hosket"),
                 ("Anthony Bennett", "Anthony Brown")]:
        assert k(a) != k(b), (a, b)
    print("test_rule1_name_key_strips_what_it_should PASS")


def test_rule1_a_different_name_is_never_a_second_match():
    cases = [
        # player, BR date, the right article, the other name born in range
        ("Andrew Harrison", "1994-10-28",
         ("Andrew Harrison (basketball)", "Q14469654", "1994-10-28"),
         ("Aaron Harrison", "Q14469653", "1994-10-28")),
        ("Anthony Edwards", "2001-08-05",
         ("Anthony Edwards (basketball)", "Q60951627", "2001-08-05"),
         ("Jesse Edwards (basketball)", "Q100785232", "2000-03-18")),
        ("Bill Hosket", "1946-12-20",
         ("Bill Hosket Jr.", "Q1463735", "1946-12-20"),
         ("Don May (basketball)", "Q2736149", "1946-01-03")),
        ("Anthony Brown", "1992-10-10",
         ("Anthony Brown (basketball)", "Q20440606", "1992-10-10"),
         ("Anthony Bennett (basketball)", "Q4772101", "1993-03-14")),
    ]
    for name, bref, right, other in cases:
        report = _tie(name, bref, [right[0], other[0]],
                      {right[0]: (right[1], right[2], True),
                       other[0]: (other[1], other[2], True)})
        assert list(report["verified"]) == [name], (name, report["needs_a_human"])
        rec = report["verified"][name]
        assert rec["wikidata_id"] == right[1], (name, rec)
        assert rec["decided_by"] == "rule 1: exact name", (name, rec)
    print("test_rule1_a_different_name_is_never_a_second_match PASS")


def test_rule1_redirect_aliases_are_one_candidate():
    """'Cat Barber' redirects to the same item as 'Anthony Barber
    (basketball)': one player, not two, and the named title is kept."""
    report = _tie("Anthony Barber", "", ["Cat Barber"],
                  {"Anthony Barber (basketball)": ("Q16209351", "1994-07-25",
                                                   True),
                   "Cat Barber": ("Q16209351", "1994-07-25", True)},
                  career=["2016-2017", "2017-2018"])
    rec = report["verified"]["Anthony Barber"]
    assert rec["wikidata_id"] == "Q16209351"
    assert rec["matched_candidate"] == "Anthony Barber (basketball)", rec
    print("test_rule1_redirect_aliases_are_one_candidate PASS")


def test_rule1_a_redirect_with_another_name_alone_is_not_enough():
    """An item reached only through a different name is not accepted."""
    report = _tie("Anthony Barber", "", ["Cat Barber"],
                  {"Cat Barber": ("Q16209351", "1994-07-25", True)},
                  career=["2016-2017"])
    assert report["verified"] == {}
    cand = [c for c in report["needs_a_human"][0]["candidates"]
            if c["title"] == "Cat Barber"][0]
    assert "rule 1" in cand["why_not"], cand
    print("test_rule1_a_redirect_with_another_name_alone_is_not_enough PASS")


def test_rule2_the_career_stands_in_for_a_missing_birth_date():
    """Brandon Williams has no Basketball-Reference row; his first stint is
    2021, so the 1999 Brandon Williams fits (1997-2004) and the 1975 one
    does not."""
    report = _tie("Brandon Williams", "",
                  ["Brandon Williams (basketball, born 1999)",
                   "Brandon Williams (basketball, born 1975)"],
                  {"Brandon Williams (basketball, born 1999)":
                       ("Q100987066", "1999-11-22", True),
                   "Brandon Williams (basketball, born 1975)":
                       ("Q2923793", "1975-02-27", True)},
                  career=["2022-2023", "2021-2022"])
    rec = report["verified"]["Brandon Williams"]
    assert rec["wikidata_id"] == "Q100987066", rec
    assert "rule 2" in rec["decided_by"], rec
    assert "1997-2004" in rec["decided_by"], rec
    print("test_rule2_the_career_stands_in_for_a_missing_birth_date PASS")


def test_rule2_window_edges_and_a_new_player():
    # born 1997 or 2004 with a first stint in 2021: both edges are inside
    for born in ("1997-01-01", "2004-12-31"):
        report = _tie("Herb Jones", "", ["Herb Jones (basketball)"],
                      {"Herb Jones (basketball)": ("Q1", born, True)},
                      career=["2021-2025"])
        assert list(report["verified"]) == ["Herb Jones"], born
    # one year outside either edge is not
    for born in ("1996-12-31", "2005-01-01"):
        report = _tie("Herb Jones", "", ["Herb Jones (basketball)"],
                      {"Herb Jones (basketball)": ("Q1", born, True)},
                      career=["2021-2025"])
        assert report["verified"] == {}, born
        why = report["needs_a_human"][0]["candidates"][0]["why_not"]
        assert "rule 2" in why, why
    print("test_rule2_window_edges_and_a_new_player PASS")


def test_rule2_no_date_and_no_career_is_still_a_human():
    report = _tie("Braden Smith", "", ["Braden Smith (basketball)"],
                  {"Braden Smith (basketball)": ("Q1", "2003-05-05", True)},
                  career=[])
    assert report["verified"] == {}
    assert "no career on file" in report["needs_a_human"][0]["why"]
    print("test_rule2_no_date_and_no_career_is_still_a_human PASS")


def test_rule2_is_only_used_without_a_basketball_reference_date():
    """A BR date decides even when the career would say otherwise."""
    report = _tie("Brandon Williams", "1975-02-27",
                  ["Brandon Williams (basketball, born 1999)",
                   "Brandon Williams (basketball, born 1975)"],
                  {"Brandon Williams (basketball, born 1999)":
                       ("Q100987066", "1999-11-22", True),
                   "Brandon Williams (basketball, born 1975)":
                       ("Q2923793", "1975-02-27", True)},
                  career=["2021-2022"])
    rec = report["verified"]["Brandon Williams"]
    assert rec["wikidata_id"] == "Q2923793"
    assert rec["decided_by"] == "Basketball-Reference birth year", rec
    print("test_rule2_is_only_used_without_a_basketball_reference_date PASS")


def test_rule3_the_flagged_namesake_is_dropped_and_the_rest_decides():
    """The flagged item passes P106, the name and the birth year -- it is
    still never accepted back, and the other one wins."""
    report = _tie("Jay Miller", "1943-07-19",
                  ["Jay Miller (basketball)", "Jay Miller (basketball, born 1943)"],
                  {"Jay Miller (basketball)": ("Q518561", "1943-01-01", True),
                   "Jay Miller (basketball, born 1943)":
                       ("Q777", "1943-07-19", True)},
                  rejected="Q518561")
    rec = report["verified"]["Jay Miller"]
    assert rec["wikidata_id"] == "Q777", rec
    assert rec["decided_by"] == "rule 3: namesake item dropped", rec
    # the flagged one alone: nothing left, so a human
    report = _tie("Jay Miller", "1943-07-19", ["Jay Miller (basketball)"],
                  {"Jay Miller (basketball)": ("Q518561", "1943-01-01", True)},
                  rejected="Q518561")
    assert report["verified"] == {}
    why = report["needs_a_human"][0]["candidates"][0]["why_not"]
    assert "rule 3" in why, why
    print("test_rule3_the_flagged_namesake_is_dropped_and_the_rest_decides PASS")


def test_rule4_one_left_is_accepted_several_are_listed():
    """Chris Johnson: two of the name, both born 17-24 years before a 2009
    first stint. Still a coin toss, still a human."""
    report = _tie("Chris Johnson", "",
                  ["Chris Johnson (basketball, born 1985)",
                   "Chris Johnson (basketball, born 1990)"],
                  {"Chris Johnson (basketball, born 1985)":
                       ("Q2455991", "1985-04-15", True),
                   "Chris Johnson (basketball, born 1990)":
                       ("Q3662449", "1990-09-29", True)},
                  career=["2009-2010"])
    assert report["verified"] == {}
    assert "2 different basketball players" in report["needs_a_human"][0]["why"]
    # one left -> accepted, and the summary names the rule
    report = _tie("Andrew Harrison", "1994-10-28",
                  ["Andrew Harrison (basketball)", "Aaron Harrison"],
                  {"Andrew Harrison (basketball)": ("Q1", "1994-10-28", True),
                   "Aaron Harrison": ("Q2", "1994-10-28", True)})
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rpu.print_resolution(report)
    out = buf.getvalue()
    assert "Decided by" in out and "rule 1: exact name" in out, out
    print("test_rule4_one_left_is_accepted_several_are_listed PASS")


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
    test_a_human_verified_article_skips_the_gate_and_the_search()
    test_a_missing_or_disambiguation_article_is_skipped_not_written()
    test_a_redirect_to_a_real_article_is_written_as_its_target()
    test_human_and_machine_players_resolve_side_by_side()
    test_an_unsigned_human_entry_is_ignored()
    test_settle_drops_the_overridden_players_from_the_review()
    test_the_bio_fetcher_trusts_a_human_verified_item_without_p106()
    test_rule1_name_key_strips_what_it_should()
    test_rule1_a_different_name_is_never_a_second_match()
    test_rule1_redirect_aliases_are_one_candidate()
    test_rule1_a_redirect_with_another_name_alone_is_not_enough()
    test_rule2_the_career_stands_in_for_a_missing_birth_date()
    test_rule2_window_edges_and_a_new_player()
    test_rule2_no_date_and_no_career_is_still_a_human()
    test_rule2_is_only_used_without_a_basketball_reference_date()
    test_rule3_the_flagged_namesake_is_dropped_and_the_rest_decides()
    test_rule4_one_left_is_accepted_several_are_listed()
    print("\nall player-URL override tests PASS")
