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
from sync_era_locations import LOC  # noqa: E402  (era-accurate team locations)

ROOT = Path(__file__).resolve().parent.parent
CAREERS = ROOT / "data" / "players" / "nba_players_careers.json"
TEAM_LOCATIONS = ROOT / "data" / "teams" / "team_locations.json"
TRANSACTIONS = ROOT / "data" / "logs" / "transactions.json"
INDEX_HTML = ROOT / "index.html"
OUT = ROOT / "data" / "dashboard_data.json"
TEAM_PAGES_OUT = ROOT / "data" / "team_pages.json"
CLUB_PAGES_OUT = ROOT / "data" / "club_pages.json"
NBA_TEAM_INDEX_OUT = ROOT / "data" / "nba_team_index.json"
PLAYER_ALIASES_OUT = ROOT / "data" / "player_aliases.json"
PLAYER_INDEX_OUT = ROOT / "data" / "player_index.json"  # all names, light homepage search
SITEMAP_OUT = ROOT / "sitemap.xml"

# Absolute origin the site is served from, used only for sitemap.xml. This is
# the permanent production origin (do not revert to a placeholder); an env
# override is allowed for non-prod builds.
import os  # noqa: E402
SITE_BASE_URL = os.environ.get(
    "SITE_BASE_URL", "https://jsierrahoopshype.github.io/nba-career-map").rstrip("/")

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

# Historical NBA era names that a modern (G League) club has re-used verbatim.
# A stint under such a name whose START year is >= the cutoff is the modern
# club, NOT the NBA era, so it must not be filed under the franchise's history.
# (Only "San Diego Clippers": the LA Clippers' G League affiliate relocated to
# San Diego in 2024 and shares the 1978-1984 NBA era name.)
ERA_COLLISIONS = {"San Diego Clippers": 2024}
_YEAR_PRESENT = 9999  # sentinel for "current" when only a team name is known


def _yr(years) -> int:
    m = re.search(r"\d{4}", str(years or ""))
    return int(m.group()) if m else 0


def _is_modern_collision(team: str, year: int | None) -> bool:
    cut = ERA_COLLISIONS.get(team)
    return cut is not None and year is not None and year >= cut


def is_nba_team(team: str, year: int | None = None) -> bool:
    if _is_modern_collision(team, year):
        return False
    return team in NBA_FRANCHISE_NAMES


def nba_franchise_of(team: str, year: int | None = None) -> str | None:
    """Canonical current franchise for an NBA team name, else None (non-NBA).

    A collision era name (e.g. "San Diego Clippers") resolves to None when the
    stint's year is in the modern G-League club's range, so the G League club is
    never filed under the NBA franchise's history."""
    if _is_modern_collision(team, year):
        return None
    return _ERA_TO_CURRENT.get(team)


# Country of each current NBA franchise's home city. Prefer the era-accurate LOC
# map (correct e.g. Memphis=USA), fall back to the auto-generated team_locations
# for never-relocated franchises (e.g. Toronto=Canada), default USA. Used so
# nba_active players get their franchise's real country (not a hardcoded USA).
_TEAM_LOCATIONS = json.loads(TEAM_LOCATIONS.read_text(encoding="utf-8")) \
    if TEAM_LOCATIONS.exists() else {}
FRANCHISE_COUNTRY: dict[str, str] = {}
for _fr in NBA_TEAMS:
    FRANCHISE_COUNTRY[_fr] = (
        (LOC.get(_fr) or (None, None, None))[2]
        or (_TEAM_LOCATIONS.get(_fr, {}) or {}).get("country")
        or "USA")


def franchise_country(team: str) -> str:
    """Country of an NBA franchise (by any era name), defaulting to USA."""
    return FRANCHISE_COUNTRY.get(nba_franchise_of(team) or "", "USA")


# --- helpers ----------------------------------------------------------------
def _current_stint(player: dict) -> dict | None:
    """The player's own stint matching current_team (most recent match)."""
    ct = player.get("current_team", "")
    match = None
    for s in player.get("career_history", []):
        if s.get("team") == ct:
            match = s
    return match


def _nat_star(player: dict) -> dict:
    """nationality/all_star for a roster row, sparse — omitted (not stored as
    empty/null) when the source player has neither, so today's fully-unpopulated
    fields (the backfill is a separate GitHub Actions run) don't bloat every
    roster row across team_pages.json/club_pages.json with placeholders."""
    out = {}
    if player.get("nationality"):
        out["nationality"] = player["nationality"]
    if player.get("all_star") is not None:
        out["all_star"] = player["all_star"]
    return out


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
                    "country": (st or {}).get("country", "") if st else "",
                    "last_updated": p.get("last_updated", "")})
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
            if not t or is_nba_team(t, _yr(s.get("years"))) or t in seen:
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
        if not t or is_nba_team(t, _YEAR_PRESENT):
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
        if not t or is_nba_team(t, _YEAR_PRESENT):
            continue
        franchises = set()
        for s in p.get("career_history", []):
            fr = nba_franchise_of(s.get("team", ""), _yr(s.get("years")))
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
                     "in_coords": in_coords, "last_updated": p.get("last_updated", "")})
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


def _relocation_timeline(franchise: str) -> list[dict]:
    """Name/city history for a franchise, from the relocation reference table.

    Returns [] for franchises that never relocated/renamed (single era). Each
    entry is {name, start_year, end_year, current}: start_year is null for the
    founding era (the reference table records relocation boundaries, not
    founding years, so we do not invent one); end_year is the next boundary, or
    null for the present-day name.
    """
    eras = ERA_TABLE.get(franchise)
    if not eras or len(eras) < 2:
        return []
    out = []
    for i, (boundary, name) in enumerate(eras):
        start = None if boundary == 0 else boundary
        end = eras[i + 1][0] if i + 1 < len(eras) else None
        out.append({"name": name, "start_year": start, "end_year": end,
                    "current": end is None})
    return out


def compute_related(players: list, max_suggestions: int = 8) -> dict:
    """For every franchise and club, the up-to-N entities sharing the most alumni
    with it, back-filled with the largest clubs from the same country.

    Returns {(type, name): [{type, name, shared}, ...]} where type is "team"
    (franchise page) or "club" (club page). Computed here (data layer) so the
    frontend just renders links.
    """
    from collections import defaultdict
    membership: dict = defaultdict(set)     # entity -> set(players)
    country_of: dict = {}                   # entity -> country
    co: dict = defaultdict(int)             # (entityA, entityB) sorted -> shared players

    for p in players:
        ents = set()
        for s in p.get("career_history", []):
            team = s.get("team", "")
            if not team:
                continue
            fr = nba_franchise_of(team, _yr(s.get("years")))
            if fr:
                e = ("team", fr)
                country_of[e] = FRANCHISE_COUNTRY.get(fr, "USA")
            else:
                e = ("club", team)
                if not country_of.get(e):
                    country_of[e] = (s.get("country") or "").strip()
            ents.add(e)
        for e in ents:
            membership[e].add(p["player"])
        el = sorted(ents)
        for i in range(len(el)):
            for j in range(i + 1, len(el)):
                co[(el[i], el[j])] += 1

    size = {e: len(ps) for e, ps in membership.items()}
    neighbors: dict = defaultdict(list)
    for (a, b), cnt in co.items():
        neighbors[a].append((b, cnt))
        neighbors[b].append((a, cnt))

    # largest clubs per country, for back-fill
    clubs_by_country: dict = defaultdict(list)
    for e in membership:
        if e[0] == "club":
            clubs_by_country[country_of.get(e, "")].append(e)
    for c in clubs_by_country.values():
        c.sort(key=lambda e: (-size[e], e[1]))

    related: dict = {}
    for e in membership:
        # Suggest only non-NBA clubs — for BOTH franchise and club pages. Every
        # player in the DB is an NBA alumnus, so franchises always dominate
        # shared-alumni counts and would swamp the list; the interesting signal
        # is which CLUBS this entity's alumni also played for.
        picks, seen = [], {e}
        club_nbrs = [(nb, cnt) for nb, cnt in neighbors.get(e, []) if nb[0] == "club"]
        for nb, cnt in sorted(club_nbrs, key=lambda x: (-x[1], -size.get(x[0], 0), x[0][1])):
            if nb in seen:
                continue
            picks.append({"type": nb[0], "name": nb[1], "shared": cnt})
            seen.add(nb)
            if len(picks) >= max_suggestions:
                break
        if len(picks) < max_suggestions:
            for nb in clubs_by_country.get(country_of.get(e, ""), []):
                if nb in seen:
                    continue
                picks.append({"type": nb[0], "name": nb[1], "shared": 0})
                seen.add(nb)
                if len(picks) >= max_suggestions:
                    break
        related[e] = picks
    return related


def w_team_pages(players: list, related: dict | None = None) -> dict:
    """Per-franchise alumni rosters for the 30 current NBA teams.

    A player belongs to a franchise's roster if ANY of their stints was under
    that franchise (any era name) — reusing nba_franchise_of so the Lakers page
    includes Minneapolis-era players, the Thunder page includes Seattle-era
    players, etc. One roster row per stint (its own years span); a player with
    two separate stints for the franchise appears twice.
    """
    teams: dict[str, dict] = {}
    for fr in sorted(NBA_TEAMS):
        teams[fr] = {"franchise": fr,
                     "relocations": _relocation_timeline(fr),
                     "roster": [], "active_elsewhere": []}

    # active_elsewhere is deduped per (franchise, player); track seen alumni
    seen_active: dict[str, set] = {fr: set() for fr in teams}
    for p in players:
        status = p.get("status", "")
        ct = p.get("current_team", "")
        cs = _current_stint(p) or {}
        # country of the player's current/last team (where they are now for
        # active players; their final club for retired players). NBA-active
        # players get their franchise's real country (Toronto -> Canada).
        cur_country = (franchise_country(ct) if status == "nba_active"
                       else cs.get("country", ""))
        for s in p.get("career_history", []):
            fr = nba_franchise_of(s.get("team", ""), _yr(s.get("years")))
            if fr is None:
                continue
            teams[fr]["roster"].append({
                "player": p["player"],
                "years": s.get("years", ""),
                "stint_team": s.get("team", ""),
                "status": status,
                "current_team": ct,
                "current_country": cur_country,
                **_nat_star(p),
            })
            # "currently active elsewhere": alum still playing, not on this
            # franchise right now (current_team is not one of its era names).
            if status in ("nba_active", OVERSEAS) and nba_franchise_of(ct, _YEAR_PRESENT) != fr:
                if p["player"] not in seen_active[fr]:
                    seen_active[fr].add(p["player"])
                    teams[fr]["active_elsewhere"].append({
                        "player": p["player"], "status": status,
                        "current_team": ct,
                        "country": cs.get("country", "") if status == OVERSEAS
                        else franchise_country(ct),
                        **_nat_star(p),
                    })

    for fr, t in teams.items():
        t["roster"].sort(key=lambda r: (r["player"], r["years"]))
        t["active_elsewhere"].sort(key=lambda r: r["player"])
        t["roster_count"] = len(t["roster"])
        t["alumni_count"] = len({r["player"] for r in t["roster"]})
        if related is not None:
            t["related"] = related.get(("team", fr), [])
    return teams


# Cap per-club all-time roster to keep club_pages.json bounded. The largest club
# (~300 stints) is well under this, so no club is currently truncated; the cap
# is only a safety valve. Truncated clubs are flagged with "truncated": true.
CLUB_ROSTER_CAP = 500


def _join_years(spans: list) -> str:
    return ", ".join(s for s in sorted(spans, key=_year_start) if s)


def _year_start(years: str) -> int:
    m = re.search(r"\d{4}", str(years or ""))
    return int(m.group()) if m else 0


def w_club_pages(players: list, related: dict | None = None) -> dict:
    """All-time NBA-alumni roster for every non-NBA club (overseas / G League /
    ABA / college / etc.). club name -> {location, roster:[{player,years,status}],
    count}. ONE row per player, their year-spans at the club comma-joined
    (Carlos Delfino once with "2002-2004, 2019", not two rows)."""
    clubs: dict[str, dict] = {}
    for p in players:
        status = p.get("status", "")
        for s in p.get("career_history", []):
            team = s.get("team", "")
            if not team or is_nba_team(team, _yr(s.get("years"))):
                continue  # modern G-League "San Diego Clippers" falls through as a club
            c = clubs.setdefault(team, {"club": team, "city": "", "state": "",
                                        "country": "", "_by": {}})
            # club location: first stint that carries a country wins (so a
            # location-less stint never blanks a club that is placed elsewhere)
            if not c["country"] and (s.get("country") or "").strip():
                c["city"], c["state"], c["country"] = \
                    s.get("city", ""), s.get("state", ""), s.get("country", "")
            g = c["_by"].setdefault(p["player"], {"player": p["player"], "status": status,
                                                  "last_team": p.get("current_team", ""), "spans": [],
                                                  **_nat_star(p)})
            g["spans"].append(s.get("years", ""))

    for c in clubs.values():
        roster = []
        for g in c["_by"].values():
            roster.append({"player": g["player"], "years": _join_years(g["spans"]),
                           "status": g["status"], "last_team": g["last_team"],
                           **{k: g[k] for k in ("nationality", "all_star") if k in g}})
        roster.sort(key=lambda r: r["player"])
        del c["_by"]
        c["count"] = len(roster)
        if len(roster) > CLUB_ROSTER_CAP:
            roster = roster[:CLUB_ROSTER_CAP]
            c["truncated"] = True
        c["roster"] = roster
        if related is not None:
            c["related"] = related.get(("club", c["club"]), [])
    return clubs


def w_player_aliases(players: list) -> dict:
    """Canonical player name -> alternate strings (aliases + a differing
    display_name), for alias-aware search. Only players with an alt are listed."""
    out: dict[str, list] = {}
    for p in players:
        name = p.get("player", "")
        if not name:
            continue
        alts = set(p.get("aliases") or [])
        dn = p.get("display_name")
        if dn and dn != name:
            alts.add(dn)
        alts.discard(name)
        if alts:
            out[name] = sorted(alts)
    return out


def build_sitemap(players: list) -> str:
    """XML sitemap: team pages + player pages + country place-pages (query URLs)."""
    from urllib.parse import quote
    urls = [f"{SITE_BASE_URL}/index.html"]
    urls += [f"{SITE_BASE_URL}/teams.html"]
    urls += [f"{SITE_BASE_URL}/teams.html?team={quote(fr)}" for fr in sorted(NBA_TEAMS)]
    countries = sorted({(s.get("country") or "").strip()
                        for p in players for s in p.get("career_history", [])
                        if (s.get("country") or "").strip()})
    urls += [f"{SITE_BASE_URL}/teams.html?country={quote(c)}" for c in countries]
    names = sorted({p["player"] for p in players if str(p.get("player") or "").strip()})
    urls += [f"{SITE_BASE_URL}/index.html?player={quote(n)}" for n in names]
    body = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{body}\n</urlset>\n")


def build(players: list | None = None) -> dict:
    if players is None:
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

    players = json.loads(CAREERS.read_text(encoding="utf-8"))
    data = build(players)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size:,} bytes)")

    # Related-teams suggestions (shared alumni, then same-country fill), computed
    # once and attached to both franchise and club pages.
    related = compute_related(players)

    # Team pages (Phase 2): a separate file so dashboard_data.json stays lean.
    team_pages = {"generated_from": "data/players/nba_players_careers.json",
                  "teams": w_team_pages(players, related)}
    TEAM_PAGES_OUT.write_text(json.dumps(team_pages, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    print(f"wrote {TEAM_PAGES_OUT.relative_to(ROOT)}  ({TEAM_PAGES_OUT.stat().st_size:,} bytes)")

    # Club pages (Phase 2.6-B): all-time NBA alumni per non-NBA club.
    club_pages = {"generated_from": "data/players/nba_players_careers.json",
                  "clubs": w_club_pages(players, related)}
    CLUB_PAGES_OUT.write_text(json.dumps(club_pages, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    truncated = sum(1 for c in club_pages["clubs"].values() if c.get("truncated"))
    print(f"wrote {CLUB_PAGES_OUT.relative_to(ROOT)}  "
          f"({CLUB_PAGES_OUT.stat().st_size:,} bytes, {len(club_pages['clubs'])} clubs, "
          f"{truncated} truncated at {CLUB_ROSTER_CAP})")

    # Tiny NBA-name -> current-franchise index (Phase 2.6-B): lets index.html
    # resolve a stint's team to its canonical team-page URL (or detect a club)
    # without loading the multi-MB team_pages file. One source of truth (derived
    # from ERA_TABLE) shared by both pages.
    NBA_TEAM_INDEX_OUT.write_text(json.dumps(
        {"franchises": sorted(NBA_TEAMS), "eras": _ERA_TO_CURRENT,
         "collisions": ERA_COLLISIONS},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {NBA_TEAM_INDEX_OUT.relative_to(ROOT)}  "
          f"({NBA_TEAM_INDEX_OUT.stat().st_size:,} bytes)")

    # Player aliases (Phase 2.6-E): alias-aware search on both pages.
    aliases = w_player_aliases(players)
    PLAYER_ALIASES_OUT.write_text(json.dumps(aliases, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
    print(f"wrote {PLAYER_ALIASES_OUT.relative_to(ROOT)}  "
          f"({PLAYER_ALIASES_OUT.stat().st_size:,} bytes, {len(aliases)} players)")

    # Player index (Phase 3): the full name list, so the dashboard homepage has
    # complete search without loading the multi-MB career file.
    names = sorted(p["player"] for p in players if str(p.get("player") or "").strip())
    PLAYER_INDEX_OUT.write_text(json.dumps(names, ensure_ascii=False, indent=0) + "\n",
                                encoding="utf-8")
    print(f"wrote {PLAYER_INDEX_OUT.relative_to(ROOT)}  "
          f"({PLAYER_INDEX_OUT.stat().st_size:,} bytes, {len(names)} names)")

    # sitemap.xml (Phase 2.6-B): team + player URLs for search-engine discovery.
    SITEMAP_OUT.write_text(build_sitemap(players), encoding="utf-8")
    print(f"wrote {SITEMAP_OUT.relative_to(ROOT)}  ({SITEMAP_OUT.stat().st_size:,} bytes, "
          f"base={SITE_BASE_URL})")

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
