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


if __name__ == "__main__":
    test_the_sweep_names_the_record_built_from_the_wrong_article()
    test_the_fix_replaces_the_invented_career_with_the_real_one()
    test_dedupe_unions_the_stints_and_keeps_the_canonical_key()
    test_dedupe_refuses_to_bury_a_wrong_article_record()
    print("\nALL ARTICLE AUDIT TESTS PASS")
