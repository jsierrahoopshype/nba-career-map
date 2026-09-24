"""Tests for the newly-detected vs newly-started signings guard.

Run:  python3 scripts/test_signing_guard.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signing_guard as sg  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

WALKER = {
    "player": "Lonnie Walker",
    "current_team": "Partizan",
    "career_history": [
        {"team": "Philadelphia 76ers", "years": "2025"},
        {"team": "Partizan", "years": "2025–2026"},
    ],
}
CRUTCHER = {
    "player": "Jalen Crutcher",
    "current_team": "Iowa Wolves",
    "career_history": [
        {"team": "Osceola Magic", "years": "2025"},
        {"team": "Iowa Wolves", "years": "2025–present", "start_date": "2025-12-24"},
        {"team": "Leones de Ponce", "years": "2026"},
    ],
}


def test_a_stint_that_started_before_the_ledger_is_not_a_signing():
    verdict, why = sg.is_new_signing(WALKER, "Partizan")
    assert verdict is False, (verdict, why)
    assert "2025" in why, why
    assert not sg.counts_as_signing(WALKER, "Partizan")
    print("test_a_stint_that_started_before_the_ledger_is_not_a_signing PASS")


def test_an_exact_signing_date_beats_the_year_span():
    """'2025-present' reads as 2025 either way, but the curated date is what
    the reason line must quote -- that is the fact a human can check."""
    verdict, why = sg.is_new_signing(CRUTCHER, "Iowa Wolves")
    assert verdict is False, (verdict, why)
    assert "2025-12-24" in why, why
    print("test_an_exact_signing_date_beats_the_year_span PASS")


def test_a_stint_starting_in_the_ledger_year_counts():
    rec = {"career_history": [{"team": "Lokomotiv Kuban", "years": "2026–present"}]}
    verdict, _why = sg.is_new_signing(rec, "Lokomotiv Kuban")
    assert verdict is True
    rec2 = {"career_history": [{"team": "Anywhere", "years": "2027"}]}
    assert sg.is_new_signing(rec2, "Anywhere")[0] is True
    print("test_a_stint_starting_in_the_ledger_year_counts PASS")


def test_an_exact_date_after_the_ledger_start_counts():
    rec = {"career_history": [{"team": "X", "years": "2026–present",
                               "start_date": "2026-07-11"}]}
    assert sg.is_new_signing(rec, "X")[0] is True, "the ledger start day itself counts"
    rec2 = {"career_history": [{"team": "X", "years": "2026–present",
                                "start_date": "2026-07-10"}]}
    assert sg.is_new_signing(rec2, "X")[0] is False
    print("test_an_exact_date_after_the_ledger_start_counts PASS")


def test_undetermined_is_reported_not_guessed():
    """A club renamed between runs leaves no stint under the new name. The
    guard must say so, and the live policy must still let it post -- dropping
    a real signing is worse than carrying a stale row."""
    rec = {"career_history": [{"team": "South Bay Lakers", "years": "2025–2026"}]}
    verdict, why = sg.is_new_signing(rec, "South Bay/Coachella Valley Lakers")
    assert verdict is None, (verdict, why)
    assert "no stint" in why, why
    assert sg.counts_as_signing(rec, "South Bay/Coachella Valley Lakers")
    assert sg.is_new_signing({"career_history": []}, "")[0] is None
    print("test_undetermined_is_reported_not_guessed PASS")


def test_a_return_is_judged_on_the_latest_spell():
    rec = {"career_history": [{"team": "Real Madrid", "years": "2017–2021"},
                              {"team": "Houston Rockets", "years": "2021–2023"},
                              {"team": "Real Madrid", "years": "2026–present"}]}
    assert sg.is_new_signing(rec, "Real Madrid")[0] is True, \
        "the spell he just started, not the one he finished years ago"
    print("test_a_return_is_judged_on_the_latest_spell PASS")


def test_curated_start_dates_survive_a_reparse():
    """apply_start_dates stamps a fresh career_history -- the one the daily
    Wikipedia pass rebuilds from scratch -- and matches on the start YEAR, so
    a span that later closes still matches."""
    tmp = Path(tempfile.mkdtemp()) / "start_dates.json"
    tmp.write_text(json.dumps({"overrides": [
        {"player": "Jalen Crutcher", "team": "Iowa Wolves",
         "start_year": 2025, "start_date": "2025-12-24"}]}), encoding="utf-8")
    reparsed = [{"player": "Jalen Crutcher", "career_history": [
        {"team": "Iowa Wolves", "years": "2025–2026"}]}]
    assert sg.apply_start_dates(reparsed, tmp) == 1
    assert reparsed[0]["career_history"][0]["start_date"] == "2025-12-24"
    assert sg.apply_start_dates(reparsed, tmp) == 0, "must be idempotent"
    # a different spell at the same club is not the one that was dated
    other = [{"player": "Jalen Crutcher", "career_history": [
        {"team": "Iowa Wolves", "years": "2021–2022"}]}]
    assert sg.apply_start_dates(other, tmp) == 0
    print("test_curated_start_dates_survive_a_reparse PASS")


def test_the_shipped_overrides_load_and_apply():
    rows = sg.load_start_dates()
    assert rows, "data/teams/stint_start_dates.json has no usable overrides"
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    assert sg.apply_start_dates(db) == 0, \
        "the shipped career data should already carry every curated start date"
    print(f"test_the_shipped_overrides_load_and_apply PASS ({len(rows)} override(s))")


def test_the_shipped_ledger_is_clean():
    """Every entry on file either starts on/after the ledger opened, or is one
    the guard cannot date. Nothing clearly older may survive."""
    db = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                    .read_text(encoding="utf-8"))
    by_name = {}
    for p in db:
        for k in (p.get("player"), p.get("display_name"), *(p.get("aliases") or [])):
            if k:
                by_name.setdefault(k, p)
    doc = json.loads((ROOT / "data" / "logs" / "transactions.json")
                     .read_text(encoding="utf-8"))
    ledger = doc["transactions"] if isinstance(doc, dict) else doc
    stale = [t for t in ledger
             if (rec := by_name.get(t.get("player", "")))
             and sg.is_new_signing(rec, t.get("to_team", ""))[0] is False]
    assert not stale, f"{len(stale)} pre-ledger entries still logged: {stale[:3]}"
    # ...and the Slack cursor must not point past the (now shorter) ledger, or
    # entries that slid under it would be re-posted.
    marker = json.loads((ROOT / "data" / "logs" / "slack_posted_marker.json")
                        .read_text(encoding="utf-8"))
    assert marker.get("posted_count", 0) <= len(ledger), marker
    print(f"test_the_shipped_ledger_is_clean PASS ({len(ledger)} entries)")


def test_ledger_start_is_the_ledgers_first_day():
    doc = json.loads((ROOT / "data" / "logs" / "transactions.json")
                     .read_text(encoding="utf-8"))
    ledger = doc["transactions"] if isinstance(doc, dict) else doc
    first = min(t["date"] for t in ledger if t.get("date"))
    assert sg.LEDGER_START == dt.date.fromisoformat(first), \
        f"guard cutoff {sg.LEDGER_START} != ledger's first day {first}"
    print("test_ledger_start_is_the_ledgers_first_day PASS")


if __name__ == "__main__":
    test_a_stint_that_started_before_the_ledger_is_not_a_signing()
    test_an_exact_signing_date_beats_the_year_span()
    test_a_stint_starting_in_the_ledger_year_counts()
    test_an_exact_date_after_the_ledger_start_counts()
    test_undetermined_is_reported_not_guessed()
    test_a_return_is_judged_on_the_latest_spell()
    test_curated_start_dates_survive_a_reparse()
    test_the_shipped_overrides_load_and_apply()
    test_the_shipped_ledger_is_clean()
    test_ledger_start_is_the_ledgers_first_day()
    print("\nall signing-guard tests PASS")
