"""How many clubs waiting for a location would the sports-club guard refuse?

837 clubs sit in teams_needing_review.json with no location. The guard added to
_discover_location refuses an article that says nothing about a sport, which is
what would have stopped "Libertas Forlì" becoming an Irish political party's
registered office. This says, before any of them are written, how many of the
queue that guard would turn away -- and what those articles actually are, so a
refusal is a visible fact rather than a blank field.

Read-only. Run:  python3 scripts/audit_queued_places.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from update_careers import SPORTS_CLUB  # noqa: E402
from wikipedia_api import WikipediaClient  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "data" / "teams" / "teams_needing_review.json"
OUT = ROOT / "logs" / "queued_place_audit.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--max-requests", type=int, default=200)
    args = ap.parse_args()

    queued = sorted(json.loads(REVIEW.read_text(encoding="utf-8")))
    if args.limit:
        queued = queued[:args.limit]
    client = WikipediaClient(delay=args.delay, max_requests=args.max_requests)
    extracts = client.get_extracts(queued)

    no_article, refused, passed = [], [], []
    for team in queued:
        text = extracts.get(team) or ""
        if not text.strip():
            no_article.append(team)
        elif not SPORTS_CLUB.search(text):
            refused.append({"team": team, "extract": text[:200]})
        else:
            passed.append(team)

    print(f"{len(queued)} club(s) waiting for a location\n")
    print(f"   no article at all          : {len(no_article):4}")
    print(f"   article, refused by guard  : {len(refused):4}")
    print(f"   article, passes the guard  : {len(passed):4}")
    print("\nrefused, with what the article turned out to be:")
    for r in refused[:40]:
        print(f"   {r['team']!r:38} {r['extract'][:110]!r}")
    if len(refused) > 40:
        print(f"   ... and {len(refused) - 40} more")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"queued": len(queued), "no_article": no_article,
         "refused": refused, "passed": passed}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}  ({client.requests_made} requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
