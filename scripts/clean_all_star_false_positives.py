"""One-time cleanup: null out `all_star` on every record where it's truthy
but `all_star_count` is absent (idempotent).

Rationale: the Awards CSV (ingest_allstar_counts.py) is comprehensive for
NBA All-Stars back to 1969 and exact-match verified, so a genuine NBA
All-Star always has all_star_count set. A record with all_star set but no
all_star_count is therefore a regex false positive from wiki_parser.py's
_parse_all_star() — before the fix in this same round, that regex matched
ANY "All-Star" mention regardless of league (G League, EuroLeague, NBL,
CBA, a player's pre-NBA domestic league, etc.), not just NBA All-Star. This
migration is the retroactive cleanup: the regex fix only prevents NEW false
positives on future live fetches, it doesn't touch the ~5,000 already-
stored records.

Does NOT touch all_star_count, career_history, status, or any other field.
A record that legitimately has both (e.g. LeBron James) is untouched --
only all_star records with NO corroborating count are nulled.

Idempotent: re-running finds nothing left to clean (0 changes).

Run:  python3 scripts/clean_all_star_false_positives.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
READY = ROOT / "nba_players_careers_READY.json"
REPORT_OUT = ROOT / "logs" / "all_star_false_positive_cleanup.json"


def _clean(records: list) -> list[dict]:
    cleaned = []
    for r in records:
        if r.get("all_star") and r.get("all_star_count") is None:
            cleaned.append({"player": r["player"], "status": r.get("status", ""),
                            "removed_all_star": r["all_star"]})
            del r["all_star"]
    return cleaned


def main() -> None:
    careers = json.loads(CAREERS.read_text(encoding="utf-8"))
    ready = json.loads(READY.read_text(encoding="utf-8"))

    cleaned_careers = _clean(careers)
    cleaned_ready = _clean(ready)

    CAREERS.write_text(json.dumps(careers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    READY.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    REPORT_OUT.write_text(json.dumps(
        {"cleaned": sorted(cleaned_careers, key=lambda r: r["player"])},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"data/players/nba_players_careers.json: {len(cleaned_careers)} records cleaned")
    print(f"nba_players_careers_READY.json: {len(cleaned_ready)} records cleaned")
    by_status: dict[str, int] = {}
    for r in cleaned_careers:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"by status: {by_status}")
    print(f"Full list written to {REPORT_OUT.relative_to(ROOT)}")
    for r in sorted(cleaned_careers, key=lambda r: r["player"]):
        print(f"  {r['player']} ({r['status']}): removed all_star={r['removed_all_star']!r}")


if __name__ == "__main__":
    main()
