"""Display names are the record's own name, never the article title.

The bug this guards: update_careers.merge_player wrote the article it read into
`display_name`, so after the override re-scrapes ~200 records read "Anthony
Edwards (basketball)", "Harry Giles III" or "Cat Barber" on their pages, in
search, on team/club/place pages and in the quiz.

Run:  python3 scripts/test_display_names.py
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from names import display_name_for, strip_disambiguator  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
WIKI = "https://en.wikipedia.org/wiki/"

# Any parenthetical in a display name is a Wikipedia disambiguator leaking
# through: "(basketball)", "(basketball, born 1990)", "(basketball player)",
# "(1990)". A player's shown name never legitimately carries one.
_PAREN = re.compile(r"\([^)]*\)")


def test_no_display_name_carries_a_disambiguator():
    for path in (CAREERS, READY):
        rows = json.loads(path.read_text(encoding="utf-8"))
        bad = [(r.get("player"), r["display_name"]) for r in rows
               if r.get("display_name") and (_PAREN.search(r["display_name"])
                                             or "(basketball" in r["display_name"])]
        assert not bad, f"{path.name}: {len(bad)} display name(s) with a " \
                        f"disambiguator, e.g. {bad[:5]}"
    print("test_no_display_name_carries_a_disambiguator PASS")


def test_the_rule():
    d = display_name_for
    # a disambiguator is stripped
    assert d("Anthony Edwards", "Anthony Edwards (basketball)") == "Anthony Edwards"
    assert d("Mike James (1990)", "",
             "Mike James (basketball, born 1990)") == "Mike James"
    # a suffix the key does not carry is not the record's name
    assert d("Harry Giles", "Harry Giles III") == "Harry Giles"
    assert d("Michael Porter", "", "Michael Porter Jr.") == "Michael Porter"
    # ...but one the key DOES carry stays
    assert d("Duane Washington Jr", "Duane Washington Jr.") == "Duane Washington Jr."
    assert d("Walt Lemon Jr.", "", "Walt Lemon Jr.") == "Walt Lemon Jr."
    # a different name is not a spelling of this one
    assert d("Anthony Barber", "Cat Barber") == "Anthony Barber"
    assert d("Kenny McIntosh", "", "Kennedy McIntosh") == "Kenny McIntosh"
    # the same name with accents / initials / capitalisation is kept
    assert d("Nikola Jokic", "", "Nikola Jokić") == "Nikola Jokić"
    assert d("AJ Green", "", "A. J. Green (basketball)") == "A. J. Green"
    assert d("Mamadou N'diaye", "",
             "Mamadou N'Diaye (basketball, born 1975)") == "Mamadou N'Diaye"
    # the record's own spelling wins over the title's
    assert d("Alex Garcia", "Alex García", "Alex Garcia (basketball)") == "Alex García"
    assert strip_disambiguator("Travis Williams (basketball player)") == \
        "Travis Williams"
    print("test_the_rule PASS")


def _db(tmp: Path, record: dict):
    import update_careers as uc
    uc.CAREERS = careers = tmp / "careers.json"
    uc.LOCATIONS = tmp / "locations.json"
    uc.REVIEW = tmp / "review.json"
    careers.write_text(json.dumps([record]), encoding="utf-8")
    (tmp / "locations.json").write_text(json.dumps({
        "New Orleans Pelicans": {"team": "New Orleans Pelicans",
                                 "city": "New Orleans", "state": "Louisiana",
                                 "country": "USA"}}), encoding="utf-8")
    (tmp / "review.json").write_text("{}", encoding="utf-8")
    return uc, uc.Database()


class _Client:
    def __init__(self, pages):
        self.pages = pages

    def get_wikitext_and_title(self, title):
        page = self.pages.get(title)
        return (page, title) if page else (None, None)


class _Overrides:
    """A temporary overrides file installed for player_urls."""

    def __init__(self, doc):
        self.doc = doc

    def __enter__(self):
        import player_urls
        self.pu = player_urls
        self.dir = tempfile.TemporaryDirectory()
        path = Path(self.dir.name) / "player_url_overrides.json"
        path.write_text(json.dumps(self.doc), encoding="utf-8")
        self.saved = player_urls.OVERRIDES
        player_urls.OVERRIDES = path
        player_urls.reset_cache()

    def __exit__(self, *exc):
        self.pu.OVERRIDES = self.saved
        self.pu.reset_cache()
        self.dir.cleanup()


def _rescrape(record, title, overrides=None):
    """Run one record through merge_player against a single article."""
    import importlib
    import update_careers as uc
    importlib.reload(uc)
    with tempfile.TemporaryDirectory() as d, _Overrides(overrides or {}):
        uc, db = _db(Path(d), record)
        client = _Client({title: "{{Infobox basketball biography}}"})
        parsed = {"career_history": [{"years": "2021–present",
                                      "team": "New Orleans Pelicans"}],
                  "current_team": "New Orleans Pelicans", "status": "success"}
        saved = uc.parse_player
        uc.parse_player = lambda wt, name, norm: dict(parsed)
        try:
            rec, *_ = uc.merge_player(db, record["player"], client, {}, set(),
                                      2026)
        finally:
            uc.parse_player = saved
    return rec


def test_an_override_rescrape_keeps_the_display_name():
    """The exact path that broke: an override points at "Herbert Jones
    (basketball)", and the record must still read "Herbert Jones"."""
    rec = _rescrape(
        {"player": "Herbert Jones", "display_name": "Herbert Jones",
         "wikipedia_url": WIKI + "Herbert_Jones", "career_history": [],
         "status": "nba_active"},
        "Herbert Jones (basketball)",
        {"overrides": {"Herbert Jones": {
            "wikipedia_url": WIKI + "Herbert_Jones_(basketball)",
            "verified": True}}})
    assert rec["display_name"] == "Herbert Jones", rec["display_name"]
    # the article is still the link
    assert rec["wikipedia_url"] == WIKI + "Herbert_Jones_(basketball)"
    assert rec["player"] == "Herbert Jones"
    print("test_an_override_rescrape_keeps_the_display_name PASS")


def test_a_suffixed_article_does_not_rename_the_record():
    rec = _rescrape(
        {"player": "Harry Giles", "display_name": "Harry Giles",
         "wikipedia_url": WIKI + "Harry_Giles", "career_history": [],
         "status": "nba_active"},
        "Harry Giles III",
        {"overrides": {"Harry Giles": {
            "wikipedia_url": WIKI + "Harry_Giles_III", "verified": True}}})
    assert rec["display_name"] == "Harry Giles", rec["display_name"]
    print("test_a_suffixed_article_does_not_rename_the_record PASS")


def test_a_record_with_no_display_name_gets_a_clean_one():
    """A record with none takes the title's spelling only when it is the same
    name -- never its disambiguator."""
    rec = _rescrape(
        {"player": "Pepe Sanchez", "wikipedia_url": WIKI + "Pepe_Sanchez",
         "career_history": [], "status": "retired"},
        "Pepe Sánchez (basketball)",
        {"overrides": {"Pepe Sanchez": {
            "wikipedia_url": WIKI + "Pepe_S%C3%A1nchez_(basketball)",
            "verified": True}}})
    assert rec["display_name"] == "Pepe Sánchez", rec["display_name"]
    print("test_a_record_with_no_display_name_gets_a_clean_one PASS")


def test_a_legacy_disambiguator_is_cleaned_on_the_next_run():
    rec = _rescrape(
        {"player": "Anthony Edwards",
         "display_name": "Anthony Edwards (basketball)",
         "wikipedia_url": WIKI + "Anthony_Edwards_(basketball)",
         "career_history": [], "status": "nba_active"},
        "Anthony Edwards (basketball)",
        {"overrides": {"Anthony Edwards": {
            "wikipedia_url": WIKI + "Anthony_Edwards_(basketball)",
            "verified": True}}})
    assert rec["display_name"] == "Anthony Edwards", rec["display_name"]
    print("test_a_legacy_disambiguator_is_cleaned_on_the_next_run PASS")


if __name__ == "__main__":
    test_no_display_name_carries_a_disambiguator()
    test_the_rule()
    test_an_override_rescrape_keeps_the_display_name()
    test_a_suffixed_article_does_not_rename_the_record()
    test_a_record_with_no_display_name_gets_a_clean_one()
    test_a_legacy_disambiguator_is_cleaned_on_the_next_run()
    print("\nall display-name tests PASS")
