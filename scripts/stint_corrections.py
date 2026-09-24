"""Stints the source lists that the player never actually played.

THE PROBLEM. career_history is rebuilt from the Wikipedia article on every
pipeline run, so a stint deleted by hand is back the next morning. Andrei
Kirilenko's 2001 line at Partizan is the case this exists for: he signed a
contract with the club and left for the Utah Jazz before ever playing for
them, but the infobox lists the stint like any other, and the map drew a stop
in Belgrade he never made.

THE MECHANISM. The removals live in data/teams/stint_corrections.json and are
re-applied after every parse -- the mirror image of the curated signing dates
in scripts/signing_guard.py, which are re-stamped in the same place for the
same reason.

MATCHING is on player + team + start YEAR rather than the exact `years`
string, so an edit that reshapes the span ("2001" -> "2001-2002") does not
quietly stop matching. Omit start_year to remove every stint the player has
at that club.

CURRENT TEAM. Removing a player's last stint would leave current_team naming
a club he has no stint at, so the field falls back to the last remaining stop.

Run standalone to apply the removals to the stored database (and to the map
file the frontend reads) without waiting for a pipeline run:

    python3 scripts/stint_corrections.py            # report only
    python3 scripts/stint_corrections.py --apply    # rewrite both files
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORRECTIONS = ROOT / "data" / "teams" / "stint_corrections.json"
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"

_YEAR = re.compile(r"\d{4}")


def _start_year(stint: dict) -> int | None:
    m = _YEAR.search(str((stint or {}).get("years") or ""))
    return int(m.group()) if m else None


def load_removals(path: Path = CORRECTIONS) -> list[dict]:
    """The curated removals, ignoring rows that name no player or team."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("removals", []) if isinstance(doc, dict) else doc
    return [r for r in rows
            if str(r.get("player") or "").strip()
            and str(r.get("team") or "").strip()]


def _matches(stint: dict, rule: dict) -> bool:
    if (stint.get("team") or "").strip() != rule["team"].strip():
        return False
    year = rule.get("start_year")
    return year is None or _start_year(stint) == year


def apply_removals(players, path: Path = CORRECTIONS) -> int:
    """Drop the curated phantom stints from `players`. Returns the count."""
    rules = load_removals(path)
    if not rules:
        return 0
    by_player: dict[str, list] = {}
    for r in rules:
        by_player.setdefault(r["player"].strip(), []).append(r)

    removed = 0
    for p in players:
        names = {n for n in (p.get("player"), p.get("display_name")) if n}
        rows = [r for n in names for r in by_player.get(n, [])]
        if not rows:
            continue
        hist = p.get("career_history") or []
        kept = [s for s in hist if not any(_matches(s, r) for r in rows)]
        if len(kept) == len(hist):
            continue
        removed += len(hist) - len(kept)
        p["career_history"] = kept
        # A current_team that only existed because of the dropped stint would
        # otherwise point at a club with no row on the page.
        current = (p.get("current_team") or "").strip()
        if current and not any((s.get("team") or "").strip() == current
                               for s in kept):
            p["current_team"] = (kept[-1].get("team") or "") if kept else ""
    return removed


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="rewrite the career database and the map file "
                         "(default: report what would go)")
    args = ap.parse_args()

    rules = load_removals()
    print(f"curated removals: {len(rules)}")
    for r in rules:
        year = r.get("start_year")
        print(f"  {r['player']} — {r['team']}"
              f"{f' ({year})' if year else ''}: {r.get('reason', '')}")

    for label, path in (("careers", CAREERS), ("READY", READY)):
        players = _load(path)
        n = apply_removals(players)
        print(f"{label:8s} stints removed: {n}")
        if n and args.apply:
            _write(path, players)
            print(f"         wrote {path.relative_to(ROOT)}")
    if not args.apply:
        print("\nreport only — pass --apply to write. Afterwards run "
              "scripts/build_dashboard_data.py to regenerate the pages.")


if __name__ == "__main__":
    main()
