"""Does a proposed club country agree with where the player was either side?

The place-article rule reads a location off an article and checks one thing:
that the club's name contains the place's name. That is not enough. "Parma"
contains "Parma", and the article is the Italian city, and the club two NBA
players actually played for is PBC Parma in Perm, Russia. "Pazi" contains
"Pazi", and the article is a village in West Azerbaijan, and Hasheem Thabeet
played it between two Taiwanese clubs.

A career is a chain of places, and the stops either side of a club are
evidence about it. This asks that question of every proposal:

  corroborated  some stint at this club has a neighbouring stint in the
                proposed country
  contradicted  no stint does, and at least one has neighbours with countries
                that are all something else
  no evidence   no stint at this club has a neighbour with a country at all

A contradiction is not proof -- players do change country between stops --
but no stint anywhere at the club having a neighbour in the proposed country,
when neighbours are known, is the pattern both errors above share.

Read-only. Run:  python3 scripts/corroborate_places.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
PROPOSALS = ROOT / "logs" / "place_article_rule.json"
OUT = ROOT / "logs" / "corroborated_places.json"


def neighbourhoods(players: list, clubs: set) -> dict:
    """club -> [(player, [countries of the stints either side of it]), ...]"""
    out: dict[str, list] = {c: [] for c in clubs}
    for p in players:
        hist = p.get("career_history") or []
        for i, s in enumerate(hist):
            team = (s.get("team") or "").strip()
            if team not in clubs:
                continue
            around = []
            for j in (i - 1, i + 1):
                if 0 <= j < len(hist):
                    c = (hist[j].get("country") or "").strip()
                    if c:
                        around.append(c)
            out[team].append((p["player"], s.get("years"), around))
    return out


def verdict(country: str, sightings: list) -> str:
    """Loose: any neighbouring stint in the proposed country vindicates it."""
    seen = [c for _who, _yrs, around in sightings for c in around]
    if not seen:
        return "no evidence"
    return "corroborated" if country in seen else "contradicted"


def strict_verdict(country: str, sightings: list) -> str:
    """Strict: a stint fenced on BOTH sides by one other country contradicts.

    The loose test fires far too often -- a career is a list of countries a
    player passed through, and arriving somewhere new is the normal case, not
    a red flag. This asks for the one shape that is genuinely hard to explain:
    a season whose stops before AND after are the same country, and it is not
    the one proposed.
    """
    for _who, _yrs, around in sightings:
        if len(around) == 2 and around[0] == around[1] and around[0] != country:
            return "contradicted"
    return "corroborated" if any(
        country in around for _w, _y, around in sightings) else "no evidence"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    proposals = [r for r in json.loads(PROPOSALS.read_text(encoding="utf-8"))
                 if r["verdict"] == "accept"]
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    hoods = neighbourhoods(players, {r["team"] for r in proposals})

    rows = []
    for r in sorted(proposals, key=lambda x: x["team"]):
        sightings = hoods.get(r["team"], [])
        rows.append({**r, "check": verdict(r["country"], sightings),
                     "strict": strict_verdict(r["country"], sightings),
                     "neighbours": sorted({c for _w, _y, a in sightings for c in a})})

    print(f"{len(rows)} accepted proposal(s)\n")
    for field, label in (("check", "loose (any neighbour in the country)"),
                         ("strict", "strict (fenced both sides by one other)")):
        counts: dict[str, int] = {}
        for row in rows:
            counts[row[field]] = counts.get(row[field], 0) + 1
        print(f"   {label}")
        for k in ("corroborated", "contradicted", "no evidence"):
            print(f"      {k:14} {counts.get(k, 0):4}")
    print("\nstrict contradictions:")
    for row in rows:
        if row["strict"] == "contradicted":
            print(f"   {row['team']!r:26} proposed {row['country']:12} "
                  f"neighbours {row['neighbours']}")

    for k in ("contradicted", "no evidence"):
        print(f"\n{k}:")
        for row in rows:
            if row["check"] == k:
                print(f"   {row['team']!r:26} proposed {row['country']:12} "
                      f"neighbours {row['neighbours'] or '[]'}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
