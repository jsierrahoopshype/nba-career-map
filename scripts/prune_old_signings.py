"""Run the signings guard over the ledger that was written before it existed.

data/logs/transactions.json was filled by a rule that logged a move whenever
a player's stored current_team changed -- a diff of what the pipeline had
seen, not of what had happened. scripts/signing_guard.py now stops that at
the source; this replays the same test over the entries already on file and
drops the ones it is SURE are older moves (a destination stint that started
before the ledger opened on 2026-07-11).

WHAT IT WILL NOT TOUCH. An entry the guard cannot date -- the destination has
no matching stint, usually because the club was renamed after the entry was
written -- is reported and kept. Removing a row on a guess is worse than
leaving a stale one: the ledger is the site's record of what moved.

The Slack cursor (data/logs/slack_posted_marker.json) is a COUNT of ledger
entries already posted, so removing entries from underneath it would re-post
whatever slid into the gap. It is rewritten to the number of surviving
entries that were inside the posted range.

Run:  python3 scripts/prune_old_signings.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import signing_guard  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"
SLACK_MARKER = ROOT / "data" / "logs" / "slack_posted_marker.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    by_name: dict[str, dict] = {}
    for p in players:
        for key in (p.get("player"), p.get("display_name")):
            if key:
                by_name.setdefault(key, p)
        for alias in p.get("aliases") or []:
            by_name.setdefault(alias, p)

    doc = json.loads(TRANSACTIONS.read_text(encoding="utf-8"))
    ledger = doc["transactions"] if isinstance(doc, dict) else doc
    marker = json.loads(SLACK_MARKER.read_text(encoding="utf-8")) \
        if SLACK_MARKER.exists() else {}
    posted = int(marker.get("posted_count", 0))

    keep, drop, undated = [], [], []
    posted_kept = 0
    for i, tx in enumerate(ledger):
        rec = by_name.get(tx.get("player", ""))
        if rec is None:
            verdict, why = None, "no record for this player"
        else:
            verdict, why = signing_guard.is_new_signing(rec, tx.get("to_team", ""))
        if verdict is False:
            drop.append((tx, why))
            continue
        if verdict is None:
            undated.append((tx, why))
        keep.append(tx)
        if i < posted:
            posted_kept += 1

    print(f"ledger: {len(ledger)} entries, {len(drop)} clearly older than the "
          f"ledger start ({signing_guard.LEDGER_START.isoformat()})\n")
    for tx, why in drop:
        print(f"  REMOVE  detected {tx.get('date')}  {tx.get('player')}: "
              f"{tx.get('from_team')} -> {tx.get('to_team')}  ({why})")
    print(f"\n{len(undated)} entry(ies) the guard cannot date -- KEPT:")
    for tx, why in undated:
        print(f"  keep    detected {tx.get('date')}  {tx.get('player')}: "
              f"{tx.get('from_team')} -> {tx.get('to_team')}  ({why})")
    print(f"\nkeeping {len(keep)} entries; slack posted_count {posted} -> {posted_kept}")

    if not args.apply:
        return 0
    if isinstance(doc, dict):
        doc["transactions"] = keep
    else:
        doc = {"transactions": keep}
    TRANSACTIONS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    marker["posted_count"] = posted_kept
    SLACK_MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
