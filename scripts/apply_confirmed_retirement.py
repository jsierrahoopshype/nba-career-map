"""One-time correction: apply the confirmed Álex Abrines retirement.

This is the exact bug case that motivated retirement.detect_retirement(): his
last stint (FC Barcelona, 2019-2025) ended only a year ago, so the 2-year
recency fallback alone would not retire him until mid-2027, even though his
Wikipedia article already states in prose: "On 22 July 2025, Abrines
announced his retirement from professional basketball." That sentence is
sourced directly from a human-verified quote of the live article (not from a
fresh crawl — this sandbox has no outbound network access), so it is applied
here as a one-off manual correction rather than waiting for the next
Wikipedia sweep to independently re-derive it. Every OTHER overseas_active /
nba_active player still gets the explicit-language check automatically on
their next real fetch (incremental / full / full_overseas), which is the
general fix; this script only backfills the one case we have direct
human confirmation for.

Idempotent. Run:
    python3 scripts/apply_confirmed_retirement.py
"""
from __future__ import annotations

import update_careers as uc
from player_status import classify_status

CURRENT_YEAR = 2026


def main() -> None:
    db = uc.Database()
    rec = db.by_name.get("Alex Abrines")
    if rec is None:
        print("Alex Abrines not found in the database — nothing to do.")
        return

    already = rec.get("retirement_announced") is True and rec.get("status") == "retired"
    rec["retirement_announced"] = True
    rec["retirement_date"] = "2025-07-22"
    rec["status"] = classify_status(rec, on_nba_roster=False, current_year=CURRENT_YEAR,
                                    retirement_announced=True)
    assert rec["status"] == "retired", rec["status"]

    # _persist() would also append a misleading "0 players updated" pipeline
    # run to the log, so the file writes are replicated directly here instead.
    players = [db.by_name[n] for n in db.order if str(n or "").strip()]
    uc.write_json(uc.CAREERS, players)
    nba_active = sorted(p["player"] for p in players if p.get("status") == uc.NBA_ACTIVE)
    overseas = sorted(p["player"] for p in players if p.get("status") == uc.OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in players if p.get("status") == uc.RETIRED_STATUS)
    uc.write_json(uc.ACTIVE, {"count": len(nba_active) + len(overseas),
                              "nba_active": nba_active, "overseas_active": overseas})
    uc.write_json(uc.RETIRED, {"count": len(retired), "players": retired})

    map_players = []
    for p in players:
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("last_updated"):
            mp["last_updated"] = p["last_updated"]
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        map_players.append(mp)
    uc.write_json(uc.ROOT_MAP_FILE, map_players)

    if not already:
        lines = [f"## {uc.today()} — manual correction", "",
                 "- Álex Abrines: `overseas_active` → `retired` "
                 "(confirmed retirement announcement, 22 July 2025 — "
                 "explicit-language signal; source: human-verified quote "
                 "of the live Wikipedia article)", ""]
        existing = uc.CHANGELOG.read_text(encoding="utf-8") if uc.CHANGELOG.exists() else ""
        uc.CHANGELOG.write_text("\n".join(lines) + "\n" + existing, encoding="utf-8")
    print(f"Alex Abrines: status={rec['status']!r}, "
          f"retirement_date={rec['retirement_date']!r} "
          f"({'already applied' if already else 'applied now'})")


if __name__ == "__main__":
    main()
