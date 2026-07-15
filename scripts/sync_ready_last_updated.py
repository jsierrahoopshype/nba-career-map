"""Resync nba_players_careers_READY.json's projection from the source DB.

The light map file (nba_players_careers_READY.json) is normally kept in sync
by update_careers.py::_persist on every pipeline run, but any field added to
its projection AFTER that run's data landed (last_updated for the freshness
indicator; nationality/all_star for a batch CSV ingest) needs a one-off
re-derive so already-tracked players pick it up immediately rather than
waiting for their next Wikipedia re-fetch. Source DB is left untouched; only
the derived map file is rewritten.

Idempotent: re-running after a clean pass changes nothing. Run:
    python3 scripts/sync_ready_last_updated.py
"""
from __future__ import annotations

import update_careers as uc


def main() -> None:
    db = uc.Database()
    players = [db.by_name[n] for n in db.order if str(n or "").strip()]

    map_players = []
    counts = {"last_updated": 0, "nationality": 0, "all_star": 0}
    for p in players:
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("last_updated"):
            mp["last_updated"] = p["last_updated"]
            counts["last_updated"] += 1
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        if p.get("nationality"):
            mp["nationality"] = p["nationality"]
            counts["nationality"] += 1
        if p.get("all_star") is not None:
            mp["all_star"] = p["all_star"]
            counts["all_star"] += 1
        map_players.append(mp)
    changed = map_players != uc.load_json(uc.ROOT_MAP_FILE, None)
    uc.write_json(uc.ROOT_MAP_FILE, map_players)

    note = (f"- Resynced {uc.ROOT_MAP_FILE.name} projection from the source DB: "
            f"last_updated **{counts['last_updated']}**, nationality "
            f"**{counts['nationality']}**, all_star **{counts['all_star']}** "
            f"/{len(players)} players")
    existing = uc.CHANGELOG.read_text(encoding="utf-8") if uc.CHANGELOG.exists() else ""
    if changed and note not in existing:
        lines = [f"## {uc.today()} — sync migration", "", note, ""]
        uc.CHANGELOG.write_text("\n".join(lines) + "\n" + existing, encoding="utf-8")
    print(f"wrote {uc.ROOT_MAP_FILE.relative_to(uc.ROOT)} "
          f"(last_updated={counts['last_updated']}, nationality={counts['nationality']}, "
          f"all_star={counts['all_star']}, total={len(players)}"
          f"{'' if changed else ', no change'})")


if __name__ == "__main__":
    main()
