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
    active_players.json        # current NBA + still-active players
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
  rosters.py                   # current NBA rosters from Wikipedia templates
  seed_import.py               # one-time import of existing data into /data
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

## Team-name normalization

`data/teams/team_aliases.json` maps historical and sponsored names to the
current canonical name, e.g. `Tau Cerámica → Baskonia`,
`New Jersey Nets → Brooklyn Nets`, `Seattle SuperSonics → Oklahoma City Thunder`,
`Charlotte Bobcats → Charlotte Hornets`. Teams that no longer exist (e.g.
Virtus Roma) keep their historical name. Unknown teams are added to
`teams_needing_review.json` for manual confirmation.

## Automation

`.github/workflows/update-careers.yml` runs:

- **In-season (Oct–Jun):** daily at 08:00 UTC
- **Off-season (Jul–Sep):** Mondays at 08:00 UTC

Each run fetches current NBA rosters, diffs them against the database, then
fetches + parses Wikipedia pages for new/stale players, normalizes teams,
updates locations, commits the changes, and appends a changelog entry:

```
Auto-update: YYYY-MM-DD - X players updated, Y new teams
```

### Rate limiting

- Max **100 requests per run** (configurable; roster fetches count toward it)
- **1 second** between requests
- A descriptive `User-Agent` per Wikimedia API etiquette
- When the budget is hit, the run stops cleanly and resumes next run

### Manual trigger (Actions → Run workflow)

| Input | Purpose |
|-------|---------|
| `mode = incremental` | roster newcomers + least-recently-updated active players (default) |
| `mode = full` | refresh all active players (bounded by budget, spills across runs) |
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
python3 scripts/update_careers.py --mode single --player "LeBron James"
python3 scripts/update_careers.py --mode review
```

No third-party Python packages are required (standard library only).
