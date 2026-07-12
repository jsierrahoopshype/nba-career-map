"""One-time backfill: project last_updated into nba_players_careers_READY.json.

The frontend's freshness indicator ("as of <date>" on player pages) reads
`last_updated` from the light map file, but that field was only ever recorded
in the source DB (data/players/nba_players_careers.json) — the map-file
projection in update_careers.py::_persist didn't carry it over until now. This
re-derives the map file from the (unchanged) source DB so every already-tracked
player picks up the field immediately, instead of waiting for its next
Wikipedia re-fetch.

Idempotent: re-running after a clean pass changes nothing. Run:
    python3 scripts/sync_ready_last_updated.py
"""
from __future__ import annotations

import update_careers as uc


def main() -> None:
    db = uc.Database()
    players = [db.by_name[n] for n in db.order if str(n or "").strip()]

    map_players = []
    annotated = 0
    for p in players:
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("last_updated"):
            mp["last_updated"] = p["last_updated"]
            annotated += 1
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        map_players.append(mp)
    changed = map_players != uc.load_json(uc.ROOT_MAP_FILE, None)
    uc.write_json(uc.ROOT_MAP_FILE, map_players)

    note = (f"- Backfilled `last_updated` into {uc.ROOT_MAP_FILE.name} for "
            f"**{annotated}**/{len(players)} players (frontend freshness indicator)")
    existing = uc.CHANGELOG.read_text(encoding="utf-8") if uc.CHANGELOG.exists() else ""
    if changed and note not in existing:
        lines = [f"## {uc.today()} — sync migration", "", note, ""]
        uc.CHANGELOG.write_text("\n".join(lines) + "\n" + existing, encoding="utf-8")
    print(f"wrote {uc.ROOT_MAP_FILE.relative_to(uc.ROOT)} "
          f"({annotated}/{len(players)} players annotated"
          f"{'' if changed else ', no change'})")


if __name__ == "__main__":
    main()
