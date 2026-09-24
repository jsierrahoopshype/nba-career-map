# Saved API fixtures

Responses captured from the shapes the live APIs return, so
`scripts/fetch_bio_wikidata.py` can be exercised without reaching
Wikipedia, Wikidata or GitHub.

| file | stands in for |
| --- | --- |
| `careers.json` | an eight-player slice of `data/players/nba_players_careers.json` |
| `pageprops-1.json` | `en.wikipedia.org/w/api.php?action=query&prop=pageprops` — includes a redirect (`Redirected Player` → `Renamed Player`) and an article that does not exist |
| `sparql-1.json` | `query.wikidata.org/sparql` — day-, month- and year-precision dates, a living player, two dead ones, a place whose label service falls back to a bare item ID, and two items the identity gate turns down (`?bball` false) |
| `sparql-2.json` | the replacement search that follows a rejection — the basketball player of that name born the year Basketball-Reference says, plus a decoy born decades earlier |
| `bref.csv` | the Basketball-Reference career CSV, with one row that disagrees with Wikidata, one player Wikidata has no item for, and one player (`Early Player`) it has no row for at all |

`FixtureTransport` reads `pageprops-N.json` / `sparql-N.json` in call order and
raises if a run asks for one that was never saved, so a test cannot pass by
quietly making fewer requests than it should. The facts query is the first
SPARQL call of a run and the replacement search is the second, which is why
they are numbered that way.

The five synthetic players (`Early Player`, `Redirected Player` /
`Renamed Player`, `Unknown Player`, `Namesake Player`, `Lost Player`) are
invented, so the edge cases are exercised without putting made-up facts about
a real person in the repo. `Namesake Player` is the shape the gate exists for:
the article resolves to someone else entirely (here an ice hockey player who
died in 1992), and the right item has to be found by name and birth year.
`Lost Player` is the same trap with no right item to find.

Run the tests with `python3 scripts/test_fetch_bio_wikidata.py`.
