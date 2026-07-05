## 2026-07-05 — single run

- Players updated: **1** (0 new)
- New teams discovered: **0**
- Team moves detected: **0**
- Status changes: **0** (0 → overseas, 0 → retired)
- Wikipedia requests: 1

## 2026-07-05 — single run

- Players updated: **1** (0 new)
- New teams discovered: **0**
- Team moves detected: **1**
- Status changes: **1** (0 → overseas, 0 → retired)
- Wikipedia requests: 1
  - Jonathan Kuminga: Santa Cruz Warriors → Atlanta Hawks
  - Jonathan Kuminga: [overseas_active → nba_active]

## 2026-07-04 — dedupe merge migration

- Merged **8** duplicate player pairs (kept ASCII primary key + canonical `display_name` + old spelling as alias)
- Backfilled wikipedia_url/display_name on **110** records
- Expanded leftover `{{nbay}}` year strings on **44** records

  - `Alperen Şengün` → `Alperen Sengun`
  - `Dennis Schröder` → `Dennis Schroeder`
  - `Craig Porter Jr.` → `Craig Porter`
  - `Dereck Lively II` → `Dereck Lively`
  - `Derrick Jones Jr.` → `Derrick Jones`
  - `A. J. Green` → `AJ Green`
  - `A. J. Lawson` → `AJ Lawson`
  - `Bub Carrington` → `Carlton Carrington`

## 2026-07-04 — incremental run

- Players updated: **64** (64 new)
- New teams discovered: **5**
- Team moves detected: **0**
- Status changes: **0** (0 → overseas, 0 → retired)
- Wikipedia requests: 100  ⚠️ budget exhausted — continues next run
- New players: A. J. Green, A. J. Lawson, AJ Dybantsa, Ace Bailey, Aday Mara, Adou Thiero, Alex Morales, Alijah Martin, Allen Graves, Alperen Şengün, Amari Williams, Asa Newell, Baba Miller, Ben Saraf, Bennett Stirtz, Bez Mbeng, Blake Hinson, Bogoljub Marković, Braden Smith, Brayden Burries, Brooks Barnhizer, Bruce Thornton, Bub Carrington, C. J. Huntley, Caleb Wilson …
- New teams: Anagan Olivar, Birmingham/Laketown Squadron, CBP Huesca, College Park Skyhawks, Elitzur Kiryat Ata

## 2026-07-03 — cleanup migration

- Removed **56** non-player records (coaches / `Category:` links / high schools) added by the bad run
- Removed **14** now-orphaned teams
- Deduped `Brian Shaw (basketball)`; real `Brian Shaw` remains retired

## 2026-07-03 — incremental run

- Players updated: **56** (56 new)
- New teams discovered: **14**
- Team moves detected: **0**
- Status changes: **0** (0 → overseas, 0 → retired)
- Wikipedia requests: 100  ⚠️ budget exhausted — continues next run
- New players: Adam Caporn, Alexis Ajinça, Beau Levesque, Ben Sullivan (basketball), Billy Lange, Blaine Mueller, Bob Beyer, Bret Brielmaier, Brett Brown, Brian Keefe, Brian Randle, Brian Shaw (basketball), Bruce Fraser (basketball), Bryan Gates, Carlton J. Kell High School, Category:Atlanta Hawks templates, Category:Boston Celtics templates, Category:Brooklyn Nets templates, Category:Charlotte Hornets templates, Category:Chicago Bulls templates, Category:Cleveland Cavaliers templates, Category:Dallas Mavericks templates, Category:Denver Nuggets templates, Category:Detroit Pistons templates, Category:Golden State Warriors templates …
- New teams: Araberri, Atlanta Aliens, Galil Gilboa, Hapoel Gilboa/Afula, Il Messaggero Roma, Northern Cement, Oviedo, Pinar Karsiyaka, Plymouth Raiders, R.B.C. Verviers-Pepinster, Rethymno, Sheffield Forgers / Sharks, Siarka Tarnobrzeg, Zalakeramia-ZTE KK

## 2026-07-03 — single run

- Players updated: **1** (0 new)
- New teams discovered: **0**
- Team moves detected: **0**
- Wikipedia requests: 1

# Career Database Changelog

## Seed import — initial

- Imported ~5,003 players from `nba_players_careers_READY.json`.
- Normalized all historical team names via `data/teams/team_aliases.json`.
- Derived `data/teams/team_locations.json` from existing city/state/country data.
- Flagged teams with missing location data in `data/teams/teams_needing_review.json`.

Subsequent automated runs append their entries above this line.
