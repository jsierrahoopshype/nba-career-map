"""Regression tests for the All-Star-count CSV ingest
(scripts/ingest_allstar_counts.py).

Run:  python3 scripts/test_ingest_allstar_counts.py
"""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path

from ingest_allstar_counts import extract_counts, match_counts, _ALL_STAR_RE


def _write_csv(rows: list[dict]) -> Path:
    fieldnames = ["PLAYER / COACH", "CAREER AWARDS"]
    p = Path(tempfile.mktemp(suffix=".csv"))
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def test_regex_isolates_all_star_selection_not_mvp_or_five():
    """"All-Star (N)" must match; "All-Star MVP (N)" and "All-Star Five (N)"
    (distinct awards that also contain the substring "All-Star") must not."""
    assert _ALL_STAR_RE.search("All-Star (18)").group(1) == "18"
    assert _ALL_STAR_RE.search("All-Star MVP (4)") is None
    assert _ALL_STAR_RE.search("All-Star Five (1)") is None
    print("test_regex_isolates_all_star_selection_not_mvp_or_five PASS")


def test_dedupe_across_repeated_rows():
    """The CAREER AWARDS summary repeats per row (one row per season award);
    the same count across all of a player's rows collapses to one value."""
    csv_path = _write_csv([
        {"PLAYER / COACH": "Kobe Bryant",
         "CAREER AWARDS": "All-Defensive First Team (9), All-Star (18)"},
        {"PLAYER / COACH": "Kobe Bryant",
         "CAREER AWARDS": "All-Defensive First Team (9), All-Star (18)"},
        {"PLAYER / COACH": "Klay Thompson",
         "CAREER AWARDS": "All-Star MVP (1), NBA Champion (4)"},  # no plain All-Star(N)
    ])
    counts, conflicts = extract_counts(csv_path)
    assert counts == {"Kobe Bryant": 18}
    assert conflicts == []
    print("test_dedupe_across_repeated_rows PASS")


def test_internal_conflict_across_rows_is_skipped_not_guessed():
    """If a player's own rows disagree on the count (shouldn't happen for a
    static field, but checked), it's excluded from counts and logged."""
    csv_path = _write_csv([
        {"PLAYER / COACH": "Weird Case", "CAREER AWARDS": "All-Star (5)"},
        {"PLAYER / COACH": "Weird Case", "CAREER AWARDS": "All-Star (6)"},
    ])
    counts, conflicts = extract_counts(csv_path)
    assert "Weird Case" not in counts
    assert conflicts == [{"csv_player": "Weird Case", "conflicting_counts": [5, 6]}]
    print("test_internal_conflict_across_rows_is_skipped_not_guessed PASS")


def test_match_tiers_reuse_normkey_and_ambiguity_rules():
    players = [
        {"player": "Nikola Jokic", "display_name": "Nikola Jokić"},
        {"player": "Mike James (1990)", "aliases": ["Mike James"]},
        {"player": "Mike James (1992)", "aliases": ["Mike James"]},
    ]
    counts = {"Nikola Jokic": 3, "Nikola Jokić": 3, "Mike James": 1, "Nobody Here": 2}
    exact, via_normkey, ambiguous, unmatched = match_counts(counts, players)
    matched_names = {n for n, _, _ in exact + via_normkey}
    assert "Nikola Jokic" in matched_names
    assert any(a["csv_player"] == "Mike James" for a in ambiguous)
    assert unmatched == ["Nobody Here"]
    print("test_match_tiers_reuse_normkey_and_ambiguity_rules PASS")


if __name__ == "__main__":
    test_regex_isolates_all_star_selection_not_mvp_or_five()
    test_dedupe_across_repeated_rows()
    test_internal_conflict_across_rows_is_skipped_not_guessed()
    test_match_tiers_reuse_normkey_and_ambiguity_rules()
    print("\nALL ALL-STAR-COUNT INGEST TESTS PASS")
