"""One-time ingest: All-Star selection COUNT from a HoopsHype awards-database
CSV export into nba_players_careers.json, as a new `all_star_count` field.

The CSV is per-award-per-year (one row per player per season award), so the
CAREER AWARDS column — a static lifetime summary — is repeated identically
across every row for a given player (e.g. "All-Star (18)" appears on all of
Kobe Bryant's rows). This extracts the count via regex, deduped to one value
per CSV player name (an internal mismatch across a player's own rows would
mean the source data disagrees with itself — logged and skipped, not
guessed).

`all_star_count` is a NEW, separate field from the existing `all_star` field
(a year-list/boolean parsed from live Wikipedia infobox fetches — different
shape, different source, different meaning: "known All-Star years" vs "known
lifetime selection count"). Never merged or overwritten into each other; a
player with both simply has both.

Matching: reuses ingest_nationalities.py's exact-match / normkey / alias
infrastructure verbatim (import, not reimplementation) — exact PLAYER-name
match against the DB's primary key first, then names.normkey against every
DB player's key/display_name/aliases, with a normkey collision left
unmatched rather than guessed. No force-matching.

Idempotent: re-running with the same CSV sets the same values again (a no-op
in substance); a changed CSV overwrites the count for that player (a single-
sourced field has no "conflict" concept — there's nothing else to disagree
with it).

Run:  python3 scripts/ingest_allstar_counts.py <path-to-csv>
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_nationalities import build_norm_groups  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
UNMATCHED_OUT = ROOT / "logs" / "all_star_count_unmatched.json"

_ALL_STAR_RE = re.compile(r"All-Star \((\d+)\)")


def extract_counts(csv_path: Path) -> tuple[dict[str, int], list[dict]]:
    """CSV player name -> All-Star count, deduped across that player's rows.

    Returns (counts, internal_conflicts) — a conflict is a player whose own
    rows disagree on the count (should never happen for a static field, but
    checked rather than assumed; such a name is excluded from `counts`)."""
    by_player: dict[str, set[int]] = defaultdict(set)
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("PLAYER / COACH") or "").strip()
            if not name:
                continue
            m = _ALL_STAR_RE.search(row.get("CAREER AWARDS") or "")
            if m:
                by_player[name].add(int(m.group(1)))

    counts, conflicts = {}, []
    for name, vals in by_player.items():
        if len(vals) == 1:
            counts[name] = next(iter(vals))
        else:
            conflicts.append({"csv_player": name, "conflicting_counts": sorted(vals)})
    return counts, conflicts


def match_counts(counts: dict[str, int], players: list[dict]):
    """Same tiered strategy as ingest_nationalities.match_players: exact
    primary-key match first, then normkey (key/display_name/aliases), with a
    normkey collision across >1 distinct player left unmatched."""
    from names import normkey

    by_key = {p["player"]: p for p in players}
    norm_groups = build_norm_groups(players)

    exact, via_normkey, ambiguous, unmatched = [], [], [], []
    for name, count in counts.items():
        if name in by_key:
            exact.append((name, count, by_key[name]))
            continue
        candidates = norm_groups.get(normkey(name), set())
        if len(candidates) == 1:
            via_normkey.append((name, count, by_key[next(iter(candidates))]))
        elif len(candidates) > 1:
            ambiguous.append({"csv_player": name, "candidates": sorted(candidates)})
        else:
            unmatched.append(name)
    return exact, via_normkey, ambiguous, unmatched


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python3 scripts/ingest_allstar_counts.py <path-to-csv>")
        sys.exit(1)
    csv_path = Path(sys.argv[1])
    counts, internal_conflicts = extract_counts(csv_path)
    players = json.loads(CAREERS.read_text(encoding="utf-8"))

    exact, via_normkey, ambiguous, unmatched = match_counts(counts, players)
    total_matched = len(exact) + len(via_normkey)

    set_count = 0
    for name, count, rec in exact + via_normkey:
        if rec.get("all_star_count") != count:
            set_count += 1
        rec["all_star_count"] = count

    CAREERS.write_text(json.dumps(players, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    UNMATCHED_OUT.parent.mkdir(parents=True, exist_ok=True)
    UNMATCHED_OUT.write_text(json.dumps(
        {"unmatched": sorted(unmatched), "ambiguous": ambiguous,
         "internal_conflicts": internal_conflicts}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    print(f"CSV players with an All-Star (N) count: {len(counts)}")
    print(f"Internal conflicts (same CSV name, disagreeing counts — skipped): {len(internal_conflicts)}")
    print(f"Exact-name matches: {len(exact)}")
    print(f"normkey/alias matches: {len(via_normkey)}")
    print(f"Total matched: {total_matched} ({total_matched/len(counts)*100:.2f}%)")
    print(f"Ambiguous (left unmatched): {len(ambiguous)}")
    print(f"Unmatched (no candidate at all): {len(unmatched)}")
    print(f"all_star_count changed/set this run: {set_count}")
    print(f"Unmatched/ambiguous/conflicts written to {UNMATCHED_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
