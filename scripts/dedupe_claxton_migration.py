"""One-time merge of the "Nic Claxton" / "Nicolas Claxton" duplicate pair.

Both records were fully identical (same career_history, current_team, status,
birth info, draft info) as of the 2026-07-13 auto-update — a queue entry for
"Nic Claxton" resolved to the same Wikipedia article ("Nic_Claxton") as the
pre-existing "Nicolas Claxton" record but wasn't folded into it, so it got
inserted as a second top-level record instead of an alias.

Merge rule (same as merge_migration.py): keep the pre-existing primary key
("Nicolas Claxton" — present since before the "Nic Claxton" duplicate first
appeared in the 2026-07-11 auto-update), drop the duplicate, and make sure the
duplicate's spelling is preserved as an alias (it already is — the existing
record's `aliases` list already contains "Nic Claxton").

Also mirrors the removal into nba_players_careers_READY.json (the frontend
map file), which carries the identical duplicate. Downstream derived files
(dashboard_data.json, team_pages.json, club_pages.json, player_index.json,
player_aliases.json) are regenerated separately via build_dashboard_data.py.

Idempotent. Run:  python3 scripts/dedupe_claxton_migration.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"

PRIMARY = "Nicolas Claxton"
DUPLICATE = "Nic Claxton"


def _dedupe(path: Path) -> bool:
    records = json.loads(path.read_text(encoding="utf-8"))
    by_name = {r["player"]: r for r in records}
    if DUPLICATE not in by_name:
        return False  # already merged
    assert PRIMARY in by_name, f"{PRIMARY!r} missing from {path}"
    primary = by_name[PRIMARY]
    aliases = set(primary.get("aliases", []))
    aliases.add(DUPLICATE)
    primary["aliases"] = sorted(aliases)
    out = [r for r in records if r["player"] != DUPLICATE]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main() -> None:
    changed_careers = _dedupe(CAREERS)
    changed_ready = _dedupe(READY) if READY.exists() else False
    print(f"data/players/nba_players_careers.json: "
          f"{'merged' if changed_careers else 'already merged'}")
    print(f"nba_players_careers_READY.json: "
          f"{'merged' if changed_ready else ('already merged' if READY.exists() else 'not found')}")

    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    by_name = {r["player"]: r for r in careers}
    assert DUPLICATE not in by_name
    assert PRIMARY in by_name
    assert DUPLICATE in by_name[PRIMARY].get("aliases", [])
    print("sanity checks PASS")


if __name__ == "__main__":
    main()
