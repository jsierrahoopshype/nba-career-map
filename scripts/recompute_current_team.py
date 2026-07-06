"""Offline recompute of current_team + tracking status from stored career data.

Reapplies the corrected _select_current_team() (latest end-year, present =
latest, NBA-preferred on ties) and classify_status() to every record's existing
career_history — no re-fetch. Only current_team / status are touched; all other
fields are left as-is. Re-derives the active/retired splits and the map file.

Run:  python3 scripts/recompute_current_team.py
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from wiki_parser import _select_current_team
from player_status import classify_status, NBA_ACTIVE, OVERSEAS_ACTIVE, RETIRED

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "players"
CAREERS = P / "nba_players_careers.json"


def main() -> None:
    year = dt.datetime.now(dt.timezone.utc).year
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    changes = []
    for rec in players:
        old_ct = rec.get("current_team", "")
        old_st = rec.get("status", "")
        new_ct = _select_current_team(rec.get("career_history", []))
        rec["current_team"] = new_ct
        # classify from the corrected current_team (roster signal unavailable
        # offline; classify_status uses current_team NBA-ness + recency).
        new_st = classify_status(rec, on_nba_roster=False, current_year=year)
        rec["status"] = new_st
        if new_ct != old_ct or new_st != old_st:
            changes.append((rec["player"], old_ct, new_ct, old_st, new_st))

    _write(CAREERS, players)
    _rederive(players)

    print(f"records changed: {len(changes)} / {len(players)}")
    ct_only = sum(1 for _, oc, nc, os_, ns in changes if nc != oc and ns == os_)
    st_only = sum(1 for _, oc, nc, os_, ns in changes if nc == oc and ns != os_)
    both = sum(1 for _, oc, nc, os_, ns in changes if nc != oc and ns != os_)
    print(f"  current_team only: {ct_only} | status only: {st_only} | both: {both}")
    print("first 30 changes (player | current_team old->new | status old->new):")
    for name, oc, nc, os_, ns in changes[:30]:
        ct = f"{oc!r}->{nc!r}" if nc != oc else f"(={nc!r})"
        st = f"{os_}->{ns}" if ns != os_ else f"(={ns})"
        print(f"  {name} | {ct} | {st}")


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rederive(players: list) -> None:
    nba = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    ov = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    ret = sorted(p["player"] for p in players if p.get("status") == RETIRED)
    _write(P / "active_players.json",
           {"count": len(nba) + len(ov), "nba_active": nba, "overseas_active": ov})
    _write(P / "retired_players.json", {"count": len(ret), "players": ret})
    map_players = []
    for p in players:
        mp = {"player": p["player"], "status": p.get("status", ""),
              "career_history": [{"years": s.get("years", ""), "team": s["team"],
                                  "city": s.get("city", ""), "state": s.get("state", ""),
                                  "country": s.get("country", "")}
                                 for s in p.get("career_history", [])]}
        if p.get("display_name") and p["display_name"] != p["player"]:
            mp["display_name"] = p["display_name"]
        if p.get("wikipedia_url"):
            mp["wikipedia_url"] = p["wikipedia_url"]
        map_players.append(mp)
    _write(ROOT / "nba_players_careers_READY.json", map_players)


if __name__ == "__main__":
    main()
