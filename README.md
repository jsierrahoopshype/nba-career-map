# NBA Career Database

An automated, self-updating database of NBA player career histories — active
players, rookies/newcomers, and former NBA players still active overseas or in
the G League. Career data is sourced from Wikipedia via the **MediaWiki API**
(no HTML scraping), with team names normalized to their most-recent identity
and mapped to their cities/countries.

The existing `index.html` map reads `nba_players_careers_READY.json`; the
updater keeps that file in sync automatically, so the map keeps working.

> **Repo note:** the original request asked for a separate
> `nba-career-database` repo. This session's access is scoped to the existing
> `nba-career-map` repo, so the system was built here. It is self-contained
> under `data/`, `logs/`, `scripts/`, and `.github/workflows/` and can be moved
> to its own repo unchanged.

## Layout

```
data/
  players/
    nba_players_careers.json   # canonical career database (source of truth)
    active_players.json        # {nba_active:[...], overseas_active:[...]}
    retired_players.json       # no longer playing anywhere
  teams/
    team_aliases.json          # historical/sponsored name -> current name
    team_locations.json        # canonical team -> city/state/country/league
    teams_needing_review.json  # teams with missing/uncertain location
logs/
  update_log.json              # machine-readable history of runs
  changelog.md                 # human-readable change history
scripts/
  wikipedia_api.py             # rate-limited MediaWiki API client
  team_normalizer.py           # applies team_aliases.json
  wiki_parser.py               # parses {{Infobox basketball biography}}
  rosters.py                   # NBA rosters: parses {{player2}} rows (+ metadata)
  names.py                     # name normalization + canonical Wikipedia URL
  geo.py                       # region/US-state -> country resolution
  player_status.py             # tracking-status classification (see below)
  seed_import.py               # one-time import of existing data into /data
  merge_migration.py           # one-time: merge duplicate player pairs
  update_careers.py            # main orchestrator (all modes)
  test_sample.py               # 10-player end-to-end smoke test
nba_players_careers_READY.json # map data file (kept in sync by the updater)
.github/workflows/update-careers.yml
```

## Data extracted per player

Full career history (all teams + years), current team, position, jersey
number(s), birth date/place, death date/place (if applicable), high school,
college, and draft info. Career stints also carry normalized team name plus
city/state/country for mapping.

## Tracking status

Every player record has a `status` field with one of three values (the parse
outcome is stored separately in `parse_status` so the two never collide):

| status | meaning |
|--------|---------|
| `nba_active` | on a current NBA roster (or Wikipedia lists an NBA franchise as their current team) |
| `overseas_active` | no longer in the NBA but still playing (overseas league, G League, …) within the recency window |
| `retired` | no team for 2+ years |

Rosters are read from each team's Wikipedia template, which lists players as
`{{player2 | first=.. | last=.. | num=.. | pos=.. | note=.. | inj=.. }}` rows
(names split across params, no wikilinks). The parser combines first+last
(preserving suffixes like "Jaren Jackson Jr."), captures jersey/position/note/
injury metadata, and ignores the header, coach block, `Category:` links and
high schools. Players flagged `note=FA` (free agent / expiring) **are included**
as roster members: they still appear on the template, and their true status is
decided from their own page by the guard below — excluding them would risk a
real player being mistaken for "dropped from the roster".

Roster membership is only a **candidate** signal (who to fetch); it never by
itself confers `nba_active`, so coaches and staff listed on roster templates
are not marked active. `nba_active` is confirmed only when the fetched page
parses as a player with a **current NBA stint** (a recent/"present" stint on an
NBA franchise). A record with no playing career at all (a pure coach) is
`retired`. Classification (`scripts/player_status.py`) also checks recency
**before** the NBA-team check, so a player whose most recent team is an NBA
franchise but who has not played in 2+ years is `retired`, not `nba_active`. A player who leaves
an NBA roster but whose Wikipedia shows a current overseas team becomes
`overseas_active` — so someone like **Patty Mills**, years removed from the NBA,
keeps getting updated when he changes clubs in Australia.

The seed import classifies with a more lenient gap so borderline players still
enter the overseas re-check queue rather than being stranded as `retired`;
live runs apply the strict 2-year rule against fresh Wikipedia data. (Once a
player is `retired` they are only revisited if they reappear on an NBA roster
or via a manual `single`/`full` run — a documented limitation.)

## Team-name normalization

`data/teams/team_aliases.json` maps historical and sponsored names to the
current canonical name, e.g. `Tau Cerámica → Baskonia`,
`New Jersey Nets → Brooklyn Nets`, `Seattle SuperSonics → Oklahoma City Thunder`,
`Charlotte Bobcats → Charlotte Hornets`. Teams that no longer exist (e.g.
Virtus Roma) keep their historical name. Unknown teams are added to
`teams_needing_review.json` for manual confirmation.

## Deduplication

A player can appear on a roster under a different spelling than the database
uses — diacritics (Şengün/Sengun), transliteration (Schröder/Schroeder),
suffixes (Jr./II), disambiguators (`(basketball)`), initial spacing (A. J./AJ),
or a nickname (Bub/Carlton). Matching is by **canonical Wikipedia article**, not
by raw name string:

- **Pre-fetch** (`build_queue`): a roster candidate is matched to an existing
  record by normalized name key (`names.normkey`), so variants aren't queued as
  newcomers, and their existing record isn't re-fetched as "dropped from roster"
  every run.
- **At-fetch** (`merge_player`): the page's canonical title/URL is resolved; if
  it matches an existing record (catches nicknames and redirects that the name
  key misses) the data is **merged** into that record rather than inserted as a
  duplicate.
- Every record stores `wikipedia_url` (backfilled lazily on fetch); the dropped
  spelling is kept in `aliases` (which are also indexed, so the variant resolves
  and the duplicate can't reappear).

**Display names and the map.** The map/quiz (`index.html`, `nba_players_careers_READY.json`)
key players by `player`, and those keys are ASCII (`PLAYERS_400_GAMES` etc.), so
the primary `player` key is kept stable. The canonical spelling (e.g.
`Alperen Şengün`) lives in `display_name`, which is also exported to READY.json
for the frontend to adopt later; adding it is safe because `index.html` ignores
unknown fields.

## Automation

`.github/workflows/update-careers.yml` runs:

- **In-season (Oct–Jun):** daily at 08:00 UTC → `incremental`
- **Off-season (Jul–Sep):** Mondays at 08:00 UTC → `incremental`
- **Monthly (1st, 09:00 UTC):** → `full_overseas` (re-check every overseas player)

An `incremental` run fetches current NBA rosters and processes, in priority
order: roster newcomers, players who dropped off a roster (re-checked so a move
overseas isn't mistaken for retirement), **all** `overseas_active` players (to
catch club changes), then the least-recently-updated `nba_active` players — all
within the request budget, spilling into later runs. Each run normalizes teams,
updates locations, re-classifies status, commits, and appends a changelog entry:

```
Auto-update: YYYY-MM-DD - X players updated, Y new teams[, Z status changes]
```

### Rate limiting

- Max **100 requests per run** (configurable; roster fetches count toward it)
- **1 second** between requests
- A descriptive `User-Agent` per Wikimedia API etiquette
- When the budget is hit, the run stops cleanly and resumes next run

### Manual trigger (Actions → Run workflow)

| Input | Purpose |
|-------|---------|
| `mode = incremental` | roster newcomers + dropped players + all overseas + stale NBA (default) |
| `mode = full` | refresh all active players, NBA + overseas (bounded by budget) |
| `mode = full_overseas` | re-check **all** `overseas_active` players (runs monthly on schedule) |
| `mode = single` + `player` | refresh one player by name |
| `mode = review` | try to resolve locations for `teams_needing_review.json` |

## Local usage

```bash
# One-time (re)build of /data from the existing dataset
python3 scripts/seed_import.py

# Smoke test against live Wikipedia (10 players, no DB writes)
python3 scripts/test_sample.py

# Run an update
python3 scripts/update_careers.py --mode incremental --max-requests 100
python3 scripts/update_careers.py --mode full_overseas   # re-check all overseas
python3 scripts/update_careers.py --mode single --player "LeBron James"
python3 scripts/update_careers.py --mode review
```

No third-party Python packages are required (standard library only).
