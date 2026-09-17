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

    The test is not plain containment, because "Valencia BC" and "Valencia
    Basket" are one club written twice and neither contains the other either.
    What separates the two cases is WHAT the names disagree about: descriptors
    (one club) or place and mascot names (two).
    """
    from team_normalizer import GENERIC_TOKENS

    merges, _held = mcr.plan([DB])
    for canonical, variants in merges:
        members = sorted([canonical, *variants])
        toks = {m: set(spelling_tokens(m)) for m in members}
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                ok = (toks[a] < toks[b] or toks[b] < toks[a]
                      or not (toks[a] ^ toks[b]) - GENERIC_TOKENS)
                assert ok, (f"{a!r} and {b!r} are in one group and differ by "
                            f"{sorted((toks[a] ^ toks[b]) - GENERIC_TOKENS)}")
    # the case this was written for stays refused
    names = {m for c, vs in merges for m in (c, *vs)}
    assert not {"Las Vegas Silvers", "Albuquerque Silvers"} <= names, \
        "two cities were folded into one club"
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
    # A first team to its own reserve side is a real roster move, but it is
    # still a containment pair, and containment pairs are not announced. This
    # is a deliberate trade: the class is closed at the cost of a rare and
    # low-value signing. It goes to the review file like the rest.
    posted, reason = why("Real Madrid", "Real Madrid Castilla")
    assert not posted and reason == "rename-review", reason
    print("test_the_move_guard_suppresses_the_rename_and_keeps_the_real_move "
          "PASS")


def test_the_sponsor_list_still_describes_real_clubs():
    """A hand-written list rots. This is what catches it rotting.

    Every entry must name two clubs that really are in the index, really are a
    containment pair, and really sit in the same city. A typo, a club renamed
    upstream, or a pair someone added from memory fails here rather than
    quietly merging two real clubs on the next run.
    """
    from merge_club_renames import SPONSOR_PAIRS, NOT_SPONSORS, index

    names, places, _cities = index([DB])
    known = set(names)
    problems = []
    for pair in SPONSOR_PAIRS:
        a, b = sorted(pair)
        # A pair already applied is gone from the index; that is fine, it is
        # kept as a record of the decision.
        if a not in known or b not in known:
            continue
        ta, tb = set(spelling_tokens(a)), set(spelling_tokens(b))
        if not (ta < tb or tb < ta):
            problems.append(f"{a!r}/{b!r}: neither name contains the other")
            continue
        ca, cb = places.get(a, ("", "")), places.get(b, ("", ""))
        if ca[0] and cb[0] and (
                set(spelling_tokens(ca[0])) != set(spelling_tokens(cb[0]))
                or set(spelling_tokens(ca[1])) != set(spelling_tokens(cb[1]))):
            problems.append(f"{a!r}/{b!r}: {ca} vs {cb}")
    assert not problems, "sponsor list is stale:\n  " + "\n  ".join(problems)
    clash = set(SPONSOR_PAIRS) & set(NOT_SPONSORS)
    assert not clash, f"a pair is on both lists: {[sorted(p) for p in clash]}"
    for why in SPONSOR_PAIRS.values():
        assert why.strip(), "every entry has to say what it is claiming"
    print(f"test_the_sponsor_list_still_describes_real_clubs PASS "
          f"({len(SPONSOR_PAIRS)} pairs, {len(NOT_SPONSORS)} refused)")


def test_containment_is_never_posted_however_it_resolves():
    """A pair we will not MERGE is still a pair we will not ANNOUNCE.

    The merger has to be sure enough to fold two club pages into one, which is
    a much higher bar than "this is not a signing". Sponsors, reserve sides and
    clubs that merely share a name all reach the ledger as transfers if only
    the confident cases are suppressed, and each one has to be chased
    individually afterwards. Every containment verdict suppresses; the review
    file still gets all of them.
    """
    places, cities = uc.place_index(DB)
    n = TeamNormalizer()
    cases = [
        # the rename the guard is sure about
        ("Valencia", "Valencia Basket"),
        ("Valencia Basket", "Valencia"),
        # a sponsor it will not merge without a human
        ("Fabriano", "Indesit Fabriano"),
        ("Verona", "Tezenis Verona"),
        # a reserve side
        ("Real Madrid", "Real Madrid Castilla"),
        ("Barcelona", "Barcelona B"),
        # two clubs that share a name
        ("Al Nasr", "Al-Nasr Benghazi"),
    ]
    for a, b in cases:
        posted, why = uc.classify_move(n, a, b, club_places=places,
                                       cities_by_country=cities)
        assert not posted, f"{a!r} -> {b!r} was posted as {why}"
        assert why in ("club-rename", "rename-review", "same-club",
                       "spelling-variant"), why
    print(f"test_containment_is_never_posted_however_it_resolves PASS "
          f"({len(cases)} pairs)")


def test_sharing_a_word_is_not_containment():
    """The guard must not widen from "contains" to "resembles".

    Zach Lofton really did move from Homenetmen Beirut to Al Riyadi Club
    Beirut. The two names share "Beirut" and sit in one city, and a rule that
    keyed on overlap rather than containment would eat the transfer. Neither
    token set contains the other, so it is not this rule's business.
    """
    places, cities = uc.place_index(DB)
    n = TeamNormalizer()
    live = [
        ("Homenetmen Beirut", "Al Riyadi Club Beirut"),
        ("AS Monaco", "Valencia Basket"),
        ("ASVEL", "Valencia"),
        ("Valencia", "Fenerbahçe"),
    ]
    for a, b in live:
        posted, why = uc.classify_move(n, a, b, club_places=places,
                                       cities_by_country=cities)
        assert posted, f"real move {a!r} -> {b!r} was suppressed as {why}"
    # and the same thing said about the rule directly, not just its caller
    ok, why = rename_containment("Homenetmen Beirut", "Al Riyadi Club Beirut",
                                 place_a=("Beirut", "Lebanon"),
                                 place_b=("Beirut", "Lebanon"))
    assert ok is False and why == "", (ok, why)
    ta = set(spelling_tokens("Homenetmen Beirut"))
    tb = set(spelling_tokens("Al Riyadi Club Beirut"))
    assert ta & tb, "this case is only interesting because they DO overlap"
    assert not (ta < tb or tb < ta), "and only because neither contains it"
    print(f"test_sharing_a_word_is_not_containment PASS ({len(live)} moves)")


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
    test_the_sponsor_list_still_describes_real_clubs()
    test_containment_is_never_posted_however_it_resolves()
    test_sharing_a_word_is_not_containment()
    test_no_phantom_rename_survives_in_the_ledger()
    print("\nall club-rename tests PASS")
