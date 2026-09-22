"""What the redirect guard would cost, measured against the locations we have.

_discover_location now refuses an article whose title redirected away from the
name requested. That is what would have stopped "Chicago Packers" taking the
Washington Wizards' city. It is also the strictest guard in the pipeline, and
a club whose short name redirects to its full name -- "Cantù" to
"Pallacanestro Cantù" -- is refused along with the wrong ones, because nothing
in the two titles tells those cases apart.

So before it runs anywhere, ask the question the other way round: of the club
locations we ALREADY have and rely on, how many would this guard refuse if
they were being discovered today? That number is the cost.

Read-only. Run:  python3 scripts/audit_redirect_guard.py [--limit N]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from update_careers import same_article  # noqa: E402
from wikipedia_api import WikipediaClient  # noqa: E402
from team_normalizer import strip_diacritics  # noqa: E402

# Words that say what kind of thing a club is, not which club it is. A shared
# "BC" or "basketball" means nothing; a shared "Cantu" or "Seahorses" is the
# club's identity surviving a rename.
GENERIC = {"bc", "kk", "cb", "fc", "sc", "ac", "as", "bk", "cd", "ca", "sk",
           "club", "basket", "basketball", "baloncesto", "pallacanestro",
           "sports", "sport", "society", "association", "team", "nba", "league",
           "spor", "kulubu", "the", "de", "of"}

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


def _words(name: str) -> set:
    s = re.sub(r"\(.*?\)", " ", strip_diacritics(name or "").casefold())
    return {w for w in re.split(r"[^a-z0-9]+", s)
            if len(w) > 2 and w not in GENERIC}


def shares_a_word(team: str, title: str) -> bool:
    """Would a shared-word tolerance let this redirect through?

    The strict rule refuses every redirect, and most redirects in this data
    are a sponsor name or a short name reaching the club's own article --
    "Acqua S.Bernardo Cantu" to "Pallacanestro Cantu", "Aisin Seahorses" to
    "SeaHorses Mikawa". Those keep a word that names the club. The failures we
    care about do not: "Chicago Packers" and "Washington Wizards" have nothing
    in common, and neither do "Anaheim Arsenal" and "Grand Rapids Gold".

    Measured here, not enforced: the number it produces is what should decide
    whether the guard is narrowed to it.
    """
    return bool(_words(team) & _words(title))


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

    no_article, kept, refused = [], [], []
    for team in teams:
        title = resolved.get(team)
        if title is None:
            no_article.append(team)
        elif same_article(team, title):
            kept.append(team)
        else:
            refused.append({"team": team, "resolves_to": title,
                            "same_club": looks_like_the_same_club(team, title),
                            "city": loc[team].get("city", ""),
                            "country": loc[team].get("country", "")})

    fuller = [r for r in refused if r["same_club"]]
    other = [r for r in refused if not r["same_club"]]

    print(f"{len(teams)} club location(s) that carry a place\n")
    print(f"   no article under that name : {len(no_article):4}"
          f"   (the guard never sees these)")
    print(f"   reaches its own article    : {len(kept):4}   kept")
    print(f"   redirects elsewhere        : {len(refused):4}   REFUSED")
    print(f"       ... to a fuller form of the same name : {len(fuller):4}")
    print(f"       ... to a different name entirely      : {len(other):4}")

    narrowed = [r for r in refused if not shares_a_word(r["team"], r["resolves_to"])]
    print(f"\n   a shared-word tolerance would refuse : {len(narrowed):4}"
          f"   instead of {len(refused)}")
    print("\nwhat a shared-word tolerance would still refuse, sample:")
    for r in narrowed[:30]:
        print(f"   {r['team']!r:30} -> {r['resolves_to']!r:40} "
              f"({r['city']}, {r['country']})")
    if len(narrowed) > 30:
        print(f"   ... and {len(narrowed) - 30} more")

    print("\nrefused, redirecting to a fuller form of the same name:")
    for r in fuller[:20]:
        print(f"   {r['team']!r:30} -> {r['resolves_to']!r:44} "
              f"({r['city']}, {r['country']})")
    if len(fuller) > 20:
        print(f"   ... and {len(fuller) - 20} more")

    print("\nrefused, redirecting to a different name entirely:")
    for r in other[:30]:
        print(f"   {r['team']!r:30} -> {r['resolves_to']!r:44} "
              f"({r['city']}, {r['country']})")
    if len(other) > 30:
        print(f"   ... and {len(other) - 30} more")

    for r in refused:
        r["shares_a_word"] = shares_a_word(r["team"], r["resolves_to"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"checked": len(teams), "no_article": no_article,
                               "kept": len(kept), "refused": refused},
                              indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}  ({client.requests_made} requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
