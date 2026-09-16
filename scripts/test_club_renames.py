"""Tests for the club-rename (containment) guard.

The interesting half is what it REFUSES. A rule that merges "Al Riyadi" into
"Al Riyadi Club Beirut" and also merges "Real Madrid" into "Real Madrid
Castilla" has not helped: it has swapped a phantom transfer for a silently
deleted real one, and the deleted one is the harder error to notice.

Run:  python3 scripts/test_club_renames.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_club_renames as mcr  # noqa: E402
import update_careers as uc  # noqa: E402
from team_normalizer import (GENERIC_TOKENS, RESERVE_TOKENS,  # noqa: E402
                             TeamNormalizer, rename_containment,
                             spelling_tokens)

ROOT = Path(__file__).resolve().parent.parent
DB = json.loads((ROOT / "data" / "players" / "nba_players_careers.json")
                .read_text(encoding="utf-8"))

SPAIN = ("Madrid", "Spain")


def test_a_longer_name_for_the_same_club_is_not_a_transfer():
    for a, b, place in (
        ("Al Riyadi Beirut", "Al Riyadi Club Beirut", ("Manara", "Lebanon")),
        ("ASVEL", "ASVEL Villeurbanne", ("Villeurbanne", "France")),
        ("Panathinaikos", "Panathinaikos B.C.", ("Athens", "Greece")),
        ("Maccabi Haifa", "Maccabi Haifa B.C.", ("Haifa", "Israel")),
        ("Kuwait SC", "Al-Kuwait SC", ("Kuwait City", "Kuwait")),
        ("Trabzonspor", "Trabzonspor Basketball", ("Trabzon", "Turkey")),
        ("Tijuana Zonkeys", "Zonkeys de Tijuana", ("Tijuana", "Mexico")),
    ):
        ok, why = rename_containment(a, b, place_a=place, place_b=place)
        assert ok is True, f"{a!r} / {b!r} refused: {why}"
    print("test_a_longer_name_for_the_same_club_is_not_a_transfer PASS")


def test_reserve_sides_are_never_merged_into_their_parent():
    """The expensive mistake: a farm team folded into its first team.

    Players move between the two for real, and every one of those transfers
    would vanish. Each of these is refused, and the reason names the marker so
    a future reader can see it was refused on purpose.
    """
    pairs = [
        ("Real Madrid", "Real Madrid Castilla"),
        ("Barcelona", "Barcelona B"),
        ("Baskonia", "Baskonia B"),
        ("Joventut", "Joventut B"),
        ("Bayern Munich", "Bayern Munich II"),
        ("Valencia", "Valencia Junior"),
        ("Zalgiris", "Zalgiris-2"),
        ("Partizan", "Partizan Academy"),
        ("Unicaja", "Unicaja Cantera"),
        ("Estudiantes", "Estudiantes U18"),
    ]
    for a, b in pairs:
        ok, why = rename_containment(a, b, place_a=SPAIN, place_b=SPAIN)
        assert ok is not True, f"{a!r} / {b!r} was merged into its parent"
        assert why, f"{a!r} / {b!r} refused with no reason"
    # ...and the marker is what did it, not luck: Castilla is refused as a
    # distinguishing word, the rest by the reserve list.
    _ok, why = rename_containment("Barcelona", "Barcelona B",
                                  place_a=SPAIN, place_b=SPAIN)
    assert "reserve" in why, why
    print(f"test_reserve_sides_are_never_merged_into_their_parent PASS "
          f"({len(pairs)} pairs)")


def test_a_club_type_word_is_not_mistaken_for_a_reserve_marker():
    """The other side of the same coin: BC, SC, CB are not second teams."""
    for suffix in ("BC", "B.C.", "SC", "CB", "KK", "CD", "Basket",
                   "Basketball", "Club"):
        ok, why = rename_containment("Vaqueros", f"Vaqueros {suffix}",
                                     place_a=SPAIN, place_b=SPAIN)
        assert ok is True, f"'Vaqueros {suffix}' refused: {why}"
    assert not (GENERIC_TOKENS & RESERVE_TOKENS), \
        "a token cannot be both a descriptor and a reserve marker"
    print("test_a_club_type_word_is_not_mistaken_for_a_reserve_marker PASS")


def test_clubs_that_merely_share_a_name_are_reported_not_merged():
    for a, b, pa, pb in (
        ("Al Nasr", "Al-Nasr Benghazi", ("Dubai", "UAE"),
         ("Benghazi", "Libya")),
        ("Al Ahly", "Al Ahly Benghazi", ("Cairo", "Egypt"),
         ("Benghazi", "Libya")),
        ("Palma", "La Palma", ("Palma", "Spain"),
         ("Santa Cruz de La Palma", "Spain")),
    ):
        ok, why = rename_containment(a, b, place_a=pa, place_b=pb)
        assert ok is None, f"{a!r} / {b!r} -> {ok} ({why})"
        assert "different" in why, why
    print("test_clubs_that_merely_share_a_name_are_reported_not_merged PASS")


def test_a_bare_name_inside_several_clubs_goes_to_review():
    """'Al Ahly' is inside Al Ahly Cairo, Al Ahly Benghazi and Al Ahly Ly."""
    merges, held = mcr.plan([DB])
    merged = {v for _c, vs in merges for v in vs} | {c for c, _ in merges}
    for shared in ("Al Ahly", "Al-Ahli", "Al-Ittihad", "Indios"):
        assert shared not in merged, f"{shared!r} was merged despite sharing"
    reasons = {why for _m, why in held}
    assert any("shared by" in r for r in reasons), sorted(reasons)
    print("test_a_bare_name_inside_several_clubs_goes_to_review PASS")


def test_groups_are_chains_not_stars():
    """Two names inside a third are not thereby the same as each other.

    Las Vegas Silvers and Albuquerque Silvers both sit inside
    "Las Vegas/Albuquerque Silvers" and neither sits inside the other. Left to
    union-find they became one club in two cities.
    """
    merges, _held = mcr.plan([DB])
    for canonical, variants in merges:
        members = sorted([canonical, *variants])
        toks = {m: set(spelling_tokens(m)) for m in members}
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                assert toks[a] < toks[b] or toks[b] < toks[a], \
                    f"{a!r} and {b!r} are in one group but neither contains " \
                    f"the other"
    print(f"test_groups_are_chains_not_stars PASS ({len(merges)} groups)")


def test_the_move_guard_suppresses_the_rename_and_keeps_the_real_move():
    """The two ledger rows that prompted all of this."""
    places, cities = uc.place_index(DB)
    n = TeamNormalizer()

    def why(a, b):
        return uc.classify_move(n, a, b, club_places=places,
                                cities_by_country=cities)

    posted, reason = why("Al Riyadi", "Al Riyadi Club Beirut")
    assert not posted and reason in ("same-club", "club-rename"), reason
    posted, reason = why("Homenetmen Beirut", "Al Riyadi Club Beirut")
    assert posted, f"Lofton's real move was suppressed as {reason}"
    posted, _r = why("Real Madrid", "Real Madrid Castilla")
    assert posted, "a move to the reserve side was suppressed"
    print("test_the_move_guard_suppresses_the_rename_and_keeps_the_real_move "
          "PASS")


def test_no_phantom_rename_survives_in_the_ledger():
    doc = json.loads((ROOT / "data" / "logs" / "transactions.json")
                     .read_text(encoding="utf-8"))
    txns = doc["transactions"] if isinstance(doc, dict) else doc
    n = TeamNormalizer()
    bad = [t for t in txns
           if n.normalize(t.get("from_team") or "")
           == n.normalize(t.get("to_team") or "")]
    assert not bad, f"phantom transfers still in the ledger: {bad}"
    print(f"test_no_phantom_rename_survives_in_the_ledger PASS "
          f"({len(txns)} rows)")


if __name__ == "__main__":
    test_a_longer_name_for_the_same_club_is_not_a_transfer()
    test_reserve_sides_are_never_merged_into_their_parent()
    test_a_club_type_word_is_not_mistaken_for_a_reserve_marker()
    test_clubs_that_merely_share_a_name_are_reported_not_merged()
    test_a_bare_name_inside_several_clubs_goes_to_review()
    test_groups_are_chains_not_stars()
    test_the_move_guard_suppresses_the_rename_and_keeps_the_real_move()
    test_no_phantom_rename_survives_in_the_ledger()
    print("\nall club-rename tests PASS")
