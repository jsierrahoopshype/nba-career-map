"""Main orchestrator for the automated career-database update.

Modes (see --mode):
  incremental   : default. Add players newly appearing on NBA rosters; re-check
                  players who dropped off an NBA roster (they may have moved
                  overseas rather than retired); re-check ALL overseas_active
                  players to catch team changes (e.g. Patty Mills in Australia);
                  then refresh least-recently-updated nba_active players. All
                  within the request budget; continues next run.
  full          : refresh every active player (NBA + overseas), budget-bounded.
  full_overseas : re-check ALL overseas_active players (intended to run monthly
                  so long-time overseas players stay current even between the
                  daily incremental passes).
  single        : refresh one player by name (--player "First Last").
  review        : try to resolve locations for teams in teams_needing_review.json
                  by reading their Wikipedia lead extract.

Each player carries a tracking ``status`` (nba_active / overseas_active /
retired); see player_status.classify_status. A player who leaves an NBA roster
but whose Wikipedia shows a current overseas team becomes overseas_active (not
retired); one with no team for 2+ years becomes retired.

Rate limiting: WikipediaClient enforces --delay seconds between requests and a
hard --max-requests budget per run (roster fetches count toward it).

Outputs are written under /data and /logs; the root
nba_players_careers_READY.json is kept in sync so index.html keeps working.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

from wikipedia_api import WikipediaClient, RequestBudgetExceeded
from team_normalizer import TeamNormalizer
from wiki_parser import parse_player
from rosters import fetch_all_rosters, NBA_TEAMS
from player_status import (classify_status, NBA_ACTIVE, OVERSEAS_ACTIVE,
                           RETIRED)
from geo import resolve_location

ROOT = Path(__file__).resolve().parent.parent
DATA, LOGS = ROOT / "data", ROOT / "logs"
CAREERS = DATA / "players" / "nba_players_careers.json"
ACTIVE = DATA / "players" / "active_players.json"
RETIRED = DATA / "players" / "retired_players.json"
LOCATIONS = DATA / "teams" / "team_locations.json"
REVIEW = DATA / "teams" / "teams_needing_review.json"
UPDATE_LOG = LOGS / "update_log.json"
CHANGELOG = LOGS / "changelog.md"
ROOT_MAP_FILE = ROOT / "nba_players_careers_READY.json"


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _is_slash_joined(team: str) -> bool:
    """True for names like "Sheffield Forgers / Sharks" (slash padded by spaces),
    which usually means two team names got merged. A slash without surrounding
    spaces (e.g. "Hapoel Gilboa/Afula") is a legitimate single name."""
    return bool(re.search(r"\S\s+/\s+\S", team or ""))


class Database:
    def __init__(self):
        players = load_json(CAREERS, [])
        self.by_name = {p["player"]: p for p in players}
        self.order = [p["player"] for p in players]
        self.locations = load_json(LOCATIONS, {})
        self.review = load_json(REVIEW, {})
        self.normalizer = TeamNormalizer()

    # -- team locations -----------------------------------------------------

    def location_for(self, team: str) -> dict:
        return self.locations.get(team, {})

    def enrich_stint(self, stint: dict, client: WikipediaClient,
                     discovered: dict) -> None:
        """Attach city/state/country to a parsed stint from known locations,
        discovering + flagging unknown teams."""
        team = stint["team"]

        # A slash-joined name (e.g. "Sheffield Forgers / Sharks") is almost
        # always two historical names run together by a misparse. Flag it for
        # manual review instead of trying to geolocate it.
        if _is_slash_joined(team):
            stint["city"] = stint["state"] = stint["country"] = ""
            self.review[team] = {"team": team,
                                 "reason": "slash-joined name — likely misparse of two teams",
                                 "city": "", "country": ""}
            return

        loc = self.locations.get(team)
        if loc and (loc.get("city") or loc.get("country")):
            stint["city"] = loc.get("city", "")
            stint["state"] = loc.get("state", "")
            stint["country"] = loc.get("country", "")
            return
        # unknown / incomplete team -> try discovery, then flag for review
        found = discovered.get(team)
        if found is None:
            found = self._discover_location(team, client)
            discovered[team] = found
        stint["city"] = found.get("city", "")
        stint["state"] = found.get("state", "")
        stint["country"] = found.get("country", "")
        self.locations[team] = {"team": team, **found,
                                "league": self.locations.get(team, {}).get("league", "")}
        if not found.get("city") or not found.get("country"):
            self.review[team] = {"team": team,
                                 "reason": "auto-added; needs location confirmation",
                                 "city": found.get("city", ""),
                                 "country": found.get("country", "")}

    def _discover_location(self, team: str, client: WikipediaClient) -> dict:
        try:
            extract = client.get_extract(team)
        except RequestBudgetExceeded:
            raise
        except Exception:  # noqa: BLE001
            extract = None
        if not extract:
            return {"city": "", "state": "", "country": ""}
        # light heuristic: "... based in <City>, <Region-or-Country>"
        m = re.search(r"based in ([A-Z][\w.\- ]+?)(?:,\s*([A-Z][\w.\- ]+?))?[.,]",
                      extract)
        if m:
            city = m.group(1).strip()
            # resolve region names (Lazio, Subcarpathian Voivodeship, …) to a
            # country; unknown tokens keep the country blank so review flags it.
            state, country = resolve_location(m.group(2) or "")
            return {"city": city, "state": state, "country": country}
        return {"city": "", "state": "", "country": ""}


def merge_player(db: Database, name: str, client: WikipediaClient,
                 discovered: dict, roster_players: set[str],
                 current_year: int) -> tuple[dict | None, list[str]]:
    """Fetch + parse a player, enrich locations, classify tracking status, and
    merge into DB. Returns (record, new_teams_discovered)."""
    wt = client.get_wikitext(name)
    if not wt:
        return None, []
    rec = parse_player(wt, name, db.normalizer)
    rec.pop("_raw_teams", None)
    # The parser's "status" is a parse outcome; keep it under parse_status so it
    # never collides with the tracking status set below.
    rec["parse_status"] = rec.pop("status", "success")
    new_teams = []
    for stint in rec["career_history"]:
        if stint["team"] not in db.locations or not (
                db.locations[stint["team"]].get("city")
                or db.locations[stint["team"]].get("country")):
            if stint["team"] not in db.locations:
                new_teams.append(stint["team"])
        db.enrich_stint(stint, client, discovered)
    rec["status"] = classify_status(rec, on_nba_roster=name in roster_players,
                                     current_year=current_year)
    rec["last_updated"] = today()
    db.by_name[name] = rec
    if name not in db.order:
        db.order.append(name)
    return rec, new_teams


def _dedupe(seq) -> list[str]:
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


def _by_status(db: Database, status: str) -> list[str]:
    """DB players with the given tracking status, least-recently-updated first."""
    names = [n for n, r in db.by_name.items() if r.get("status") == status]
    return sorted(names, key=lambda n: db.by_name[n].get("last_updated", "0000-00-00"))


def build_queue(db: Database, mode: str, player: str | None,
                roster_players: set[str]) -> list[str]:
    if mode == "single":
        return [player] if player else []

    overseas = _by_status(db, OVERSEAS_ACTIVE)
    if mode == "full_overseas":
        return overseas

    nba_active = _by_status(db, NBA_ACTIVE)
    # roster newcomers not yet tracked (rookies / signings)
    new_players = sorted(roster_players - set(db.by_name))
    # players we thought were NBA-active but who are no longer on any roster:
    # re-check to see if they moved overseas (-> overseas_active) or retired.
    dropped = [n for n in nba_active if n not in roster_players]
    stale_nba = [n for n in nba_active if n in roster_players]

    if mode == "full":
        # every active player (NBA + overseas) plus any roster newcomers
        pool = new_players + dropped + stale_nba + overseas
    else:  # incremental
        pool = new_players + dropped + overseas + stale_nba
    return _dedupe(pool)


def run(mode: str, player: str | None, delay: float, max_requests: int) -> dict:
    db = Database()
    client = WikipediaClient(delay=delay, max_requests=max_requests)
    current_year = dt.datetime.now(dt.timezone.utc).year
    summary = {"date": today(), "mode": mode, "players_updated": [],
               "new_players": [], "new_teams": [], "team_moves": [],
               "status_changes": [], "newly_overseas": [], "newly_retired": [],
               "requests": 0, "budget_exhausted": False}

    if mode == "review":
        _run_review(db, client, summary)
    else:
        roster_players: set[str] = set()
        if mode in ("incremental", "full"):
            rosters = fetch_all_rosters(client)
            for team_players in rosters.values():
                roster_players.update(team_players)
        queue = build_queue(db, mode, player, roster_players)
        discovered: dict = {}
        for name in queue:
            prev = db.by_name.get(name)
            prev_current = prev.get("current_team") if prev else None
            prev_status = prev.get("status") if prev else None
            try:
                rec, new_teams = merge_player(db, name, client, discovered,
                                              roster_players, current_year)
            except RequestBudgetExceeded:
                summary["budget_exhausted"] = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[update] {name}: failed ({exc})")
                continue
            if rec is None:
                continue
            if prev is None:
                summary["new_players"].append(name)
            summary["players_updated"].append(name)
            summary["new_teams"].extend(new_teams)
            if prev_current and rec.get("current_team") and \
                    prev_current != rec["current_team"]:
                summary["team_moves"].append(
                    {"player": name, "from": prev_current,
                     "to": rec["current_team"]})
            new_status = rec.get("status")
            if prev_status and new_status and prev_status != new_status:
                summary["status_changes"].append(
                    {"player": name, "from": prev_status, "to": new_status})
                if new_status == OVERSEAS_ACTIVE:
                    summary["newly_overseas"].append(name)
                elif new_status == RETIRED:
                    summary["newly_retired"].append(name)

    summary["requests"] = client.requests_made
    summary["new_teams"] = sorted(set(summary["new_teams"]))
    _persist(db, summary)
    return summary


def _run_review(db: Database, client: WikipediaClient, summary: dict) -> None:
    resolved = []
    for team in list(db.review):
        try:
            found = db._discover_location(team, client)
        except RequestBudgetExceeded:
            summary["budget_exhausted"] = True
            break
        if found.get("city") and found.get("country"):
            db.locations[team] = {"team": team, **found,
                                  "league": db.locations.get(team, {}).get("league", "")}
            db.review.pop(team, None)
            resolved.append(team)
    summary["resolved_teams"] = resolved


def _persist(db: Database, summary: dict) -> None:
    players = [db.by_name[n] for n in db.order]
    write_json(CAREERS, players)
    write_json(LOCATIONS, dict(sorted(db.locations.items())))
    write_json(REVIEW, dict(sorted(db.review.items())))

    # active/retired refresh from the stored tracking status
    nba_active = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    overseas = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in players if p.get("status") == RETIRED)
    write_json(ACTIVE, {"count": len(nba_active) + len(overseas),
                        "nba_active": nba_active, "overseas_active": overseas})
    write_json(RETIRED, {"count": len(retired), "players": retired})

    # keep the map data file in sync (drop non-map metadata for a lean file)
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
    write_json(ROOT_MAP_FILE, map_players)

    _append_logs(summary)


def _append_logs(summary: dict) -> None:
    log = load_json(UPDATE_LOG, {"runs": []})
    log["runs"].append(summary)
    write_json(UPDATE_LOG, log)

    lines = [
        f"## {summary['date']} — {summary['mode']} run",
        "",
        f"- Players updated: **{len(summary['players_updated'])}**"
        f" ({len(summary['new_players'])} new)",
        f"- New teams discovered: **{len(summary['new_teams'])}**",
        f"- Team moves detected: **{len(summary['team_moves'])}**",
        f"- Status changes: **{len(summary.get('status_changes', []))}**"
        f" ({len(summary.get('newly_overseas', []))} → overseas,"
        f" {len(summary.get('newly_retired', []))} → retired)",
        f"- Wikipedia requests: {summary['requests']}"
        + ("  ⚠️ budget exhausted — continues next run"
           if summary.get("budget_exhausted") else ""),
    ]
    if summary["new_players"]:
        lines.append(f"- New players: {', '.join(summary['new_players'][:25])}"
                     + (" …" if len(summary["new_players"]) > 25 else ""))
    if summary["new_teams"]:
        lines.append(f"- New teams: {', '.join(summary['new_teams'][:25])}"
                     + (" …" if len(summary["new_teams"]) > 25 else ""))
    for mv in summary["team_moves"][:25]:
        lines.append(f"  - {mv['player']}: {mv['from']} → {mv['to']}")
    for sc in summary.get("status_changes", [])[:25]:
        lines.append(f"  - {sc['player']}: [{sc['from']} → {sc['to']}]")
    lines.append("")
    header = "" if CHANGELOG.exists() else "# Career Database Changelog\n\n"
    existing = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else ""
    CHANGELOG.parent.mkdir(parents=True, exist_ok=True)
    CHANGELOG.write_text(header + "\n".join(lines) + "\n" + existing,
                         encoding="utf-8")


def parse_args():
    ap = argparse.ArgumentParser(description="Update NBA career database.")
    ap.add_argument("--mode",
                    choices=["incremental", "full", "full_overseas", "single",
                             "review"],
                    default="incremental")
    ap.add_argument("--player", default=None, help="player name for --mode single")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--max-requests", type=int, default=100)
    return ap.parse_args()


def main():
    args = parse_args()
    summary = run(args.mode, args.player, args.delay, args.max_requests)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v)
                      for k, v in summary.items()}, indent=2))
    # expose a one-line commit message for the workflow
    msg = (f"Auto-update: {summary['date']} - "
           f"{len(summary['players_updated'])} players updated, "
           f"{len(summary['new_teams'])} new teams")
    n_status = len(summary.get("status_changes", []))
    if n_status:
        msg += f", {n_status} status changes"
    (LOGS).mkdir(parents=True, exist_ok=True)
    (LOGS / "last_commit_message.txt").write_text(msg + "\n", encoding="utf-8")
    print(msg)


if __name__ == "__main__":
    main()
