"""One-time cleanup of the bad incremental run (commit 6d60141).

That run's roster extractor pulled coaches, `Category:` links and high schools
into the database as ``nba_active`` players. This migration:

  * removes every record added by that run (its logged new_players) plus, as a
    safety net, any surviving `Category:` / `*High School*` record and any
    ``nba_active`` record with an empty career history (pure coach/staff),
  * dedupes ``Brian Shaw (basketball)`` (a coach) against the real
    ``Brian Shaw`` playing record, which stays ``retired``,
  * drops the teams that run introduced *iff* no remaining player references
    them (real clubs referenced elsewhere are kept),
  * re-derives active/retired splits and the map file, and logs the cleanup.

Idempotent: re-running after a clean pass changes nothing. Run:
    python3 scripts/cleanup_migration.py
"""
from __future__ import annotations

import re

import update_careers as uc
from player_status import NBA_ACTIVE, OVERSEAS_ACTIVE, RETIRED


def _bad_run(runs: list[dict]) -> dict | None:
    """The incremental run that added the coach/category junk."""
    for r in reversed(runs):
        if r.get("mode") == "incremental" and r.get("new_players"):
            if any(n.startswith("Category:") for n in r["new_players"]):
                return r
    return None


def main() -> None:
    db = uc.Database()
    log = uc.load_json(uc.UPDATE_LOG, {"runs": []})
    run = _bad_run(log.get("runs", []))
    logged_new = set(run["new_players"]) if run else set()
    logged_teams = set(run["new_teams"]) if run else set()

    # Records to remove: the run's additions + safety-net junk still present.
    to_remove = set()
    for name, rec in db.by_name.items():
        if name in logged_new:
            to_remove.add(name)
        elif name.startswith("Category:") or "high school" in name.lower():
            to_remove.add(name)
        elif rec.get("status") == NBA_ACTIVE and not rec.get("career_history"):
            to_remove.add(name)

    for name in to_remove:
        db.by_name.pop(name, None)
    db.order = [n for n in db.order if n in db.by_name]

    # Drop teams introduced by the bad run that no player references anymore.
    referenced = {s["team"] for r in db.by_name.values()
                  for s in r.get("career_history", [])}
    removed_teams = []
    for team in sorted(logged_teams):
        present = team in db.locations or team in db.review
        if present and team not in referenced:
            db.locations.pop(team, None)
            db.review.pop(team, None)
            removed_teams.append(team)

    # Sanity: the real Brian Shaw survives and stays retired.
    bs = db.by_name.get("Brian Shaw")
    assert bs is not None, "expected real 'Brian Shaw' to remain"
    assert bs.get("status") == RETIRED, f"Brian Shaw should be retired, got {bs.get('status')}"
    assert "Brian Shaw (basketball)" not in db.by_name, "coach duplicate not removed"

    _persist(db, sorted(to_remove), removed_teams)

    nba = sum(1 for r in db.by_name.values() if r.get("status") == NBA_ACTIVE)
    over = sum(1 for r in db.by_name.values() if r.get("status") == OVERSEAS_ACTIVE)
    print(f"removed records : {len(to_remove)}")
    print(f"removed teams   : {len(removed_teams)}")
    print(f"players remaining: {len(db.by_name)}")
    print(f"nba_active      : {nba}")
    print(f"overseas_active : {over}")
    print("Brian Shaw       : present & retired ✓; coach duplicate removed ✓")


def _persist(db: uc.Database, removed_players: list[str],
             removed_teams: list[str]) -> None:
    players = [db.by_name[n] for n in db.order]
    uc.write_json(uc.CAREERS, players)
    uc.write_json(uc.LOCATIONS, dict(sorted(db.locations.items())))
    uc.write_json(uc.REVIEW, dict(sorted(db.review.items())))

    nba_active = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    overseas = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in players if p.get("status") == RETIRED)
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
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        map_players.append(mp)
    uc.write_json(uc.ROOT_MAP_FILE, map_players)

    # log the cleanup
    logobj = uc.load_json(uc.UPDATE_LOG, {"runs": []})
    logobj["runs"].append({"date": uc.today(), "mode": "cleanup",
                           "removed_players": removed_players,
                           "removed_teams": removed_teams,
                           "requests": 0})
    uc.write_json(uc.UPDATE_LOG, logobj)

    lines = [f"## {uc.today()} — cleanup migration", "",
             f"- Removed **{len(removed_players)}** non-player records "
             f"(coaches / `Category:` links / high schools) added by the bad run",
             f"- Removed **{len(removed_teams)}** now-orphaned teams",
             "- Deduped `Brian Shaw (basketball)`; real `Brian Shaw` remains retired",
             ""]
    existing = uc.CHANGELOG.read_text(encoding="utf-8") if uc.CHANGELOG.exists() else ""
    uc.CHANGELOG.write_text("\n".join(lines) + "\n" + existing, encoding="utf-8")


if __name__ == "__main__":
    main()
