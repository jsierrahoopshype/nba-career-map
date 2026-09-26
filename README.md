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
    player_bio.json            # birth/death dates + places (Basketball-Reference + Wikidata)
    bio_needs_review.json      # birth dates that disagree with Basketball-Reference
    player_url_overrides.json  # curated Wikipedia article per player (see below)
  teams/
    team_aliases.json          # historical/sponsored name -> current name
    team_locations.json        # canonical team -> city/state/country/league
    teams_needing_review.json  # teams with missing/uncertain location
    stint_start_dates.json     # curated exact signing dates (see the ledger below)
    stint_corrections.json     # stints the source lists that were never played
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
  signing_guard.py             # newly-DETECTED vs newly-STARTED stint (ledger)
  stint_corrections.py         # re-applies stint_corrections.json after every parse
  fetch_bio_wikidata.py        # birth/death facts from Wikidata (see below)
  player_urls.py               # reads player_url_overrides.json (curated articles)
  resolve_player_urls.py       # finds the real article for a wrong-person record
  merge_club_groups.py         # fold club-name variants into one club
  fix_club_countries.py        # curated place corrections + the UK label sweep
  audit_club_countries.py      # REPORT ONLY: clubs whose country looks wrong
  prune_old_signings.py        # one-off: replay the guard over the old ledger
  seed_import.py               # one-time import of existing data into /data
  merge_migration.py           # one-time: merge duplicate player pairs
  update_careers.py            # main orchestrator (all modes)
  test_sample.py               # 10-player end-to-end smoke test
tests/
  fixtures/                    # saved API responses (offline tests for the bio fetcher)
nba_players_careers_READY.json # map data file (kept in sync by the updater)
.github/workflows/update-careers.yml
.github/workflows/player-bio.yml
.github/workflows/fix-player-urls.yml
```

## Data extracted per player

Full career history (all teams + years), current team, position, jersey
number(s), birth date/place, death date/place (if applicable), high school,
college, and draft info. Career stints also carry normalized team name plus
city/state/country for mapping.

## Tracking status

## The transactions ledger

`data/logs/transactions.json` records one entry per real club change and feeds
the Latest Signings widget, `teams.html?view=signings` and the Slack
`#signings` post. It only ever recorded a move the pipeline *detected*, so a
Wikipedia edit that added or reordered an OLD stint booked a signing dated
today — Lonnie Walker's 2025-26 season at Partizan surfaced as a September 2026
signing. `scripts/signing_guard.py` now gates it: a detected move counts only
when the destination stint STARTED on or after the day the ledger opened
(2026-07-11). Wikipedia stints are year-granular, so the comparison normally
runs at season granularity; where a real signing date is known it goes in
`data/teams/stint_start_dates.json` and is re-stamped onto the stint after
every parse. A move the guard cannot date still posts — losing a real signing
is the worse failure — and is listed in the run summary.

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

## Curated stint removals

`career_history` is rebuilt from the Wikipedia article on every run, so a stint
deleted by hand is back the next morning. Stints the source lists that the
player never actually played are recorded in `data/teams/stint_corrections.json`
instead and dropped again after every parse — the mirror image of the curated
signing dates above.

The case it was built for: Andrei Kirilenko's 2001 line at Partizan. He signed
a contract with the club and left for the Utah Jazz before ever playing for
them, so the map drew a stop in Belgrade he never made.

```bash
python3 scripts/stint_corrections.py           # report what the rules would drop
python3 scripts/stint_corrections.py --apply   # apply to the DB + the map file
```

Matching is on player + team + start year, so an edit that reshapes the span
(`2001` → `2001-2002`) does not quietly stop matching; leave `start_year` out
to drop every stint the player has at that club. Every rule carries a `reason`,
and `scripts/test_stint_corrections.py` fails if the shipped database has
drifted back out of line with the rules.

## Birth and death facts

`scripts/fetch_bio_wikidata.py` writes `data/players/player_bio.json`, keyed by
the same player name the career database uses:

```json
"Kobe Bryant": {
  "birth_date": "1978-08-23", "death_date": "2020-01-26",
  "birth_place": "Philadelphia", "death_place": "Calabasas",
  "wikidata_id": "Q25369", "source": "basketball-reference",
  "checked": "2026-09-24"
}
```

`source` names where `birth_date` came from and nothing else —
`basketball-reference`, `wikidata`, or `unresolved`. `wikidata_id` is the item
the places and the death date came from, empty when no item passed the identity
gate. Two optional keys appear only when they have something to say:
`wikidata_birth_date` (the value Basketball-Reference beat, kept so the review
file can still report the disagreement) and `rejected_wikidata_id` (the
namesake the gate turned down).

Three batched hops: the Wikipedia `pageprops` API (50 titles a request) maps
each article to its Wikidata item, then one SPARQL query per 250 items reads
P569 (birth date), P570 (death date), P19 (place of birth), P20 (place of
death) and the gate flag, and a third goes looking for replacements. The dates
come off the statement's value node, not the `wdt:` shortcut, because only the
value node carries the **precision** — a date Wikidata knows only to the year
stays `"1922"` rather than being padded to January 1st, in the JSON-LD as well
as the file. Statements Wikidata has marked *deprecated* (its way of saying a
value is known to be wrong) are skipped.

- **The identity gate.** A name like Ace Bailey, Michael Phelps or Reggie
  Jackson resolves to a namesake: an ice hockey player who died in 1992, a
  swimmer, a baseball Hall of Famer. An item is only believed when Wikidata
  says P106 (occupation) = basketball player (`Q3665646`) or P641 (sport) =
  basketball (`Q5372`). One that fails has **all** of its dates and places
  discarded; the run then searches Wikidata for a basketball player of that
  name born within a year of Basketball-Reference's date, and takes it only
  when there is exactly one candidate.
- **Source precedence.** Basketball-Reference
  (`sumitrodatta/bball-reference-datasets`) is keyed to the actual NBA player,
  so it decides the birth date whenever it has one; the gated Wikidata value is
  the fallback. The death date and both places are Wikidata's alone, and only
  from a gated item whose birth year is within a year of
  Basketball-Reference's — a right-occupation item with the wrong birth year is
  still likely a different basketball player, and his home town is not this
  player's.
- **Incremental.** A run resolves the players missing from the file, and
  re-checks living players whose record is over `--refresh-days` (7) old so a
  death is picked up within the week. `--refresh-limit` spreads that sweep over
  several days instead of re-reading everyone at once. `--full` re-reads
  everyone and **rewrites every record from scratch** under the current rules,
  which is what to run after the rules themselves change.
- **Re-appliable offline.** `--reapply` re-runs the rules over the records
  already on file without touching Wikipedia or Wikidata. It cannot consult
  P106, so it stands in the strongest signal a stored record still carries: a
  Wikidata birth year five or more years from Basketball-Reference's is a
  namesake, not a disputed date. A later `--full` run re-checks all of it
  against the real gate.
- **Fails loudly.** If Wikidata, Wikipedia or the Basketball-Reference mirror
  cannot be reached the run exits non-zero and writes **nothing** — an empty
  file would strip the dates out of every player page's structured data.

`data/players/bio_needs_review.json` is the human's queue, in three lists —
`wrong_entity` (the gate failed: what it had resolved to, and whether a
replacement was found), `date_disagreement` (the same person, different dates,
and which one the record uses) and `still_missing` (no birth date from either
source) — plus `wikipedia_url_wrong_person`, the wrong-entity players whose
stored Wikipedia URL is the namesake's article, so their **club history** may
be wrong too. Nothing in it edits the career database.

The facts reach no Career Map page. They are not printed, and they are **not**
in each page's schema.org `Person` block either: `birthDate`, `birthPlace`,
`deathDate` and `deathPlace` were published there and have been removed, because
that data belongs to a separate section of the site. The `Person` block keeps the
player's name, page URL, nationality and (when it is his — see below) Wikipedia
link. `player_bio.json` and `.github/workflows/player-bio.yml` are unchanged and
still kept current — `scripts/prerender.py` simply does not read them.

```bash
python3 scripts/fetch_bio_wikidata.py                   # incremental
python3 scripts/fetch_bio_wikidata.py --limit 500       # bounded backfill
python3 scripts/fetch_bio_wikidata.py --full            # rewrite everyone
python3 scripts/fetch_bio_wikidata.py --reapply         # offline re-apply
python3 scripts/fetch_bio_wikidata.py --fixtures tests/fixtures --dry-run
python3 scripts/test_fetch_bio_wikidata.py              # offline, no network
```

## Wrong-person Wikipedia articles

The scraper asks Wikipedia for a name and takes the article it gets back. For
`David Duke`, `Jack White`, `Ace Bailey`, `Michael Phelps` or `Mike Lynn` that
article is the Klansman, the guitarist, the ice hockey player, the swimmer and
the Minnesota Vikings general manager — and the club history parsed off it became
the NBA player's. `same_person()` cannot help: the names are identical.

`data/players/player_url_overrides.json` is the fix that sticks. It holds one
curated article per player and is read by `scripts/player_urls.py`, which both
`update_careers.py` (in `right_article`) and `fetch_bio_wikidata.py` (in
`_title_of`) consult **before** asking Wikipedia anything, so a daily run cannot
revert it. Two tiers, and only one is live:

- **`overrides`** — verified. Written by `scripts/resolve_player_urls.py` only
  after Wikidata vouched for the article: `P106 = Q3665646` (basketball player)
  **and** a birth year within 1 of Basketball-Reference's for this player. These
  are what the pipeline uses. A hand-written entry whose value is a bare URL
  string also counts as verified — a human typing an article in *is* the
  verification.
- **`human_verified`** — a person's decision, staged. Each entry is
  `{"wikipedia_url": ..., "verified_by": "jorge"}`. The resolver writes it
  through to `overrides` **without** the P106 gate or the ambiguity check, once
  Wikipedia confirms the article exists and is not (and does not redirect to) a
  disambiguation page; otherwise the player is skipped, listed in the run
  summary, and stays staged. The collision guard (an article another record
  already holds) still applies. The Basketball-Reference birth year is compared
  and reported as a warning, never a block. The written entry carries
  `"human_verified": true`, which tells `fetch_bio_wikidata.py` to take the
  article's Wikidata item as this player even when it lacks P106.
- **`candidates`** — un-verified guesses from Wikipedia's naming conventions
  (`Ron Holland II`, `A. J. Green (basketball)`). The resolver tries them first;
  nothing here reaches the site.

An overridden record's career is **replaced**, not merged: `_richer()` exists to
stop a thin parse clobbering good data, and would otherwise protect the wrong
man's career. An article that parses to nothing is still refused.

### `sameAs` on the player pages

`sameAs` tells a crawler "this page and that page are about the same person", so
it is the one field on a prerendered page that can actively assert something
false. `prerender.wikipedia_link()` decides it from the same two files:

1. a curated article in `player_url_overrides.json` **is** the link, whatever the
   career record still says;
2. otherwise, a player listed in `wikipedia_url_wrong_person` gets **no `sameAs`
   at all** — silence is a missing field, a namesake's URL is a false claim;
3. otherwise, the record's `wikipedia_url`, as before.

A player drops off that list as soon as his curated article is written, so the
suppression lifts by itself as the repairs land.

```bash
python3 scripts/resolve_player_urls.py audit      # offline: what the flagged records show
python3 scripts/resolve_player_urls.py resolve    # dry run
python3 scripts/resolve_player_urls.py resolve --apply
python3 scripts/resolve_player_urls.py settle     # drop the repaired players from the wrong-person list
python3 scripts/update_careers.py --mode override # re-scrape the repaired players
python3 scripts/test_player_urls.py               # offline, no network
```

`.github/workflows/fix-player-urls.yml` (Actions → Run workflow) does the whole
sequence: audit, resolve, write the overrides, re-scrape, drop the repaired
players from `wikipedia_url_wrong_person`, rebuild the pages, commit.

| Input | Purpose |
|-------|---------|
| `players` | only these players (comma-separated; default: everything flagged) |
| `dry_run` | resolve and report only — write nothing, commit nothing |
| `rescrape` | re-scrape the repaired players' careers (default true) |
| `max_requests` | Wikipedia request budget for the re-scrape (default 200) |
| `search_limit` | Wikipedia search hits considered per player (default 8) |

After the P106 gate, three tie-breaks run in order:

1. **Exact name** — only an item with an article titled with the player's own
   name counts (parenthetical and `Jr.`/`Sr.`/`II`/`III`/`IV` stripped, accents
   folded). Aaron Harrison is not a second Andrew Harrison. Redirect aliases are
   one item: `Cat Barber` and `Anthony Barber (basketball)` are both Q16209351.
2. **Career window** — with no Basketball-Reference birth date, a candidate
   must be born 17–24 years before the player's first stint in
   `nba_players_careers.json`.
3. **Namesake** — the item flagged for this record is never accepted back.

Exactly one item left is accepted, and the summary's **Decided by** column
names the rules it needed. Players the resolver still cannot settle — nothing
left, or two basketball players of the name in range — are listed in the run
summary and in `logs/player_url_resolution.json`. It never guesses.

A player with a verified override leaves `wikipedia_url_wrong_person` in the
same run, and `fetch_bio_wikidata.py` keeps him off it. His `wrong_entity` row
and the rejected-item note in `player_bio.json` stay until his bio record is
re-read (`player-bio.yml` with `mode = full`).

## Team-name normalization

`data/teams/team_aliases.json` maps historical and sponsored names to the
current canonical name, e.g. `Tau Cerámica → Baskonia`,
`New Jersey Nets → Brooklyn Nets`, `Seattle SuperSonics → Oklahoma City Thunder`,
`Charlotte Bobcats → Charlotte Hornets`. Teams that no longer exist (e.g.
Virtus Roma) keep their historical name. Unknown teams are added to
`teams_needing_review.json` for manual confirmation.

**Club merges go in the alias table, not just in the data.** Rewriting the
stored stints alone lasts one pipeline run: the daily pass re-parses each page
and whatever spelling the article carries comes back. Every merge is therefore
written into `team_aliases.json` (see `scripts/merge_club_groups.py`), which is
what `TeamNormalizer` consults on the way in — so the club cannot split again,
and the move detector stays quiet because `classify_move` normalizes both sides
before comparing. An alias must never point at another alias: the normalizer
follows exactly one hop, and an alias may never be a club's own surviving
name (flipping a merge's direction leaves exactly that, and it maps the club
off its own name).

**One field, two clubs.** A `teamN` field can name two clubs — a rename the
player's spell ran through — written either with an interpunct
(`[[Old]]·[[New]]`) or as two wikilinks side by side. The second shape used to
run the names together into a club nobody could look up
(`Anyang KGCAnyang Jung Kwan Jang Red Boosters`). `wiki_parser` now separates
adjacent links before stripping the markup and stores the FIRST name, which is
always complete where a later segment is often an abbreviated continuation;
the whole field is kept in `team_raw`. Slash-joined names are deliberately
left alone — `split_combined_teams.py` resolves those by year majority.

**Country labels.** `geo.COUNTRY_ALIASES` folds England/Scotland/Wales/Northern
Ireland into `United Kingdom`, so that is the one label UK clubs carry;
`scripts/fix_club_countries.py` sweeps any stragglers and holds the curated
place corrections. `scripts/audit_club_countries.py` reports — and never
writes — clubs that share a city name but not a country, and clubs labelled
`USA` whose city this project attests as non-US somewhere else.

**Retired URLs.** A prerendered page whose subject legitimately disappears is
deleted, but a URL that was published and whose subject MOVED gets a stub
instead: `prerender.REDIRECTS` lists them, and each keeps a real file with a
meta refresh and a canonical pointing at the survivor (GitHub Pages has no
server redirects). Stubs are `noindex` and never enter `sitemap.xml`.

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

The daily run also refreshes player birth/death facts (see above) before the
data rebuild. That step is `continue-on-error`: the fetcher writes nothing when
Wikidata is unreachable, and a Wikidata outage must not throw away the career
update the same run just did.

`.github/workflows/player-bio.yml` is the by-hand one (Actions → Run workflow):
the first full backfill, a re-run after an outage, or a `--full` re-fetch.

| Input | Purpose |
|-------|---------|
| `mode = incremental` | players missing from `player_bio.json` + the weekly death re-checks (default) |
| `mode = full` | re-read everyone and rewrite every record from scratch under the current rules |
| `mode = reapply` | offline: re-apply the rules to what is already on file, no Wikidata |
| `limit` | cap how many players this run resolves (blank = all) |
| `refresh_limit` | cap the living-player death sweep (default 1000) |
| `rebuild_pages` | rebuild the prerendered pages afterwards (default true) |

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
| `mode = override` | re-scrape only the players with a curated article in `player_url_overrides.json` |
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
