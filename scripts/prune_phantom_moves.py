"""Find (and optionally remove) phantom transfers in the transactions ledger.

A phantom is a row whose two sides are the SAME club: either the alias table
resolves both to one name, or the two names are one name spelled differently
(case, diacritics, punctuation, doubled letters). Those rows reached the
ledger before the move detector learned to catch spelling variants, so the
back catalogue has to be swept once by hand.

The ledger is otherwise append-only, so a removal is not silent: every row
taken out is written to data/logs/spelling_review.json with the reason, which
is also where the live detector now parks pairs it will not vouch for.

Rows whose clubs are merely CLOSE are reported but never removed. One edit
separates plenty of genuinely different clubs -- Palencia/Valencia,
Palma/Parma, Iraklio/Iraklis, Chicago Rockers/Rockets -- so closeness is a
prompt to look, not a verdict.

Run this BEFORE the merge script for a given club. A merge rewrites the
variant spellings in past ledger rows, so running it first leaves the phantom
reading "X -> X" and the review record can no longer say which spelling the
feed actually saw.

Run:  python3 scripts/prune_phantom_moves.py           # report only
      python3 scripts/prune_phantom_moves.py --prune   # report and remove
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_normalizer import (TeamNormalizer, edit_distance,  # noqa: E402
                             is_spelling_variant, spelling_key)

ROOT = Path(__file__).resolve().parent.parent
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"
SPELLING_REVIEW = ROOT / "data" / "logs" / "spelling_review.json"

NEAR_MISS_DISTANCE = 2


def classify(tn: TeamNormalizer, frm: str, to: str) -> str:
    if not frm or not to:
        return "incomplete"
    a, b = tn.normalize(frm), tn.normalize(to)
    if a == b:
        return "same-club"
    if is_spelling_variant(a, b):
        return "spelling-variant"
    if edit_distance(spelling_key(a), spelling_key(b),
                     cap=NEAR_MISS_DISTANCE) <= NEAR_MISS_DISTANCE:
        return "near-miss"
    return "real"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prune", action="store_true",
                    help="remove the phantom rows (default: report only)")
    args = ap.parse_args()

    tn = TeamNormalizer()
    ledger = json.loads(TRANSACTIONS.read_text(encoding="utf-8"))
    txns = ledger["transactions"] if isinstance(ledger, dict) else ledger

    phantoms, near = [], []
    for i, t in enumerate(txns):
        why = classify(tn, t.get("from_team", ""), t.get("to_team", ""))
        if why in ("same-club", "spelling-variant"):
            phantoms.append((i, t, why))
        elif why == "near-miss":
            near.append((i, t))

    print(f"ledger rows: {len(txns)}")
    print(f"\n=== phantom rows (same club on both sides): {len(phantoms)} ===")
    for i, t, why in phantoms:
        print(f"  [{i:3d}] {t['date']}  {t['player']}: "
              f"{t['from_team']!r} -> {t['to_team']!r}   ({why})")
    if not phantoms:
        print("  none")

    print(f"\n=== near misses (kept, flagged for a look): {len(near)} ===")
    for i, t in near:
        print(f"  [{i:3d}] {t['date']}  {t['player']}: "
              f"{t['from_team']!r} -> {t['to_team']!r}")
    if not near:
        print("  none")

    if not args.prune:
        print("\n(report only — pass --prune to remove the phantom rows)")
        return
    if not phantoms:
        print("\nnothing to prune")
        return

    drop = {i for i, _, _ in phantoms}
    kept = [t for i, t in enumerate(txns) if i not in drop]
    if isinstance(ledger, dict):
        ledger["transactions"] = kept
    else:
        ledger = {"transactions": kept}
    TRANSACTIONS.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")

    doc = json.loads(SPELLING_REVIEW.read_text(encoding="utf-8")) \
        if SPELLING_REVIEW.exists() else {"pairs": []}
    if isinstance(doc, list):
        doc = {"pairs": doc}
    for _, t, why in phantoms:
        doc["pairs"].append({
            "player": t["player"], "from": t["from_team"], "to": t["to_team"],
            "reason": why, "posted": True, "date": t.get("date", ""),
            "note": "removed from transactions.json by prune_phantom_moves.py",
        })
    SPELLING_REVIEW.parent.mkdir(parents=True, exist_ok=True)
    SPELLING_REVIEW.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(f"\nremoved {len(phantoms)} row(s); ledger now {len(kept)}")
    print(f"recorded in {SPELLING_REVIEW.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
