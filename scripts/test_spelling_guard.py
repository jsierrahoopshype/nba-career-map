"""Tests for the spelling-variant guard on the move detector.

The guard exists because "Ironi Nes Ziona -> Ironi Ness Ziona" was logged as a
transfer: one club, one S, neither spelling in the alias table. The hard part
is not catching that pair, it is catching it WITHOUT swallowing the many real
clubs in this dataset that sit one edit from each other.

Run:  python3 scripts/test_spelling_guard.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import update_careers as u  # noqa: E402
from team_normalizer import (TeamNormalizer, edit_distance,  # noqa: E402
                             is_spelling_variant, spelling_key)

# Same club, written two ways. Every one of these must be suppressed.
SAME_CLUB = [
    ("Ironi Nes Ziona", "Ironi Ness Ziona"),      # the reported bug
    ("Besiktas", "Beşiktaş"),                      # diacritics
    ("P.A.O.K.", "PAOK"),                          # punctuation
    ("Talk 'N Text Phone Pals", "Talk 'n Text Phone Pals"),   # case
    ("Erie BayHawks", "Erie Bayhawks"),            # internal capital
    ("Caja Madrid", "Cajamadrid"),                 # spacing
    ("Maccabi Ra'anana", "Maccabi Raanana"),       # apostrophe
    ("Sheboygan Red Skins", "Sheboygan Redskins"),
    ("Arkansas RimRockers", "Arkansas Rimrockers"),
    ("medi bayreuth", "Medi Bayreuth"),
]

# Genuinely DIFFERENT clubs that a naive edit-distance rule would merge. Every
# one of these must survive as a real move. All are real pairs from the
# corpus, and all but the last two are a single edit apart.
DIFFERENT_CLUBS = [
    ("Palencia", "Valencia"),
    ("Palma", "Parma"),
    ("Iraklio", "Iraklis"),
    ("Chicago Rockers", "Chicago Rockets"),
    ("FAP", "FMP"),
    ("Al Shaab", "Al Shabab"),
    ("Grand Rapids Mackers", "Grand Rapids Tackers"),
    ("Tenerife AB", "Tenerife CB"),
    ("Al Nasr", "Al Nassr"),        # same spelling key, but Dubai vs Riyadh
    ("Brooklyn Nets", "Chicago Bulls"),
]


def test_spelling_key_folds_only_noise():
    assert spelling_key("Ironi Nes Ziona") == spelling_key("Ironi Ness Ziona")
    assert spelling_key("Beşiktaş") == spelling_key("Besiktas")
    assert spelling_key("P.A.O.K.") == spelling_key("PAOK")
    assert spelling_key("Palencia") != spelling_key("Valencia")
    assert spelling_key("") == ""
    print("test_spelling_key_folds_only_noise PASS")


def test_same_club_pairs_suppressed():
    for a, b in SAME_CLUB:
        assert is_spelling_variant(a, b), f"{a!r} vs {b!r} should be one club"
        assert is_spelling_variant(b, a), "must be symmetric"
    print(f"test_same_club_pairs_suppressed PASS ({len(SAME_CLUB)} pairs)")


def test_different_clubs_survive():
    for a, b in DIFFERENT_CLUBS:
        assert not is_spelling_variant(a, b), \
            f"{a!r} vs {b!r} are different clubs and must stay a real move"
    print(f"test_different_clubs_survive PASS ({len(DIFFERENT_CLUBS)} pairs)")


def test_empty_and_degenerate():
    assert not is_spelling_variant("", "Beşiktaş")
    assert not is_spelling_variant("Beşiktaş", "")
    # Names that reduce to an empty key must not collide with each other.
    assert not is_spelling_variant("...", "???")
    print("test_empty_and_degenerate PASS")


def test_classify_move_reasons():
    sb = Path(tempfile.mkdtemp())
    ap = sb / "team_aliases.json"
    ap.write_text(json.dumps({"aliases": {"Beşiktaş Gain": "Beşiktaş"}}))
    tn = TeamNormalizer(aliases_path=ap)

    assert u.classify_move(tn, "", "Beşiktaş") == (False, "incomplete")
    assert u.classify_move(tn, "Beşiktaş Gain", "Beşiktaş") == (False, "same-club")
    assert u.classify_move(tn, "Ironi Nes Ziona", "Ironi Ness Ziona") \
        == (False, "spelling-variant")
    assert u.classify_move(tn, "Brooklyn Nets", "Chicago Bulls") == (True, "real")
    # close but different -> still a move, just flagged
    is_move, why = u.classify_move(tn, "Palencia", "Valencia")
    assert is_move and why == "near-miss", (is_move, why)
    print("test_classify_move_reasons PASS")


def test_phantom_never_reaches_the_ledger():
    """End to end: a spelling variant writes no transaction but is not lost."""
    sb = Path(tempfile.mkdtemp())
    u.TRANSACTIONS = sb / "transactions.json"
    u.SPELLING_REVIEW = sb / "spelling_review.json"

    summary = {"date": "2026-09-10", "team_moves": [], "spelling_review": []}
    ap = sb / "team_aliases.json"
    ap.write_text(json.dumps({"aliases": {}}))
    tn = TeamNormalizer(aliases_path=ap)

    for player, frm, to in [
        ("Jacob Wiley", "Ironi Nes Ziona", "Ironi Ness Ziona"),   # phantom
        ("Real Mover", "Santeros de Aguada", "Ironi Ness Ziona"), # real
    ]:
        is_move, why = u.classify_move(tn, frm, to)
        if is_move:
            summary["team_moves"].append({"player": player, "from": frm, "to": to})
        if why in ("spelling-variant", "near-miss"):
            summary["spelling_review"].append(
                {"player": player, "from": frm, "to": to,
                 "reason": why, "posted": is_move})

    u._append_transactions(summary)
    u._append_spelling_review(summary)

    led = json.loads(u.TRANSACTIONS.read_text())["transactions"]
    assert len(led) == 1, f"only the real move belongs in the ledger: {led}"
    assert led[0]["player"] == "Real Mover"

    rev = json.loads(u.SPELLING_REVIEW.read_text())["pairs"]
    assert len(rev) == 1 and rev[0]["player"] == "Jacob Wiley", rev
    assert rev[0]["reason"] == "spelling-variant" and rev[0]["posted"] is False
    assert rev[0]["date"] == "2026-09-10"
    print("test_phantom_never_reaches_the_ledger PASS")


def test_review_file_is_append_only():
    sb = Path(tempfile.mkdtemp())
    u.SPELLING_REVIEW = sb / "spelling_review.json"
    u._append_spelling_review({"date": "2026-01-01", "spelling_review": [
        {"player": "A", "from": "X", "to": "X.", "reason": "spelling-variant",
         "posted": False}]})
    u._append_spelling_review({"date": "2026-01-02", "spelling_review": []})  # no-op
    u._append_spelling_review({"date": "2026-01-03", "spelling_review": [
        {"player": "B", "from": "Y", "to": "Y!", "reason": "spelling-variant",
         "posted": False}]})
    pairs = json.loads(u.SPELLING_REVIEW.read_text())["pairs"]
    assert len(pairs) == 2, "must append, not overwrite"
    assert pairs[1]["date"] == "2026-01-03"
    print("test_review_file_is_append_only PASS")


def test_edit_distance_cap():
    assert edit_distance("abc", "abc") == 0
    assert edit_distance("abc", "abd") == 1
    assert edit_distance("kitten", "sitting") == 3
    # far apart: returns cap+1 rather than the true distance
    assert edit_distance("abc", "zzzzzzzzzz", cap=2) == 3
    print("test_edit_distance_cap PASS")


if __name__ == "__main__":
    test_spelling_key_folds_only_noise()
    test_same_club_pairs_suppressed()
    test_different_clubs_survive()
    test_empty_and_degenerate()
    test_classify_move_reasons()
    test_phantom_never_reaches_the_ledger()
    test_review_file_is_append_only()
    test_edit_distance_cap()
    print("\nall spelling-guard tests PASS")
