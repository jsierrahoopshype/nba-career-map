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
from player_status import (classify_status, last_active_year, PRESENT,
                           NBA_ACTIVE, OVERSEAS_ACTIVE,
                           RETIRED as RETIRED_STATUS)  # RETIRED name is the file path below
from geo import resolve_location
from names import normkey, url_key, canonical_url

ROOT = Path(__file__).resolve().parent.parent
DATA, LOGS = ROOT / "data", ROOT / "logs"
CAREERS = DATA / "players" / "nba_players_careers.json"
ACTIVE = DATA / "players" / "active_players.json"
RETIRED = DATA / "players" / "retired_players.json"
LOCATIONS = DATA / "teams" / "team_locations.json"
REVIEW = DATA / "teams" / "teams_needing_review.json"
UPDATE_LOG = LOGS / "update_log.json"
CHANGELOG = LOGS / "changelog.md"
# Append-only transaction ledger: one record per real current_team change,
# captured going forward (past runs' previous values weren't kept, so this
# cannot be backfilled). Feeds the dashboard "latest_signings" widget.
TRANSACTIONS = DATA / "logs" / "transactions.json"
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
        # dedupe indexes: normalized name / aliases, and canonical Wikipedia URL
        self.norm_index: dict[str, str] = {}
        self.url_index: dict[str, str] = {}
        for p in players:
            self._index(p)

    # -- dedupe index -------------------------------------------------------

    def _index(self, rec: dict) -> None:
        """Register a record's name, aliases and URL in the dedupe indexes."""
        name = rec["player"]
        for key in (name, rec.get("display_name", ""), *rec.get("aliases", [])):
            k = normkey(key)
            if k:
                self.norm_index.setdefault(k, name)
        u = url_key(rec.get("wikipedia_url", ""))
        if u:
            self.url_index.setdefault(u, name)

    def resolve_by_name(self, candidate: str) -> str | None:
        """Existing player matching a candidate by normalized name, else None."""
        return self.norm_index.get(normkey(candidate))

    def resolve_canonical(self, canonical_title: str) -> str | None:
        """Existing player matching a resolved Wikipedia article (URL or name)."""
        if not canonical_title:
            return None
        return (self.url_index.get(url_key(canonical_url(canonical_title)))
                or self.norm_index.get(normkey(canonical_title)))

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
        else:
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

        # A slash without surrounding spaces (e.g. "Birmingham/Laketown Squadron")
        # may still be two merged names — flag for verification without discarding
        # the location we found. (Space-padded slashes are handled above.)
        if "/" in team:
            self.review.setdefault(team, {
                "team": team, "reason": "contains '/', verify not two merged teams",
                "city": stint.get("city", ""), "country": stint.get("country", "")})

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
            # resolve region/US-state names (Lazio, Georgia, …) to a country,
            # passing the city so ambiguous "Georgia" disambiguates; unknown
            # tokens keep the country blank so review flags it.
            state, country = resolve_location(m.group(2) or "", city)
            return {"city": city, "state": state, "country": country}
        return {"city": "", "state": "", "country": ""}


def _richer(a: list, b: list) -> bool:
    """True if career history `a` is at least as rich as `b` (more stints)."""
    return len(a or []) >= len(b or [])


def _is_real_move(normalizer: TeamNormalizer, prev: str, new: str) -> bool:
    """True only when `prev` and `new` resolve to different CANONICAL clubs.

    A raw string diff can fire on a club-name normalization rather than an
    actual transfer — e.g. a cached pre-alias spelling ("Beşiktaş Gain") next
    to a freshly-normalized one ("Beşiktaş"), or a club's infobox name
    changing without the player moving. Both sides go through the same
    team_aliases-backed normalizer before comparing.
    """
    if not prev or not new:
        return False
    return normalizer.normalize(prev) != normalizer.normalize(new)


def merge_player(db: Database, name: str, client: WikipediaClient,
                 discovered: dict, roster_players: set[str],
                 current_year: int) -> tuple[dict | None, list[str], bool]:
    """Fetch + parse a player, dedupe against existing records by canonical
    Wikipedia article, enrich locations, classify status, and upsert.

    Returns (record, new_teams, is_new). A candidate that resolves to an
    existing record (diacritics, suffix, nickname, redirect) is MERGED into it
    rather than inserted as a duplicate.
    """
    wt, canonical_title = client.get_wikitext_and_title(name)
    if not wt:
        # page not found (e.g. a "(1990)" disambiguated title with no matching
        # article/redirect) — skip cleanly. Return the full 5-tuple so the
        # caller's unpack never raises "expected 5, got 3".
        return None, [], False, None, None
    fresh = parse_player(wt, name, db.normalizer)
    fresh.pop("_raw_teams", None)
    fresh["parse_status"] = fresh.pop("status", "success")
    fresh_valid = bool(fresh.get("career_history"))
    curl = canonical_url(canonical_title) if canonical_title else canonical_url(name)

    # Resolve to an existing record: same key, same canonical article (URL or
    # normalized title), or same normalized display name.
    existing_name = (name if name in db.by_name
                     else db.resolve_canonical(canonical_title)
                     or db.resolve_by_name(name))
    is_new = existing_name is None
    base = db.by_name.get(existing_name, {}) if existing_name else {}
    prev_status = base.get("status")
    prev_current = base.get("current_team")

    # Choose the history source: the freshly-fetched page when it parsed and is
    # at least as rich, otherwise the existing record (so a failed/empty parse
    # like the "A. J. Green" disambiguation page never clobbers real data).
    use_fresh = fresh_valid and _richer(fresh.get("career_history"),
                                        base.get("career_history"))
    primary = fresh if use_fresh else (base or fresh)

    rec = dict(base)  # start from existing to preserve fields we don't refresh
    rec["career_history"] = primary.get("career_history", [])
    rec["current_team"] = primary.get("current_team", "")
    # scalar fields: prefer the chosen source, fall back to the other
    for f in ("position", "number", "birth_date", "birth_place", "death_date",
              "death_place", "high_school", "college", "draft",
              "nationality", "all_star"):
        val = primary.get(f) or fresh.get(f) or base.get(f)
        if val:
            rec[f] = val
    rec["parse_status"] = fresh.get("parse_status", base.get("parse_status", "success"))

    # primary key + display name + aliases
    if is_new:
        key = canonical_title or name
    else:
        key = existing_name  # keep existing (frontend-load-bearing) primary key
    # never create/persist a record with an empty name (guards against junk
    # rows like the blank-name seed artifact)
    if not str(key or "").strip():
        return None, [], False, None, None
    rec["player"] = key
    rec["display_name"] = canonical_title or rec.get("display_name") or key
    aliases = set(base.get("aliases", []))
    for alt in (name, canonical_title, base.get("player")):
        if alt and alt != key:
            aliases.add(alt)
    if aliases:
        rec["aliases"] = sorted(aliases)
    rec["wikipedia_url"] = curl or base.get("wikipedia_url", "")

    # locations
    new_teams = []
    for stint in rec["career_history"]:
        if stint["team"] not in db.locations:
            new_teams.append(stint["team"])
        db.enrich_stint(stint, client, discovered)

    # Explicit-retirement-announcement signal (bug fix): sticky once detected —
    # a transient regex miss on a later re-fetch (e.g. the prose gets copy-
    # edited) must not un-retire someone we already confirmed. The one
    # exception is a genuine comeback: if this run's fresh, richer history
    # shows a stint dated after the recorded retirement year, the retirement
    # no longer holds and the flag clears.
    fresh_retired = bool(fresh.get("retirement_announced"))
    prev_retired = bool(base.get("retirement_announced"))
    comeback = False
    if prev_retired and not fresh_retired and use_fresh:
        ret_year = re.search(r"\d{4}", base.get("retirement_date", "") or "")
        ly = last_active_year(rec["career_history"])
        if ret_year and ly and (ly == PRESENT or ly > int(ret_year.group())):
            comeback = True
    if fresh_retired or (prev_retired and not comeback):
        rec["retirement_announced"] = True
        rd = fresh.get("retirement_date") or base.get("retirement_date", "")
        if rd:
            rec["retirement_date"] = rd
    else:
        rec.pop("retirement_announced", None)
        rec.pop("retirement_date", None)

    rec["status"] = classify_status(
        rec, on_nba_roster=name in roster_players or key in roster_players,
        current_year=current_year, retirement_announced=rec.get("retirement_announced", False))
    rec["last_updated"] = today()

    # upsert: if we merged into a different existing key, drop the queue name
    if not is_new and key != name and name in db.by_name:
        db.by_name.pop(name, None)
    db.by_name[key] = rec
    if key not in db.order:
        db.order.append(key)
    db._index(rec)
    return rec, new_teams, is_new, prev_status, prev_current


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
    # Map roster candidates to existing records by canonical name key, so a
    # roster spelling that differs from the DB spelling (Şengün vs Sengun) is
    # recognized as the same player rather than a newcomer.
    on_roster = {db.resolve_by_name(c) for c in roster_players}
    on_roster.discard(None)
    # roster newcomers not yet tracked (rookies / signings)
    new_players = _dedupe(sorted(c for c in roster_players
                                 if db.resolve_by_name(c) is None))
    # NBA-active players not matched by any roster candidate: re-check whether
    # they moved overseas (-> overseas_active) or retired. Canonical matching
    # stops variant-spelling players (Alperen Sengun) being re-fetched as
    # "dropped" every run while their diacritic spelling is added as "new".
    dropped = [n for n in nba_active if n not in on_roster]
    stale_nba = [n for n in nba_active if n in on_roster]

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
            try:
                rec, new_teams, is_new, prev_status, prev_current = merge_player(
                    db, name, client, discovered, roster_players, current_year)
            except RequestBudgetExceeded:
                summary["budget_exhausted"] = True
                break
            except Exception as exc:  # noqa: BLE001
                print(f"[update] {name}: failed ({exc})")
                continue
            if rec is None:
                continue
            key = rec["player"]
            if is_new:
                summary["new_players"].append(key)
            summary["players_updated"].append(key)
            summary["new_teams"].extend(new_teams)
            new_current = rec.get("current_team")
            if _is_real_move(db.normalizer, prev_current, new_current):
                summary["team_moves"].append(
                    {"player": key, "from": prev_current, "to": new_current})
            new_status = rec.get("status")
            if prev_status and new_status and prev_status != new_status:
                summary["status_changes"].append(
                    {"player": key, "from": prev_status, "to": new_status})
                if new_status == OVERSEAS_ACTIVE:
                    summary["newly_overseas"].append(key)
                elif new_status == RETIRED_STATUS:
                    summary["newly_retired"].append(key)

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
    # defensively drop any empty-name record so junk rows never reach disk
    for empty in [n for n in list(db.order) if not str(n or "").strip()]:
        db.by_name.pop(empty, None)
        db.order.remove(empty)
    players = [db.by_name[n] for n in db.order if str(n or "").strip()]
    write_json(CAREERS, players)
    write_json(LOCATIONS, dict(sorted(db.locations.items())))
    write_json(REVIEW, dict(sorted(db.review.items())))

    # active/retired refresh from the stored tracking status
    nba_active = sorted(p["player"] for p in players if p.get("status") == NBA_ACTIVE)
    overseas = sorted(p["player"] for p in players if p.get("status") == OVERSEAS_ACTIVE)
    retired = sorted(p["player"] for p in players if p.get("status") == RETIRED_STATUS)
    write_json(ACTIVE, {"count": len(nba_active) + len(overseas),
                        "nba_active": nba_active, "overseas_active": overseas})
    write_json(RETIRED, {"count": len(retired), "players": retired})

    # keep the map data file in sync (drop non-map metadata for a lean file)
    map_players = []
    for p in players:
        # `player` stays the primary key the map/quiz already index on (ASCII,
        # frontend-load-bearing). display_name carries the canonical spelling
        # (e.g. diacritics) for the frontend to adopt when ready — index.html
        # ignores unknown fields, so adding it is safe today.
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
        if p.get("nationality"):
            mp["nationality"] = p["nationality"]
        if p.get("all_star") is not None:
            mp["all_star"] = p["all_star"]
        map_players.append(mp)
    write_json(ROOT_MAP_FILE, map_players)

    _append_logs(summary)
    _append_transactions(summary)


def _append_transactions(summary: dict) -> None:
    """Append this run's current_team changes to the append-only ledger.

    Each ``team_moves`` entry ({player, from, to}) becomes a dated transaction
    record. The file is only ever appended to; existing records are preserved.
    """
    moves = summary.get("team_moves", [])
    if not moves:
        return
    ledger = load_json(TRANSACTIONS, {"transactions": []})
    if isinstance(ledger, list):  # tolerate a bare-list file shape
        ledger = {"transactions": ledger}
    date = summary.get("date", today())
    for mv in moves:
        ledger["transactions"].append({
            "player": mv["player"],
            "from_team": mv.get("from", ""),
            "to_team": mv.get("to", ""),
            "date": date,
        })
    write_json(TRANSACTIONS, ledger)


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
