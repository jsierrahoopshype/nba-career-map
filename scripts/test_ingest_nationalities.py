"""Regression tests for the nationality-CSV ingest (scripts/ingest_nationalities.py).

Run:  python3 scripts/test_ingest_nationalities.py
"""
from __future__ import annotations

from ingest_nationalities import (apply_nationality, build_norm_groups,
                                  match_players, _resolve_countries)


def test_exact_and_normkey_matching():
    players = [
        {"player": "Nikola Jokic", "display_name": "Nikola Jokić"},
        {"player": "Mike James (1990)", "aliases": ["Mike James"]},
        {"player": "Mike James (1992)", "aliases": ["Mike James"]},
    ]
    rows = [
        {"player": "Nikola Jokic", "nationality": "Serbia"},   # exact
        {"player": "Nikola Jokić", "nationality": "Serbia"},   # normkey (diacritic)
        {"player": "Mike James", "nationality": "USA"},        # ambiguous: two candidates
        {"player": "Nobody Here", "nationality": "USA"},       # unmatched
    ]
    exact, via_normkey, ambiguous, unmatched = match_players(rows, players)
    assert len(exact) == 1 and exact[0][1]["player"] == "Nikola Jokic"
    assert len(via_normkey) == 1 and via_normkey[0][1]["player"] == "Nikola Jokic"
    assert len(ambiguous) == 1 and ambiguous[0]["csv_player"] == "Mike James"
    assert unmatched == ["Nobody Here"]
    print("test_exact_and_normkey_matching PASS")


def test_set_when_no_existing_value():
    rec = {"player": "X"}
    conflicts = []
    outcome = apply_nationality(rec, "France", conflicts)
    assert outcome == "set"
    assert rec["nationality"] == "France"
    assert conflicts == []
    print("test_set_when_no_existing_value PASS")


def test_single_value_agreement_overwrites():
    """existing='French' (demonym) vs csv='France' (country) — same country,
    single existing value -> overwrite with the CSV's country-name form."""
    rec = {"player": "X", "nationality": "French"}
    conflicts = []
    outcome = apply_nationality(rec, "France", conflicts)
    assert outcome == "agreed_overwrite"
    assert rec["nationality"] == "France"
    assert conflicts == []
    print("test_single_value_agreement_overwrites PASS")


def test_compound_agreement_keeps_richer_existing_value():
    """existing='American / Nigerian' (dual) vs csv='Nigeria' — CSV matches
    ONE of the two; the richer compound value must be KEPT, not overwritten
    with the CSV's single-country value (no information loss)."""
    rec = {"player": "X", "nationality": "American / Nigerian"}
    conflicts = []
    outcome = apply_nationality(rec, "Nigeria", conflicts)
    assert outcome == "agreed_kept_existing"
    assert rec["nationality"] == "American / Nigerian"   # unchanged
    assert conflicts == []
    print("test_compound_agreement_keeps_richer_existing_value PASS")


def test_genuine_conflict_logged_not_overwritten():
    rec = {"player": "X", "nationality": "Croatian / Bosnian"}
    conflicts = []
    outcome = apply_nationality(rec, "Slovenia", conflicts)
    assert outcome == "conflict"
    assert rec["nationality"] == "Croatian / Bosnian"   # untouched
    assert len(conflicts) == 1
    assert conflicts[0]["player"] == "X"
    assert conflicts[0]["csv_nationality"] == "Slovenia"
    print("test_genuine_conflict_logged_not_overwritten PASS")


def test_puerto_rico_vs_united_states_is_a_real_conflict():
    """Puerto Rico and the United States are deliberately NOT treated as
    equivalent (the site already gives Puerto Rico its own flag entry) — a
    player recorded as Puerto Rican whose CSV row says United States is a
    genuine conflict for human review, not an auto-resolved agreement."""
    rec = {"player": "X", "nationality": "Puerto Rican"}
    conflicts = []
    outcome = apply_nationality(rec, "United States", conflicts)
    assert outcome == "conflict"
    print("test_puerto_rico_vs_united_states_is_a_real_conflict PASS")


def test_country_name_aliases_resolve_fairly():
    """"United States" (CSV spelling) must compare equal to "American"
    (existing demonym) via the country-alias layer, not report a conflict."""
    rec = {"player": "X", "nationality": "American"}
    conflicts = []
    outcome = apply_nationality(rec, "United States", conflicts)
    assert outcome == "agreed_overwrite"
    assert rec["nationality"] == "United States"
    assert conflicts == []
    print("test_country_name_aliases_resolve_fairly PASS")


def test_resolve_countries_splits_compound_values():
    assert _resolve_countries("American / Nigerian") == {"USA", "Nigeria"}
    assert _resolve_countries("France") == {"France"}
    print("test_resolve_countries_splits_compound_values PASS")


if __name__ == "__main__":
    test_exact_and_normkey_matching()
    test_set_when_no_existing_value()
    test_single_value_agreement_overwrites()
    test_compound_agreement_keeps_richer_existing_value()
    test_genuine_conflict_logged_not_overwritten()
    test_puerto_rico_vs_united_states_is_a_real_conflict()
    test_country_name_aliases_resolve_fairly()
    test_resolve_countries_splits_compound_values()
    print("\nALL NATIONALITY-INGEST TESTS PASS")
