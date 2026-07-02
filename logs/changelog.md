# Career Database Changelog

## Seed import — initial

- Imported ~5,003 players from `nba_players_careers_READY.json`.
- Normalized all historical team names via `data/teams/team_aliases.json`.
- Derived `data/teams/team_locations.json` from existing city/state/country data.
- Flagged teams with missing location data in `data/teams/teams_needing_review.json`.

Subsequent automated runs append their entries above this line.
