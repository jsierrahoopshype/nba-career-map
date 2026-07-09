"""Build the consolidated dashboard data layer (Phase 1: data only, no UI).

Reads the existing career database (data/players/nba_players_careers.json) plus
the append-only transaction log (data/logs/transactions.json) and the COORDS
table embedded in index.html, and writes ONE consolidated file
data/dashboard_data.json with one top-level key per dashboard widget.

This is additive and read-only against every existing data file (it only WRITES
data/dashboard_data.json). It runs as the final step of the update workflow so
the dashboard stays fresh after every pipeline run, and is runnable standalone:

    python3 scripts/build_dashboard_data.py            # build + write
    python3 scripts/build_dashboard_data.py --report   # build + write + print samples

"NBA franchise" here means the 30 current NBA teams UNION every historical era
name in the relocation reference table (era_correct_teams.ERA_TABLE) — so
"Seattle SuperSonics", "Syracuse Nationals", "New Orleans Jazz" etc. count as
NBA, not as overseas/other-league clubs. A "non-NBA team" is any team name
outside that set (overseas leagues, G League, ABA, college, defunct minor
leagues). Note: a handful of fully-defunct 1940s-50s BAA/NBA franchises are not
in the reference table and would be treated as non-NBA; they are rare and sort
to the bottom of the ranked widgets.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from era_correct_teams import ERA_TABLE  # noqa: E402
from rosters import NBA_TEAMS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"
INDEX_HTML = ROOT / "index.html"
OUT = ROOT / "data" / "dashboard_data.json"

OVERSEAS = "overseas_active"

# --- NBA franchise identity -------------------------------------------------
# era name -> current franchise name; current teams map to themselves.
_ERA_TO_CURRENT: dict[str, str] = {}
for _current, _eras in ERA_TABLE.items():
    for _b, _name in _eras:
        _ERA_TO_CURRENT[_name] = _current
for _t in NBA_TEAMS:
    _ERA_TO_CURRENT.setdefault(_t, _t)

NBA_FRANCHISE_NAMES = set(_ERA_TO_CURRENT)  # all NBA names, any era


def is_nba_team(team: str) -> bool:
    return team in NBA_FRANCHISE_NAMES


def nba_franchise_of(team: str) -> str | None:
    """Canonical current franchise for an NBA team name, else None (non-NBA)."""
    return _ERA_TO_CURRENT.get(team)


# --- helpers ----------------------------------------------------------------
def _current_stint(player: dict) -> dict | None:
    """The player's own stint matching current_team (most recent match)."""
    ct = player.get("current_team", "")
    match = None
    for s in player.get("career_history", []):
        if s.get("team") == ct:
            match = s
    return match


def _distinct_countries(player: dict) -> list[str]:
    out = []
    for s in player.get("career_history", []):
        c = (s.get("country") or "").strip()
        if c and c not in out:
            out.append(c)
    return out


def load_coords_keys() -> set[str]:
    """Extract the key set of the COORDS object literal embedded in index.html.

    index.html is only READ, never modified. getCoords() resolves a stint by
    trying `city|state|country` then `city||country`; a combo is "in COORDS"
    when either key is a literal COORDS entry (the country-level FALLBACK jitter
    is NOT a real coordinate and does not count as coverage).
    """
    if not INDEX_HTML.exists():
        return set()
    text = INDEX_HTML.read_text(encoding="utf-8")
    m = re.search(r"const COORDS\s*=\s*", text)
    if not m:
        return set()
    i = text.index("{", m.end())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    obj = json.loads(text[i:j + 1])
    return set(obj.keys())


def _coords_keys(city: str, state: str, country: str) -> tuple[str, str]:
    return (f"{city}|{state or ''}|{country or ''}",
            f"{city}||{country or ''}")


def _rank(counter: dict, key_name: str, val_name: str) -> list[dict]:
    """dict -> list of {key_name, val_name} sorted by value desc then key asc."""
    return [{key_name: k, val_name: v}
            for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))]


# --- widgets ----------------------------------------------------------------
def w_where_are_they_now(players: list) -> list[dict]:
    out = []
    for p in players:
        if p.get("status") != OVERSEAS:
            continue
        st = _current_stint(p)
        out.append({"player": p["player"], "current_team": p.get("current_team", ""),
                    "country": (st or {}).get("country", "") if st else ""})
    out.sort(key=lambda r: r["player"])
    return out


def w_most_well_traveled(players: list) -> list[dict]:
    rows = []
    for p in players:
        countries = _distinct_countries(p)
        rows.append({"player": p["player"], "country_count": len(countries),
                     "stint_count": len(p.get("career_history", [])),
                     "countries": countries, "status": p.get("status", "")})
    rows.sort(key=lambda r: (-r["country_count"], -r["stint_count"], r["player"]))
    return rows[:50]


def w_teams_by_alltime_nba_alumni(players: list) -> list[dict]:
    counts: dict[str, int] = {}
    for p in players:
        seen = set()
        for s in p.get("career_history", []):
            t = s.get("team", "")
            if not t or is_nba_team(t) or t in seen:
                continue
            seen.add(t)
            counts[t] = counts.get(t, 0) + 1
    return _rank(counts, "team", "players")


def w_teams_by_current_nba_alumni(players: list) -> list[dict]:
    teams: dict[str, list] = {}
    for p in players:
        if p.get("status") != OVERSEAS:
            continue
        t = p.get("current_team", "")
        if not t or is_nba_team(t):
            continue
        teams.setdefault(t, []).append(p["player"])
    return [{"team": t, "count": len(ps), "players": sorted(ps)}
            for t, ps in sorted(teams.items(), key=lambda kv: (-len(kv[1]), kv[0]))]


def w_countries_live_snapshot(players: list) -> list[dict]:
    counts: dict[str, int] = {}
    for p in players:
        if p.get("status") != OVERSEAS:
            continue
        st = _current_stint(p)
        c = (st or {}).get("country", "").strip() if st else ""
        if c:
            counts[c] = counts.get(c, 0) + 1
    return _rank(counts, "country", "count")


def w_countries_alltime_alumni(players: list) -> list[dict]:
    counts: dict[str, int] = {}
    for p in players:
        for c in _distinct_countries(p):
            counts[c] = counts.get(c, 0) + 1
    return _rank(counts, "country", "players")


def w_team_reunions(players: list) -> list[dict]:
    # current non-NBA team -> list of (player, {current NBA franchises})
    teams: dict[str, list] = {}
    for p in players:
        if p.get("status") != OVERSEAS:
            continue
        t = p.get("current_team", "")
        if not t or is_nba_team(t):
            continue
        franchises = set()
        for s in p.get("career_history", []):
            fr = nba_franchise_of(s.get("team", ""))
            if fr:
                franchises.add(fr)
        teams.setdefault(t, []).append((p["player"], franchises))

    out = []
    for t, members in sorted(teams.items()):
        if len(members) < 2:
            continue
        pairs = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                shared = sorted(members[i][1] & members[j][1])
                if shared:
                    pairs.append({"players": [members[i][0], members[j][0]],
                                  "shared_nba_franchises": shared})
        out.append({"team": t,
                    "players": sorted(m[0] for m in members),
                    "shared_franchise_pairs": pairs})
    out.sort(key=lambda r: (-len(r["players"]), r["team"]))
    return out


def w_boomerang_players(players: list) -> list[dict]:
    out = []
    for p in players:
        hist = p.get("career_history", [])
        # collapse consecutive runs of the same team; a team appearing in 2+
        # separate runs means the player left and came back (non-contiguous).
        runs = []  # (team, [years...])
        for s in hist:
            t = s.get("team", "")
            if runs and runs[-1][0] == t:
                runs[-1][1].append(s.get("years", ""))
            else:
                runs.append((t, [s.get("years", "")]))
        run_counts: dict[str, int] = {}
        for t, _ in runs:
            run_counts[t] = run_counts.get(t, 0) + 1
        boomerangs = []
        for t, n in run_counts.items():
            if t and n >= 2:
                spans = [s.get("years", "") for s in hist if s.get("team") == t]
                boomerangs.append({"team": t, "stints": n, "years": spans})
        if boomerangs:
            out.append({"player": p["player"],
                        "teams": sorted(boomerangs, key=lambda b: (-b["stints"], b["team"]))})
    out.sort(key=lambda r: (-max(b["stints"] for b in r["teams"]), r["player"]))
    return out


def w_world_tour_heatmap(players: list, coords_keys: set[str]) -> dict:
    rows, no_city, city_not_in_coords = [], 0, 0
    missing_combos: dict[str, int] = {}
    for p in players:
        if p.get("status") != OVERSEAS:
            continue
        st = _current_stint(p) or {}
        city = (st.get("city") or "").strip()
        state = (st.get("state") or "").strip()
        country = (st.get("country") or "").strip()
        k1, k2 = _coords_keys(city, state, country)
        in_coords = bool(city) and (k1 in coords_keys or k2 in coords_keys)
        if not in_coords:
            if not city:
                no_city += 1
            else:
                city_not_in_coords += 1
                missing_combos[k1] = missing_combos.get(k1, 0) + 1
        rows.append({"player": p["player"], "current_team": p.get("current_team", ""),
                     "city": city, "state": state, "country": country,
                     "in_coords": in_coords})
    missing = no_city + city_not_in_coords
    return {"players": rows,
            "coverage": {"total": len(rows), "in_coords": len(rows) - missing,
                         "missing_from_coords": missing,
                         "missing_no_city": no_city,
                         "missing_city_absent_from_coords": city_not_in_coords,
                         "distinct_missing_combos": len(missing_combos)}}


def w_latest_signings() -> list[dict]:
    if not TRANSACTIONS.exists():
        return []
    data = json.loads(TRANSACTIONS.read_text(encoding="utf-8"))
    txns = data.get("transactions", []) if isinstance(data, dict) else data
    # append-only, chronological; most recent 50 (newest first)
    return list(reversed(txns[-50:]))


def build() -> dict:
    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    coords_keys = load_coords_keys()
    return {
        "generated_from": "data/players/nba_players_careers.json",
        "player_count": len(players),
        "where_are_they_now": w_where_are_they_now(players),
        "most_well_traveled": w_most_well_traveled(players),
        "teams_by_alltime_nba_alumni": w_teams_by_alltime_nba_alumni(players),
        "teams_by_current_nba_alumni": w_teams_by_current_nba_alumni(players),
        "countries_live_snapshot": w_countries_live_snapshot(players),
        "countries_alltime_alumni": w_countries_alltime_alumni(players),
        "team_reunions": w_team_reunions(players),
        "boomerang_players": w_boomerang_players(players),
        "world_tour_heatmap": w_world_tour_heatmap(players, coords_keys),
        "latest_signings": w_latest_signings(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="print a top-10-ish sample of each widget after writing")
    args = ap.parse_args()

    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes)")

    if args.report:
        _report(data)


def _report(data: dict) -> None:
    def head(title, rows, n=10):
        print(f"\n=== {title}  (total {len(rows)}) ===")
        for r in rows[:n]:
            print("  ", json.dumps(r, ensure_ascii=False))

    head("1. where_are_they_now", data["where_are_they_now"])
    head("2. most_well_traveled", data["most_well_traveled"])
    head("3. teams_by_alltime_nba_alumni", data["teams_by_alltime_nba_alumni"])
    head("4. teams_by_current_nba_alumni", data["teams_by_current_nba_alumni"])
    head("5. countries_live_snapshot", data["countries_live_snapshot"])
    head("6. countries_alltime_alumni", data["countries_alltime_alumni"])
    head("7. team_reunions", data["team_reunions"])
    head("8. boomerang_players", data["boomerang_players"])
    ht = data["world_tour_heatmap"]
    print(f"\n=== 9. world_tour_heatmap  (total {ht['coverage']['total']}) ===")
    print("   coverage:", json.dumps(ht["coverage"]))
    for r in ht["players"][:10]:
        print("  ", json.dumps(r, ensure_ascii=False))
    head("10. latest_signings", data["latest_signings"])


if __name__ == "__main__":
    main()
