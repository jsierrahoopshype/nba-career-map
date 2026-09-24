# Saved API fixtures

Responses captured from the shapes the live APIs return, so
`scripts/fetch_bio_wikidata.py` can be exercised without reaching
Wikipedia, Wikidata or GitHub.

| file | stands in for |
| --- | --- |
| `careers.json` | a six-player slice of `data/players/nba_players_careers.json` |
| `pageprops-1.json` | `en.wikipedia.org/w/api.php?action=query&prop=pageprops` — includes a redirect (`Redirected Player` → `Renamed Player`) and an article that does not exist |
| `sparql-1.json` | `query.wikidata.org/sparql` — day-precision, month-precision and year-precision dates, a living player, two dead ones, and a place whose label service falls back to a bare item ID |
| `bref.csv` | the Basketball-Reference career CSV, with one row that disagrees with Wikidata and one player Wikidata has no item for |

`FixtureTransport` reads `pageprops-N.json` / `sparql-N.json` in call order and
raises if a run asks for one that was never saved, so a test cannot pass by
quietly making fewer requests than it should.

The three synthetic players (`Early Player`, `Redirected Player` /
`Renamed Player`, `Unknown Player`) are invented, so the edge cases are
exercised without putting made-up facts about a real person in the repo.

Run the tests with `python3 scripts/test_fetch_bio_wikidata.py`.
