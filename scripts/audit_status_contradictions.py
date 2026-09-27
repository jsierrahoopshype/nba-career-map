"""Scan every player for status / current-team contradictions.

Read-only against the career DB; writes logs/status_contradictions.json.

Categories (the three the site must never show):
  retired_with_current_team   status "retired" but a stint is still open
                              ("2015–present").
  nba_active_no_current_team  status "nba_active" but no stint is open, or no
                              current_team at all -- the map (READY.json, which
                              has no current_team field) shows no current club.
  nba_active_non_nba_team     status "nba_active" but current_team is not one
                              of the 30 franchises.
Plus one informational category of the same family:
  overseas_with_open_nba_stint  overseas_active with an open NBA stint.

Each row carries a `cause` and a `resolution`:
  pipeline  -- the root-cause fixes in update_careers.py resolve it on the
               player's next refresh (or it already cleared on a re-check).
  manual    -- the data cannot settle it without a guess; needs a human.

Run:  python3 scripts/audit_status_contradictions.py [--stale-days 7]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from player_status import is_nba_team, last_active_year, PRESENT

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
OUT = ROOT / "logs" / "status_contradictions.json"


def is_open(years) -> bool:
    y = str(years or "")
    return "present" in y.lower() or bool(re.search(r"[–\-]\s*$", y))


def _end(years) -> int:
    if is_open(years):
        return PRESENT
    ys = re.findall(r"\d{4}", str(years or ""))
    return int(ys[-1]) if ys else 0


def _start(years) -> int:
    m = re.search(r"\d{4}", str(years or ""))
    return int(m.group()) if m else 0


def _tail(h: list, n: int = 3) -> list:
    return [f"{s.get('team')} ({s.get('years')})" for s in h[-n:]]


def _retired_cause(p: dict, opens: list) -> tuple[str, str]:
    rd = p.get("retirement_date") or ""
    ry = re.search(r"\d{4}", rd)
    if p.get("retirement_announced") and ry and any(
            _start(s.get("years")) > int(ry.group()) for s in opens):
        return ("comeback: open stint started after the recorded retirement "
                f"({ry.group()}); the old comeback check needed the prose to "
                "disappear from the page", "pipeline")
    if p.get("retirement_announced") and not rd:
        nba_open = [s for s in opens if is_nba_team(s.get("team", ""))]
        if nba_open:
            return ("undated retirement-prose match outranked an open NBA stint "
                    "(sticky flag); cleared by the roster rule if he is on the "
                    f"{nba_open[0]['team']} template under contract",
                    "pipeline")
        return ("undated retirement-prose match outranked an open non-NBA "
                "stint; no roster evidence exists for non-NBA clubs, so the "
                "flag cannot be cleared without a human reading the page",
                "manual")
    if p.get("retirement_announced"):
        return ("dated retirement announcement with an open stint that began "
                "before it (infobox not yet closed)", "manual")
    return ("open stint but classified retired (not announcement-driven)",
            "manual")


def _nba_cause(p: dict, h: list, stale_before: str) -> tuple[str, str]:
    ct = p.get("current_team") or ""
    if not h:
        return "no career history", "manual"
    if (p.get("last_updated") or "") < stale_before:
        return (f"stale record (last refreshed {p.get('last_updated')}): the "
                "incremental queue spent the whole budget on overseas players "
                "before reaching on-roster NBA players", "pipeline")
    top = max(_end(s.get("years")) for s in h)
    latest = [s for s in h if _end(s.get("years")) == top]
    non_nba = [s for s in latest if not is_nba_team(s.get("team", ""))
               and s.get("team") != ct]
    if non_nba and any(is_nba_team(s.get("team", "")) for s in latest):
        return (f"same-year tie between {ct} and {non_nba[0]['team']} "
                f"({non_nba[0].get('years')}); the NBA-preferred tie-break picked "
                f"{ct}. G League assignment or a move abroad -- the infobox "
                "cannot tell which", "manual")
    ct_stints = [s for s in h if s.get("team") == ct]
    if ct_stints and max(_end(s.get("years")) for s in ct_stints) < top:
        return (f"current_team {ct} is not his latest stint "
                f"({', '.join(s['team'] for s in latest)})", "manual")
    return (f"{ct} stint closed ({ct_stints[-1].get('years') if ct_stints else '?'}) "
            "and no open stint: unsigned free agent kept nba_active by the "
            "2-year window, or an infobox lagging a re-signing. The roster rule "
            "resolves it on refresh if he is on a template under contract; if "
            "not, there is no free-agent status to put him in", "manual")


def audit(players: list, today: dt.date, stale_days: int) -> dict:
    stale_before = (today - dt.timedelta(days=stale_days)).isoformat()
    out = {"retired_with_current_team": [], "nba_active_no_current_team": [],
           "nba_active_non_nba_team": [], "overseas_with_open_nba_stint": []}
    for p in players:
        h = p.get("career_history") or []
        opens = [s for s in h if is_open(s.get("years"))]
        st, ct = p.get("status"), p.get("current_team") or ""
        row = {"player": p["player"], "status": st, "current_team": ct,
               "last_updated": p.get("last_updated"), "recent_stints": _tail(h)}
        if st == "retired" and opens:
            cause, res = _retired_cause(p, opens)
            out["retired_with_current_team"].append({**row, "cause": cause,
                                                     "resolution": res})
        if st == "nba_active" and (not ct or not opens):
            cause, res = _nba_cause(p, h, stale_before)
            out["nba_active_no_current_team"].append({**row, "cause": cause,
                                                      "resolution": res})
        if st == "nba_active" and ct and not is_nba_team(ct):
            out["nba_active_non_nba_team"].append(
                {**row, "cause": "current_team is not an NBA franchise",
                 "resolution": "manual"})
        if st == "overseas_active" and any(is_nba_team(s.get("team", "")) for s in opens):
            out["overseas_with_open_nba_stint"].append(
                {**row, "cause": "open NBA stint but classified overseas",
                 "resolution": "manual"})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stale-days", type=int, default=7)
    args = ap.parse_args()
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    today = dt.datetime.now(dt.timezone.utc).date()
    res = audit(players, today, args.stale_days)
    OUT.write_text(json.dumps({"date": today.isoformat(), **res},
                              ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for cat, rows in res.items():
        print(f"\n## {cat}: {len(rows)}")
        for r in rows:
            print(f"- [{r['resolution']}] {r['player']} | {r['current_team']} | "
                  f"{'; '.join(r['recent_stints'])} | {r['cause']}")


if __name__ == "__main__":
    main()
