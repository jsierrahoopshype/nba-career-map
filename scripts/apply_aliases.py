"""Apply team_aliases.json to the stored career data + scan for fragments.

Renames every career-history stint whose team name is a known alias to its
canonical name (case-insensitive), so sponsor/name variants collapse into one
club (unifying club pages and alumni counts). Idempotent — re-running renames
nothing once the data is canonical.

Then scans the resulting non-NBA club index for LIKELY same-club fragments (one
name being a sponsor-prefixed form of another, sharing the same city) and prints
them as candidates for review. It does NOT auto-merge beyond the alias table.

Run:  python3 scripts/apply_aliases.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from era_correct_teams import ERA_TABLE  # noqa: E402
from rosters import NBA_TEAMS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
ALIASES = ROOT / "data" / "teams" / "team_aliases.json"

_NBA_NAMES = set(NBA_TEAMS)
for _eras in ERA_TABLE.values():
    for _b, _n in _eras:
        _NBA_NAMES.add(_n)

_ALIASES = json.loads(ALIASES.read_text(encoding="utf-8")).get("aliases", {})
_CI = {re.sub(r"\s+", " ", k).strip().casefold(): v for k, v in _ALIASES.items()}


def _canon(team: str) -> str:
    return _CI.get(re.sub(r"\s+", " ", team or "").strip().casefold(), team)


def _apply(players: list, tally: dict) -> int:
    changed = 0
    for p in players:
        for s in p.get("career_history", []):
            old = s.get("team", "")
            new = _canon(old)
            if new != old:
                s["team"] = new
                tally[f"{old} -> {new}"] = tally.get(f"{old} -> {new}", 0) + 1
                changed += 1
    return changed


def _fragment_candidates(players: list) -> list[str]:
    """Non-NBA clubs where one name is `<sponsor> <other>` and both share a city."""
    from collections import defaultdict
    by_city: dict[tuple, set] = defaultdict(set)
    for p in players:
        for s in p.get("career_history", []):
            t = (s.get("team") or "").strip()
            city = (s.get("city") or "").strip()
            country = (s.get("country") or "").strip()
            if t and city and t not in _NBA_NAMES:
                by_city[(city, country)].add(t)

    out = []
    for (city, country), names in by_city.items():
        names = sorted(names)
        for a in names:
            for b in names:
                if a == b:
                    continue
                # b is a suffix word-sequence of a: a == "<prefix> b" (sponsor prefix)
                if a.endswith(" " + b) and len(a) > len(b):
                    out.append(f"{a!r}  ~  {b!r}   [{city}, {country}]")
    return sorted(set(out))


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))
    tally: dict = {}
    n1 = _apply(careers, tally)
    n2 = _apply(ready, {})
    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"careers.json stints renamed: {n1}")
    print(f"READY.json  stints renamed: {n2}")
    print("renames:")
    for k, v in sorted(tally.items()):
        print(f"  x{v}  {k}")

    cands = _fragment_candidates(careers)
    print(f"\n=== fragment candidates (sponsor-prefix, same city) — REVIEW ONLY, {len(cands)} ===")
    for c in cands:
        print(f"  {c}")


if __name__ == "__main__":
    main()
