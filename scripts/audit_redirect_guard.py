"""What the redirect guard would cost, measured against the locations we have.

_discover_location refuses an article whose title redirected away from the
name requested, unless the title it landed on still keeps a word that names
the club. That is what stops "Chicago Packers" taking the Washington Wizards'
city while letting "Acqua S.Bernardo Cantù" reach "Pallacanestro Cantù".

This asks the question the other way round: of the club locations we ALREADY
have and rely on, how many would the guard refuse if they were being
discovered today? That number is the cost. It reports the strict
title-equality rule alongside it, because the gap between the two is the
whole argument for the shared-word tolerance.

Read-only. Run:  python3 scripts/audit_redirect_guard.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from update_careers import same_article  # noqa: E402
from wikipedia_api import WikipediaClient  # noqa: E402
from team_normalizer import strip_diacritics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
OUT = ROOT / "logs" / "redirect_guard_audit.json"


def looks_like_the_same_club(team: str, title: str) -> bool:
    """Is the redirect target just this club's fuller name?

    Not a rule the guard uses -- the guard refuses both -- but the split
    between "redirected to a longer form of itself" and "redirected to
    something else" is what says whether the cost is mostly harmless.
    """
    a = strip_diacritics(team).casefold()
    b = strip_diacritics(title).casefold()
    return a in b or b in a


def titles_equal(team: str, title: str) -> bool:
    """The strict rule the guard used to be: same title or nothing.

    Kept only to report what the shared-word tolerance saved.
    """
    a = (team or "").replace("_", " ").strip()
    b = (title or "").replace("_", " ").strip()
    return bool(b) and (a == b or a[:1].upper() + a[1:] == b[:1].upper() + b[1:])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--max-requests", type=int, default=400)
    args = ap.parse_args()

    loc = json.loads(LOCATIONS.read_text(encoding="utf-8"))
    # Only the records that actually carry a place: a blank one has nothing
    # to lose.
    teams = sorted(t for t, e in loc.items() if (e.get("city") or "").strip())
    if args.limit:
        teams = teams[:args.limit]

    client = WikipediaClient(delay=args.delay, max_requests=args.max_requests)
    resolved = client.resolve_titles(teams)

    no_article, kept, refused, strict_only = [], [], [], []
    for team in teams:
        title = resolved.get(team)
        if title is None:
            no_article.append(team)
            continue
        row = {"team": team, "resolves_to": title,
               "same_club": looks_like_the_same_club(team, title),
               "city": loc[team].get("city", ""),
               "country": loc[team].get("country", "")}
        if not same_article(team, title):
            refused.append(row)
        else:
            kept.append(team)
            # kept by the shared-word tolerance, and only by it
            if not titles_equal(team, title):
                strict_only.append(row)

    print(f"{len(teams)} club location(s) that carry a place\n")
    print(f"   no article under that name : {len(no_article):4}"
          f"   (the guard never sees these)")
    print(f"   kept                       : {len(kept):4}")
    print(f"       of which, kept only because a word survived the redirect:"
          f" {len(strict_only)}")
    print(f"   REFUSED                    : {len(refused):4}")
    print(f"   (strict title equality would have refused "
          f"{len(refused) + len(strict_only)})")

    print("\nrefused, sample:")
    for r in refused[:30]:
        print(f"   {r['team']!r:32} -> {r['resolves_to']!r:44} "
              f"({r['city']}, {r['country']})")
    if len(refused) > 30:
        print(f"   ... and {len(refused) - 30} more")

    print("\nkept only by the shared-word tolerance, sample:")
    for r in strict_only[:15]:
        print(f"   {r['team']!r:32} -> {r['resolves_to']!r:44} "
              f"({r['city']}, {r['country']})")
    if len(strict_only) > 15:
        print(f"   ... and {len(strict_only) - 15} more")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"checked": len(teams), "no_article": no_article,
                               "kept": len(kept), "refused": refused,
                               "kept_by_shared_word": strict_only},
                              indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}  ({client.requests_made} requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
